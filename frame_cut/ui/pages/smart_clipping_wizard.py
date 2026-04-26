from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frame_cut.models import SmartClippingProjectState
from frame_cut.services import ProjectStateService
from frame_cut.services.ai_service import AIService
from frame_cut.services.video_analysis_service import VideoAnalysisService


class SmartClippingWizard(QFrame):
    finished = Signal()
    cancelled = Signal()

    def __init__(
        self,
        project: SmartClippingProjectState,
        project_state_service: ProjectStateService,
        ai_service: AIService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project = project
        self._project_state_service = project_state_service
        self._analysis_service = VideoAnalysisService(ai_service)
        self._current_step = self._project.current_step
        self._total_steps = 4
        self.setObjectName("smartClippingWizard")
        self._setup_ui()
        self._update_step_indicators()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("wizardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 24, 0, 24)

        self._step_indicators = []
        self._step_numbers = []
        self._step_texts = []

        steps = [
            "上传字幕",
            "AI视频分析",
            "剪辑手法",
            "导出",
        ]

        for i, step_text in enumerate(steps):
            step_container = QFrame()
            step_layout = QHBoxLayout(step_container)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(12)

            step_num = QLabel(str(i + 1))
            step_num.setObjectName("stepNumber")
            step_layout.addWidget(step_num)
            self._step_numbers.append(step_num)

            step_label = QLabel(step_text)
            step_label.setObjectName("stepText")
            step_layout.addWidget(step_label)
            self._step_texts.append(step_label)

            self._step_indicators.append(step_container)
            header_layout.addWidget(step_container)

            if i < len(steps) - 1:
                separator = QLabel("")
                separator.setObjectName("stepSeparator")
                separator.setMinimumWidth(40)
                header_layout.addWidget(separator)

        header_layout.addStretch()
        layout.addWidget(header)

        self._stack = QStackedWidget()

        from frame_cut.ui.pages.upload_subtitle_page import UploadSubtitlePage

        self._upload_page = UploadSubtitlePage(self._project.videos)
        self._stack.addWidget(self._upload_page)

        from frame_cut.ui.pages.ai_analysis_page import AiAnalysisPage

        self._ai_page = AiAnalysisPage(
            self._project, self._analysis_service, self._project_state_service,
        )
        self._ai_page.ready_for_next.connect(self._on_ai_ready)
        self._stack.addWidget(self._ai_page)

        from frame_cut.ui.pages.clipping_method_page import ClippingMethodPage

        self._method_page = ClippingMethodPage()
        self._stack.addWidget(self._method_page)

        from frame_cut.ui.pages.export_page import ExportPage

        self._export_page = ExportPage()
        self._stack.addWidget(self._export_page)

        layout.addWidget(self._stack, stretch=1)

        footer = QFrame()
        footer.setObjectName("wizardFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 16, 24, 16)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("wizardCancelBtn")
        self._cancel_btn.clicked.connect(self._on_cancel)
        footer_layout.addWidget(self._cancel_btn)

        footer_layout.addStretch()

        self._prev_btn = QPushButton("上一步")
        self._prev_btn.setObjectName("wizardPrevBtn")
        self._prev_btn.clicked.connect(self._on_prev)
        self._prev_btn.setEnabled(False)
        footer_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一步")
        self._next_btn.setObjectName("wizardNextBtn")
        self._next_btn.clicked.connect(self._on_next)
        footer_layout.addWidget(self._next_btn)

        self._finish_btn = QPushButton("完成")
        self._finish_btn.setObjectName("wizardFinishBtn")
        self._finish_btn.clicked.connect(self._on_finish)
        self._finish_btn.hide()
        footer_layout.addWidget(self._finish_btn)

        layout.addWidget(footer)

        self._stack.setCurrentIndex(self._current_step)
        self._update_nav_buttons()

    def _update_step_indicators(self):
        for i in range(self._total_steps):
            is_active = i == self._current_step
            is_done = i < self._current_step

            self._step_numbers[i].setProperty("active", is_active)
            self._step_numbers[i].setProperty("done", is_done)
            self._step_numbers[i].style().unpolish(self._step_numbers[i])
            self._step_numbers[i].style().polish(self._step_numbers[i])

            self._step_texts[i].setProperty("active", is_active)
            self._step_texts[i].setProperty("done", is_done)
            self._step_texts[i].style().unpolish(self._step_texts[i])
            self._step_texts[i].style().polish(self._step_texts[i])

    def _update_nav_buttons(self):
        self._prev_btn.setEnabled(self._current_step > 0)

        if self._current_step == self._total_steps - 1:
            self._next_btn.hide()
            self._finish_btn.show()
        else:
            self._next_btn.show()
            self._finish_btn.hide()

    def _on_prev(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._project.current_step = self._current_step
            self._save_project()
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()
            if self._current_step == 0:
                self._next_btn.setEnabled(True)

    def _on_next(self):
        if self._current_step < self._total_steps - 1:
            if self._current_step == 0:
                subtitles = self._upload_page.get_subtitles()
                for video_state in self._project.videos:
                    video_state.subtitle_path = subtitles.get(video_state.video_path)
                self._save_project()
            elif self._current_step == 1:
                if not self._project.is_all_videos_ready():
                    return
                self._save_project()

            self._current_step += 1
            self._project.current_step = self._current_step
            self._save_project()
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()

            if self._current_step == 1:
                self._next_btn.setEnabled(False)

    def _on_ai_ready(self, ready):
        if self._current_step == 1:
            self._next_btn.setEnabled(ready)

    def _on_finish(self):
        self.finished.emit()

    def _on_cancel(self):
        self._save_project()
        self.cancelled.emit()

    def _save_project(self):
        self._project_state_service.save_project(self._project)
