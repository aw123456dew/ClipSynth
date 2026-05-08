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

from clip_synth.core.config import AppConfig
from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.jianying_export_service import JianYingExportService
from clip_synth.services.narrate_export_service import NarrateExportService
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.widgets.subtitle_preview_dialog import (
    RemoveSubtitlePreviewDialog,
    SubtitlePreviewDialog,
)

logger = logging.getLogger("clip_synth.narrate_export")


class ExportWorker(QThread):
    progress = Signal(str)
    export_finished = Signal(str)
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
            self.export_finished.emit(output_path)
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
        self._jianying_worker: JianYingExportWorker | None = None
        self.setObjectName("narrateExportPage")
        self._setup_ui()

    def set_project(self, project: NarrateProjectState, export_service: NarrateExportService):
        self._project = project
        self._export_service = export_service
        self._reset_ui()
        self._subtitle_check.setChecked(getattr(project, "enable_subtitle", False))
        self._on_subtitle_toggle(self._subtitle_check.isChecked())
        self._remove_subtitle_check.setChecked(getattr(project, "enable_remove_subtitle", False))
        self._on_remove_subtitle_toggle(self._remove_subtitle_check.isChecked())

    def set_settings_service(self, settings_service: SettingsService):
        self._settings_service = settings_service

    def _reset_ui(self):
        self._card_container.show()
        self._progress_container.hide()
        self._result_container.hide()

    def _cleanup_workers(self):
        if self._worker is not None:
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(2000)
            self._worker.deleteLater()
            self._worker = None
        if self._jianying_worker is not None:
            self._jianying_worker.cancel()
            self._jianying_worker.quit()
            self._jianying_worker.wait(2000)
            self._jianying_worker.deleteLater()
            self._jianying_worker = None

    def hideEvent(self, event):
        self._cleanup_workers()
        super().hideEvent(event)

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

        groups_row = QHBoxLayout()
        groups_row.setSpacing(16)

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

        tips_label = QLabel("字幕样式（字体、大小、颜色等）请在预览时设置")
        tips_label.setObjectName("subtitleTipsLabel")
        tips_label.setStyleSheet("color: #64748b; font-size: 12px;")
        subtitle_layout.addWidget(tips_label)

        groups_row.addWidget(subtitle_group, stretch=1)

        remove_subtitle_group = QGroupBox("启用移除字幕")
        remove_subtitle_group.setObjectName("removeSubtitleGroup")
        remove_subtitle_layout = QVBoxLayout(remove_subtitle_group)
        remove_subtitle_layout.setContentsMargins(16, 24, 16, 16)
        remove_subtitle_layout.setSpacing(12)

        remove_enable_row = QHBoxLayout()
        remove_enable_row.setSpacing(12)
        self._remove_subtitle_check = QPushButton("☐ 启用")
        self._remove_subtitle_check.setObjectName("removeSubtitleEnableBtn")
        self._remove_subtitle_check.setCheckable(True)
        self._remove_subtitle_check.setCursor(Qt.PointingHandCursor)
        self._remove_subtitle_check.clicked.connect(self._on_remove_subtitle_toggle)
        remove_enable_row.addWidget(self._remove_subtitle_check)
        
        self._remove_preview_btn = QPushButton("👁 预览")
        self._remove_preview_btn.setObjectName("removeSubtitlePreviewBtn")
        self._remove_preview_btn.setCursor(Qt.PointingHandCursor)
        self._remove_preview_btn.setEnabled(False)
        self._remove_preview_btn.clicked.connect(self._on_remove_subtitle_preview)
        remove_enable_row.addWidget(self._remove_preview_btn)
        remove_enable_row.addStretch()
        remove_subtitle_layout.addLayout(remove_enable_row)

        remove_tips_label = QLabel("点击预览设置模糊区域")
        remove_tips_label.setObjectName("removeSubtitleTipsLabel")
        remove_tips_label.setStyleSheet("color: #64748b; font-size: 12px;")
        remove_subtitle_layout.addWidget(remove_tips_label)

        groups_row.addWidget(remove_subtitle_group, stretch=1)

        layout.addLayout(groups_row)

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
        self._subtitle_check.setText("☑ 启用字幕" if checked else "☐ 启用字幕")
        if self._project:
            self._project.enable_subtitle = checked
    
    def _on_remove_subtitle_toggle(self, checked: bool) -> None:
        self._remove_preview_btn.setEnabled(checked)
        self._remove_subtitle_check.setText("☑ 启用" if checked else "☐ 启用")
        if self._project:
            self._project.enable_remove_subtitle = checked

    def _on_remove_subtitle_preview(self) -> None:
        if not self._project or not self._project.videos:
            return
        video_path = self._project.videos[0].video_path
        if not video_path or not os.path.exists(video_path):
            logger.warning("视频文件不存在")
            return

        dialog = RemoveSubtitlePreviewDialog(video_path, self)
        if dialog.exec_() == QDialog.Accepted:
            settings = dialog.get_settings()
            # 保存遮罩设置到项目中
            self._project.mask_x = settings["mask_x"]
            self._project.mask_y = settings["mask_y"]
            self._project.mask_width = settings["mask_width"]
            self._project.mask_height = settings["mask_height"]
            self._project.mask_blur_radius = settings["blur_radius"]
            logger.info(f"保存遮罩设置: {settings}")

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
            # 直接保存到项目中
            self._project.subtitle_font = settings["font"]
            self._project.subtitle_font_size = settings["font_size"]
            self._project.subtitle_font_color = settings["font_color"]
            self._project.subtitle_bg_color = settings["bg_color"]
            self._project.subtitle_bg_opacity = settings["bg_opacity"]
            self._project.subtitle_position = settings.get("position", "bottom")
            self._project.subtitle_offset_x = settings.get("offset_x", 0.5)
            self._project.subtitle_offset_y = settings.get("offset_y", 0.9)

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
        self._project.enable_remove_subtitle = self._remove_subtitle_check.isChecked()

        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("准备导出...")

        self._cleanup_workers()
        self._worker = ExportWorker(self._export_service, self._project)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.export_finished.connect(self._on_export_finished)
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
        
        self._project.enable_subtitle = self._subtitle_check.isChecked()
        
        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("正在准备导出到剪映草稿...")
        
        # 创建剪映导出服务
        jianying_service = JianYingExportService(self._settings_service)

        # 获取临时输出目录
        config = AppConfig()
        output_dir = str(config.export_dir)
        os.makedirs(output_dir, exist_ok=True)

        self._cleanup_workers()
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
