import asyncio
import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.project_state import SmartClippingProjectState, VideoSegment
from clip_synth.models.project_state import AnalysisMode
from clip_synth.services.project_state_service import ProjectStateService
from clip_synth.services.video_analysis_service import VideoAnalysisService

logger = logging.getLogger(__name__)


class AnalysisWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        video_path: str,
        subtitle_path: str | None,
        service: VideoAnalysisService,
        mode: AnalysisMode = AnalysisMode.PRECISE,
    ):
        super().__init__()
        self._video_path = video_path
        self._subtitle_path = subtitle_path
        self._service = service
        self._mode = mode

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                segments = loop.run_until_complete(
                    self._service.analyze_video(
                        self._video_path,
                        self._subtitle_path,
                        mode=self._mode,
                        progress_callback=self._on_progress,
                    )
                )
                self.finished.emit(segments)
            finally:
                try:
                    loop.run_until_complete(self._service.close())
                except Exception:
                    pass
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, pct: int, message: str):
        self.progress.emit(pct, message)


class SegmentItem(QFrame):
    play_requested = Signal(str, str)

    def __init__(self, segment: VideoSegment, parent=None):
        super().__init__(parent)
        self._segment = segment
        self.setObjectName("segmentItem")
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top_layout = QHBoxLayout()

        time_label = QLabel(f"{self._segment.start_time} - {self._segment.end_time}")
        time_label.setObjectName("segmentTimeLabel")
        top_layout.addWidget(time_label)

        top_layout.addStretch()
        layout.addLayout(top_layout)

        desc_label = QLabel(self._segment.description)
        desc_label.setObjectName("segmentDescLabel")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

    def mousePressEvent(self, event):  # noqa: N802
        self.play_requested.emit(self._segment.start_time, self._segment.end_time)
        super().mousePressEvent(event)

    @property
    def segment(self) -> VideoSegment:
        return self._segment


class SegmentGroup(QFrame):
    toggled = Signal(bool)
    play_requested = Signal(str, str)

    def __init__(self, segment_type: str, segments: list[VideoSegment], parent=None):
        super().__init__(parent)
        self._type = segment_type
        self._segments = segments
        self._expanded = False
        self.setObjectName("segmentGroup")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QFrame()
        self._header.setObjectName("segmentGroupHeader")
        self._header.setMinimumHeight(44)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.mousePressEvent = lambda event: self._on_toggle()  # noqa: ARG005
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 10, 12, 10)

        self._toggle_icon = QLabel("▶")
        self._toggle_icon.setObjectName("toggleIcon")
        header_layout.addWidget(self._toggle_icon)

        type_label = QLabel(self._get_type_name())
        type_label.setObjectName("groupTypeLabel")
        header_layout.addWidget(type_label)

        count_label = QLabel(f"({len(self._segments)}个)")
        count_label.setObjectName("groupCountLabel")
        header_layout.addWidget(count_label)

        header_layout.addStretch()
        layout.addWidget(self._header)

        self._content = QFrame()
        self._content.setObjectName("segmentGroupContent")
        self._content.setVisible(False)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        self._segment_items = []
        for seg in self._segments:
            item = SegmentItem(seg)
            item.play_requested.connect(self.play_requested.emit)
            content_layout.addWidget(item)
            self._segment_items.append(item)

        layout.addWidget(self._content)

    def _get_type_name(self):
        type_names = {
            "gold_3s": "AI黄金3秒",
            "highlight": "AI亮点解析",
            "plot": "AI剧情解析",
            "ending": "AI结尾悬念",
        }
        return type_names.get(self._type, self._type)

    def _on_toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._toggle_icon.setText("▼" if self._expanded else "▶")
        self.toggled.emit(self._expanded)


class VideoPlayerWidget(QFrame):
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self._video_path = video_path
        self._is_playing = False
        self._duration = 0.0
        self._position = 0.0
        self._is_seeking = False
        self._loop_start_ms = -1
        self._loop_end_ms = -1
        self._is_looping = False
        self.setObjectName("videoPlayerWidget")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._player_container = QFrame()
        self._player_container.setObjectName("playerContainer")
        player_layout = QVBoxLayout(self._player_container)
        player_layout.setContentsMargins(0, 0, 0, 0)

        self._video_widget = QVideoWidget()
        self._video_widget.setObjectName("videoWidget")
        player_layout.addWidget(self._video_widget)

        layout.addWidget(self._player_container, stretch=1)

        self._media_player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setVideoOutput(self._video_widget)
        self._media_player.setSource(self._video_path)
        self._media_player.durationChanged.connect(self._on_duration_changed)
        self._media_player.positionChanged.connect(self._on_position_changed)
        self._media_player.playbackStateChanged.connect(self._on_state_changed)

        controls = QFrame()
        controls.setObjectName("playerControls")
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(16, 8, 16, 8)

        self._play_btn = QPushButton("播放")
        self._play_btn.setObjectName("playerPlayBtn")
        self._play_btn.clicked.connect(self._on_play)
        control_layout.addWidget(self._play_btn)

        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setObjectName("playerProgressSlider")
        self._progress_slider.setRange(0, 1000)
        self._progress_slider.setValue(0)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderMoved.connect(self._on_seek)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        control_layout.addWidget(self._progress_slider, stretch=1)

        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setObjectName("playerTimeLabel")
        control_layout.addWidget(self._time_label)

        layout.addWidget(controls)

    def _on_duration_changed(self, duration: int):
        self._duration = duration / 1000.0
        self._update_time_display()

    def _on_position_changed(self, position: int):
        if not self._is_seeking:
            self._position = position / 1000.0
            if self._duration > 0:
                progress = int(position / (self._duration * 1000) * 1000)
                self._progress_slider.setValue(progress)
            self._update_time_display()

        if self._is_looping and self._loop_end_ms > 0 and position >= self._loop_end_ms:
            self._media_player.setPosition(self._loop_start_ms)

    def _on_state_changed(self, state):
        self._is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_btn.setText("暂停" if self._is_playing else "播放")

    def _on_play(self):
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._media_player.pause()
        else:
            self._media_player.play()

    def _on_slider_pressed(self):
        self._is_seeking = True

    def _on_slider_released(self):
        self._is_seeking = False

    def _on_seek(self, value):
        if self._duration > 0:
            position = int(value / 1000.0 * self._duration * 1000)
            self._media_player.setPosition(position)

    def _update_time_display(self):
        current_str = self._format_time(self._position)
        total_str = self._format_time(self._duration)
        self._time_label.setText(f"{current_str} / {total_str}")

    def _format_time(self, seconds: float) -> str:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"

    def play_segment(self, start_time: str, end_time: str):
        self._loop_start_ms = int(self._parse_time(start_time) * 1000)
        self._loop_end_ms = int(self._parse_time(end_time) * 1000)
        self._is_looping = True
        self._media_player.setPosition(self._loop_start_ms)
        self._media_player.play()

    def stop_loop(self):
        self._is_looping = False
        self._loop_start_ms = -1
        self._loop_end_ms = -1

    def _parse_time(self, time_str: str) -> float:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return 0.0

    def stop(self):
        self._media_player.stop()


class AiAnalysisPage(QFrame):
    ready_for_next = Signal(bool)

    def __init__(
        self,
        project: SmartClippingProjectState,
        analysis_service: VideoAnalysisService,
        project_state_service: ProjectStateService,
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._analysis_service = analysis_service
        self._project_state_service = project_state_service
        self._current_video_idx = 0
        self._worker: AnalysisWorker | None = None
        self.setObjectName("aiAnalysisPage")
        self._setup_ui()
        self._load_current_video()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left_panel = QFrame()
        left_panel.setObjectName("leftPanel")
        left_panel.setFixedWidth(320)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        video_list_header = QLabel("待处理视频")
        video_list_header.setObjectName("videoListHeader")
        video_list_header.setContentsMargins(16, 12, 16, 8)
        left_layout.addWidget(video_list_header)

        video_list_scroll = QScrollArea()
        video_list_scroll.setObjectName("videoListScroll")
        video_list_scroll.setWidgetResizable(True)
        video_list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        video_list_content = QWidget()
        video_list_content.setObjectName("videoListContent")
        self._video_list_layout = QVBoxLayout(video_list_content)
        self._video_list_layout.setContentsMargins(12, 8, 12, 12)
        self._video_list_layout.setSpacing(8)
        self._video_list_layout.setAlignment(Qt.AlignTop)

        self._video_btns = []
        for idx, video_state in enumerate(self._project.videos):
            btn = QPushButton(video_state.video_path.split("/")[-1])
            btn.setObjectName("videoListItem")
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            btn.clicked.connect(lambda checked, i=idx: self._switch_video(i))
            self._video_btns.append(btn)
            self._video_list_layout.addWidget(btn)

        video_list_scroll.setWidget(video_list_content)
        left_layout.addWidget(video_list_scroll, stretch=1)
        layout.addWidget(left_panel)

        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._stack = QStackedWidget()

        self._loading_widget = QFrame()
        self._loading_widget.setObjectName("loadingWidget")
        loading_layout = QVBoxLayout(self._loading_widget)
        loading_layout.setAlignment(Qt.AlignCenter)
        loading_layout.setSpacing(16)

        loading_label = QLabel("AI视频分析")
        loading_label.setObjectName("loadingLabel")
        loading_layout.addWidget(loading_label)

        loading_desc = QLabel("选择分析模式后开始AI分析，将自动识别视频中的精彩片段")
        loading_desc.setObjectName("loadingDesc")
        loading_desc.setAlignment(Qt.AlignCenter)
        loading_desc.setWordWrap(True)
        loading_layout.addWidget(loading_desc)

        mode_row = QFrame()
        mode_row.setObjectName("modeSelectorRow")
        mode_row_layout = QHBoxLayout(mode_row)
        mode_row_layout.setContentsMargins(0, 0, 0, 0)
        mode_row_layout.setSpacing(12)
        mode_row_layout.setAlignment(Qt.AlignCenter)

        mode_label = QLabel("分析模式:")
        mode_label.setObjectName("modeSelectorLabel")
        mode_row_layout.addWidget(mode_label)

        self._mode_combo = QComboBox()
        self._mode_combo.setObjectName("modeSelectorCombo")
        self._mode_combo.setMinimumWidth(180)
        for m in AnalysisMode:
            self._mode_combo.addItem(m.display_name, m.value)
        self._mode_combo.setCurrentIndex(1)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row_layout.addWidget(self._mode_combo)

        loading_layout.addWidget(mode_row)

        self._mode_desc_label = QLabel("")
        self._mode_desc_label.setObjectName("modeDescLabel")
        self._mode_desc_label.setAlignment(Qt.AlignCenter)
        self._mode_desc_label.setWordWrap(True)
        loading_layout.addWidget(self._mode_desc_label, alignment=Qt.AlignCenter)

        self._update_mode_desc()

        self._start_analysis_btn = QPushButton("开始分析")
        self._start_analysis_btn.setObjectName("startAnalysisBtn")
        self._start_analysis_btn.setMinimumWidth(200)
        self._start_analysis_btn.clicked.connect(self._start_analysis)
        loading_layout.addWidget(self._start_analysis_btn, alignment=Qt.AlignCenter)

        self._progress_container = QFrame()
        self._progress_container.setObjectName("analysisProgressContainer")
        self._progress_container.setVisible(False)
        progress_container_layout = QVBoxLayout(self._progress_container)
        progress_container_layout.setContentsMargins(0, 0, 0, 0)
        progress_container_layout.setSpacing(8)

        self._progress_bar_bg = QFrame()
        self._progress_bar_bg.setObjectName("analysisProgressBg")
        self._progress_bar_bg.setFixedHeight(6)
        bar_layout = QVBoxLayout(self._progress_bar_bg)
        bar_layout.setContentsMargins(0, 0, 0, 0)

        self._progress_bar_fill = QFrame()
        self._progress_bar_fill.setObjectName("analysisProgressFill")
        self._progress_bar_fill.setFixedHeight(6)
        self._progress_bar_fill.setFixedWidth(0)
        bar_layout.addWidget(self._progress_bar_fill)

        progress_container_layout.addWidget(self._progress_bar_bg)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("analysisProgressLabel")
        self._progress_label.setAlignment(Qt.AlignCenter)
        progress_container_layout.addWidget(self._progress_label)

        loading_layout.addWidget(self._progress_container, alignment=Qt.AlignCenter)

        self._stack.addWidget(self._loading_widget)

        self._content_widget = QFrame()
        self._content_widget.setObjectName("analysisContentWidget")
        content_layout = QHBoxLayout(self._content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._video_player_container = QFrame()
        self._video_player_container.setObjectName("videoPlayerContainer")
        player_container_layout = QVBoxLayout(self._video_player_container)
        player_container_layout.setContentsMargins(0, 0, 0, 0)
        self._video_player = None
        content_layout.addWidget(self._video_player_container, stretch=1)

        results_container = QFrame()
        results_container.setObjectName("resultsContainer")
        results_container.setFixedWidth(420)
        results_layout = QVBoxLayout(results_container)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)

        results_scroll = QScrollArea()
        results_scroll.setObjectName("resultsScroll")
        results_scroll.setWidgetResizable(True)
        results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._results_content = QWidget()
        self._results_content.setObjectName("resultsContent")
        self._results_layout = QVBoxLayout(self._results_content)
        self._results_layout.setContentsMargins(12, 8, 12, 12)
        self._results_layout.setSpacing(12)
        self._results_layout.setAlignment(Qt.AlignTop)

        results_scroll.setWidget(self._results_content)
        results_layout.addWidget(results_scroll, stretch=1)

        content_layout.addWidget(results_container)
        self._stack.addWidget(self._content_widget)
        right_layout.addWidget(self._stack, stretch=1)

        layout.addWidget(right_panel, stretch=1)

    def _load_current_video(self):
        video_state = self._project.videos[self._current_video_idx]

        saved_mode = video_state.analysis_mode
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == saved_mode:
                self._mode_combo.setCurrentIndex(i)
                break

        if video_state.analysis_completed:
            self._display_segments()
            self._check_ready()
        else:
            self._stack.setCurrentIndex(0)

    def _update_mode_desc(self):
        mode_value = self._mode_combo.currentData()
        mode = AnalysisMode(mode_value)
        self._mode_desc_label.setText(mode.description)

    def _on_mode_changed(self):
        self._update_mode_desc()

    def _start_analysis(self):
        self._start_analysis_btn.setEnabled(False)
        self._start_analysis_btn.setText("分析中...")
        self._start_analysis_btn.setVisible(False)
        self._progress_container.setVisible(True)
        self._progress_label.setText("准备中...")
        self._update_progress_bar(0)
        self._mode_combo.setEnabled(False)
        video_state = self._project.videos[self._current_video_idx]
        video_path = video_state.video_path
        subtitle_path = video_state.subtitle_path

        mode_value = self._mode_combo.currentData()
        mode = AnalysisMode(mode_value)
        video_state.analysis_mode = mode_value
        self._project_state_service.save_project(self._project)

        logger.info(
            "开始分析视频: %s, 字幕: %s, 模式: %s",
            video_path,
            subtitle_path,
            mode.display_name,
        )

        self._worker = AnalysisWorker(video_path, subtitle_path, self._analysis_service, mode)
        self._worker.finished.connect(self._on_analysis_finished)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.progress.connect(self._on_analysis_progress)
        self._worker.start()

    def _on_analysis_finished(self, segments: list[VideoSegment]):
        video_state = self._project.videos[self._current_video_idx]

        video_state.segments = {
            "gold_3s": [],
            "highlight": [],
            "plot": [],
            "ending": [],
        }
        for seg in segments:
            if seg.type not in video_state.segments:
                video_state.segments[seg.type] = []
            video_state.segments[seg.type].append(seg)

        video_state.analysis_completed = True
        self._project_state_service.save_project(self._project)
        self._progress_container.setVisible(False)
        self._display_segments()
        self._check_ready()

    def _on_analysis_progress(self, pct: int, message: str):
        self._progress_label.setText(message)
        self._update_progress_bar(pct)

    def _update_progress_bar(self, pct: int):
        pct = max(0, min(100, pct))
        bar_width = int(360 * pct / 100)
        self._progress_bar_fill.setFixedWidth(bar_width)

    def _on_analysis_error(self, error: str):
        logger.error("分析失败: %s", error)
        self._progress_container.setVisible(False)
        self._start_analysis_btn.setVisible(True)
        self._start_analysis_btn.setEnabled(True)
        self._start_analysis_btn.setText("重新分析")
        self._mode_combo.setEnabled(True)
        self._progress_label.setText("")

    def _display_segments(self):
        self._stack.setCurrentIndex(1)

        video_state = self._project.videos[self._current_video_idx]

        if self._video_player is not None:
            self._video_player.deleteLater()
        self._video_player = VideoPlayerWidget(video_state.video_path)
        self._video_player_container.layout().addWidget(self._video_player)

        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            if item and item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()

        seg_types = ["gold_3s", "highlight", "plot", "ending"]
        for seg_type in seg_types:
            segs = video_state.segments.get(seg_type, [])
            group = SegmentGroup(seg_type, segs)
            group.play_requested.connect(self._on_play_segment)
            self._results_layout.addWidget(group)

        self._results_layout.addSpacing(8)

        self._re_analyze_btn = QPushButton("重新分析")
        self._re_analyze_btn.setObjectName("reAnalyzeBtn")
        self._re_analyze_btn.setMinimumHeight(40)
        self._re_analyze_btn.clicked.connect(self._on_re_analyze)
        self._results_layout.addWidget(self._re_analyze_btn)

    def _switch_video(self, index):
        self._current_video_idx = index
        for idx, btn in enumerate(self._video_btns):
            btn.setChecked(idx == index)
        if self._video_player is not None:
            self._video_player.stop_loop()
        self._load_current_video()

    def _on_play_segment(self, start_time: str, end_time: str):
        if self._video_player is not None:
            self._video_player.play_segment(start_time, end_time)

    def _on_re_analyze(self):
        video_state = self._project.videos[self._current_video_idx]
        video_state.analysis_completed = False
        video_state.segments = {
            "gold_3s": [],
            "highlight": [],
            "plot": [],
            "ending": [],
        }
        self._project_state_service.save_project(self._project)

        saved_mode = video_state.analysis_mode
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == saved_mode:
                self._mode_combo.setCurrentIndex(i)
                break

        self._load_current_video()

    def check_ready(self):
        all_ready = self._project.is_all_videos_ready()
        self.ready_for_next.emit(all_ready)

    def _check_ready(self):
        self.check_ready()
