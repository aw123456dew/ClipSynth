import logging

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.narrate_v2")


class VideoPreviewDialog(QDialog):
    def __init__(self, video_path: str, start_ms: int, end_ms: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("视频预览")
        self.setMinimumSize(800, 500)
        self.setObjectName("videoPreviewDialog")
        self._video_path = video_path
        self._start_ms = start_ms
        self._end_ms = end_ms
        self._is_looping = True
        self._loaded = False
        self._setup_ui()
        self._setup_player()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        info_label = QLabel(f"循环播放片段: {self._start_ms // 1000}.{self._start_ms % 1000:03d}s - {self._end_ms // 1000}.{self._end_ms % 1000:03d}s")
        info_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        layout.addWidget(info_label)

        self._video_widget = QVideoWidget()
        self._video_widget.setObjectName("previewVideoWidget")
        self._video_widget.setMinimumSize(760, 360)
        layout.addWidget(self._video_widget, stretch=1)

        control_row = QHBoxLayout()
        control_row.setSpacing(12)

        self._loop_btn = QPushButton("循环播放 ✓")
        self._loop_btn.setObjectName("previewControlBtn")
        self._loop_btn.setCursor(Qt.PointingHandCursor)
        self._loop_btn.clicked.connect(self._toggle_loop)
        control_row.addWidget(self._loop_btn)

        self._play_btn = QPushButton("暂停")
        self._play_btn.setObjectName("previewControlBtn")
        self._play_btn.setCursor(Qt.PointingHandCursor)
        self._play_btn.clicked.connect(self._toggle_play)
        control_row.addWidget(self._play_btn)

        self._position_slider = QSlider(Qt.Horizontal)
        self._position_slider.setObjectName("previewSlider")
        self._position_slider.setRange(self._start_ms, self._end_ms)
        self._position_slider.sliderMoved.connect(self._seek)
        control_row.addWidget(self._position_slider, stretch=1)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        control_row.addWidget(self._time_label)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("previewControlBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        control_row.addWidget(close_btn)

        layout.addLayout(control_row)

    def _setup_player(self):
        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.setSource(QUrl.fromLocalFile(self._video_path))
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)

    def _toggle_loop(self):
        self._is_looping = not self._is_looping
        self._loop_btn.setText("循环播放 ✓" if self._is_looping else "循环播放 ✗")

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._play_btn.setText("播放")
        else:
            self._player.play()
            self._play_btn.setText("暂停")

    def _seek(self, position: int):
        self._player.setPosition(position)

    def _seek_and_play(self):
        self._player.setPosition(self._start_ms)
        self._player.play()

    def _on_position_changed(self, position: int):
        self._position_slider.setValue(position)
        total_sec = (self._end_ms - self._start_ms) // 1000
        current_sec = (position - self._start_ms) // 1000
        self._time_label.setText(f"{current_sec // 60}:{current_sec % 60:02d} / {total_sec // 60}:{total_sec % 60:02d}")

        if self._is_looping and position >= self._end_ms:
            self._player.setPosition(self._start_ms)

    def _on_media_status_changed(self, status):
        if status == QMediaPlayer.LoadedMedia and not self._loaded:
            self._loaded = True
            self._seek_and_play()
        elif status == QMediaPlayer.EndOfMedia and self._is_looping:
            self._player.setPosition(self._start_ms)
            self._player.play()

    def closeEvent(self, event):
        self._is_looping = False
        try:
            self._player.pause()
            self._player.positionChanged.disconnect()
            self._player.mediaStatusChanged.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._player.setSource(QUrl())
        QTimer.singleShot(50, self._player.deleteLater)
        QTimer.singleShot(50, self._audio_output.deleteLater)
        super().closeEvent(event)
