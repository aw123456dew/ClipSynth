from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
)


class ClippingMethodPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clippingMethodPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        label = QLabel("剪辑手法页面（待实现）")
        label.setObjectName("clippingPlaceholderLabel")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
