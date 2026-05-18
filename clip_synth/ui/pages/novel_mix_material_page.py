import logging
import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from clip_synth.models.novel_mix_project_state import (
    NovelMixMaterialVideo,
    NovelMixProjectState,
)

logger = logging.getLogger("clip_synth.novel_mix_material")

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}


def _get_video_info(video_path: str) -> dict:
    info = {"duration": 0.0, "size_mb": 0.0}
    try:
        file_size = os.path.getsize(video_path)
        info["size_mb"] = round(file_size / (1024 * 1024), 2)
    except OSError:
        pass

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path,
    ]
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **kwargs)
        if result.returncode == 0:
            info["duration"] = float(result.stdout.strip())
    except Exception:
        pass

    return info


class NovelMixMaterialPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("novelMixMaterialPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("选择素材文件夹")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        desc = QLabel("选择包含视频素材的文件夹，系统将从中随机选取片段拼接")
        desc.setObjectName("exportInfo")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(12)

        self._folder_label = QLabel("当前文件夹: 未选择")
        self._folder_label.setObjectName("materialFolderLabel")
        self._folder_label.setWordWrap(True)
        folder_row.addWidget(self._folder_label, stretch=1)

        self._select_btn = QPushButton("选择文件夹")
        self._select_btn.setObjectName("materialSelectBtn")
        self._select_btn.setCursor(Qt.PointingHandCursor)
        self._select_btn.clicked.connect(self._on_select_folder)
        folder_row.addWidget(self._select_btn)

        self._refresh_btn = QPushButton("刷新列表")
        self._refresh_btn.setObjectName("materialRefreshBtn")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._on_refresh)
        self._refresh_btn.setEnabled(False)
        folder_row.addWidget(self._refresh_btn)

        layout.addLayout(folder_row)

        self._count_label = QLabel("共扫描到 0 个视频文件")
        self._count_label.setObjectName("materialCountLabel")
        layout.addWidget(self._count_label)

        self._table = QTableWidget()
        self._table.setObjectName("materialTable")
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["序号", "视频名称", "时长", "文件大小"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 60)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 100)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._table.setColumnWidth(3, 100)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, stretch=1)

        self._current_folder = ""
        self._videos: list[NovelMixMaterialVideo] = []

    def _on_select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择素材文件夹")
        if not folder:
            return
        self._current_folder = folder
        self._scan_folder()

    def _on_refresh(self) -> None:
        if self._current_folder:
            self._scan_folder()

    def _scan_folder(self) -> None:
        self._folder_label.setText(f"当前文件夹: {self._current_folder}")
        self._refresh_btn.setEnabled(True)

        videos = []
        try:
            for root, dirs, files in os.walk(self._current_folder):
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in VIDEO_EXTENSIONS:
                        full_path = os.path.join(root, f)
                        info = _get_video_info(full_path)
                        duration_str = _format_duration(info["duration"])
                        videos.append(
                            NovelMixMaterialVideo(
                                name=f,
                                path=full_path,
                                duration=info["duration"],
                                size_mb=info["size_mb"],
                            )
                        )
        except Exception as e:
            logger.error("扫描文件夹失败: %s", e)

        videos.sort(key=lambda v: v.name.lower())
        self._videos = videos
        self._populate_table()

    def _populate_table(self) -> None:
        self._table.setRowCount(len(self._videos))
        self._count_label.setText(f"共扫描到 {len(self._videos)} 个视频文件")

        for i, video in enumerate(self._videos):
            self._table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self._table.setItem(i, 1, QTableWidgetItem(video.name))
            duration_str = _format_duration(video.duration)
            self._table.setItem(i, 2, QTableWidgetItem(duration_str))
            self._table.setItem(i, 3, QTableWidgetItem(f"{video.size_mb} MB"))

    def save(self, project: NovelMixProjectState) -> None:
        project.material_folder = self._current_folder
        project.material_videos = self._videos

    def restore(self, project: NovelMixProjectState) -> None:
        self._current_folder = project.material_folder
        self._videos = project.material_videos
        if self._current_folder:
            self._folder_label.setText(f"当前文件夹: {self._current_folder}")
            self._refresh_btn.setEnabled(True)
            self._populate_table()


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
