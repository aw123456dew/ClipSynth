import os
import subprocess
import tempfile

from PySide6.QtCore import QRect, Qt, QTimer, Signal
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


class _MaskPreviewFrame(QLabel):
    """带遮罩的预览帧，支持拖拽和拉伸调整遮罩"""
    
    mask_changed = Signal(QRect)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None
        self._mask_rect = QRect(100, 100, 200, 80)  # x, y, w, h in source pixmap coordinates
        self._dragging = False
        self._resizing = False
        self._resize_edge = None  # 'n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_start_rect = QRect()
        self.setMouseTracking(True)
        self._display_offset_x = 0
        self._display_offset_y = 0
        self._scale_x = 1.0
        self._scale_y = 1.0
    
    def set_source_pixmap(self, pixmap: QPixmap):
        self._source_pixmap = pixmap
        self._update_display()
    
    def _update_display(self):
        if self._source_pixmap is None:
            return
        
        # 计算缩放和偏移
        scaled = self._source_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self._display_offset_x = (self.width() - scaled.width()) / 2
        self._display_offset_y = (self.height() - scaled.height()) / 2
        self._scale_x = scaled.width() / self._source_pixmap.width()
        self._scale_y = scaled.height() / self._source_pixmap.height()
        
        # 创建带遮罩的图像
        result = QPixmap(scaled.size())
        result.fill(Qt.transparent)
        painter = QPainter(result)
        painter.drawPixmap(0, 0, scaled)
        
        # 获取 scaled 坐标下的遮罩矩形（不包含偏移）
        scaled_mask = self._get_scaled_mask_rect()
        if scaled_mask:
            # 半透明黑色遮罩
            painter.setBrush(QColor(0, 0, 0, 128))
            painter.setPen(QColor(147, 197, 253, 200))
            painter.drawRect(scaled_mask)
            
            # 绘制调整手柄
            self._draw_resize_handles(painter, scaled_mask)
        
        painter.end()
        super().setPixmap(result)
    
    def _get_scaled_mask_rect(self):
        """获取 scaled 图像坐标下的遮罩矩形（不包含偏移）"""
        if self._source_pixmap is None:
            return None
        
        x = int(self._mask_rect.x() * self._scale_x)
        y = int(self._mask_rect.y() * self._scale_y)
        w = int(self._mask_rect.width() * self._scale_x)
        h = int(self._mask_rect.height() * self._scale_y)
        
        return QRect(x, y, w, h)
    
    def _get_display_mask_rect(self):
        """获取显示坐标下的遮罩矩形（相对于控件，包含偏移）"""
        if self._source_pixmap is None:
            return None
        
        scaled_mask = self._get_scaled_mask_rect()
        if scaled_mask:
            return QRect(
                scaled_mask.x() + int(self._display_offset_x),
                scaled_mask.y() + int(self._display_offset_y),
                scaled_mask.width(),
                scaled_mask.height()
            )
        return None
    
    def _get_source_mask_rect(self):
        """获取原始图像坐标下的遮罩矩形"""
        return self._mask_rect
    
    def _draw_resize_handles(self, painter, rect):
        """绘制8个调整手柄"""
        handle_size = 10
        handles = [
            ('n', rect.center().x() - handle_size//2, rect.top() - handle_size//2),
            ('s', rect.center().x() - handle_size//2, rect.bottom() - handle_size//2),
            ('e', rect.right() - handle_size//2, rect.center().y() - handle_size//2),
            ('w', rect.left() - handle_size//2, rect.center().y() - handle_size//2),
            ('ne', rect.right() - handle_size//2, rect.top() - handle_size//2),
            ('nw', rect.left() - handle_size//2, rect.top() - handle_size//2),
            ('se', rect.right() - handle_size//2, rect.bottom() - handle_size//2),
            ('sw', rect.left() - handle_size//2, rect.bottom() - handle_size//2),
        ]
        for _, x, y in handles:
            painter.setBrush(QColor(59, 130, 246))
            painter.setPen(QColor(255, 255, 255))
            painter.drawRect(x, y, handle_size, handle_size)
    
    def setPixmap(self, pixmap):
        self._source_pixmap = pixmap
        self._update_display()
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()
    
    def _get_resize_edge(self, pos, rect):
        """判断鼠标是否在调整手柄上（使用更大的检测范围）"""
        margin = 15
        
        px = pos.x()
        py = pos.y()
        rx = rect.x()
        ry = rect.y()
        rw = rect.width()
        rh = rect.height()
        
        # 检查四个角
        if abs(px - rx) < margin and abs(py - ry) < margin:
            return 'nw'
        if abs(px - (rx + rw)) < margin and abs(py - ry) < margin:
            return 'ne'
        if abs(px - rx) < margin and abs(py - (ry + rh)) < margin:
            return 'sw'
        if abs(px - (rx + rw)) < margin and abs(py - (ry + rh)) < margin:
            return 'se'
        
        # 检查四条边
        if abs(px - rx) < margin:
            return 'w'
        if abs(px - (rx + rw)) < margin:
            return 'e'
        if abs(py - ry) < margin:
            return 'n'
        if abs(py - (ry + rh)) < margin:
            return 's'
        
        return None
    
    def mousePressEvent(self, event):
        mask_rect = self._get_display_mask_rect()
        if mask_rect:
            edge = self._get_resize_edge(event.pos(), mask_rect)
            if edge:
                self._resizing = True
                self._resize_edge = edge
                self._drag_start_x = event.pos().x()
                self._drag_start_y = event.pos().y()
                self._drag_start_rect = QRect(self._mask_rect)
                self._set_resize_cursor(edge)
                return
            
            if mask_rect.contains(event.pos()):
                self._dragging = True
                self._drag_start_x = event.pos().x()
                self._drag_start_y = event.pos().y()
                self._drag_start_rect = QRect(self._mask_rect)
                self.setCursor(Qt.ClosedHandCursor)
                return
        
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        mask_rect = self._get_display_mask_rect()
        
        if self._dragging:
            dx = event.pos().x() - self._drag_start_x
            dy = event.pos().y() - self._drag_start_y
            
            if self._source_pixmap:
                new_x = self._drag_start_rect.x() + int(dx / self._scale_x)
                new_y = self._drag_start_rect.y() + int(dy / self._scale_y)
                
                max_x = self._source_pixmap.width() - self._mask_rect.width()
                max_y = self._source_pixmap.height() - self._mask_rect.height()
                new_x = max(0, min(new_x, max_x))
                new_y = max(0, min(new_y, max_y))
                
                self._mask_rect.moveTo(new_x, new_y)
                self._update_display()
                self.mask_changed.emit(self._mask_rect)
        
        elif self._resizing:
            dx = event.pos().x() - self._drag_start_x
            dy = event.pos().y() - self._drag_start_y
            
            if self._source_pixmap:
                dx_src = int(dx / self._scale_x)
                dy_src = int(dy / self._scale_y)
                
                min_size = 20
                new_rect = QRect(self._drag_start_rect)
                
                if self._resize_edge in ['w', 'nw', 'sw']:
                    new_width = max(min_size, self._drag_start_rect.width() - dx_src)
                    delta_w = self._drag_start_rect.width() - new_width
                    new_rect.setX(self._drag_start_rect.x() + delta_w)
                    new_rect.setWidth(new_width)
                if self._resize_edge in ['e', 'ne', 'se']:
                    new_rect.setWidth(max(min_size, self._drag_start_rect.width() + dx_src))
                if self._resize_edge in ['n', 'ne', 'nw']:
                    new_height = max(min_size, self._drag_start_rect.height() - dy_src)
                    delta_h = self._drag_start_rect.height() - new_height
                    new_rect.setY(self._drag_start_rect.y() + delta_h)
                    new_rect.setHeight(new_height)
                if self._resize_edge in ['s', 'se', 'sw']:
                    new_rect.setHeight(max(min_size, self._drag_start_rect.height() + dy_src))
                
                max_x = self._source_pixmap.width() - new_rect.width()
                max_y = self._source_pixmap.height() - new_rect.height()
                new_rect.setX(max(0, min(new_rect.x(), max_x)))
                new_rect.setY(max(0, min(new_rect.y(), max_y)))
                
                self._mask_rect = new_rect
                self._update_display()
                self.mask_changed.emit(self._mask_rect)
        
        else:
            if mask_rect:
                edge = self._get_resize_edge(event.pos(), mask_rect)
                if edge:
                    self._set_resize_cursor(edge)
                elif mask_rect.contains(event.pos()):
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
            super().mouseMoveEvent(event)
    
    def _set_resize_cursor(self, edge):
        cursors = {
            'n': Qt.SizeVerCursor,
            's': Qt.SizeVerCursor,
            'e': Qt.SizeHorCursor,
            'w': Qt.SizeHorCursor,
            'ne': Qt.SizeBDiagCursor,
            'nw': Qt.SizeFDiagCursor,
            'se': Qt.SizeFDiagCursor,
            'sw': Qt.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(edge, Qt.ArrowCursor))
    
    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._resizing = False
        self.setCursor(Qt.ArrowCursor)
        super().mouseReleaseEvent(event)
    
    def set_mask_rect(self, rect: QRect):
        self._mask_rect = rect
        self._update_display()


class RemoveSubtitlePreviewDialog(QDialog):
    """移除字幕预览对话框 - 用于设置模糊遮罩区域"""
    
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("移除字幕预览")
        self.setObjectName("removeSubtitlePreviewDialog")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)
        
        self._video_path = video_path
        self._current_time: float = 60.0
        self._video_duration: float = 300.0
        self._frame_pixmap: QPixmap | None = None
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._do_extract_frame)
        
        # 遮罩区域设置（百分比）
        self._mask_x = 0.2
        self._mask_y = 0.85
        self._mask_width = 0.6
        self._mask_height = 0.1
        self._mask_feather = 5  # 羽化值
        
        self._setup_ui()
        self._load_video_info()
    
    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)  # 增加对话框边缘间距
        layout.setSpacing(0)
        
        left_panel = QFrame()
        left_panel.setObjectName("previewLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 8, 16)
        left_layout.setSpacing(8)
        
        self._preview_label = _MaskPreviewFrame()
        self._preview_label.setObjectName("previewFrame")
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(640, 360)
        self._preview_label.setText("正在加载视频帧...\n提示：拖拽遮罩可调整位置，拖动边缘可调整大小")
        self._preview_label.mask_changed.connect(self._on_mask_changed)
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
        
        hint_label = QLabel("💡 提示：拖拽遮罩调整位置，拖动边缘调整大小")
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
        right_layout.setContentsMargins(16, 20, 24, 20)  # 增加右边距
        right_layout.setSpacing(16)
        
        settings_title = QLabel("遮罩设置")
        settings_title.setObjectName("previewSettingsTitle")
        right_layout.addWidget(settings_title)
        
        # 遮罩位置输入
        x_label = QLabel("水平位置 (%)")
        x_label.setObjectName("paramLabel")
        right_layout.addWidget(x_label)
        
        self._x_spin = QComboBox()
        self._x_spin.setObjectName("maskXSpin")
        self._x_spin.setEditable(True)
        self._x_spin.addItems([str(i) for i in range(0, 101, 5)])
        self._x_spin.setCurrentText("20")
        self._x_spin.currentIndexChanged.connect(self._on_mask_param_changed)
        right_layout.addWidget(self._x_spin)
        
        y_label = QLabel("垂直位置 (%)")
        y_label.setObjectName("paramLabel")
        right_layout.addWidget(y_label)
        
        self._y_spin = QComboBox()
        self._y_spin.setObjectName("maskYSpin")
        self._y_spin.setEditable(True)
        self._y_spin.addItems([str(i) for i in range(0, 101, 5)])
        self._y_spin.setCurrentText("85")
        self._y_spin.currentIndexChanged.connect(self._on_mask_param_changed)
        right_layout.addWidget(self._y_spin)
        
        # 遮罩大小输入
        width_label = QLabel("宽度 (%)")
        width_label.setObjectName("paramLabel")
        right_layout.addWidget(width_label)
        
        self._width_spin = QComboBox()
        self._width_spin.setObjectName("maskWidthSpin")
        self._width_spin.setEditable(True)
        self._width_spin.addItems([str(i) for i in range(10, 101, 5)])
        self._width_spin.setCurrentText("60")
        self._width_spin.currentIndexChanged.connect(self._on_mask_param_changed)
        right_layout.addWidget(self._width_spin)
        
        height_label = QLabel("高度 (%)")
        height_label.setObjectName("paramLabel")
        right_layout.addWidget(height_label)
        
        self._height_spin = QComboBox()
        self._height_spin.setObjectName("maskHeightSpin")
        self._height_spin.setEditable(True)
        self._height_spin.addItems([str(i) for i in range(5, 51, 5)])
        self._height_spin.setCurrentText("10")
        self._height_spin.currentIndexChanged.connect(self._on_mask_param_changed)
        right_layout.addWidget(self._height_spin)
        
        # 模糊强度
        blur_label = QLabel("模糊强度")
        blur_label.setObjectName("paramLabel")
        right_layout.addWidget(blur_label)
        
        self._blur_slider = QSlider(Qt.Horizontal)
        self._blur_slider.setObjectName("maskBlurSlider")
        self._blur_slider.setRange(5, 50)
        self._blur_slider.setValue(20)
        self._blur_slider.setTickInterval(5)
        self._blur_slider.setTickPosition(QSlider.TicksBelow)
        right_layout.addWidget(self._blur_slider)
        
        self._blur_label = QLabel("20px")
        self._blur_label.setObjectName("maskBlurLabel")
        self._blur_label.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self._blur_label)
        self._blur_slider.valueChanged.connect(self._on_blur_changed)
        
        # 羽化值
        feather_label = QLabel("羽化值")
        feather_label.setObjectName("paramLabel")
        right_layout.addWidget(feather_label)
        
        self._feather_slider = QSlider(Qt.Horizontal)
        self._feather_slider.setObjectName("maskFeatherSlider")
        self._feather_slider.setRange(0, 20)
        self._feather_slider.setValue(5)
        self._feather_slider.setTickInterval(2)
        self._feather_slider.setTickPosition(QSlider.TicksBelow)
        right_layout.addWidget(self._feather_slider)
        
        self._feather_label = QLabel("5px")
        self._feather_label.setObjectName("maskFeatherLabel")
        self._feather_label.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self._feather_label)
        self._feather_slider.valueChanged.connect(self._on_feather_changed)
        
        # 预设按钮
        preset_label = QLabel("快速预设")
        preset_label.setObjectName("paramLabel")
        right_layout.addWidget(preset_label)
        
        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        
        bottom_btn = QPushButton("底部")
        bottom_btn.setObjectName("presetBtn")
        bottom_btn.setCursor(Qt.PointingHandCursor)
        bottom_btn.clicked.connect(lambda: self._apply_preset("bottom"))
        preset_row.addWidget(bottom_btn)
        
        top_btn = QPushButton("顶部")
        top_btn.setObjectName("presetBtn")
        top_btn.setCursor(Qt.PointingHandCursor)
        top_btn.clicked.connect(lambda: self._apply_preset("top"))
        preset_row.addWidget(top_btn)
        
        center_btn = QPushButton("中部")
        center_btn.setObjectName("presetBtn")
        center_btn.setCursor(Qt.PointingHandCursor)
        center_btn.clicked.connect(lambda: self._apply_preset("center"))
        preset_row.addWidget(center_btn)
        
        right_layout.addLayout(preset_row)
        
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
        
        self._extract_frame()
    
    def _on_time_slider_changed(self, value: int) -> None:
        self._current_time = (value / 1000.0) * self._video_duration
        self._update_time_label()
    
    def _on_slider_released(self) -> None:
        self._extract_frame()
    
    def _update_time_label(self) -> None:
        total_sec = int(self._current_time)
        h, m, s = total_sec // 3600, (total_sec % 3600) // 60, total_sec % 60
        self._time_label.setText(f"{h:02d}:{m:02d}:{s:02d}")
    
    def _on_mask_changed(self, rect: QRect) -> None:
        """遮罩被拖拽或调整大小后的回调"""
        if self._preview_label._source_pixmap:
            src_w = self._preview_label._source_pixmap.width()
            src_h = self._preview_label._source_pixmap.height()
            
            self._mask_x = rect.x() / src_w
            self._mask_y = rect.y() / src_h
            self._mask_width = rect.width() / src_w
            self._mask_height = rect.height() / src_h
            
            # 更新输入框
            self._x_spin.blockSignals(True)
            self._y_spin.blockSignals(True)
            self._width_spin.blockSignals(True)
            self._height_spin.blockSignals(True)
            
            self._x_spin.setCurrentText(f"{int(self._mask_x * 100)}")
            self._y_spin.setCurrentText(f"{int(self._mask_y * 100)}")
            self._width_spin.setCurrentText(f"{int(self._mask_width * 100)}")
            self._height_spin.setCurrentText(f"{int(self._mask_height * 100)}")
            
            self._x_spin.blockSignals(False)
            self._y_spin.blockSignals(False)
            self._width_spin.blockSignals(False)
            self._height_spin.blockSignals(False)
    
    def _on_mask_param_changed(self) -> None:
        """遮罩参数输入框变化"""
        try:
            self._mask_x = min(1.0, max(0.0, int(self._x_spin.currentText()) / 100))
            self._mask_y = min(1.0, max(0.0, int(self._y_spin.currentText()) / 100))
            self._mask_width = min(1.0, max(0.05, int(self._width_spin.currentText()) / 100))
            self._mask_height = min(1.0, max(0.05, int(self._height_spin.currentText()) / 100))
            
            # 确保不超出边界
            max_x = 1.0 - self._mask_width
            max_y = 1.0 - self._mask_height
            self._mask_x = min(max_x, max(0.0, self._mask_x))
            self._mask_y = min(max_y, max(0.0, self._mask_y))
            
            # 更新预览
            if self._preview_label._source_pixmap:
                src_w = self._preview_label._source_pixmap.width()
                src_h = self._preview_label._source_pixmap.height()
                rect = QRect(
                    int(self._mask_x * src_w),
                    int(self._mask_y * src_h),
                    int(self._mask_width * src_w),
                    int(self._mask_height * src_h)
                )
                self._preview_label.set_mask_rect(rect)
        except ValueError:
            pass
    
    def _on_blur_changed(self, value: int) -> None:
        self._blur_label.setText(f"{value}px")
    
    def _on_feather_changed(self, value: int) -> None:
        self._feather_label.setText(f"{value}px")
    
    def _apply_preset(self, preset: str) -> None:
        """应用预设位置"""
        if preset == "bottom":
            self._x_spin.setCurrentText("20")
            self._y_spin.setCurrentText("80")
            self._width_spin.setCurrentText("60")
            self._height_spin.setCurrentText("15")
        elif preset == "top":
            self._x_spin.setCurrentText("20")
            self._y_spin.setCurrentText("5")
            self._width_spin.setCurrentText("60")
            self._height_spin.setCurrentText("15")
        elif preset == "center":
            self._x_spin.setCurrentText("25")
            self._y_spin.setCurrentText("40")
            self._width_spin.setCurrentText("50")
            self._height_spin.setCurrentText("20")
        
        self._on_mask_param_changed()
    
    def _extract_frame(self) -> None:
        self._preview_label.setText("正在加载...")
        self._debounce_timer.start(250)
    
    def _do_extract_frame(self) -> None:
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
            self._preview_label.set_source_pixmap(pixmap)
            
            # 初始化遮罩位置
            src_w = pixmap.width()
            src_h = pixmap.height()
            rect = QRect(
                int(self._mask_x * src_w),
                int(self._mask_y * src_h),
                int(self._mask_width * src_w),
                int(self._mask_height * src_h)
            )
            self._preview_label.set_mask_rect(rect)
            
            self._cleanup_temp(tmp_path)
        except Exception as e:
            logger.error(f"提取帧异常: {e}", exc_info=True)
            self._preview_label.setText(f"加载失败: {str(e)}")
    
    def _format_ffmpeg_time(self, seconds: float) -> str:
        total = int(seconds)
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        return f"{h:02d}:{m:02d}:{s:02d}.{int((seconds - total) * 1000):03d}"
    
    def _cleanup_temp(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass
    
    def get_settings(self) -> dict:
        """获取遮罩设置"""
        return {
            "mask_x": self._mask_x,
            "mask_y": self._mask_y,
            "mask_width": self._mask_width,
            "mask_height": self._mask_height,
            "blur_radius": self._blur_slider.value(),
            "feather": self._feather_slider.value(),
        }
