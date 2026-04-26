from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class TimelineTrack(QWidget):
    def __init__(self, track_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._track_name = track_name
        self.setMinimumHeight(60)
        self.setMaximumHeight(80)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor("#2d2d2d"))

        painter.setPen(QPen(QColor("#555"), 1))
        painter.drawLine(0, 0, self.width(), 0)

        painter.setPen(QPen(QColor("#aaa"), 1))
        painter.drawText(8, 24, self._track_name)


class TimelinePage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_label = QLabel("Timeline")
        header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedSize(28, 28)
        header_layout.addWidget(self._zoom_in_btn)

        self._zoom_out_btn = QPushButton("-")
        self._zoom_out_btn.setFixedSize(28, 28)
        header_layout.addWidget(self._zoom_out_btn)

        layout.addLayout(header_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        tracks_widget = QWidget()
        tracks_layout = QVBoxLayout(tracks_widget)
        tracks_layout.setContentsMargins(0, 0, 0, 0)
        tracks_layout.setSpacing(1)

        tracks_layout.addWidget(TimelineTrack("Video 1"))
        tracks_layout.addWidget(TimelineTrack("Video 2"))
        tracks_layout.addWidget(TimelineTrack("Audio 1"))
        tracks_layout.addStretch()

        scroll_area.setWidget(tracks_widget)
        layout.addWidget(scroll_area, stretch=1)

        playback_layout = QHBoxLayout()
        self._play_btn = QPushButton("Play")
        playback_layout.addWidget(self._play_btn)

        self._prev_cut_btn = QPushButton("Prev Cut")
        playback_layout.addWidget(self._prev_cut_btn)

        self._next_cut_btn = QPushButton("Next Cut")
        playback_layout.addWidget(self._next_cut_btn)

        playback_layout.addStretch()

        self._current_time_label = QLabel("00:00:00")
        playback_layout.addWidget(self._current_time_label)

        layout.addLayout(playback_layout)
