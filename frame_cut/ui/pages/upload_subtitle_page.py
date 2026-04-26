from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from frame_cut.models import VideoProjectState


class VideoSubtitleItem(QFrame):
    def __init__(self, video_state: VideoProjectState, parent=None):
        super().__init__(parent)
        self._video_state = video_state
        self.setObjectName("videoSubtitleItem")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        main_row = QFrame()
        main_layout = QHBoxLayout(main_row)
        main_layout.setContentsMargins(16, 12, 16, 12)

        video_name = QLabel(Path(self._video_state.video_path).name)
        video_name.setObjectName("videoName")
        main_layout.addWidget(video_name)

        main_layout.addStretch()

        self._subtitle_label = QLabel("未上传字幕")
        self._subtitle_label.setObjectName("subtitleStatus")
        main_layout.addWidget(self._subtitle_label)

        upload_btn = QPushButton("选择字幕")
        upload_btn.setObjectName("uploadSubtitleBtn")
        upload_btn.clicked.connect(self._on_upload_subtitle)
        main_layout.addWidget(upload_btn)

        layout.addWidget(main_row)

        if self._video_state.subtitle_path:
            self._subtitle_label.setText(Path(self._video_state.subtitle_path).name)
            self._subtitle_label.setProperty("uploaded", True)
            self._subtitle_label.style().unpolish(self._subtitle_label)
            self._subtitle_label.style().polish(self._subtitle_label)

    def _on_upload_subtitle(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择字幕文件",
            "",
            "字幕文件 (*.srt);;所有文件 (*.*)",
        )
        if file_path:
            self._video_state.subtitle_path = file_path
            self._subtitle_label.setText(Path(file_path).name)
            self._subtitle_label.setProperty("uploaded", True)
            self._subtitle_label.style().unpolish(self._subtitle_label)
            self._subtitle_label.style().polish(self._subtitle_label)

    @property
    def subtitle_path(self):
        return self._video_state.subtitle_path


class UploadSubtitlePage(QFrame):
    def __init__(self, video_states: list[VideoProjectState], parent=None):
        super().__init__(parent)
        self._video_states = video_states
        self.setObjectName("uploadSubtitlePage")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("uploadPageHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(40, 40, 40, 24)

        title = QLabel("请为每个视频上传对应的字幕文件")
        title.setObjectName("uploadPageTitle")
        header_layout.addWidget(title)

        desc = QLabel("支持 .srt 格式的字幕文件")
        desc.setObjectName("uploadPageDesc")
        header_layout.addWidget(desc)

        layout.addWidget(header)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("uploadListScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        list_content = QWidget()
        list_content.setObjectName("uploadListContent")
        self._list_layout = QVBoxLayout(list_content)
        self._list_layout.setContentsMargins(24, 16, 24, 24)
        self._list_layout.setSpacing(12)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._subtitle_items = []
        for video_state in self._video_states:
            item = VideoSubtitleItem(video_state)
            self._subtitle_items.append(item)
            self._list_layout.addWidget(item)

        scroll_area.setWidget(list_content)
        layout.addWidget(scroll_area, stretch=1)

    def get_subtitles(self):
        subtitles = {}
        for item in self._subtitle_items:
            subtitles[item._video_state.video_path] = item.subtitle_path
        return subtitles
