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

from clip_synth.services.narrate_project_state_service import NarrateProjectStateService


class NarrateNewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建解说项目")
        self.setFixedSize(420, 360)
        self.setObjectName("newProjectDialog")
        self._cover_path: str | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel("新建解说项目")
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
        return name if name else "解说项目"

    @property
    def cover_path(self) -> str | None:
        return self._cover_path


class NarrateProjectCard(QFrame):
    selected_changed = Signal(str, bool)
    open_project = Signal(str)

    def __init__(
        self,
        name: str,
        thumbnail_path: str | None = None,
        project_id: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_name = name
        self._thumbnail_path = thumbnail_path
        self._project_id = project_id
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
        self._thumbnail_label.setText("解说项目")

    def set_thumbnail(self, thumbnail_path: str) -> None:
        self._thumbnail_path = thumbnail_path
        self._update_thumbnail()

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
        if self._project_id:
            self.open_project.emit(self._project_id)
            event.accept()
            return
        super().mousePressEvent(event)


class ShortDramaNarratePage(QFrame):
    start_narrate_wizard = Signal(list)
    open_narrate_project = Signal(str)

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

        title_label = QLabel("视频解说")
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
            if item and item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()

        self._cards.clear()
        idx = 0

        projects = self._narrate_project_state_service.list_projects()
        for project in projects:
            card = NarrateProjectCard(
                name=project.name,
                thumbnail_path=project.cover_path,
                project_id=project.id,
            )
            card.open_project.connect(self._on_open_narrate_project)
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
        dialog = NarrateNewProjectDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*.*)",
        )
        if file_paths:
            self.start_narrate_wizard.emit((file_paths, dialog.project_name, dialog.cover_path))

    def refresh_project_cover(self, project_id: str) -> None:
        """刷新指定项目的封面（由后台封面提取完成后调用）"""
        project = self._narrate_project_state_service.load_project(project_id)
        if not project or not project.cover_path:
            return
        for card in self._cards:
            if card._project_id == project_id:
                card.set_thumbnail(project.cover_path)
                break

    def _on_open_narrate_project(self, project_id: str) -> None:
        self.open_narrate_project.emit(project_id)

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

    def _on_card_selection_changed(self, project_id: str, checked: bool) -> None:
        selected_count = sum(1 for card in self._cards if card.is_checked())
        if selected_count > 0:
            all_count = len(self._cards)
            self._select_all_btn.setText("取消全选" if selected_count == all_count else "全选")
        else:
            self._select_all_btn.setText("全选")
