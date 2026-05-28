import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from clip_synth.core.application import Application
from clip_synth.ui.login_dialog import LoginDialog
from clip_synth.utils.ffmpeg_helper import get_resource_path


def _load_stylesheet(app: QApplication) -> None:
    qss_path = get_resource_path("clip_synth/resources/styles/main.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main():
    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        qt_app = QApplication(sys.argv)
        qt_app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)
        _load_stylesheet(qt_app)

        if not LoginDialog.authenticate():
            sys.exit(1)

        qt_app.closeAllWindows()

    app = Application()

    if sys.platform == "win32":
        import asyncio
        if sys.version_info < (3, 14):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    sys.exit(app.run())


if __name__ == "__main__":
    main()
