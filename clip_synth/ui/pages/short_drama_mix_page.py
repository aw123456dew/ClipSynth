from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
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

from clip_synth.core.database import DatabaseManager
from clip_synth.services.project_service import ProjectService
from clip_synth.services.project_state_service import ProjectStateService


class NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setFixedSize(420, 360)
        self.setObjectName("newProjectDialog")
        self._cover_path: str | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("新建智能剪辑项目")
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        name_label = QLabel("项目名称")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("dialogNameInput")
        self._name_input.setPlaceholderText("请输入项目名称")
        layout.addWidget(self._name_input)

        cover_label = QLabel("项目封面（可选）")
        cover_label.setObjectName("dialogFieldLabel")
        layout.addWidget(cover_label)

        cover_row = QHBoxLayout()
        cover_row.setSpacing(12)

        self._cover_preview = QLabel()
        self._cover_preview.setObjectName("dialogCoverPreview")
        self._cover_preview.setFixedSize(120, 68)
        self._cover_preview.setAlignment(Qt.AlignCenter)
        self._cover_preview.setText("无封面")
        cover_row.addWidget(self._cover_preview)

        cover_btn_layout = QVBoxLayout()
        cover_btn_layout.setSpacing(8)

        self._upload_cover_btn = QPushButton("上传封面")
        self._upload_cover_btn.setObjectName("dialogUploadCoverBtn")
        self._upload_cover_btn.clicked.connect(self._on_upload_cover)
        cover_btn_layout.addWidget(self._upload_cover_btn)

        self._clear_cover_btn = QPushButton("清除封面")
        self._clear_cover_btn.setObjectName("dialogClearCoverBtn")
        self._clear_cover_btn.clicked.connect(self._on_clear_cover)
        self._clear_cover_btn.setVisible(False)
        cover_btn_layout.addWidget(self._clear_cover_btn)

        cover_btn_layout.addStretch()
        cover_row.addLayout(cover_btn_layout)
        cover_row.addStretch()
        layout.addLayout(cover_row)

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

    def _on_upload_cover(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择封面图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)"
        )
        if file_path:
            self._cover_path = file_path
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self._cover_preview.setPixmap(
                    pixmap.scaled(120, 68, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )
                self._cover_preview.setScaledContents(True)
            self._clear_cover_btn.setVisible(True)

    def _on_clear_cover(self) -> None:
        self._cover_path = None
        self._cover_preview.setPixmap(QPixmap())
        self._cover_preview.setText("无封面")
        self._clear_cover_btn.setVisible(False)

    @property
    def project_name(self) -> str:
        name = self._name_input.text().strip()
        return name if name else "智能剪辑项目"

    @property
    def cover_path(self) -> str | None:
        return self._cover_path


class ProjectCard(QFrame):
    selected_changed = Signal(int, bool)
    open_project = Signal(str)

    def __init__(
        self,
        project_id: int,
        name: str,
        thumbnail_path: str | None = None,
        is_smart_project: bool = False,
        smart_project_id: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._project_name = name
        self._thumbnail_path = thumbnail_path
        self._is_smart_project = is_smart_project
        self._smart_project_id = smart_project_id
        self._checked = False
        self.setObjectName("projectCard")
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignCenter)

        thumbnail_container = QFrame()
        thumbnail_container.setObjectName("cardThumbnail")
        thumbnail_container.setFixedSize(200, 280)

        thumb_layout = QVBoxLayout(thumbnail_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        thumb_layout.setAlignment(Qt.AlignCenter)

        self._thumbnail_label = QLabel()
        self._thumbnail_label.setObjectName("cardThumbnailImage")
        self._thumbnail_label.setAlignment(Qt.AlignCenter)
        self._thumbnail_label.setFixedSize(200, 280)

        self._update_thumbnail()

        thumb_layout.addWidget(self._thumbnail_label)

        self._check_box = QCheckBox(thumbnail_container)
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

        layout.addWidget(thumbnail_container)
        layout.addSpacing(8)
        layout.addWidget(name_label)

    def _update_thumbnail(self) -> None:
        if self._thumbnail_path:
            pixmap = QPixmap(self._thumbnail_path)
            if not pixmap.isNull():
                self._thumbnail_label.setPixmap(
                    pixmap.scaled(200, 280, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )
                self._thumbnail_label.setScaledContents(True)
                return
        if self._is_smart_project:
            self._thumbnail_label.setText("智能剪辑")
        else:
            self._thumbnail_label.setText("无预览")

    def _on_check_toggled(self, checked: bool) -> None:
        self._checked = checked
        self.setProperty("selected", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.selected_changed.emit(self._project_id, checked)

    def set_checked(self, checked: bool) -> None:
        self._check_box.setChecked(checked)

    def is_checked(self) -> bool:
        return self._check_box.isChecked()

    def mousePressEvent(self, event):  # noqa: N802
        if self._is_smart_project and self._smart_project_id:
            self.open_project.emit(self._smart_project_id)
            event.accept()
            return
        super().mousePressEvent(event)

    @property
    def project_id(self) -> int:
        return self._project_id


class ShortDramaMixPage(QFrame):
    start_project_wizard = Signal(list)
    open_smart_project = Signal(str)

    def __init__(
        self,
        db_manager: DatabaseManager,
        project_state_service: ProjectStateService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._db_manager = db_manager
        self._project_service = ProjectService(db_manager.create_session())
        self._project_state_service = project_state_service
        self._cards: list[ProjectCard] = []
        self.setObjectName("shortDramaMixPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        title_label = QLabel("短剧混剪")
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

        self._new_project_btn = QPushButton("新增项目")
        self._new_project_btn.setObjectName("mixNewProjectBtn")
        self._new_project_btn.clicked.connect(self._on_new_project)
        toolbar_layout.addWidget(self._new_project_btn)

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

        smart_projects = self._project_state_service.list_projects()
        for project in smart_projects:
            card = ProjectCard(
                project_id=0,
                name=project.name,
                thumbnail_path=project.cover_path,
                is_smart_project=True,
                smart_project_id=project.id,
            )
            card.open_project.connect(self._on_open_smart_project)
            card.selected_changed.connect(self._on_card_selection_changed)
            self._cards.append(card)
            row = idx // 4
            col = idx % 4
            self._grid_layout.addWidget(card, row, col)
            idx += 1

        projects = self._project_service.get_all_projects()
        for project in projects:
            card = ProjectCard(
                project_id=project.id,
                name=project.name,
                thumbnail_path=project.file_path,
            )
            card.selected_changed.connect(self._on_card_selection_changed)
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

    def _on_new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)",
        )
        if file_paths:
            self._start_project_wizard(file_paths, dialog.project_name, dialog.cover_path)

    def _start_project_wizard(
        self,
        video_paths: list[str],
        project_name: str | None = None,
        cover_path: str | None = None,
    ) -> None:
        self.start_project_wizard.emit((video_paths, project_name, cover_path))

    def _on_open_smart_project(self, project_id: str) -> None:
        self.open_smart_project.emit(project_id)

    def _on_select_all(self) -> None:
        all_selected = all(card.is_checked() for card in self._cards)
        new_state = not all_selected
        for card in self._cards:
            card.set_checked(new_state)
        self._select_all_btn.setText("取消全选" if new_state else "全选")

    def _on_delete_selected(self) -> None:
        selected_ids = [
            card.project_id for card in self._cards
            if card.is_checked() and not card._is_smart_project
        ]
        selected_smart_ids = [
            card._smart_project_id for card in self._cards
            if card._is_smart_project and card.is_checked()
        ]

        if not selected_ids and not selected_smart_ids:
            return

        for pid in selected_ids:
            self._project_service.delete_project(pid)
        for sid in selected_smart_ids:
            if sid:
                self._project_state_service.delete_project(sid)

        self._select_all_btn.setText("全选")

        self._load_projects()

    def _on_card_selection_changed(self, project_id: int, checked: bool) -> None:
        selected_count = sum(1 for card in self._cards if card.is_checked())
        if selected_count > 0:
            all_count = len(self._cards)
            self._select_all_btn.setText("取消全选" if selected_count == all_count else "全选")
        else:
            self._select_all_btn.setText("全选")


