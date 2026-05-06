import os
import subprocess
import tempfile

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = __import__("logging").getLogger("clip_synth.subtitle_preview")


class _PreviewFrame(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dialog: SubtitlePreviewDialog | None = None
        self._dragging = False
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._offset_x = 0.5
        self._offset_y = 0.9
        self._bg_w = 0
        self._bg_h = 0
        self._source_pixmap: QPixmap | None = None

    def set_dialog(self, dialog):
        self._dialog = dialog
        self.setMouseTracking(True)

    def set_offset(self, x: float, y: float):
        self._offset_x = x
        self._offset_y = y

    def get_offset(self):
        return (self._offset_x, self._offset_y)

    def set_bg_size(self, w: int, h: int):
        self._bg_w = w
        self._bg_h = h

    def set_source_pixmap(self, pixmap: QPixmap):
        self._source_pixmap = pixmap
        self._update_display()

    def _update_display(self):
        if self._source_pixmap is None:
            return
        scaled = self._source_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        super().setPixmap(scaled)

    def setPixmap(self, pixmap):
        self._source_pixmap = pixmap
        self._update_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def _get_subtitle_rect(self):
        pw = self.width()
        ph = self.height()
        if pw <= 0 or ph <= 0 or self._bg_w <= 0 or self._bg_h <= 0 or self._source_pixmap is None:
            return None
        src_w = self._source_pixmap.width()
        src_h = self._source_pixmap.height()
        if src_w <= 0 or src_h <= 0:
            return None
        scaled = self._source_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        display_w = scaled.width()
        display_h = scaled.height()

        src_bg_x = int(src_w * self._offset_x - self._bg_w // 2)
        src_bg_y = int(src_h * self._offset_y - self._bg_h // 2)
        src_bg_x = max(4, min(src_bg_x, src_w - self._bg_w - 4))
        src_bg_y = max(4, min(src_bg_y, src_h - self._bg_h - 4))

        scale_x = display_w / src_w
        scale_y = display_h / src_h
        offset_x_display = (self.width() - display_w) / 2
        offset_y_display = (self.height() - display_h) / 2

        bg_x = int(src_bg_x * scale_x + offset_x_display)
        bg_y = int(src_bg_y * scale_y + offset_y_display)
        bg_w = int(self._bg_w * scale_x)
        bg_h = int(self._bg_h * scale_y)

        return QRect(bg_x, bg_y, bg_w, bg_h)

    def mousePressEvent(self, event):
        rect = self._get_subtitle_rect()
        if rect and rect.contains(event.pos().x(), event.pos().y()):
            self._dragging = True
            self._drag_start_x = event.pos().x()
            self._drag_start_y = event.pos().y()
            self.setCursor(Qt.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            pw = self.width()
            ph = self.height()
            if pw > 0 and ph > 0:
                self._offset_x = event.pos().x() / pw
                self._offset_y = event.pos().y() / ph
                self._offset_x = max(0.05, min(0.95, self._offset_x))
                self._offset_y = max(0.05, min(0.95, self._offset_y))
            if self._dialog:
                self._dialog.on_drag_position(self._offset_x, self._offset_y)
        else:
            rect = self._get_subtitle_rect()
            if rect and rect.contains(event.pos().x(), event.pos().y()):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
        else:
            super().mouseReleaseEvent(event)


class SubtitlePreviewDialog(QDialog):
    SAMPLE_TEXTS = [
        "这是一个示例字幕文字，用于预览效果",
        "在古老的东方，有一座神秘的山脉",
        "他深深地吸了一口气，继续向前走去",
        "画面中展现了一个令人惊叹的场景",
    ]

    def __init__(
        self,
        video_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("字幕预览")
        self.setObjectName("subtitlePreviewDialog")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)

        self._video_path = video_path
        self._current_time: float = 60.0
        self._video_duration: float = 300.0
        self._frame_pixmap: QPixmap | None = None
        self._rendered_pixmap: QPixmap | None = None
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._do_extract_and_render)

        self._setup_ui()
        self._load_video_info()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left_panel = QFrame()
        left_panel.setObjectName("previewLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 8, 16)
        left_layout.setSpacing(8)

        self._preview_label = _PreviewFrame()
        self._preview_label.setObjectName("previewFrame")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(640, 360)
        self._preview_label.setText("正在加载视频帧...\n提示：字幕可拖拽调整位置")
        self._preview_label.set_dialog(self)
        left_layout.addWidget(self._preview_label, stretch=1)

        time_row = QHBoxLayout()
        time_row.setSpacing(12)

        self._time_slider = QSlider(Qt.Horizontal)
        self._time_slider.setObjectName("previewTimeSlider")
        self._time_slider.setRange(0, 1000)
        self._time_slider.setValue(200)
        self._time_slider.valueChanged.connect(self._on_time_slider_changed)
        self._time_slider.sliderReleased.connect(self._on_slider_released)
        time_row.addWidget(self._time_slider)

        self._time_label = QLabel("00:01:00")
        self._time_label.setObjectName("previewTimeLabel")
        self._time_label.setFixedWidth(80)
        self._time_label.setAlignment(Qt.AlignCenter)
        time_row.addWidget(self._time_label)

        left_layout.addLayout(time_row)

        hint_label = QLabel("💡 提示：在预览画面上直接拖拽字幕可自由调整位置")
        hint_label.setObjectName("previewHintLabel")
        hint_label.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(hint_label)

        layout.addWidget(left_panel, stretch=1)

        right_panel = QScrollArea()
        right_panel.setObjectName("previewRightPanel")
        right_panel.setWidgetResizable(True)
        right_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_panel.setFixedWidth(280)

        right_content = QWidget()
        right_content.setObjectName("previewRightContent")
        right_layout = QVBoxLayout(right_content)
        right_layout.setContentsMargins(16, 20, 16, 20)
        right_layout.setSpacing(16)

        settings_title = QLabel("字幕设置")
        settings_title.setObjectName("previewSettingsTitle")
        right_layout.addWidget(settings_title)

        text_label = QLabel("预览文字")
        text_label.setObjectName("paramLabel")
        right_layout.addWidget(text_label)

        self._text_combo = QComboBox()
        self._text_combo.setObjectName("previewTextCombo")
        self._text_combo.addItems(self.SAMPLE_TEXTS)
        self._text_combo.currentIndexChanged.connect(self._on_setting_changed)
        right_layout.addWidget(self._text_combo)

        font_label = QLabel("字体")
        font_label.setObjectName("paramLabel")
        right_layout.addWidget(font_label)

        self._font_combo = QComboBox()
        self._font_combo.setObjectName("previewFontCombo")
        self._font_combo.addItems(["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "Arial"])
        self._font_combo.currentIndexChanged.connect(self._on_setting_changed)
        right_layout.addWidget(self._font_combo)

        size_label = QLabel("大小")
        size_label.setObjectName("paramLabel")
        right_layout.addWidget(size_label)

        self._size_combo = QComboBox()
        self._size_combo.setObjectName("previewSizeCombo")
        self._size_combo.setEditable(True)
        self._size_combo.addItems([str(i) for i in range(30, 61)])
        self._size_combo.setCurrentText("40")
        self._size_combo.currentIndexChanged.connect(self._on_setting_changed)
        right_layout.addWidget(self._size_combo)

        pos_label = QLabel("位置预设（拖拽可自由定位）")
        pos_label.setObjectName("paramLabel")
        right_layout.addWidget(pos_label)

        self._pos_combo = QComboBox()
        self._pos_combo.setObjectName("previewPosCombo")
        self._pos_combo.addItem("底部", "bottom")
        self._pos_combo.addItem("中部", "middle")
        self._pos_combo.addItem("顶部", "top")
        self._pos_combo.currentIndexChanged.connect(self._on_preset_changed)
        right_layout.addWidget(self._pos_combo)

        color_row = QHBoxLayout()
        color_row.setSpacing(12)

        fg_col = QVBoxLayout()
        fg_col.setSpacing(4)
        fg_label = QLabel("字体颜色")
        fg_label.setObjectName("paramLabel")
        fg_col.addWidget(fg_label)
        self._fg_color_btn = QPushButton()
        self._fg_color_btn.setObjectName("previewFgColorBtn")
        self._fg_color = QColor("#FFFFFF")
        self._fg_color_btn.setStyleSheet(f"background-color: {self._fg_color.name()};")
        self._fg_color_btn.setFixedSize(36, 36)
        self._fg_color_btn.setCursor(Qt.PointingHandCursor)
        self._fg_color_btn.clicked.connect(self._on_fg_color_clicked)
        fg_col.addWidget(self._fg_color_btn)
        color_row.addLayout(fg_col)

        bg_col = QVBoxLayout()
        bg_col.setSpacing(4)
        bg_label = QLabel("背景色")
        bg_label.setObjectName("paramLabel")
        bg_col.addWidget(bg_label)
        self._bg_color_btn = QPushButton()
        self._bg_color_btn.setObjectName("previewBgColorBtn")
        self._bg_color = QColor("#000000")
        self._bg_color_btn.setStyleSheet(f"background-color: {self._bg_color.name()};")
        self._bg_color_btn.setFixedSize(36, 36)
        self._bg_color_btn.setCursor(Qt.PointingHandCursor)
        self._bg_color_btn.clicked.connect(self._on_bg_color_clicked)
        bg_col.addWidget(self._bg_color_btn)
        color_row.addLayout(bg_col)

        right_layout.addLayout(color_row)

        opacity_label = QLabel("背景透明度")
        opacity_label.setObjectName("paramLabel")
        right_layout.addWidget(opacity_label)

        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setObjectName("previewOpacitySlider")
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(50)
        self._opacity_slider.valueChanged.connect(self._on_setting_changed)
        right_layout.addWidget(self._opacity_slider)

        right_layout.addStretch()

        close_btn = QPushButton("确认并应用")
        close_btn.setObjectName("previewCloseBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        right_layout.addWidget(close_btn)

        right_panel.setWidget(right_content)
        layout.addWidget(right_panel)

    def _load_video_info(self) -> None:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                self._video_path,
            ]
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
            if result.returncode == 0:
                duration_str = result.stdout.strip()
                if duration_str:
                    duration = float(duration_str)
                    if duration > 0:
                        self._video_duration = duration
                        mid_time = min(duration * 0.3, 60.0)
                        self._current_time = mid_time
                        slider_val = int((mid_time / duration) * 1000)
                        self._time_slider.blockSignals(True)
                        self._time_slider.setValue(slider_val)
                        self._time_slider.blockSignals(False)
                        self._update_time_label()
        except Exception as e:
            logger.warning(f"获取视频时长失败: {e}")

        self._extract_and_render()

    def _on_time_slider_changed(self, value: int) -> None:
        self._current_time = (value / 1000.0) * self._video_duration
        self._update_time_label()

    def _on_slider_released(self) -> None:
        self._extract_and_render()

    def _update_time_label(self) -> None:
        total_sec = int(self._current_time)
        h, m, s = total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
        self._time_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _on_setting_changed(self) -> None:
        self._render_subtitle_on_frame()

    def _on_preset_changed(self) -> None:
        position = self._pos_combo.currentData()
        if position == "top":
            self._preview_label.set_offset(0.5, 0.1)
        elif position == "middle":
            self._preview_label.set_offset(0.5, 0.5)
        else:
            self._preview_label.set_offset(0.5, 0.9)
        self._render_subtitle_on_frame()

    def on_drag_position(self, x: float, y: float) -> None:
        self._pos_combo.blockSignals(True)
        self._pos_combo.setCurrentIndex(-1)
        self._pos_combo.blockSignals(False)
        self._render_subtitle_on_frame()

    def _on_fg_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._fg_color, self, "选择字体颜色")
        if color.isValid():
            self._fg_color = color
            self._fg_color_btn.setStyleSheet(f"background-color: {color.name()};")
            self._render_subtitle_on_frame()

    def _on_bg_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._bg_color, self, "选择背景颜色")
        if color.isValid():
            self._bg_color = color
            self._bg_color_btn.setStyleSheet(f"background-color: {color.name()};")
            self._render_subtitle_on_frame()

    def _extract_and_render(self) -> None:
        self._preview_label.setText("正在加载...")
        self._debounce_timer.start(250)

    def _do_extract_and_render(self) -> None:
        try:
            tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp_file.name
            tmp_file.close()

            time_str = self._format_ffmpeg_time(self._current_time)
            cmd = [
                "ffmpeg", "-y",
                "-ss", time_str,
                "-i", self._video_path,
                "-vframes", "1",
                "-q:v", "2",
                tmp_path,
            ]
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                cmd, capture_output=True, text=False,
                timeout=30, **kwargs
            )
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
                logger.warning(f"提取帧失败: {stderr[:200]}")
                self._preview_label.setText("无法加载视频帧")
                self._cleanup_temp(tmp_path)
                return

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                self._preview_label.setText("无法加载视频帧")
                self._cleanup_temp(tmp_path)
                return

            pixmap = QPixmap(tmp_path)
            if pixmap.isNull():
                self._preview_label.setText("无法加载视频帧")
                self._cleanup_temp(tmp_path)
                return

            self._frame_pixmap = pixmap
            self._render_subtitle_on_frame()
            self._cleanup_temp(tmp_path)

        except Exception as e:
            logger.warning(f"提取帧异常: {e}")
            self._preview_label.setText("无法加载视频帧")

    def _render_subtitle_on_frame(self) -> None:
        if self._frame_pixmap is None:
            return

        frame = self._frame_pixmap
        max_w = 640
        max_h = 480
        if frame.width() > max_w or frame.height() > max_h:
            scale = min(max_w / frame.width(), max_h / frame.height())
            preview_w = int(frame.width() * scale)
            preview_h = int(frame.height() * scale)
        else:
            preview_w = frame.width()
            preview_h = frame.height()
        preview_w = max(preview_w, 1)
        preview_h = max(preview_h, 1)
        frame = frame.scaled(preview_w, preview_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        text = self._text_combo.currentText()
        font_name = self._font_combo.currentText()
        font_size = int(self._size_combo.currentText())
        fg_color = self._fg_color
        bg_color = self._bg_color
        opacity = self._opacity_slider.value()

        # 预览画面被缩放了，按缩放比例等比缩小字号，实现所见即所得
        preview_scale = frame.width() / self._frame_pixmap.width() if self._frame_pixmap.width() > 0 else 1.0
        preview_font_size = max(1, int(font_size * preview_scale))
        preview_pad_x = max(2, int(16 * preview_scale))
        preview_pad_y = max(2, int(10 * preview_scale))
        preview_radius = max(2, int(8 * preview_scale))

        offset_x, offset_y = self._preview_label.get_offset()

        img = QImage(frame.size(), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.drawPixmap(0, 0, frame)

        font = QFont(font_name, preview_font_size)
        painter.setFont(font)

        fm = painter.fontMetrics()
        text_h = fm.height()
        text_w = fm.horizontalAdvance(text)

        bg_w = text_w + preview_pad_x * 2
        bg_h = text_h + preview_pad_y * 2
        bg_x = int(img.width() * offset_x - bg_w // 2)
        bg_y = int(img.height() * offset_y - bg_h // 2)

        bg_x = max(4, min(bg_x, img.width() - bg_w - 4))
        bg_y = max(4, min(bg_y, img.height() - bg_h - 4))

        self._preview_label.set_bg_size(bg_w, bg_h)

        bg_color_alpha = QColor(
            bg_color.red(), bg_color.green(), bg_color.blue(),
            int(opacity * 2.55),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_color_alpha)
        painter.drawRoundedRect(bg_x, bg_y, bg_w, bg_h, preview_radius, preview_radius)

        text_rect = QRect(
            bg_x + preview_pad_x, bg_y + preview_pad_y,
            text_w, text_h,
        )

        painter.setPen(fg_color)
        painter.drawText(text_rect, Qt.AlignCenter, text)

        painter.end()

        result = QPixmap.fromImage(img)
        self._rendered_pixmap = result
        self._preview_label.set_source_pixmap(result)

    @staticmethod
    def _format_ffmpeg_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    @staticmethod
    def _cleanup_temp(path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    def get_settings(self) -> dict:
        pos_data = self._pos_combo.currentData()
        offset_x, offset_y = self._preview_label.get_offset()
        return {
            "font": self._font_combo.currentText(),
            "font_size": int(self._size_combo.currentText()),
            "font_color": self._fg_color.name(),
            "bg_color": self._bg_color.name(),
            "bg_opacity": self._opacity_slider.value(),
            "position": pos_data if pos_data else "bottom",
            "offset_x": offset_x,
            "offset_y": offset_y,
        }
