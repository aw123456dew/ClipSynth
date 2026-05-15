import logging
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.narrate_project_state_service import NarrateProjectStateService
from clip_synth.ui.pages.short_drama_narrate_page import NarrateProjectCard

logger = logging.getLogger("clip_synth.narrate_v2")


class NarrateV2NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建解说项目 V2")
        self.setFixedSize(420, 220)
        self.setObjectName("newProjectDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("新建解说项目 V2")
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        name_label = QLabel("项目名称")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("dialogNameInput")
        self._name_input.setPlaceholderText("请输入项目名称")
        layout.addWidget(self._name_input)

        desc_label = QLabel("封面将自动从视频第一帧提取")
        desc_label.setObjectName("dialogFieldHint")
        desc_label.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(desc_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("确定")
        self._confirm_btn.setObjectName("dialogConfirmBtn")
        self._confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._confirm_btn)

        layout.addLayout(btn_row)

    @property
    def project_name(self) -> str:
        name = self._name_input.text().strip()
        return name if name else "解说项目"


class ShortDramaNarrateV2Page(QFrame):
    start_wizard = Signal(list)
    open_project = Signal(str)

    def __init__(
        self,
        narrate_project_state_service: NarrateProjectStateService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._narrate_project_state_service = narrate_project_state_service
        self._cards: list[NarrateProjectCard] = []
        self.setObjectName("shortDramaNarratePage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        title_label = QLabel("视频解说 V2")
        title_label.setObjectName("mixTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()

        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.setObjectName("mixSelectAllBtn")
        self._select_all_btn.clicked.connect(self._on_select_all)
        toolbar_layout.addWidget(self._select_all_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.setObjectName("mixDeleteBtn")
        self._delete_btn.clicked.connect(self._on_delete_selected)
        toolbar_layout.addWidget(self._delete_btn)

        new_project_btn = QPushButton("新增项目")
        new_project_btn.setObjectName("mixNewProjectBtn")
        new_project_btn.clicked.connect(self._on_new_project)
        toolbar_layout.addWidget(new_project_btn)

        layout.addWidget(toolbar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("mixScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("mixScrollContent")
        self._grid_layout = QGridLayout(scroll_content)
        self._grid_layout.setContentsMargins(24, 16, 24, 16)
        self._grid_layout.setSpacing(16)
        self._grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._load_projects()

    def _load_projects(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()

        self._cards.clear()
        idx = 0

        projects = self._narrate_project_state_service.list_projects_by_version(2)
        for project in projects:
            card = NarrateProjectCard(
                name=project.name,
                thumbnail_path=project.cover_path,
                project_id=project.id,
            )
            card.open_project.connect(self.open_project.emit)
            self._cards.append(card)
            row = idx // 4
            col = idx % 4
            self._grid_layout.addWidget(card, row, col)
            idx += 1

        if idx == 0:
            empty_label = QLabel("暂无项目，点击「新增项目」开始创建")
            empty_label.setObjectName("mixEmptyLabel")
            empty_label.setAlignment(Qt.AlignCenter)
            self._grid_layout.addWidget(empty_label, 0, 0, 1, 4)

    def refresh_project_cover(self, project_id: str) -> None:
        project = self._narrate_project_state_service.load_project(project_id)
        if not project or not project.cover_path:
            return
        for card in self._cards:
            if card._project_id == project_id:
                card.set_thumbnail(project.cover_path)
                break

    def sync_all_covers(self) -> None:
        for card in self._cards:
            project = self._narrate_project_state_service.load_project(card._project_id)
            if project and project.cover_path and project.cover_path != card._thumbnail_path:
                card.set_thumbnail(project.cover_path)

    def _on_new_project(self) -> None:
        dialog = NarrateV2NewProjectDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)",
        )
        if file_paths:
            file_paths.sort(key=lambda p: os.path.basename(p).lower())
            self.start_wizard.emit([file_paths, dialog.project_name])

    def _on_select_all(self) -> None:
        all_selected = all(card.is_checked() for card in self._cards)
        new_state = not all_selected
        for card in self._cards:
            card.set_checked(new_state)
        self._select_all_btn.setText("取消全选" if new_state else "全选")

    def _on_delete_selected(self) -> None:
        selected_ids = [
            card._project_id for card in self._cards
            if card.is_checked()
        ]

        if not selected_ids:
            return

        for sid in selected_ids:
            if sid:
                self._narrate_project_state_service.delete_project(sid)

        self._select_all_btn.setText("全选")
        self._load_projects()
