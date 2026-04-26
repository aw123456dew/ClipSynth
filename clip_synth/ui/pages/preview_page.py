from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class PreviewPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        preview_label = QLabel("Preview")
        preview_label.setAlignment(Qt.AlignCenter)
        preview_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(preview_label)

        self._video_display = QLabel()
        self._video_display.setAlignment(Qt.AlignCenter)
        self._video_display.setMinimumSize(480, 320)
        self._video_display.setStyleSheet(
            "background-color: #1e1e1e; border: 1px solid #333; border-radius: 4px;"
        )
        self._video_display.setText("No media loaded")
        layout.addWidget(self._video_display, stretch=1)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(4)

        self._play_btn = QPushButton("Play")
        self._play_btn.setEnabled(False)
        controls_layout.addWidget(self._play_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        controls_layout.addWidget(self._stop_btn)

        controls_layout.addStretch()

        self._time_label = QLabel("00:00 / 00:00")
        controls_layout.addWidget(self._time_label)

        layout.addLayout(controls_layout)

        self._timeline_slider = QSlider(Qt.Horizontal)
        self._timeline_slider.setEnabled(False)
        self._timeline_slider.setRange(0, 100)
        layout.addWidget(self._timeline_slider)

    def display_frame(self, pixmap: QPixmap) -> None:
        self._video_display.setPixmap(
            pixmap.scaled(
                self._video_display.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def clear_display(self) -> None:
        self._video_display.clear()
        self._video_display.setText("No media loaded")
