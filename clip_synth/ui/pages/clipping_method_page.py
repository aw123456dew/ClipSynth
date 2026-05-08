import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.project_state import (
    ClippingStyle,
    SmartClippingProjectState,
    VideoSegment,
)
from clip_synth.services.clipping_analysis_service import ClippingAnalysisService

logger = logging.getLogger("clip_synth.clipping_method")


class ClippingAnalysisWorker(QThread):
    analysis_finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        service: ClippingAnalysisService,
        segments_by_video: Dict,
        style_key: str,
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._segments_by_video = segments_by_video
        self._style_key = style_key

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._service.analyze(self._segments_by_video, self._style_key)
                )
                self.analysis_finished.emit(result)
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
            logger.error("AI剪辑分析失败: %s", str(e))
            self.error.emit(str(e))


class NarrationGenerationWorker(QThread):
    narration_finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        service: ClippingAnalysisService,
        segments_by_video: Dict,
        subtitles_by_video: Dict,
        style_key: str,
        language: str = "zh",
        original_sound_ratio: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._segments_by_video = segments_by_video
        self._subtitles_by_video = subtitles_by_video
        self._style_key = style_key
        self._language = language
        self._original_sound_ratio = original_sound_ratio

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._service.generate_narration(
                        self._segments_by_video,
                        self._subtitles_by_video,
                        self._style_key,
                        self._language,
                        self._original_sound_ratio,
                    )
                )
                self.narration_finished.emit(result)
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
            logger.error("AI解说文案生成失败: %s", str(e))
            self.error.emit(str(e))


class SegmentCheckItem(QFrame):
    toggled = Signal(bool)

    def __init__(self, segment: VideoSegment, parent=None):
        super().__init__(parent)
        self._segment = segment
        self.setObjectName("segmentCheckItem")
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(16)
        self.setMinimumHeight(56)

        self._checkbox = QPushButton()
        self._checkbox.setObjectName("segmentCheckbox")
        self._checkbox.setCheckable(True)
        self._checkbox.setChecked(self._segment.selected)
        self._checkbox.clicked.connect(self._on_toggle)
        self._checkbox.setFixedSize(22, 22)
        layout.addWidget(self._checkbox, alignment=Qt.AlignCenter)

        time_label = QLabel(f"{self._segment.start_time} - {self._segment.end_time}")
        time_label.setObjectName("checkItemTime")
        time_label.setMinimumHeight(24)
        layout.addWidget(time_label, alignment=Qt.AlignCenter)

        desc_label = QLabel(self._segment.description)
        desc_label.setObjectName("checkItemDesc")
        desc_label.setWordWrap(True)
        desc_label.setMinimumHeight(24)
        layout.addWidget(desc_label, stretch=1)

    def mousePressEvent(self, event):  # noqa: N802
        self._checkbox.click()

    def _on_toggle(self, checked):
        self._segment.selected = checked
        self.toggled.emit(checked)

    @property
    def segment(self) -> VideoSegment:
        return self._segment


class TypeSegmentGroup(QFrame):
    def __init__(self, type_name: str, segments: List[VideoSegment], parent=None):
        super().__init__(parent)
        self._type_name = type_name
        self._segments = segments
        self.setObjectName("typeSegmentGroup")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("typeGroupHeader")
        header.setMinimumHeight(40)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)

        type_label = QLabel(self._get_type_name())
        type_label.setObjectName("typeGroupLabel")
        header_layout.addWidget(type_label)

        self._count_label = QLabel(f"0/{len(self._segments)}")
        self._count_label.setObjectName("typeGroupCount")
        header_layout.addWidget(self._count_label)

        header_layout.addStretch()
        layout.addWidget(header)

        for seg in self._segments:
            item = SegmentCheckItem(seg)
            item.toggled.connect(self._on_item_toggled)
            layout.addWidget(item)

    def _get_type_name(self):
        names = {
            "gold_3s": "AI黄金3秒",
            "highlight": "AI亮点解析",
            "plot": "AI剧情解析",
            "ending": "AI结尾悬念",
        }
        return names.get(self._type_name, self._type_name)

    def _on_item_toggled(self, _checked):
        selected = sum(1 for seg in self._segments if seg.selected)
        self._count_label.setText(f"{selected}/{len(self._segments)}")

    def has_selection(self) -> bool:
        return any(seg.selected for seg in self._segments)


class AiResultItem(QFrame):
    def __init__(self, segment: VideoSegment, reason: str, parent=None):
        super().__init__(parent)
        self._segment = segment
        self._reason = reason
        self.setObjectName("aiResultItem")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(8)

        type_label = QLabel(self._get_type_badge())
        type_label.setObjectName("aiResultType")
        header.addWidget(type_label)

        time_label = QLabel(f"{self._segment.start_time} - {self._segment.end_time}")
        time_label.setObjectName("aiResultTime")
        header.addWidget(time_label)

        header.addStretch()
        layout.addLayout(header)

        desc_label = QLabel(self._segment.description)
        desc_label.setObjectName("aiResultDesc")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        reason_label = QLabel(f"💡 {self._reason}")
        reason_label.setObjectName("aiResultReason")
        reason_label.setWordWrap(True)
        layout.addWidget(reason_label)

    def _get_type_badge(self):
        badges = {
            "gold_3s": "黄金3秒",
            "highlight": "亮点",
            "plot": "剧情",
            "ending": "结尾",
        }
        return badges.get(self._segment.type, self._segment.type)


class NarrationScriptItem(QFrame):
    def __init__(self, segment: Optional[VideoSegment], narration_result: dict, parent=None):
        super().__init__(parent)
        self._segment = segment
        self._result = narration_result
        self._start_time = narration_result.get("start_time", "")
        self._end_time = narration_result.get("end_time", "")
        self._content_type = narration_result.get("content_type", "narration")
        self._story_summary = narration_result.get("story_summary", "")
        self._narration_script = narration_result.get("narration_script", "")
        self._seg_type = narration_result.get("type", "plot")
        if segment:
            self._start_time = segment.start_time
            self._end_time = segment.end_time
            self._seg_type = segment.type
        self.setObjectName("narrationScriptItem")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)

        type_label = QLabel(self._get_type_badge())
        type_label.setObjectName("narrationType")
        header.addWidget(type_label)

        time_label = QLabel(f"{self._start_time} - {self._end_time}")
        time_label.setObjectName("narrationTime")
        header.addWidget(time_label)

        content_badge = "🔊 原声" if self._content_type == "original_sound" else "🎤 解说"
        content_label = QLabel(content_badge)
        content_label.setObjectName("narrationContentType")
        header.addWidget(content_label)

        header.addStretch()
        layout.addLayout(header)

        if self._story_summary:
            summary_label = QLabel(f"📝 故事梗概：{self._story_summary}")
            summary_label.setObjectName("narrationSummary")
            summary_label.setWordWrap(True)
            layout.addWidget(summary_label)

        if self._content_type == "narration" and self._narration_script:
            script_label = QLabel(f"解说文案：{self._narration_script}")
            script_label.setObjectName("narrationScript")
            script_label.setWordWrap(True)
            layout.addWidget(script_label)

    def _get_type_badge(self):
        badges = {
            "gold_3s": "✨ 黄金3秒",
            "highlight": "🎬 亮点",
            "plot": "📖 剧情",
            "ending": "🎯 结尾",
        }
        return badges.get(self._seg_type, "📌 片段")


class ClippingMethodPage(QFrame):
    ready_for_next = Signal(bool)

    def __init__(
        self,
        project: SmartClippingProjectState,
        clipping_analysis_service: ClippingAnalysisService | None = None,
        project_state_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self._project = project
        self._analysis_service = clipping_analysis_service
        self._project_state_service = project_state_service
        self._type_groups: List[TypeSegmentGroup] = []
        self._narration_results: List[dict] = []
        self._all_segments: Dict[str, VideoSegment] = {}
        self.setObjectName("clippingMethodPage")
        self._setup_ui()
        self._collect_all_segments()
        self._restore_narration_results()
        self._update_ready_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 32, 20)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(16)

        title = QLabel("选择解说风格")
        title.setObjectName("clippingMethodTitle")
        header_row.addWidget(title)

        header_row.addStretch()
        layout.addLayout(header_row)

        self._stack = QFrame()
        self._stack.setObjectName("clippingStack")
        stack_layout = QVBoxLayout(self._stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)

        self._ai_widget = QFrame()
        self._ai_widget.setObjectName("aiWidget")
        self._build_ai_ui()
        stack_layout.addWidget(self._ai_widget, stretch=1)

        layout.addWidget(self._stack, stretch=1)

        self._update_visible()

    def _build_manual_ui(self):
        layout = QVBoxLayout(self._manual_widget)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        self._manual_scroll = QScrollArea()
        self._manual_scroll.setObjectName("manualScrollArea")
        self._manual_scroll.setWidgetResizable(True)
        self._manual_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._manual_scroll_content = QFrame()
        self._manual_scroll_content.setObjectName("manualScrollContent")
        self._manual_scroll_layout = QVBoxLayout(self._manual_scroll_content)
        self._manual_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._manual_scroll_layout.setSpacing(4)

        self._manual_scroll.setWidget(self._manual_scroll_content)
        layout.addWidget(self._manual_scroll, stretch=1)

    def refresh_manual_ui(self):
        self._type_groups.clear()
        while self._manual_scroll_layout.count():
            item = self._manual_scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_any_segment = False
        for video_state in self._project.videos:
            video_name = video_state.video_path.split("/")[-1].split("\\")[-1]
            video_header = QLabel(f"📹 {video_name}")
            video_header.setObjectName("manualVideoHeader")
            self._manual_scroll_layout.addWidget(video_header)

            video_has_segment = False
            for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                segments = video_state.segments.get(seg_type, [])
                if not segments:
                    continue
                video_has_segment = True
                has_any_segment = True
                group = TypeSegmentGroup(seg_type, segments)
                self._type_groups.append(group)
                self._manual_scroll_layout.addWidget(group)

            if not video_has_segment:
                empty_hint = QLabel("暂无片段，请先完成上一步的AI视频分析")
                empty_hint.setObjectName("manualEmptyHint")
                empty_hint.setAlignment(Qt.AlignCenter)
                empty_hint.setWordWrap(True)
                self._manual_scroll_layout.addWidget(empty_hint)

            self._manual_scroll_layout.addSpacing(8)

        self._manual_scroll_layout.addStretch()
        self._update_ready_state()
        self._update_visible()

    def _build_ai_ui(self):
        layout = QVBoxLayout(self._ai_widget)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        self._params_widget = QFrame()
        params_layout = QVBoxLayout(self._params_widget)
        params_layout.setContentsMargins(0, 0, 0, 0)
        params_layout.setSpacing(12)

        style_title = QLabel("解说风格")
        style_title.setObjectName("aiStyleTitle")
        params_layout.addWidget(style_title)

        self._style_combo = QComboBox()
        self._style_combo.setObjectName("styleCombo")
        self._style_combo.addItem("💗 情感共鸣", "emotional")
        self._style_combo.addItem("😂 搞笑幽默", "humorous")
        self._style_combo.addItem("🧠 逻辑严谨", "logical")
        self._style_combo.addItem("⚡ 超快节奏", "fast_paced")
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)
        params_layout.addWidget(self._style_combo)

        self._style_desc = QLabel()
        self._style_desc.setObjectName("styleDesc")
        self._style_desc.setWordWrap(True)
        params_layout.addWidget(self._style_desc)

        lang_title = QLabel("解说语言")
        lang_title.setObjectName("aiStyleTitle")
        params_layout.addWidget(lang_title)

        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("styleCombo")
        self._lang_combo.addItem("中文", "zh")
        self._lang_combo.addItem("英文", "en")
        self._lang_combo.addItem("泰文", "th")
        self._lang_combo.addItem("印尼文", "id")
        params_layout.addWidget(self._lang_combo)

        ratio_title = QLabel("原声片段比例")
        ratio_title.setObjectName("aiStyleTitle")
        params_layout.addWidget(ratio_title)

        self._ratio_combo = QComboBox()
        self._ratio_combo.setObjectName("styleCombo")
        self._ratio_combo.addItem("0%", 0)
        self._ratio_combo.addItem("30%", 30)
        self._ratio_combo.addItem("40%", 40)
        self._ratio_combo.addItem("50%", 50)
        self._ratio_combo.addItem("60%", 60)
        self._ratio_combo.addItem("70%", 70)
        params_layout.addWidget(self._ratio_combo)

        if self._project.clipping_style:
            idx = self._style_combo.findData(self._project.clipping_style)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
        self._update_style_desc()

        if self._project.narration_language:
            idx = self._lang_combo.findData(self._project.narration_language)
            if idx >= 0:
                self._lang_combo.setCurrentIndex(idx)

        idx = self._ratio_combo.findData(self._project.original_sound_ratio)
        if idx >= 0:
            self._ratio_combo.setCurrentIndex(idx)

        params_layout.addStretch()
        layout.addWidget(self._params_widget)

        self._generate_narration_btn = QPushButton(" 生成解说文案 ")
        self._generate_narration_btn.setObjectName("startAiAnalysisBtn")
        self._generate_narration_btn.setCursor(Qt.PointingHandCursor)
        self._generate_narration_btn.clicked.connect(self._on_generate_narration)
        layout.addWidget(self._generate_narration_btn)

        if self._project.narration_scripts:
            self._params_widget.hide()
            self._generate_narration_btn.hide()

        self._narration_loading = QFrame()
        self._narration_loading.setObjectName("narrationLoadingFrame")
        narration_loading_layout = QVBoxLayout(self._narration_loading)
        narration_loading_layout.setContentsMargins(0, 20, 0, 20)
        narration_loading_layout.setAlignment(Qt.AlignCenter)

        narration_loading_label = QLabel("AI正在生成解说文案，请稍候...")
        narration_loading_label.setObjectName("narrationLoadingLabel")
        narration_loading_layout.addWidget(narration_loading_label)
        layout.addWidget(self._narration_loading)
        self._narration_loading.hide()

        self._narration_results_widget = QFrame()
        self._narration_results_widget.setObjectName("narrationResultsWidget")
        self._narration_results_layout = QVBoxLayout(self._narration_results_widget)
        self._narration_results_layout.setContentsMargins(0, 0, 0, 0)
        self._narration_results_layout.setSpacing(8)

        results_title = QLabel("解说文案预览")
        results_title.setObjectName("aiStyleTitle")
        self._narration_results_layout.addWidget(results_title)

        self._narration_table = QTableWidget()
        self._narration_table.setObjectName("narrationTable")
        self._narration_table.setColumnCount(2)
        self._narration_table.setHorizontalHeaderLabels(["原片片段", "解说文案"])
        self._narration_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self._narration_table.setColumnWidth(0, 240)
        self._narration_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._narration_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._narration_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._narration_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._narration_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._narration_table.setWordWrap(True)
        self._narration_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._narration_table.verticalHeader().setMinimumSectionSize(70)
        self._narration_table.cellClicked.connect(self._on_narration_cell_clicked)
        self._narration_results_layout.addWidget(self._narration_table, stretch=1)

        self._re_analyze_btn = QPushButton("重新生成")
        self._re_analyze_btn.setObjectName("reAnalyzeBtn")
        self._re_analyze_btn.setCursor(Qt.PointingHandCursor)
        self._re_analyze_btn.clicked.connect(self._on_re_analyze)
        self._re_analyze_btn.hide()
        self._narration_results_layout.addWidget(self._re_analyze_btn)

        layout.addWidget(self._narration_results_widget, stretch=1)
        self._narration_results_widget.hide()

    def _collect_all_segments(self):
        self._all_segments = {}
        for video_state in self._project.videos:
            for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                for seg in video_state.segments.get(seg_type, []):
                    seg.video_path = video_state.video_path
                    self._all_segments[seg.id] = seg

    def _find_segment_by_time(self, start_time: str, end_time: str) -> Optional[VideoSegment]:
        for seg in self._all_segments.values():
            if seg.start_time == start_time and seg.end_time == end_time:
                return seg
        return None

    def _collect_segments_by_video(self) -> Dict:
        """收集所有视频的片段信息"""
        segments_by_video = {}
        self._all_segments = {}

        for video_state in self._project.videos:
            video_segments = {}
            for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                segs = video_state.segments.get(seg_type, [])
                video_segments[seg_type] = segs
                for seg in segs:
                    seg.video_path = video_state.video_path
                    self._all_segments[seg.id] = seg
            segments_by_video[video_state.video_path] = video_segments

        return segments_by_video

    def _update_style_desc(self):
        style_key = self._style_combo.currentData()
        for s in ClippingStyle:
            if s.value == style_key:
                self._style_desc.setText(s.description)
                break

    def _on_style_changed(self):
        self._update_style_desc()

    def _on_re_analyze(self):
        self._narration_results_widget.hide()
        self._re_analyze_btn.hide()
        self._clear_narration_results()
        self._narration_results = []
        self._project.narration_scripts = []
        self._style_combo.setEnabled(True)
        self._params_widget.show()
        self._generate_narration_btn.setEnabled(True)
        self._generate_narration_btn.setText(" 生成解说文案 ")
        self._generate_narration_btn.show()
        self._update_ready_state()

    def _update_visible(self):
        self._ai_widget.setVisible(True)

    def _update_ready_state(self):
        has_narration_results = len(self._narration_results) > 0
        self.ready_for_next.emit(has_narration_results)

    def save_state(self):
        self._project.clipping_style = self._style_combo.currentData()
        self._project.narration_language = self._lang_combo.currentData()
        self._project.original_sound_ratio = self._ratio_combo.currentData()
        self._project.narration_scripts = self._narration_results

    def _collect_subtitles_by_video(self) -> Dict[str, str]:
        """收集所有视频的字幕内容"""
        subtitles_by_video = {}
        for video_state in self._project.videos:
            if video_state.subtitle_path:
                try:
                    with open(video_state.subtitle_path, "r", encoding="utf-8") as f:
                        subtitles_by_video[video_state.video_path] = f.read()
                except Exception as e:
                    logger.warning(f"读取字幕文件失败: {video_state.subtitle_path}, {e}")
                    subtitles_by_video[video_state.video_path] = ""
            else:
                subtitles_by_video[video_state.video_path] = ""
        return subtitles_by_video

    def _on_generate_narration(self):
        if not self._analysis_service:
            logger.error("AI分析服务未配置")
            return

        style_key = self._style_combo.currentData()
        if not style_key:
            return

        self._generate_narration_btn.setEnabled(False)
        self._generate_narration_btn.setText("生成中...")
        self._params_widget.hide()
        self._narration_results_widget.hide()
        self._narration_loading.show()

        language = self._lang_combo.currentData()
        ratio = self._ratio_combo.currentData()

        segments_by_video = self._collect_segments_by_video()
        subtitles_by_video = self._collect_subtitles_by_video()

        self._narration_worker = NarrationGenerationWorker(
            self._analysis_service, segments_by_video, subtitles_by_video, style_key,
            language, ratio, self,
        )
        self._narration_worker.narration_finished.connect(self._on_narration_finished)
        self._narration_worker.error.connect(self._on_narration_error)
        self._narration_worker.start()

    def _on_narration_finished(self, results: List[dict]):
        self._narration_results = results
        self._narration_loading.hide()
        self._generate_narration_btn.hide()
        self._params_widget.hide()
        self._re_analyze_btn.show()

        self._project.narration_scripts = results
        if self._project_state_service:
            self._project_state_service.save_project(self._project)

        self._clear_narration_results()

        if results:
            self._narration_table.setRowCount(len(results))
            for i, nar_result in enumerate(results):
                seg_id = nar_result.get("segment_id", "")
                content_type = nar_result.get("content_type", "narration")
                start_time = nar_result.get("start_time", "")
                end_time = nar_result.get("end_time", "")
                story_summary = nar_result.get("story_summary", "")
                narration_script = nar_result.get("narration_script", "")
                seg = self._all_segments.get(seg_id)
                
                if content_type == "original_sound":
                    col1_text = f"🔊 原声 {start_time} - {end_time}"
                else:
                    col1_text = f"🎬 {start_time} - {end_time}"
                
                if not seg:
                    seg = self._find_segment_by_time(start_time, end_time)
                
                col1_btn = QPushButton(col1_text)
                col1_btn.setObjectName("segmentPlayBtn")
                col1_btn.setCursor(Qt.PointingHandCursor)
                col1_btn.setMinimumWidth(200)
                col1_btn.setFixedHeight(40)
                col1_btn.segment = seg
                col1_btn.narration_result = nar_result
                col1_btn.clicked.connect(self._on_segment_btn_clicked)
                
                col1_container = QWidget()
                col1_layout = QHBoxLayout(col1_container)
                col1_layout.setContentsMargins(4, 4, 4, 4)
                col1_layout.addWidget(col1_btn, 0, Qt.AlignVCenter)
                self._narration_table.setCellWidget(i, 0, col1_container)
                
                if content_type == "original_sound":
                    col2_text = "（原声片段）"
                else:
                    col2_text = narration_script if narration_script else story_summary
                
                col2_label = QLabel(col2_text)
                col2_label.setWordWrap(True)
                col2_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                col2_label.setStyleSheet("color: #e2e8f0; padding: 6px;")
                self._narration_table.setCellWidget(i, 1, col2_label)
                
                self._narration_table.setRowHeight(i, 64)

            self._narration_results_widget.show()
        else:
            error_label = QLabel("未生成任何解说文案，请重试")
            error_label.setObjectName("narrationErrorLabel")
            error_label.setWordWrap(True)
            self._narration_results_layout.insertWidget(0, error_label)
            self._narration_results_widget.show()

        self._update_ready_state()

    def _on_narration_error(self, error_msg: str):
        self._narration_loading.hide()
        self._generate_narration_btn.setText(" 生成解说文案 ")
        self._generate_narration_btn.setEnabled(True)
        self._params_widget.show()

        error_label = QLabel(f"文案生成失败：{error_msg}")
        error_label.setObjectName("narrationErrorLabel")
        error_label.setWordWrap(True)
        self._clear_narration_results()
        self._narration_results_layout.insertWidget(0, error_label)
        self._narration_results_widget.show()

    def _clear_narration_results(self):
        self._narration_table.setRowCount(0)

    def _on_segment_btn_clicked(self):
        btn = self.sender()
        if btn:
            narration_result = getattr(btn, 'narration_result', None)
            start_time = narration_result.get('start_time') if narration_result else None
            end_time = narration_result.get('end_time') if narration_result else None
            
            if start_time and end_time:
                video_path = None
                if hasattr(btn, 'segment') and btn.segment:
                    video_path = getattr(btn.segment, 'video_path', None)
                
                if not video_path:
                    for video_state in self._project.videos:
                        video_path = video_state.video_path
                        break
                
                if video_path:
                    self._play_video_segment(video_path, start_time, end_time)

    def _play_video_segment(self, video_path: str, start_time: str, end_time: str):
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton, QHBoxLayout
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimediaWidgets import QVideoWidget

        dialog = QDialog(self)
        dialog.setWindowTitle(f"预览 {start_time} - {end_time}")
        dialog.resize(800, 600)

        layout = QVBoxLayout(dialog)

        player = QMediaPlayer()
        video_widget = QVideoWidget()
        layout.addWidget(video_widget)

        player.setVideoOutput(video_widget)
        player.setSource(QUrl.fromLocalFile(video_path))

        from clip_synth.utils.time import parse_time
        start_ms = int(parse_time(start_time) * 1000)
        end_ms = int(parse_time(end_time) * 1000)

        def on_position_changed(pos):
            if end_ms > 0 and pos >= end_ms:
                player.setPosition(start_ms)

        player.positionChanged.connect(on_position_changed)

        def on_media_status(status):
            if status == QMediaPlayer.LoadedMedia:
                player.setPosition(start_ms)
                player.play()

        player.mediaStatusChanged.connect(on_media_status)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.finished.connect(lambda: player.stop())
        dialog.exec()

    def _on_narration_cell_clicked(self, row: int, col: int):
        pass

    def _restore_narration_results(self):
        if not self._project.narration_scripts:
            return
        self._narration_results = self._project.narration_scripts
        self._clear_narration_results()
        self._generate_narration_btn.hide()
        self._re_analyze_btn.show()

        self._collect_all_segments()

        if self._narration_results:
            self._narration_table.setRowCount(len(self._narration_results))
            for i, nar_result in enumerate(self._narration_results):
                seg_id = nar_result.get("segment_id", "")
                content_type = nar_result.get("content_type", "narration")
                start_time = nar_result.get("start_time", "")
                end_time = nar_result.get("end_time", "")
                story_summary = nar_result.get("story_summary", "")
                narration_script = nar_result.get("narration_script", "")
                seg = self._all_segments.get(seg_id)
                
                if content_type == "original_sound":
                    col1_text = f"🔊 原声 {start_time} - {end_time}"
                else:
                    col1_text = f"🎬 {start_time} - {end_time}"
                
                if not seg:
                    seg = self._find_segment_by_time(start_time, end_time)
                
                col1_btn = QPushButton(col1_text)
                col1_btn.setObjectName("segmentPlayBtn")
                col1_btn.setCursor(Qt.PointingHandCursor)
                col1_btn.setMinimumWidth(200)
                col1_btn.setFixedHeight(36)
                col1_btn.segment = seg
                col1_btn.narration_result = nar_result
                col1_btn.clicked.connect(self._on_segment_btn_clicked)
                
                col1_container = QWidget()
                col1_layout = QHBoxLayout(col1_container)
                col1_layout.setContentsMargins(4, 4, 4, 4)
                col1_layout.addWidget(col1_btn, 0, Qt.AlignVCenter)
                self._narration_table.setCellWidget(i, 0, col1_container)
                
                if content_type == "original_sound":
                    col2_text = "（原声片段）"
                else:
                    col2_text = narration_script if narration_script else story_summary
                
                col2_label = QLabel(col2_text)
                col2_label.setWordWrap(True)
                col2_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                col2_label.setStyleSheet("color: #e2e8f0; padding: 6px;")
                self._narration_table.setCellWidget(i, 1, col2_label)
                
                self._narration_table.setRowHeight(i, 64)
        self._narration_results_widget.show()
