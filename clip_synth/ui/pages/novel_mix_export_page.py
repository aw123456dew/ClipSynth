import logging
import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clip_synth.core.config import AppConfig
from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.widgets.subtitle_preview_dialog import SubtitlePreviewDialog

logger = logging.getLogger("clip_synth.novel_mix_export")

LANDSCAPE_RATIOS = {
    "16:9": "16:9 (1920×1080)",
    "4:3": "4:3 (1440×1080)",
    "21:9": "21:9 (1920×822)",
    "1:1": "1:1 (1080×1080)",
}

PORTRAIT_RATIOS = {
    "9:16": "9:16 (1080×1920)",
    "3:4": "3:4 (1080×1440)",
    "4:5": "4:5 (1080×1350)",
    "1:1": "1:1 (1080×1080)",
}


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

        arrow_label = QLabel("\u2192")
        arrow_label.setObjectName("exportCardArrow")
        arrow_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(arrow_label)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class NovelMixExportWorker(QThread):
    progress = Signal(str)
    export_finished = Signal(str)
    error = Signal(str)

    def __init__(self, project: NovelMixProjectState, parent=None):
        super().__init__(parent)
        self._project = project
        self._output_path = ""
        self._canceled = False

    def run(self):
        try:
            from clip_synth.services.novel_mix_export_service import NovelMixExportService
            service = NovelMixExportService()
            output_path = service.export(
                self._project,
                progress_callback=lambda msg: self.progress.emit(msg),
                is_canceled=lambda: self._canceled,
            )
            self._output_path = output_path
            self.export_finished.emit(output_path)
        except Exception as e:
            logger.error("导出失败: %s", e, exc_info=True)
            self.error.emit(str(e))

    def cancel(self):
        self._canceled = True


class NovelMixJianyingExportWorker(QThread):
    progress = Signal(str)
    export_finished = Signal(object)
    error = Signal(str)

    def __init__(self, project: NovelMixProjectState, settings_service: SettingsService, parent=None):
        super().__init__(parent)
        self._project = project
        self._settings_service = settings_service
        self._canceled = False

    def run(self):
        try:
            from clip_synth.services.novel_mix_jianying_export_service import NovelMixJianyingExportService
            service = NovelMixJianyingExportService(self._settings_service)
            config = AppConfig()
            output_dir = str(config.export_dir)
            os.makedirs(output_dir, exist_ok=True)
            result = service.export_to_jianying(
                self._project,
                output_dir,
                progress_callback=lambda msg: self.progress.emit(msg),
                is_canceled=lambda: self._canceled,
            )
            self.export_finished.emit(result)
        except Exception as e:
            logger.error("导出到剪映草稿失败: %s", e, exc_info=True)
            self.error.emit(str(e))

    def cancel(self):
        self._canceled = True


class NovelMixExportPage(QFrame):
    def __init__(self, settings_service: SettingsService | None = None, parent=None):
        super().__init__(parent)
        self._project: NovelMixProjectState | None = None
        self._settings_service = settings_service
        self._worker: NovelMixExportWorker | None = None
        self._jianying_worker: NovelMixJianyingExportWorker | None = None
        self._output_path: str | None = None
        self._current_mode: str = ""
        self.setObjectName("novelMixExportPage")
        self._setup_ui()

    def set_project(self, project: NovelMixProjectState) -> None:
        self._project = project
        mode = project.dub_mode
        if mode != self._current_mode:
            self._build_content(mode)
            self._current_mode = mode
        self._reset_ui()
        self._restore_ui_from_project()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("mixScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(self._scroll)

    def _build_content(self, mode: str) -> None:
        is_system = mode == "system"

        content = QWidget()
        content.setObjectName("mixScrollContent")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("导出视频")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        self._info_label = QLabel("请选择导出设置")
        self._info_label.setObjectName("exportInfo")
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        if not is_system:
            self._build_upload_section(layout)

        self._build_settings_cards(layout)
        self._build_export_cards(layout)
        self._build_progress_section(layout)
        self._build_result_section(layout)

        layout.addStretch()

        old_widget = self._scroll.takeWidget()
        if old_widget is not None:
            old_widget.deleteLater()
        self._scroll.setWidget(content)

    def _build_upload_section(self, layout: QVBoxLayout) -> None:
        section = QFrame()
        section.setObjectName("selfDubSection")
        row_layout = QHBoxLayout(section)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(16)

        audio_group = QGroupBox("配音音频")
        audio_group.setObjectName("selfAudioGroup")
        audio_group.setFixedHeight(140)
        audio_layout = QVBoxLayout(audio_group)
        audio_layout.setContentsMargins(16, 16, 16, 16)
        audio_layout.setSpacing(8)
        self._audio_path_label = QLabel("未上传音频文件")
        self._audio_path_label.setObjectName("filePathLabel")
        self._audio_path_label.setWordWrap(True)
        audio_layout.addWidget(self._audio_path_label)
        self._upload_audio_btn = QPushButton("上传音频文件 (mp3/wav/m4a)")
        self._upload_audio_btn.setObjectName("uploadAudioBtn")
        self._upload_audio_btn.setCursor(Qt.PointingHandCursor)
        self._upload_audio_btn.clicked.connect(self._on_upload_audio)
        audio_layout.addWidget(self._upload_audio_btn)
        row_layout.addWidget(audio_group, stretch=1)

        subtitle_group = QGroupBox("字幕文件 (SRT)")
        subtitle_group.setObjectName("selfSubtitleGroup")
        subtitle_group.setFixedHeight(140)
        subtitle_layout = QVBoxLayout(subtitle_group)
        subtitle_layout.setContentsMargins(16, 16, 16, 16)
        subtitle_layout.setSpacing(8)
        self._subtitle_path_label = QLabel("未上传字幕文件")
        self._subtitle_path_label.setObjectName("filePathLabel")
        self._subtitle_path_label.setWordWrap(True)
        subtitle_layout.addWidget(self._subtitle_path_label)
        self._upload_subtitle_btn = QPushButton("上传 SRT 字幕 (可选)")
        self._upload_subtitle_btn.setObjectName("uploadSubtitleBtn")
        self._upload_subtitle_btn.setCursor(Qt.PointingHandCursor)
        self._upload_subtitle_btn.clicked.connect(self._on_upload_subtitle)
        subtitle_layout.addWidget(self._upload_subtitle_btn)
        row_layout.addWidget(subtitle_group, stretch=1)

        layout.addWidget(section)

    def _build_settings_cards(self, layout: QVBoxLayout) -> None:
        groups_row = QHBoxLayout()
        groups_row.setSpacing(16)

        orientation_group = QGroupBox("画面方向")
        orientation_group.setObjectName("orientationGroup")
        orientation_group.setFixedHeight(140)
        orientation_layout = QVBoxLayout(orientation_group)
        orientation_layout.setContentsMargins(12, 8, 12, 12)
        orientation_layout.setSpacing(8)

        direction_row = QHBoxLayout()
        direction_row.setSpacing(12)

        self._landscape_btn = QPushButton("\u25cf 横屏")
        self._landscape_btn.setObjectName("subtitleEnableBtn")
        self._landscape_btn.setCheckable(True)
        self._landscape_btn.setChecked(True)
        self._landscape_btn.setCursor(Qt.PointingHandCursor)
        self._landscape_btn.clicked.connect(lambda: self._on_orientation_change("landscape"))
        direction_row.addWidget(self._landscape_btn)

        self._portrait_btn = QPushButton("\u25cb 竖屏")
        self._portrait_btn.setObjectName("subtitleEnableBtn")
        self._portrait_btn.setCheckable(True)
        self._portrait_btn.setCursor(Qt.PointingHandCursor)
        self._portrait_btn.clicked.connect(lambda: self._on_orientation_change("portrait"))
        direction_row.addWidget(self._portrait_btn)

        direction_row.addStretch()
        orientation_layout.addLayout(direction_row)
        orientation_layout.addSpacing(18)

        self._ratio_combo = QComboBox()
        self._ratio_combo.setObjectName("ratioCombo")
        self._ratio_combo.setMinimumHeight(28)
        orientation_layout.addWidget(self._ratio_combo)

        self._populate_ratio_combo("landscape")

        groups_row.addWidget(orientation_group, stretch=1)

        subtitle_settings_group = QGroupBox("字幕设置")
        subtitle_settings_group.setObjectName("subtitleSettingsGroup")
        subtitle_settings_group.setFixedHeight(140)
        subtitle_settings_layout = QVBoxLayout(subtitle_settings_group)
        subtitle_settings_layout.setContentsMargins(12, 8, 12, 12)
        subtitle_settings_layout.setSpacing(8)

        enable_row = QHBoxLayout()
        enable_row.setSpacing(12)
        self._subtitle_check = QPushButton("\u2610 启用字幕")
        self._subtitle_check.setObjectName("subtitleEnableBtn")
        self._subtitle_check.setCheckable(True)
        self._subtitle_check.setCursor(Qt.PointingHandCursor)
        self._subtitle_check.clicked.connect(self._on_subtitle_toggle)
        enable_row.addWidget(self._subtitle_check)

        self._preview_btn = QPushButton("\U0001f441 预览")
        self._preview_btn.setObjectName("subtitlePreviewBtn")
        self._preview_btn.setCursor(Qt.PointingHandCursor)
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._on_preview)
        enable_row.addWidget(self._preview_btn)
        enable_row.addStretch()
        subtitle_settings_layout.addLayout(enable_row)

        tips_label = QLabel("字幕样式（字体、大小、颜色等）请在预览时设置")
        tips_label.setObjectName("subtitleTipsLabel")
        tips_label.setStyleSheet("color: #64748b; font-size: 12px;")
        subtitle_settings_layout.addWidget(tips_label)

        groups_row.addWidget(subtitle_settings_group, stretch=1)

        layout.addLayout(groups_row)

    def _build_export_cards(self, layout: QVBoxLayout) -> None:
        self._card_container = QFrame()
        self._card_container.setObjectName("exportCardsFrame")
        card_inner = QHBoxLayout(self._card_container)
        card_inner.setContentsMargins(0, 0, 0, 0)
        card_inner.setSpacing(16)

        self._export_video_card = _ExportActionCard(
            icon="\U0001f3ac",
            title="导出视频",
            description="将配音与随机选取的素材片段合成，导出为完整的MP4视频文件",
        )
        self._export_video_card.clicked.connect(self._on_export_video)
        self._export_video_card.setObjectName("exportVideoCard")
        self._export_video_card.setFixedHeight(140)
        card_inner.addWidget(self._export_video_card, stretch=1)

        self._export_draft_card = _ExportActionCard(
            icon="\u2702\ufe0f",
            title="导出到剪映草稿",
            description="将素材片段、配音和字幕导入到剪映草稿，可继续编辑",
        )
        self._export_draft_card.clicked.connect(self._on_export_draft)
        self._export_draft_card.setObjectName("exportDraftCard")
        self._export_draft_card.setFixedHeight(140)
        card_inner.addWidget(self._export_draft_card, stretch=1)

        layout.addWidget(self._card_container)

    def _build_progress_section(self, layout: QVBoxLayout) -> None:
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

    def _build_result_section(self, layout: QVBoxLayout) -> None:
        self._result_container = QFrame()
        self._result_container.setObjectName("exportResultContainer")
        result_layout = QVBoxLayout(self._result_container)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(16)
        result_layout.setAlignment(Qt.AlignCenter)

        success_label = QLabel("\u2705 导出完成")
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

    def _reset_ui(self) -> None:
        self._output_path = None
        self._card_container.show()
        self._progress_container.hide()
        self._result_container.hide()

    def _populate_ratio_combo(self, orientation: str) -> None:
        self._ratio_combo.clear()
        ratios = LANDSCAPE_RATIOS if orientation == "landscape" else PORTRAIT_RATIOS
        for key, label in ratios.items():
            self._ratio_combo.addItem(label, key)
        self._ratio_combo.setCurrentIndex(0)

    def _on_orientation_change(self, orientation: str) -> None:
        if orientation == "landscape":
            self._landscape_btn.setText("\u25cf 横屏")
            self._landscape_btn.setChecked(True)
            self._portrait_btn.setText("\u25cb 竖屏")
            self._portrait_btn.setChecked(False)
        else:
            self._landscape_btn.setText("\u25cb 横屏")
            self._landscape_btn.setChecked(False)
            self._portrait_btn.setText("\u25cf 竖屏")
            self._portrait_btn.setChecked(True)
        self._populate_ratio_combo(orientation)

    def _on_subtitle_toggle(self, checked: bool) -> None:
        self._preview_btn.setEnabled(checked)
        self._subtitle_check.setText("\u2611 启用字幕" if checked else "\u2610 启用字幕")

    def _on_upload_audio(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择音频文件", "",
            "音频文件 (*.mp3 *.wav *.m4a *.aac);;所有文件 (*.*)"
        )
        if file_path:
            self._audio_path_label.setText(file_path)

    def _on_upload_subtitle(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "",
            "字幕文件 (*.srt);;所有文件 (*.*)"
        )
        if file_path:
            self._subtitle_path_label.setText(file_path)

    def _on_preview(self) -> None:
        if not self._project:
            return
        if not self._project.material_videos:
            logger.warning("没有素材视频可预览")
            return
        video_path = self._project.material_videos[0].path
        if not video_path or not os.path.exists(video_path):
            logger.warning("视频文件不存在")
            return

        dialog = SubtitlePreviewDialog(video_path, self)
        if dialog.exec_() == SubtitlePreviewDialog.Accepted:
            settings = dialog.get_settings()
            if self._project:
                self._project.subtitle_font = settings["font"]
                self._project.subtitle_font_size = settings["font_size"]
                self._project.subtitle_font_color = settings["font_color"]
                self._project.subtitle_bg_color = settings["bg_color"]
                self._project.subtitle_bg_opacity = settings["bg_opacity"]
                self._project.subtitle_position = settings.get("position", "bottom")
                self._project.subtitle_offset_x = settings.get("offset_x", 0.5)
                self._project.subtitle_offset_y = settings.get("offset_y", 0.9)

    def _on_export_video(self) -> None:
        if not self._project:
            return

        self._save_export_settings()
        if not self._validate_export():
            return

        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("准备导出...")

        self._cleanup_workers()
        self._worker = NovelMixExportWorker(self._project)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.export_finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._worker.start()

    def _on_export_draft(self) -> None:
        if not self._project or not self._settings_service:
            return

        self._save_export_settings()

        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("正在准备导出到剪映草稿...")

        self._cleanup_workers()
        self._jianying_worker = NovelMixJianyingExportWorker(self._project, self._settings_service)
        self._jianying_worker.progress.connect(self._on_export_progress)
        self._jianying_worker.export_finished.connect(self._on_jianying_export_finished)
        self._jianying_worker.error.connect(self._on_export_error)
        self._jianying_worker.start()

    def _validate_export(self) -> bool:
        if not self._project.material_videos:
            logger.warning("没有素材视频")
            return False
        if self._project.dub_mode == "system":
            if not self._project.audio_files:
                logger.warning("没有生成配音")
                return False
        elif self._project.dub_mode == "self":
            audio_path = self._audio_path_label.text()
            if not audio_path or audio_path == "未上传音频文件" or not os.path.exists(audio_path):
                logger.warning("没有上传配音音频")
                return False
        return True

    def _save_export_settings(self) -> None:
        if not self._project:
            return
        self._project.aspect_ratio = self._ratio_combo.currentData() or "16:9"
        self._project.orientation = "landscape" if self._landscape_btn.isChecked() else "portrait"
        self._project.enable_subtitle = self._subtitle_check.isChecked()
        if self._project.dub_mode == "self":
            self._project.self_audio_path = self._audio_path_label.text()
            self._project.self_subtitle_path = self._subtitle_path_label.text()
            if self._project.self_subtitle_path == "未上传字幕文件":
                self._project.self_subtitle_path = ""

    def _restore_ui_from_project(self) -> None:
        if not self._project:
            return

        is_system = self._project.dub_mode == "system"

        if is_system:
            duration = self._project.total_audio_duration
            self._info_label.setText(f"配音已生成，总时长 {_format_duration(duration)}，请选择导出设置")
        else:
            self._info_label.setText("请上传配音文件，选择导出设置")
            if self._project.self_audio_path:
                self._audio_path_label.setText(self._project.self_audio_path)
            if self._project.self_subtitle_path:
                self._subtitle_path_label.setText(self._project.self_subtitle_path)

        if self._project.orientation == "portrait":
            self._on_orientation_change("portrait")
        else:
            self._landscape_btn.setChecked(True)
            self._portrait_btn.setChecked(False)

        idx = self._ratio_combo.findData(self._project.aspect_ratio)
        if idx >= 0:
            self._ratio_combo.setCurrentIndex(idx)

        self._subtitle_check.setChecked(self._project.enable_subtitle)
        self._on_subtitle_toggle(self._project.enable_subtitle)

    def restore(self, project: NovelMixProjectState) -> None:
        self._project = project
        if self._current_mode:
            self._restore_ui_from_project()

    def _on_export_progress(self, msg: str) -> None:
        self._progress_label.setText(msg)

    def _on_export_finished(self, output_path: str) -> None:
        self._progress_container.hide()
        self._result_container.show()
        self._output_path = output_path
        logger.info("导出成功: %s", output_path)

    def _on_jianying_export_finished(self, result) -> None:
        self._progress_container.hide()
        self._card_container.show()
        if result is None:
            logger.error("导出结果为空")
            from clip_synth.ui.components.toast import show_toast
            show_toast(self, "导出结果为空", "error", duration=3000)
            return
        draft_name = result.get("draft_name", "")
        logger.info("导出到剪映草稿成功: %s", draft_name)
        from clip_synth.ui.components.toast import show_toast
        show_toast(self, f"成功导出到剪映草稿: {draft_name}", "success", duration=3000)

    def _on_export_error(self, msg: str) -> None:
        self._progress_container.hide()
        self._card_container.show()
        logger.error("导出失败: %s", msg)
        from clip_synth.ui.components.toast import show_toast
        show_toast(self, f"导出失败: {msg}", "error", duration=5000)

    def _on_open_folder(self) -> None:
        if self._output_path:
            folder = os.path.dirname(self._output_path)
            if os.path.exists(folder):
                try:
                    subprocess.Popen(f'explorer /select,"{self._output_path}"')
                except Exception as e:
                    logger.error("打开文件夹失败: %s", e)

    def _cleanup_workers(self) -> None:
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


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
