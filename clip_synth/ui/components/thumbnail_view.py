from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class ThumbnailItem(QFrame):
    clicked = Signal(str)

    def __init__(
        self, file_path: str, thumbnail: QPixmap | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self._file_path = file_path
        self.setFixedSize(140, 100)
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setFixedSize(132, 72)
        self._image_label.setStyleSheet("background-color: #1e1e1e; border-radius: 2px;")

        if thumbnail is not None:
            self._image_label.setPixmap(
                thumbnail.scaled(132, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self._image_label.setText("No Preview")

        layout.addWidget(self._image_label)

        name = file_path.split("/")[-1].split("\\")[-1]
        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-size: 10px; color: #aaa;")
        name_label.setFixedWidth(132)
        layout.addWidget(name_label)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self._file_path)
        super().mousePressEvent(event)


class ThumbnailView(QScrollArea):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._grid_layout = QGridLayout(self._container)
        self._grid_layout.setSpacing(8)
        self._grid_layout.setContentsMargins(8, 8, 8, 8)

        self.setWidget(self._container)

    def add_thumbnail(self, file_path: str, thumbnail: QPixmap | None = None) -> None:
        item = ThumbnailItem(file_path, thumbnail)
        count = self._grid_layout.count()
        row = count // 4
        col = count % 4
        self._grid_layout.addWidget(item, row, col)

    def clear(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
