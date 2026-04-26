import asyncio
import logging
from pathlib import Path
from typing import List, Tuple

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from clip_synth.models.project_state import SmartClippingProjectState
from clip_synth.services.export_service import ExportService

logger = logging.getLogger("clip_synth.export_page")


class ExportWorker(QThread):
    progress = Signal(float)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        service: ExportService,
        project_name: str,
        segments: List[Tuple[str, str, str]],
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._project_name = project_name
        self._segments = segments

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            output_path = loop.run_until_complete(
                self._service.export_video(
                    self._project_name,
                    self._segments,
                    progress_callback=lambda p: self.progress.emit(p),
                )
            )
            self.finished.emit(str(output_path))
        except Exception as e:
            self.error.emit(str(e))
        finally:
            loop.close()

    def cancel(self):
        self._service.cancel_export()


class ExportPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: SmartClippingProjectState | None = None
        self._export_service: ExportService | None = None
        self._worker: ExportWorker | None = None
        self._output_path: str | None = None
        self.setObjectName("exportPage")
        self._setup_ui()

    def set_project(
        self,
        project: SmartClippingProjectState,
        export_service: ExportService,
    ):
        self._project = project
        self._export_service = export_service
        self._reset_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("导出视频")
        title.setObjectName("exportTitle")
        layout.addWidget(title)

        info = QLabel("将选中的视频片段切割并合并为一个完整的视频文件")
        info.setObjectName("exportInfo")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._export_btn = QPushButton(" 开始导出 ")
        self._export_btn.setObjectName("startExportBtn")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export)
        layout.addWidget(self._export_btn)

        self._progress_container = QFrame()
        self._progress_container.setObjectName("exportProgressContainer")
        progress_layout = QVBoxLayout(self._progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(6)

        self._progress_bar = QFrame()
        self._progress_bar.setObjectName("exportProgressBar")
        self._progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self._progress_bar)

        self._progress_label = QLabel("0%")
        self._progress_label.setObjectName("exportProgressLabel")
        progress_layout.addWidget(self._progress_label)

        layout.addWidget(self._progress_container)
        self._progress_container.hide()

        self._result_area = QFrame()
        self._result_area.setObjectName("exportResultArea")
        result_layout = QVBoxLayout(self._result_area)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(12)

        success_label = QLabel("✅ 视频导出完成")
        success_label.setObjectName("exportSuccessLabel")
        result_layout.addWidget(success_label)

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)

        self._video_widget = QVideoWidget()
        self._video_widget.setObjectName("exportVideoWidget")
        self._video_widget.setMinimumHeight(300)
        result_layout.addWidget(self._video_widget)

        self._player.setVideoOutput(self._video_widget)

        controls = QFrame()
        controls.setObjectName("exportPlayerControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("exportPlayBtn")
        self._play_btn.setCursor(Qt.PointingHandCursor)
        self._play_btn.setFixedSize(36, 36)
        self._play_btn.clicked.connect(self._on_toggle_play)
        controls_layout.addWidget(self._play_btn)

        self._position_slider = QSlider(Qt.Horizontal)
        self._position_slider.setObjectName("exportPositionSlider")
        self._position_slider.setRange(0, 0)
        self._position_slider.sliderMoved.connect(self._player.setPosition)
        controls_layout.addWidget(self._position_slider)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setObjectName("exportTimeLabel")
        controls_layout.addWidget(self._time_label)

        result_layout.addWidget(controls)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._open_folder_btn = QPushButton(" 打开文件夹 ")
        self._open_folder_btn.setObjectName("openFolderBtn")
        self._open_folder_btn.setCursor(Qt.PointingHandCursor)
        self._open_folder_btn.clicked.connect(self._on_open_folder)
        btn_row.addWidget(self._open_folder_btn)

        self._re_export_btn = QPushButton(" 重新导出 ")
        self._re_export_btn.setObjectName("reExportBtn")
        self._re_export_btn.setCursor(Qt.PointingHandCursor)
        self._re_export_btn.clicked.connect(self._on_re_export)
        btn_row.addWidget(self._re_export_btn)

        result_layout.addLayout(btn_row)

        layout.addWidget(self._result_area, stretch=1)
        self._result_area.hide()

        layout.addStretch()

        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)

    def _reset_ui(self):
        self._output_path = None
        self._export_btn.show()
        self._progress_container.hide()
        self._result_area.hide()
        self._export_btn.setEnabled(True)
        self._export_btn.setText(" 开始导出 ")

    def _collect_segments(self) -> List[Tuple[str, str, str]]:
        if not self._project:
            return []

        segments: List[Tuple[str, str, str]] = []

        if self._project.clipping_mode == "ai":
            selected_ids = {
                r["seg_id"] for r in self._project.ai_analysis_results
            }
            for video_state in self._project.videos:
                for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                    for seg in video_state.segments.get(seg_type, []):
                        if seg.id in selected_ids:
                            segments.append(
                                (video_state.video_path, seg.start_time, seg.end_time)
                            )
        else:
            for video_state in self._project.videos:
                for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                    for seg in video_state.segments.get(seg_type, []):
                        if seg.selected:
                            segments.append(
                                (video_state.video_path, seg.start_time, seg.end_time)
                            )

        segments.sort(key=lambda s: (s[0], s[1]))
        return segments

    def _on_export(self):
        if not self._export_service:
            return

        segments = self._collect_segments()
        if not segments:
            logger.warning("没有选中的片段")
            return

        self._export_btn.setEnabled(False)
        self._export_btn.setText("导出中...")
        self._progress_container.show()
        self._progress_bar.setFixedWidth(0)
        self._progress_label.setText("0%")
        self._result_area.hide()

        project_name = self._project.name if self._project else "未命名"
        self._worker = ExportWorker(self._export_service, project_name, segments)
        self._worker.progress.connect(self._on_export_progress)
        self._worker.finished.connect(self._on_export_finished)
        self._worker.error.connect(self._on_export_error)
        self._worker.start()

    def _on_export_progress(self, pct: float):
        parent = self._progress_container.parent()
        if parent:
            max_w = parent.width() - 64
            if max_w > 50:
                w = int(max_w * pct / 100.0)
                self._progress_bar.setFixedWidth(w)
        self._progress_label.setText(f"{int(pct)}%")

    def _on_export_finished(self, output_path: str):
        self._output_path = output_path
        self._export_btn.hide()
        self._progress_container.hide()
        self._result_area.show()

        self._player.setSource(QUrl.fromLocalFile(output_path))
        self._play_btn.setText("▶")

    def _on_export_error(self, error_msg: str):
        logger.error("导出失败: %s", error_msg)
        self._export_btn.setEnabled(True)
        self._export_btn.setText(" 开始导出 ")
        self._progress_container.hide()

    def _on_open_folder(self):
        if self._output_path:
            folder = str(Path(self._output_path).parent)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_re_export(self):
        self._player.stop()
        self._reset_ui()

    def _on_toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_position_changed(self, pos: int):
        if not self._position_slider.isSliderDown():
            self._position_slider.setValue(pos)
        total = self._player.duration()
        current = self._format_ms(pos)
        total_str = self._format_ms(total)
        self._time_label.setText(f"{current} / {total_str}")

    def _on_duration_changed(self, duration: int):
        self._position_slider.setRange(0, duration)

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._play_btn.setText("⏸")
        else:
            self._play_btn.setText("▶")

    @staticmethod
    def _format_ms(ms: int) -> str:
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"
