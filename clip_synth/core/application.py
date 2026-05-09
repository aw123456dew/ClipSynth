import os
import sys

import qasync
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from clip_synth.core.database import DatabaseManager
from clip_synth.ui.main_window import MainWindow
from clip_synth.utils.ffmpeg_helper import add_ffmpeg_to_path, get_resource_path
from clip_synth.utils.logger import setup_logger


def _patch_pyjianying_assets() -> None:
    """修复 pyJianYingDraft 在打包后的资源文件路径问题"""
    if not getattr(sys, 'frozen', False):
        return
    
    try:
        import pyJianYingDraft.assets as assets_module
        
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            actual_assets_dir = os.path.join(meipass, 'pyJianYingDraft', 'assets')
            if os.path.isdir(actual_assets_dir):
                assets_module.ASSETS_DIR = type(assets_module.ASSETS_DIR)(actual_assets_dir)
    except Exception:
        pass


class Application:
    def __init__(self):
        self._qt_app: QApplication | None = None
        self._main_window: MainWindow | None = None
        self._db_manager: DatabaseManager | None = None

    def initialize(self) -> None:
        setup_logger()
        add_ffmpeg_to_path()
        _patch_pyjianying_assets()

        self._qt_app = QApplication(sys.argv)
        self._qt_app.setApplicationName("ClipSynth")
        self._qt_app.setOrganizationName("ClipSynth")
        self._qt_app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

        # 设置应用图标（标题栏 + 任务栏）
        icon_path = get_resource_path("clip_synth/resources/icons/icon.ico")
        if os.path.exists(icon_path):
            self._qt_app.setWindowIcon(QIcon(icon_path))

        self._load_stylesheet()

        self._db_manager = DatabaseManager()
        self._db_manager.initialize()

        self._main_window = MainWindow(self._db_manager)
        self._main_window.show()

    def _load_stylesheet(self) -> None:
        qss_path = get_resource_path("clip_synth/resources/styles/main.qss")
        if os.path.exists(qss_path):
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
