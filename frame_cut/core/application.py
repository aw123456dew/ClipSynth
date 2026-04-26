import sys
from pathlib import Path

import qasync
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from frame_cut.core.database import DatabaseManager
from frame_cut.ui.main_window import MainWindow
from frame_cut.utils.logger import setup_logger


class Application:
    def __init__(self):
        self._qt_app: QApplication | None = None
        self._main_window: MainWindow | None = None
        self._db_manager: DatabaseManager | None = None

    def initialize(self) -> None:
        setup_logger()

        self._qt_app = QApplication(sys.argv)
        self._qt_app.setApplicationName("FrameCut")
        self._qt_app.setOrganizationName("FrameCut")
        self._qt_app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

        self._load_stylesheet()

        self._db_manager = DatabaseManager()
        self._db_manager.initialize()

        self._main_window = MainWindow(self._db_manager)
        self._main_window.show()

    def _load_stylesheet(self) -> None:
        qss_path = Path(__file__).parent.parent / "resources" / "styles" / "main.qss"
        if qss_path.exists():
            with open(qss_path, "r", encoding="utf-8") as f:
                self._qt_app.setStyleSheet(f.read())

    async def run_async(self) -> int:
        self.initialize()
        loop = qasync.QEventLoop(self._qt_app)
        with loop:
            return loop.run_forever()

    def run(self) -> int:
        self.initialize()
        return self._qt_app.exec()

    @property
    def main_window(self) -> MainWindow:
        assert self._main_window is not None
        return self._main_window

    @property
    def db_manager(self) -> DatabaseManager:
        assert self._db_manager is not None
        return self._db_manager
