"""小说改写项目列表页面，与混剪项目列表页结构一致"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.novel_rewrite_state_service import NovelRewriteStateService

logger = logging.getLogger("clip_synth.novel_rewrite")


class NovelRewriteNewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建小说改写项目")
        self.setFixedSize(420, 200)
        self.setObjectName("newProjectDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("新建小说改写项目")
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        name_label = QLabel("项目名称")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("dialogNameInput")
        self._name_input.setPlaceholderText("请输入项目名称")
        layout.addWidget(self._name_input)

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
        return name if name else "小说改写项目"


class NovelRewriteProjectCard(QFrame):
    selected_changed = Signal(str, bool)
    open_project = Signal(str)

    def __init__(
        self,
        project_id: str,
        name: str,
        chapter_count: int = 0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._project_name = name
        self._chapter_count = chapter_count
        self._checked = False
        self.setObjectName("projectCard")
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignCenter)

        placeholder = QFrame()
        placeholder.setObjectName("cardThumbnail")
        placeholder.setFixedSize(200, 280)

        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setAlignment(Qt.AlignCenter)

        icon_label = QLabel("\U0001f4d6")
        icon_label.setObjectName("novelMixCardIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 48px;")
        placeholder_layout.addWidget(icon_label)

        info_label = QLabel(f"{self._chapter_count} 章")
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        placeholder_layout.addWidget(info_label)

        self._check_box = QCheckBox(placeholder)
        self._check_box.setObjectName("cardCheckBox")
        self._check_box.toggled.connect(self._on_check_toggled)
        self._check_box.move(166, 8)
        self._check_box.raise_()

        name_label = QLabel(self._project_name)
        name_label.setObjectName("cardName")
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setFixedWidth(200)
        name_label.setMinimumHeight(40)

        layout.addWidget(placeholder)
        layout.addSpacing(8)
        layout.addWidget(name_label)

    def _on_check_toggled(self, checked: bool) -> None:
        self._checked = checked
        self.selected_changed.emit(self._project_id, checked)

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool) -> None:
        self._checked = checked
        self._check_box.setChecked(checked)

    def mousePressEvent(self, event) -> None:
        if not self._check_box.geometry().contains(event.pos()):
            self.open_project.emit(self._project_id)
        super().mousePressEvent(event)


class NovelRewriteProjectListPage(QFrame):
    open_project = Signal(str)

    def __init__(
        self,
        state_service: NovelRewriteStateService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._state_service = state_service
        self._cards: list[NovelRewriteProjectCard] = []
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

        title_label = QLabel("小说改写")
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
        self._grid_layout = QVBoxLayout(scroll_content)
        self._grid_layout.setContentsMargins(24, 16, 24, 16)
        self._grid_layout.setSpacing(16)
        self._grid_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._refresh()

    def _refresh(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
        self._cards.clear()

        projects = self._state_service.list_projects()
        if not projects:
            empty_label = QLabel("暂无项目，点击「新增项目」开始创建")
            empty_label.setObjectName("chapterEmptyLabel")
            empty_label.setAlignment(Qt.AlignCenter)
            self._grid_layout.addWidget(empty_label)
            return

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(16)
        row_layout.setAlignment(Qt.AlignLeft)

        for proj in projects:
            card = NovelRewriteProjectCard(
                proj["id"], proj["name"], proj.get("chapter_count", 0),
            )
            card.selected_changed.connect(self._on_card_selected)
            card.open_project.connect(self._on_open_project)
            self._cards.append(card)
            row_layout.addWidget(card)

        self._grid_layout.addWidget(row_widget)

    def _on_card_selected(self, project_id: str, checked: bool) -> None:
        pass

    def _on_select_all(self) -> None:
        all_checked = any(not c.is_checked() for c in self._cards)
        for card in self._cards:
            card.set_checked(all_checked)

    def _on_delete_selected(self) -> None:
        for card in self._cards:
            if card.is_checked():
                self._state_service.delete_project(card._project_id)
        self._refresh()

    def _on_new_project(self) -> None:
        dialog = NovelRewriteNewProjectDialog(self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        name = dialog.project_name
        project_id = self._state_service.create_project(name)
        self._refresh()
        self.open_project.emit(project_id)

    def _on_open_project(self, project_id: str) -> None:
        self.open_project.emit(project_id)
