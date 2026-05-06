import logging
import os
import subprocess
from typing import Dict

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.jianying_export_service import JianYingExportService
from clip_synth.services.narrate_export_service import NarrateExportService
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.widgets.subtitle_preview_dialog import SubtitlePreviewDialog

logger = logging.getLogger("clip_synth.narrate_export")


class ExportWorker(QThread):
    progress = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, service: NarrateExportService, project: NarrateProjectState, parent=None):
        super().__init__(parent)
        self._service = service
        self._project = project
        self._output_path = ""

    def run(self):
        try:
            output_path = self._service.export(
                self._project,
                progress_callback=lambda msg, pct: self.progress.emit(msg),
            )
            self._output_path = output_path
            self.finished.emit(output_path)
        except Exception as e:
            logger.error(f"导出失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def cancel(self):
        self._service.cancel()


class JianYingExportWorker(QThread):
    progress = Signal(str)
    export_finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        service: JianYingExportService,
        project: NarrateProjectState,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._project = project
        self._output_dir = output_dir

    def run(self):
        try:
            result = self._service.export_to_jianying(
                self._project,
                self._output_dir,
                progress_callback=lambda msg, pct: self.progress.emit(msg),
            )
            self.export_finished.emit(result)
        except Exception as e:
            logger.error(f"导出到剪映草稿失败: {e}", exc_info=True)
            self.error.emit(str(e))

    def cancel(self):
        self._service.cancel()


class _ExportActionCard(QFrame):
    clicked = Signal()

    def __init__(self, icon: str, title: str, description: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui(icon, title, description)

    def _setup_ui(self, icon: str, title: str, description: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)

        icon_label = QLabel(icon)
        icon_label.setObjectName("exportCardIcon")
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("exportCardTitle")
        text_layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setObjectName("exportCardDesc")
        desc_label.setWordWrap(True)
        text_layout.addWidget(desc_label)

        layout.addLayout(text_layout, stretch=1)

        arrow_label = QLabel("→")
        arrow_label.setObjectName("exportCardArrow")
        arrow_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(arrow_label)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class NarrateExportPage(QFrame):
    def __init__(self, settings_service: SettingsService | None = None, parent=None):
        super().__init__(parent)
        self._project: NarrateProjectState | None = None
        self._export_service: NarrateExportService | None = None
        self._settings_service = settings_service
        self._worker: ExportWorker | None = None
        self.setObjectName("narrateExportPage")
        self._setup_ui()

    def set_project(self, project: NarrateProjectState, export_service: NarrateExportService):
        self._project = project
        self._export_service = export_service
        self._reset_ui()

    def set_settings_service(self, settings_service: SettingsService):
        self._settings_service = settings_service

    def _reset_ui(self):
        self._card_container.show()
        self._progress_container.hide()
        self._result_container.hide()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("导出视频")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        info = QLabel("解说配音已生成完毕，请选择导出方式")
        info.setObjectName("exportInfo")
        info.setWordWrap(True)
        layout.addWidget(info)

        subtitle_group = QGroupBox("字幕设置")
        subtitle_group.setObjectName("subtitleGroup")
        subtitle_layout = QVBoxLayout(subtitle_group)
        subtitle_layout.setContentsMargins(16, 24, 16, 16)
        subtitle_layout.setSpacing(12)

        enable_row = QHBoxLayout()
        enable_row.setSpacing(12)
        self._subtitle_check = QPushButton("☐ 启用字幕")
        self._subtitle_check.setObjectName("subtitleEnableBtn")
        self._subtitle_check.setCheckable(True)
        self._subtitle_check.setCursor(Qt.PointingHandCursor)
        self._subtitle_check.clicked.connect(self._on_subtitle_toggle)
        enable_row.addWidget(self._subtitle_check)
        
        self._preview_btn = QPushButton("👁 预览")
        self._preview_btn.setObjectName("subtitlePreviewBtn")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._on_preview)
        enable_row.addWidget(self._preview_btn)
        enable_row.addStretch()
        subtitle_layout.addLayout(enable_row)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(16)

        font_col = QVBoxLayout()
        font_col.setSpacing(4)
        font_label = QLabel("字体")
        font_label.setObjectName("paramLabel")
        font_col.addWidget(font_label)
        self._font_combo = QComboBox()
        self._font_combo.setObjectName("fontCombo")
        self._font_combo.addItems(["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "Arial"])
        self._font_combo.setFixedHeight(32)
        font_col.addWidget(self._font_combo)
        settings_row.addLayout(font_col)

        size_col = QVBoxLayout()
        size_col.setSpacing(4)
        size_label = QLabel("大小")
        size_label.setObjectName("paramLabel")
        size_col.addWidget(size_label)
        self._font_size_combo = QComboBox()
        self._font_size_combo.setObjectName("fontSizeCombo")
        self._font_size_combo.setEditable(True)
        self._font_size_combo.addItems([str(i) for i in range(30, 61)])
        self._font_size_combo.setCurrentText("40")
        self._font_size_combo.setFixedHeight(32)
        size_col.addWidget(self._font_size_combo)
        settings_row.addLayout(size_col)

        color_col = QVBoxLayout()
        color_col.setSpacing(4)
        color_label = QLabel("字体颜色")
        color_label.setObjectName("paramLabel")
        color_col.addWidget(color_label)
        self._font_color_btn = QPushButton()
        self._font_color_btn.setObjectName("fontColorBtn")
        self._font_color = QColor("#FFFFFF")
        self._font_color_btn.setStyleSheet(f"background-color: {self._font_color.name()}; border: 2px solid #1e293b; border-radius: 6px;")
        self._font_color_btn.setFixedSize(32, 32)
        self._font_color_btn.setCursor(Qt.PointingHandCursor)
        self._font_color_btn.clicked.connect(self._on_font_color_clicked)
        color_col.addWidget(self._font_color_btn)
        settings_row.addLayout(color_col)

        bg_col = QVBoxLayout()
        bg_col.setSpacing(4)
        bg_label = QLabel("背景颜色")
        bg_label.setObjectName("paramLabel")
        bg_col.addWidget(bg_label)
        self._bg_color_btn = QPushButton()
        self._bg_color_btn.setObjectName("bgColorBtn")
        self._bg_color = QColor("#000000")
        self._bg_color_btn.setStyleSheet(f"background-color: {self._bg_color.name()}; border: 2px solid #1e293b; border-radius: 6px;")
        self._bg_color_btn.setFixedSize(32, 32)
        self._bg_color_btn.setCursor(Qt.PointingHandCursor)
        self._bg_color_btn.clicked.connect(self._on_bg_color_clicked)
        bg_col.addWidget(self._bg_color_btn)
        settings_row.addLayout(bg_col)

        opacity_col = QVBoxLayout()
        opacity_col.setSpacing(4)
        opacity_label = QLabel("背景透明度")
        opacity_label.setObjectName("paramLabel")
        opacity_col.addWidget(opacity_label)
        self._bg_opacity_slider = QSlider(Qt.Horizontal)
        self._bg_opacity_slider.setRange(0, 100)
        self._bg_opacity_slider.setValue(50)
        self._bg_opacity_slider.setObjectName("bgOpacitySlider")
        self._bg_opacity_slider.setFixedWidth(80)
        opacity_col.addWidget(self._bg_opacity_slider)
        settings_row.addLayout(opacity_col)

        position_col = QVBoxLayout()
        position_col.setSpacing(4)
        position_label = QLabel("位置")
        position_label.setObjectName("paramLabel")
        position_col.addWidget(position_label)
        self._position_combo = QComboBox()
        self._position_combo.setObjectName("positionCombo")
        self._position_combo.addItem("底部", "bottom")
        self._position_combo.addItem("中部", "middle")
        self._position_combo.addItem("顶部", "top")
        self._position_combo.setFixedHeight(32)
        position_col.addWidget(self._position_combo)
        settings_row.addLayout(position_col)

        settings_row.addStretch()
        subtitle_layout.addLayout(settings_row)

        layout.addWidget(subtitle_group)

        self._card_container = QFrame()
        self._card_container.setObjectName("exportCardsFrame")
        card_inner = QHBoxLayout(self._card_container)
        card_inner.setContentsMargins(0, 0, 0, 0)
        card_inner.setSpacing(16)
        card_inner.setAlignment(Qt.AlignCenter)

        self._export_video_card = _ExportActionCard(
            icon="🎬",
            title="直接导出视频",
            description="将解说配音与视频合并，直接导出为完整的MP4视频文件",
        )
        self._export_video_card.clicked.connect(self._on_export_video)
        self._export_video_card.setObjectName("exportVideoCard")
        self._export_video_card.setMinimumWidth(280)
        card_inner.addWidget(self._export_video_card)

        self._export_draft_card = _ExportActionCard(
            icon="✂️",
            title="导出到剪映草稿",
            description="将解说配音与视频生成剪映草稿文件，可在剪映专业版中继续编辑",
        )
        self._export_draft_card.clicked.connect(self._on_export_draft)
        self._export_draft_card.setObjectName("exportDraftCard")
        self._export_draft_card.setMinimumWidth(280)
        card_inner.addWidget(self._export_draft_card)

        layout.addWidget(self._card_container, stretch=1)

        self._progress_container = QFrame()
        self._progress_container.setObjectName("exportProgressContainer")
        progress_layout = QVBoxLayout(self._progress_container)
        progress_layout.setContentsMargins(16, 12, 16, 12)
        progress_layout.setSpacing(8)

        self._progress_label = QLabel("正在导出...")
        self._progress_label.setObjectName("exportProgressLabel")
        self._progress_label.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self._progress_label)

        self._progress_bar = QFrame()
        self._progress_bar.setObjectName("exportProgressBar")
        self._progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self._progress_bar)

        layout.addWidget(self._progress_container)
        self._progress_container.hide()

        self._result_container = QFrame()
        self._result_container.setObjectName("exportResultContainer")
        result_layout = QVBoxLayout(self._result_container)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(16)
        result_layout.setAlignment(Qt.AlignCenter)

        success_label = QLabel("✅ 视频导出完成")
        success_label.setObjectName("exportResultLabel")
        success_label.setAlignment(Qt.AlignCenter)
        result_layout.addWidget(success_label)

        open_btn = QPushButton(" 打开文件夹 ")
        open_btn.setObjectName("exportOpenFolderBtn")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._on_open_folder)
        result_layout.addWidget(open_btn, alignment=Qt.AlignCenter)

        layout.addWidget(self._result_container)
        self._result_container.hide()

        layout.addStretch()

    def _on_subtitle_toggle(self, checked: bool) -> None:
        self._preview_btn.setEnabled(checked)
        self._font_combo.setEnabled(checked)
        self._font_size_combo.setEnabled(checked)
        self._font_color_btn.setEnabled(checked)
        self._bg_color_btn.setEnabled(checked)
        self._bg_opacity_slider.setEnabled(checked)
        self._position_combo.setEnabled(checked)
        self._subtitle_check.setText("☑ 启用字幕" if checked else "☐ 启用字幕")
        if self._project:
            self._project.enable_subtitle = checked

    def _on_preview(self) -> None:
        if not self._project or not self._project.videos:
            return
        video_path = self._project.videos[0].video_path
        if not video_path or not os.path.exists(video_path):
            logger.warning("视频文件不存在")
            return

        dialog = SubtitlePreviewDialog(video_path, self)
        if dialog.exec_() == QDialog.Accepted:
            settings = dialog.get_settings()
            font_index = self._font_combo.findText(settings["font"])
            if font_index >= 0:
                self._font_combo.setCurrentIndex(font_index)
            size_index = self._font_size_combo.findText(str(settings["font_size"]))
            if size_index >= 0:
                self._font_size_combo.setCurrentIndex(size_index)
            self._font_color = QColor(settings["font_color"])
            self._font_color_btn.setStyleSheet(f"background-color: {settings['font_color']}; border: 2px solid #1e293b; border-radius: 6px;")
            self._bg_color = QColor(settings["bg_color"])
            self._bg_color_btn.setStyleSheet(f"background-color: {settings['bg_color']}; border: 2px solid #1e293b; border-radius: 6px;")
            self._bg_opacity_slider.setValue(settings["bg_opacity"])
            pos = settings.get("position", "bottom")
            pos_index = self._position_combo.findData(pos)
            if pos_index >= 0:
                self._position_combo.setCurrentIndex(pos_index)
            self._subtitle_offset_x = settings.get("offset_x", 0.5)
            self._subtitle_offset_y = settings.get("offset_y", 0.9)

    def _on_font_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._font_color, self, "选择字体颜色")
        if color.isValid():
            self._font_color = color
            self._font_color_btn.setStyleSheet(f"background-color: {color.name()}; border: 2px solid #1e293b; border-radius: 6px;")

    def _on_bg_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._bg_color, self, "选择背景颜色")
        if color.isValid():
            self._bg_color = color
            self._bg_color_btn.setStyleSheet(f"background-color: {color.name()}; border: 2px solid #1e293b; border-radius: 6px;")

    def _on_export_video(self):
        if not self._project or not self._export_service:
            logger.warning("项目或导出服务未设置")
            return
        if not self._project.narration_scripts:
            logger.warning("没有解说文案可导出")
            return
        if not self._project.audio_files:
            logger.warning("没有配音文件可导出")
            return

        self._project.enable_subtitle = self._subtitle_check.isChecked()
        self._project.subtitle_font = self._font_combo.currentText()
        self._project.subtitle_font_size = int(self._font_size_combo.currentText())
        self._project.subtitle_font_color = self._font_color.name()
        self._project.subtitle_bg_color = self._bg_color.name()
        self._project.subtitle_bg_opacity = self._bg_opacity_slider.value()
        self._project.subtitle_position = self._position_combo.currentData()
        self._project.subtitle_offset_x = getattr(self, "_subtitle_offset_x", 0.5)
        self._project.subtitle_offset_y = getattr(self, "_subtitle_offset_y", 0.9)
        logger.info(f"字幕位置设置: pos_data={self._position_combo.currentData()}, subtitle_position={self._project.subtitle_position}, offset=({self._project.subtitle_offset_x}, {self._project.subtitle_offset_y})")

        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("准备导出...")

        self._worker = ExportWorker(self._export_service, self._project)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._worker.start()

    def _on_export_draft(self):
        if not self._project:
            logger.warning("项目未设置")
            return
        
        if not self._settings_service:
            logger.error("SettingsService 未设置")
            from clip_synth.ui.components.toast import show_toast
            show_toast(self, "系统配置服务未初始化", "error", duration=3000)
            return
        
        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("正在准备导出到剪映草稿...")
        
        # 创建剪映导出服务
        jianying_service = JianYingExportService(self._settings_service)
        
        # 获取临时输出目录
        output_dir = os.path.join(os.path.expanduser("~"), ".clip_synth", "exports")
        os.makedirs(output_dir, exist_ok=True)
        
        self._jianying_worker = JianYingExportWorker(jianying_service, self._project, output_dir)
        self._jianying_worker.progress.connect(self._on_export_progress)
        self._jianying_worker.export_finished.connect(self._on_jianying_export_finished)
        self._jianying_worker.error.connect(self._on_export_error)
        self._jianying_worker.start()

    def _on_jianying_export_finished(self, result: Dict[str, str] = None):
        self._progress_container.hide()
        self._card_container.show()
        
        if result is None:
            logger.error("导出结果为空")
            from clip_synth.ui.components.toast import show_toast
            show_toast(self, "导出结果为空", "error", duration=3000)
            return
        
        draft_path = result.get("draft_path", "")
        draft_name = result.get("draft_name", "")
        logger.info(f"导出到剪映草稿成功: {draft_name} -> {draft_path}")
        
        from clip_synth.ui.components.toast import show_toast
        show_toast(self, f"成功导出到剪映草稿: {draft_name}", "success", duration=3000)

    def _on_export_progress(self, message: str):
        self._progress_label.setText(message)

    def _on_export_finished(self, output_path: str):
        self._progress_container.hide()
        self._result_container.show()
        self._output_path = output_path
        logger.info(f"导出成功: {output_path}")

    def _on_export_error(self, message: str):
        self._progress_container.hide()
        self._card_container.show()
        logger.error(f"导出失败: {message}")

        from clip_synth.ui.components.toast import show_toast
        show_toast(self, f"导出失败: {message}", "error", duration=5000)

    def _on_open_folder(self):
        if hasattr(self, "_output_path") and self._output_path:
            folder = os.path.dirname(self._output_path)
            if os.path.exists(folder):
                try:
                    subprocess.Popen(f'explorer /select,"{self._output_path}"')
                except Exception as e:
                    logger.error(f"打开文件夹失败: {e}")
