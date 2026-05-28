import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.video_cut")

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

DETECTOR_OPTIONS = {
    "ContentDetector": "内容变化检测（推荐）",
    "AdaptiveDetector": "自适应阈值检测",
    "ThresholdDetector": "黑屏淡入淡出检测",
}


def _scan_videos(folder: str) -> List[str]:
    videos = []
    try:
        for root, dirs, files in os.walk(folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    videos.append(os.path.join(root, f))
    except Exception as e:
        logger.error("扫描视频文件夹失败 %s: %s", folder, e)
    return sorted(videos)


def _get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                return float(stdout)
    except Exception:
        pass
    return 0.0


class VideoCutWorker(QThread):
    video_progress = Signal(str, float)
    video_finished = Signal(str, bool)
    all_finished = Signal()

    def __init__(self, video_paths: List[str], output_dir: str, detector_name: str,
                 threshold: float = 27.0, parent=None):
        super().__init__(parent)
        self._video_paths = video_paths
        self._output_dir = output_dir
        self._detector_name = detector_name
        self._threshold = threshold
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def run(self):
        for video_path in self._video_paths:
            if self._canceled:
                break

            video_name = Path(video_path).stem
            self.video_progress.emit(video_name, 0.0)

            try:
                self._process_single_video(video_path)
                self.video_finished.emit(video_name, True)
            except Exception as e:
                logger.error("切割失败 %s: %s", video_path, e)
                self.video_finished.emit(video_name, False)

        self.all_finished.emit()

    def _process_single_video(self, video_path: str) -> None:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector

        video_name = Path(video_path).stem

        self.video_progress.emit(video_name, 0.1)

        video = open_video(video_path)
        scene_manager = SceneManager()

        if self._detector_name == "AdaptiveDetector":
            detector = AdaptiveDetector()
        elif self._detector_name == "ContentDetector":
            detector = ContentDetector(threshold=self._threshold)
        elif self._detector_name == "ThresholdDetector":
            detector = ThresholdDetector(threshold=self._threshold, add_final_scene=False)
        else:
            detector = AdaptiveDetector()

        scene_manager.add_detector(detector)
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list(start_in_scene=True)

        logger.info(
            "%s: %s 检测到 %d 个场景, 阈值=%s",
            video_name, self._detector_name, len(scene_list), self._threshold,
        )

        self.video_progress.emit(video_name, 0.5)

        if not scene_list:
            logger.warning("%s: 未检测到任何场景, 回退为整段视频", video_name)
            total_dur = _get_media_duration(video_path)
            if total_dur > 0:
                scene_list = []
                from scenedetect import FrameTimecode
                tc_start = FrameTimecode(0, video.frame_rate or 30.0)
                tc_end = FrameTimecode(total_dur, video.frame_rate or 30.0)
                scene_list = [(tc_start, tc_end)]

        output_subdir = os.path.join(self._output_dir, "拆分结果", video_name)
        os.makedirs(output_subdir, exist_ok=True)

        total_scenes = len(scene_list)
        scene_count = 0
        for idx, (start_tc, end_tc) in enumerate(scene_list):
            if self._canceled:
                return

            start_sec = start_tc.seconds
            end_sec = end_tc.seconds
            duration = end_sec - start_sec

            if duration < 0.5:
                continue

            scene_count += 1

            ext = Path(video_path).suffix
            out_path = os.path.join(output_subdir, f"{video_name}_part_{idx+1:04d}{ext}")

            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-ss", str(start_sec),
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-map", "0:v:0",
                "-an",
                out_path,
            ]
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, **kwargs,
            )
            _, stderr = proc.communicate()
            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
                raise RuntimeError(f"ffmpeg切割失败: {error_msg}")

            progress = 0.5 + ((idx + 1) / total_scenes) * 0.5
            self.video_progress.emit(video_name, progress)

        logger.info(
            "%s: 切割完成, 共输出 %d 个片段 (原始 %d 个场景)",
            video_name, scene_count, total_scenes,
        )


class _VideoItem(QFrame):
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.setObjectName("cutVideoItem")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        name = os.path.basename(self.video_path)
        self._name_label = QLabel(name)
        self._name_label.setObjectName("cutVideoName")
        self._name_label.setToolTip(self.video_path)
        layout.addWidget(self._name_label, stretch=1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("cutVideoProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedWidth(160)
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("未开始")
        self._status_label.setObjectName("cutVideoStatus")
        layout.addWidget(self._status_label)

    def set_progress(self, value: float) -> None:
        self._progress_bar.show()
        self._progress_bar.setValue(int(value * 100))
        self._status_label.setText("处理中")

    def set_finished(self, success: bool) -> None:
        self._progress_bar.hide()
        if success:
            self._status_label.setText("已完成")
            self._status_label.setObjectName("cutVideoStatusDone")
        else:
            self._status_label.setText("处理失败")
            self._status_label.setObjectName("cutVideoStatusFail")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)


class VideoCutPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("videoCutPage")
        self._video_items: List[_VideoItem] = []
        self._worker: VideoCutWorker | None = None
        self._input_folder = ""
        self._output_folder = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("视频切割")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(12)

        self._input_folder_btn = QPushButton("选择输入文件夹")
        self._input_folder_btn.setObjectName("cutFolderBtn")
        self._input_folder_btn.setCursor(Qt.PointingHandCursor)
        self._input_folder_btn.clicked.connect(self._on_select_input_folder)
        folder_row.addWidget(self._input_folder_btn)

        self._input_folder_label = QLabel("未选择文件夹")
        self._input_folder_label.setObjectName("cutFolderLabel")
        folder_row.addWidget(self._input_folder_label, stretch=1)

        layout.addLayout(folder_row)

        algo_row = QHBoxLayout()
        algo_row.setSpacing(12)

        algo_label = QLabel("分割算法:")
        algo_label.setObjectName("cutAlgoLabel")
        algo_row.addWidget(algo_label)

        self._algo_combo = QComboBox()
        self._algo_combo.setObjectName("cutAlgoCombo")
        self._algo_combo.setMinimumWidth(200)
        for key, display in DETECTOR_OPTIONS.items():
            self._algo_combo.addItem(display, key)
        self._algo_combo.currentIndexChanged.connect(self._on_algo_changed)
        algo_row.addWidget(self._algo_combo)

        self._threshold_label = QLabel("阈值:")
        self._threshold_label.setObjectName("cutAlgoLabel")
        algo_row.addWidget(self._threshold_label)

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setObjectName("cutThresholdSpin")
        self._threshold_spin.setRange(1.0, 255.0)
        self._threshold_spin.setSingleStep(1.0)
        self._threshold_spin.setDecimals(0)
        self._threshold_spin.setValue(27.0)
        self._threshold_spin.setFixedWidth(80)
        algo_row.addWidget(self._threshold_spin)

        algo_row.addStretch()

        self._output_folder_btn = QPushButton("选择输出目录")
        self._output_folder_btn.setObjectName("cutFolderBtn")
        self._output_folder_btn.setCursor(Qt.PointingHandCursor)
        self._output_folder_btn.clicked.connect(self._on_select_output_folder)
        algo_row.addWidget(self._output_folder_btn)

        self._output_folder_label = QLabel("未选择")
        self._output_folder_label.setObjectName("cutFolderLabel")
        algo_row.addWidget(self._output_folder_label, stretch=1)

        layout.addLayout(algo_row)

        self._video_list_frame = QFrame()
        self._video_list_frame.setObjectName("cutVideoListFrame")
        video_list_layout = QVBoxLayout(self._video_list_frame)
        video_list_layout.setContentsMargins(0, 0, 0, 0)
        video_list_layout.setSpacing(4)

        self._video_list_scroll = QScrollArea()
        self._video_list_scroll.setObjectName("cutVideoScroll")
        self._video_list_scroll.setWidgetResizable(True)
        self._video_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._video_list_content = QWidget()
        self._video_list_content.setObjectName("cutVideoListContent")
        self._video_list_layout = QVBoxLayout(self._video_list_content)
        self._video_list_layout.setContentsMargins(0, 0, 0, 0)
        self._video_list_layout.setSpacing(4)
        self._video_list_layout.setAlignment(Qt.AlignTop)
        self._video_list_scroll.setWidget(self._video_list_content)

        video_list_layout.addWidget(self._video_list_scroll, stretch=1)

        empty_hint = QLabel("请先选择输入文件夹")
        empty_hint.setObjectName("cutEmptyHint")
        empty_hint.setAlignment(Qt.AlignCenter)
        self._video_list_layout.addWidget(empty_hint)

        layout.addWidget(self._video_list_frame, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._video_count_label = QLabel("共 0 个视频")
        self._video_count_label.setObjectName("cutVideoCount")
        btn_row.addWidget(self._video_count_label)

        btn_row.addStretch()

        self._start_btn = QPushButton("开始拆分视频")
        self._start_btn.setObjectName("cutStartBtn")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)

        layout.addLayout(btn_row)

        self._on_algo_changed()

    def _refresh_video_list(self) -> None:
        for i in reversed(range(self._video_list_layout.count())):
            item = self._video_list_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        self._video_items = []
        if not self._input_folder:
            empty_hint = QLabel("请先选择输入文件夹")
            empty_hint.setObjectName("cutEmptyHint")
            empty_hint.setAlignment(Qt.AlignCenter)
            self._video_list_layout.addWidget(empty_hint)
            return

        videos = _scan_videos(self._input_folder)
        if not videos:
            empty_hint = QLabel("该文件夹中没有视频文件")
            empty_hint.setObjectName("cutEmptyHint")
            empty_hint.setAlignment(Qt.AlignCenter)
            self._video_list_layout.addWidget(empty_hint)
        else:
            for vp in videos:
                item = _VideoItem(vp)
                self._video_items.append(item)
                self._video_list_layout.addWidget(item)

        self._video_count_label.setText(f"共 {len(videos)} 个视频")

    def _on_select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            self._input_folder = folder
            self._input_folder_label.setText(folder)
            self._refresh_video_list()
            self._update_start_button()

    def _on_select_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self._output_folder = folder
            self._output_folder_label.setText(folder)
            self._update_start_button()

    def _update_start_button(self) -> None:
        self._start_btn.setEnabled(
            bool(self._input_folder)
            and bool(self._output_folder)
            and bool(self._video_items)
            and (self._worker is None or not self._worker.isRunning())
        )

    def _on_algo_changed(self) -> None:
        detector = self._algo_combo.currentData()
        has_threshold = detector in ("ContentDetector", "ThresholdDetector")
        self._threshold_label.setVisible(has_threshold)
        self._threshold_spin.setVisible(has_threshold)

    def _on_start(self) -> None:
        if self._worker and self._worker.isRunning():
            return

        for item in self._video_items:
            item._progress_bar.hide()
            item._progress_bar.setValue(0)
            item._status_label.setText("未开始")

        video_paths = [item.video_path for item in self._video_items]
        detector_name = self._algo_combo.currentData()
        threshold = self._threshold_spin.value()

        self._worker = VideoCutWorker(
            video_paths, self._output_folder, detector_name, threshold,
        )
        self._worker.video_progress.connect(self._on_video_progress)
        self._worker.video_finished.connect(self._on_video_finished)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.start()

        self._start_btn.setEnabled(False)
        self._start_btn.setText("正在处理...")

    def _on_video_progress(self, video_name: str, progress: float) -> None:
        for item in self._video_items:
            if os.path.basename(item.video_path).startswith(video_name):
                item.set_progress(progress)
                break

    def _on_video_finished(self, video_name: str, success: bool) -> None:
        for item in self._video_items:
            base = os.path.basename(item.video_path)
            name_no_ext = Path(base).stem
            if name_no_ext == video_name:
                item.set_finished(success)
                break

    def _on_all_finished(self) -> None:
        self._start_btn.setEnabled(True)
        self._start_btn.setText("开始拆分视频")
        logger.info("所有视频切割完成")
