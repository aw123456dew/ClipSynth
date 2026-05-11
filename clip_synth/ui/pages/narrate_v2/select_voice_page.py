import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.narrate_v2")


class SelectVoicePage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("选择配音")
        title.setObjectName("aiStyleTitle")
        layout.addWidget(title)

        desc = QLabel("选择配音音色和参数，为解说文案添加语音。")
        desc.setObjectName("dialogFieldHint")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        placeholder = QFrame()
        placeholder.setObjectName("segmentCheckItem")
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setAlignment(Qt.AlignCenter)
        placeholder_label = QLabel("配音选择功能即将上线，敬请期待！")
        placeholder_label.setAlignment(Qt.AlignCenter)
        placeholder_label.setStyleSheet("color: #64748b; font-size: 14px;")
        placeholder_layout.addWidget(placeholder_label)
        layout.addWidget(placeholder, stretch=1)
