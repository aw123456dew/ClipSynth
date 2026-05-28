import logging

from PySide6.QtWidgets import (
    QFrame,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.ui.pages.video_cut_page import VideoCutPage
from clip_synth.ui.pages.video_dedup_page import VideoDedupPage

logger = logging.getLogger(__name__)


class VideoProcessPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("videoProcessPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("videoProcessTab")

        dedup_page = VideoDedupPage()
        self._tab_widget.addTab(dedup_page, "视频优化")

        cut_page = VideoCutPage()
        self._tab_widget.addTab(cut_page, "视频切割")

        layout.addWidget(self._tab_widget)
