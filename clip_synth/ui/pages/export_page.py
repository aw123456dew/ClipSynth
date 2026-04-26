from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
)


class ExportPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exportPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        label = QLabel("导出到剪映页面（待实现）")
        label.setObjectName("exportPlaceholderLabel")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
