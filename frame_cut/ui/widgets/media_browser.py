
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class MediaBrowser(QFrame):
    media_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel)
        self.setMinimumWidth(240)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header_layout = QHBoxLayout()
        header_label = QLabel("Media Browser")
        header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        import_btn = QPushButton("Import")
        import_btn.clicked.connect(self._on_import)
        header_layout.addWidget(import_btn)

        layout.addLayout(header_layout)

        self._file_model = QFileSystemModel()
        self._file_model.setRootPath("")
        self._file_model.setNameFilters(
            ["*.mp4", "*.avi", "*.mov", "*.mkv", "*.jpg", "*.png", "*.jpeg"]
        )
        self._file_model.setNameFilterDisables(False)

        self._tree_view = QTreeView()
        self._tree_view.setModel(self._file_model)
        self._tree_view.setRootIndex(self._file_model.index(""))
        self._tree_view.setAnimated(True)
        self._tree_view.setIndentation(16)
        self._tree_view.setSortingEnabled(True)
        self._tree_view.hideColumn(1)
        self._tree_view.hideColumn(2)
        self._tree_view.hideColumn(3)

        layout.addWidget(self._tree_view, stretch=1)

    def _on_import(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Media Folder")
        if folder:
            self._tree_view.setRootIndex(self._file_model.index(folder))
