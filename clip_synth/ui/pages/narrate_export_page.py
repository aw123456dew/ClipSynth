import logging
import os
import subprocess

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.narrate_export_service import NarrateExportService

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: NarrateProjectState | None = None
        self._export_service: NarrateExportService | None = None
        self._worker: ExportWorker | None = None
        self.setObjectName("narrateExportPage")
        self._setup_ui()

    def set_project(self, project: NarrateProjectState, export_service: NarrateExportService):
        self._project = project
        self._export_service = export_service
        self._reset_ui()

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

        self._card_container = QFrame()
        self._card_container.setObjectName("exportCardsFrame")
        card_inner = QVBoxLayout(self._card_container)
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
        card_inner.addWidget(self._export_video_card)

        self._export_draft_card = _ExportActionCard(
            icon="✂️",
            title="导出到剪映草稿",
            description="将解说配音与视频生成剪映草稿文件，可在剪映专业版中继续编辑",
        )
        self._export_draft_card.clicked.connect(self._on_export_draft)
        self._export_draft_card.setObjectName("exportDraftCard")
        card_inner.addWidget(self._export_draft_card)

        hint = QLabel("提示：导出到剪映草稿功能开发中")
        hint.setObjectName("exportPlaceholderHint")
        hint.setAlignment(Qt.AlignCenter)
        card_inner.addWidget(hint)

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

        from PySide6.QtWidgets import QPushButton

        open_btn = QPushButton(" 打开文件夹 ")
        open_btn.setObjectName("exportOpenFolderBtn")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._on_open_folder)
        result_layout.addWidget(open_btn, alignment=Qt.AlignCenter)

        layout.addWidget(self._result_container)
        self._result_container.hide()

        layout.addStretch()

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

        self._card_container.hide()
        self._progress_container.show()
        self._progress_label.setText("准备导出...")

        self._worker = ExportWorker(self._export_service, self._project)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._worker.start()

    def _on_export_draft(self):
        logger.info("导出到剪映草稿（功能待实现）")

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
