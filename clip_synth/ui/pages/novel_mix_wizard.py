import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.core.database import DatabaseManager
from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.novel_mix_state_service import NovelMixStateService
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger("clip_synth.novel_mix_wizard")


class NovelMixWizard(QFrame):
    finished = Signal()
    cancelled = Signal()

    def __init__(
        self,
        project: NovelMixProjectState,
        state_service: NovelMixStateService,
        settings_service: SettingsService,
        db_manager: DatabaseManager | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project = project
        self._state_service = state_service
        self._settings_service = settings_service
        self._db_manager = db_manager
        self._current_step = self._project.current_step
        self._total_steps = 3

        self.setObjectName("smartNarrateWizard")
        self._setup_ui()
        self._update_nav_buttons()
        self._stack.setCurrentIndex(self._current_step)
        self._update_step_indicators()

    @property
    def current_step(self) -> int:
        return self._current_step

    def is_system_dub(self) -> bool:
        return self._project.dub_mode == "system"

    def is_self_dub(self) -> bool:
        return self._project.dub_mode == "self"

    def _setup_ui(self) -> None:
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
            "选择素材",
            "文案配音",
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

        from clip_synth.ui.pages.novel_mix_material_page import NovelMixMaterialPage
        self._material_page = NovelMixMaterialPage()
        self._material_page.scanning_changed.connect(self._on_scanning_changed)
        self._stack.addWidget(self._material_page)

        from clip_synth.ui.pages.novel_mix_dub_page import NovelMixDubPage
        self._dub_page = NovelMixDubPage()
        self._dub_page.set_settings_service(self._settings_service)
        self._stack.addWidget(self._dub_page)

        from clip_synth.ui.pages.novel_mix_export_page import NovelMixExportPage
        self._export_page = NovelMixExportPage(self._settings_service)
        self._stack.addWidget(self._export_page)

        layout.addWidget(self._stack, stretch=1)

        self._scanning_hint = QLabel("素材扫描中，请稍候...")
        self._scanning_hint.setObjectName("ttsProgressLabel")
        self._scanning_hint.setAlignment(Qt.AlignCenter)
        self._scanning_hint.hide()
        layout.addWidget(self._scanning_hint)

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

        self._restore_page_state()

    def _update_step_indicators(self) -> None:
        for i in range(self._total_steps):
            is_active = i == self._current_step
            is_done = i < self._current_step

            if self.is_self_dub() and i == 1:
                is_active = False
                is_done = True

            self._step_numbers[i].setProperty("active", is_active)
            self._step_numbers[i].setProperty("done", is_done)
            self._step_numbers[i].style().unpolish(self._step_numbers[i])
            self._step_numbers[i].style().polish(self._step_numbers[i])

            self._step_texts[i].setProperty("active", is_active)
            self._step_texts[i].setProperty("done", is_done)
            self._step_texts[i].style().unpolish(self._step_texts[i])
            self._step_texts[i].style().polish(self._step_texts[i])

    def _update_nav_buttons(self) -> None:
        self._prev_btn.setEnabled(self._current_step > 0)

        if self._current_step == self._total_steps - 1:
            self._next_btn.hide()
            self._finish_btn.show()
        else:
            self._next_btn.show()
            self._finish_btn.hide()

    def _restore_page_state(self) -> None:
        self._material_page.restore(self._project)
        self._dub_page.restore(self._project)
        self._export_page.restore(self._project)

        if self._current_step == self._total_steps - 1:
            self._export_page.set_project(self._project)

    def _save_project(self) -> None:
        self._project.current_step = self._current_step
        self._state_service.save_project(self._project)

    def _on_scanning_changed(self, scanning: bool) -> None:
        if self._current_step != 0:
            return
        if scanning:
            self._next_btn.setEnabled(False)
            self._next_btn.setText("素材扫描中...")
            self._scanning_hint.show()
        else:
            self._next_btn.setEnabled(True)
            self._next_btn.setText("下一步")
            self._scanning_hint.hide()

    def _on_prev(self) -> None:
        if self._current_step > 0:
            if self.is_self_dub() and self._current_step == 2:
                self._current_step = 0
            else:
                self._current_step -= 1
            self._save_project()
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()
            self._next_btn.setEnabled(True)

    def _on_next(self) -> None:
        if self._current_step == 0:
            self._material_page.save(self._project)

            self._show_dub_mode_dialog()
            if not self._project.dub_mode:
                return

            self._save_project()

            if self.is_self_dub():
                self._current_step = 2
                self._export_page.set_project(self._project)
            else:
                self._current_step = 1
                self._dub_page.restore(self._project)

        elif self._current_step == 1:
            self._dub_page.save(self._project)
            self._save_project()
            self._current_step = 2
            self._export_page.set_project(self._project)

        self._stack.setCurrentIndex(self._current_step)
        self._update_step_indicators()
        self._update_nav_buttons()

    def _show_dub_mode_dialog(self) -> None:
        dialog = _DubModeDialog(self)
        if dialog.exec() != _DubModeDialog.Accepted:
            return
        self._project.dub_mode = dialog.selected_mode
        self._save_project()

    def _on_cancel(self) -> None:
        self.cancelled.emit()

    def _on_finish(self) -> None:
        self.finished.emit()


class _DubModeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.selected_mode = ""
        self.setWindowTitle("选择配音方式")
        self.setFixedSize(480, 280)
        self.setObjectName("dubModeDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("选择配音方式")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(16)

        system_card = QPushButton()
        system_card.setObjectName("dubModeCard")
        system_card.setCursor(Qt.PointingHandCursor)
        system_card.setMinimumHeight(140)
        system_card.clicked.connect(lambda: self._select("system"))
        system_card_layout = QVBoxLayout(system_card)
        system_card_layout.setAlignment(Qt.AlignCenter)
        system_card_layout.setSpacing(8)
        icon1 = QLabel("\U0001f399")
        icon1.setAlignment(Qt.AlignCenter)
        icon1.setStyleSheet("font-size: 32px;")
        system_card_layout.addWidget(icon1)
        title1 = QLabel("系统配音")
        title1.setObjectName("dubModeCardTitle")
        title1.setAlignment(Qt.AlignCenter)
        system_card_layout.addWidget(title1)
        desc1 = QLabel("输入小说文本，使用豆包TTS\n自动生成配音和字幕")
        desc1.setObjectName("dubModeCardDesc")
        desc1.setAlignment(Qt.AlignCenter)
        desc1.setWordWrap(True)
        system_card_layout.addWidget(desc1)
        cards_layout.addWidget(system_card)

        self_card = QPushButton()
        self_card.setObjectName("dubModeCard")
        self_card.setCursor(Qt.PointingHandCursor)
        self_card.setMinimumHeight(140)
        self_card.clicked.connect(lambda: self._select("self"))
        self_card_layout = QVBoxLayout(self_card)
        self_card_layout.setAlignment(Qt.AlignCenter)
        self_card_layout.setSpacing(8)
        icon2 = QLabel("\U0001f3b5")
        icon2.setAlignment(Qt.AlignCenter)
        icon2.setStyleSheet("font-size: 32px;")
        self_card_layout.addWidget(icon2)
        title2 = QLabel("自行配音")
        title2.setObjectName("dubModeCardTitle")
        title2.setAlignment(Qt.AlignCenter)
        self_card_layout.addWidget(title2)
        desc2 = QLabel("上传自己的音频文件和\n字幕文件，直接进入导出")
        desc2.setObjectName("dubModeCardDesc")
        desc2.setAlignment(Qt.AlignCenter)
        desc2.setWordWrap(True)
        self_card_layout.addWidget(desc2)
        cards_layout.addWidget(self_card)

        layout.addLayout(cards_layout)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn, alignment=Qt.AlignCenter)

    def _select(self, mode: str) -> None:
        self.selected_mode = mode
        self.accept()
