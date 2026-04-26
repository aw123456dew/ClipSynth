from PySide6.QtCore import Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QWidget,
)

from frame_cut.core.database import DatabaseManager
from frame_cut.services.project_state_service import ProjectStateService
from frame_cut.services.settings_service import SettingsService
from frame_cut.ui.widgets.content_area import ContentArea
from frame_cut.ui.widgets.nav_sidebar import NavSidebar


class MainWindow(QMainWindow):
    def __init__(self, db_manager: DatabaseManager, parent: QWidget | None = None):
        super().__init__(parent)
        self._db_manager = db_manager

        self._setup_window()
        self._setup_menu_bar()
        self._setup_central_widget()
        self._setup_status_bar()

    def _setup_window(self) -> None:
        self.setWindowTitle("FrameCut - AI Video Editor")
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

    def _setup_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        new_action = QAction("&New Project", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self._on_new_project)
        file_menu.addAction(new_action)

        open_action = QAction("&Open Project...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open_project)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        import_action = QAction("&Import Media...", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(self._on_import_media)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        export_action = QAction("&Export Video...", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._on_export)
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = menu_bar.addMenu("&Edit")
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.Undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.Redo)
        edit_menu.addAction(redo_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About FrameCut", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _setup_central_widget(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._nav_sidebar = NavSidebar()
        layout.addWidget(self._nav_sidebar)

        self._settings_service = SettingsService(self._db_manager)
        self._project_state_service = ProjectStateService()
        self._content_area = ContentArea(
            self._settings_service, self._db_manager, self._project_state_service
        )
        layout.addWidget(self._content_area, stretch=1)

        self._nav_sidebar.page_changed.connect(self._content_area.switch_to)

    def _setup_status_bar(self) -> None:
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        status_bar.showMessage("Ready")

    @Slot()
    def _on_new_project(self) -> None:
        pass

    @Slot()
    def _on_open_project(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", "FrameCut Project (*.fcproj)"
        )
        if file_path:
            pass

    @Slot()
    def _on_import_media(self) -> None:
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Media",
            "",
            "Video Files (*.mp4 *.avi *.mov *.mkv);;All Files (*.*)",
        )
        if file_paths:
            pass

    @Slot()
    def _on_export(self) -> None:
        pass

    @Slot()
    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About FrameCut",
            "FrameCut v0.1.0\n\nAI-powered video clipping application.",
        )
