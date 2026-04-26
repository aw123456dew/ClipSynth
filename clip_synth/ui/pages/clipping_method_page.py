import asyncio
import logging
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
)

from clip_synth.models.project_state import (
    ClippingStyle,
    SmartClippingProjectState,
    VideoSegment,
)
from clip_synth.services.clipping_analysis_service import ClippingAnalysisService

logger = logging.getLogger("clip_synth.clipping_method")


class ClippingAnalysisWorker(QThread):
    finished = Signal(list)
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
                self.finished.emit(result)
            finally:
                loop.close()
        except Exception as e:
            logger.error("AI剪辑分析失败: %s", str(e))
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
        layout.setContentsMargins(12, 10, 12, 10)

        self._checkbox = QPushButton()
        self._checkbox.setObjectName("segmentCheckbox")
        self._checkbox.setCheckable(True)
        self._checkbox.setChecked(self._segment.selected)
        self._checkbox.clicked.connect(self._on_toggle)
        self._checkbox.setFixedSize(22, 22)
        layout.addWidget(self._checkbox)

        time_label = QLabel(f"{self._segment.start_time} - {self._segment.end_time}")
        time_label.setObjectName("checkItemTime")
        layout.addWidget(time_label)

        desc_label = QLabel(self._segment.description)
        desc_label.setObjectName("checkItemDesc")
        desc_label.setWordWrap(True)
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
        self._ai_results: List[Tuple[str, str]] = []
        self._all_segments: Dict[str, VideoSegment] = {}
        self.setObjectName("clippingMethodPage")
        self._setup_ui()
        self._collect_all_segments()
        self._restore_ai_results()
        self._update_ready_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 32, 20)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setSpacing(16)

        title = QLabel("选择剪辑手法")
        title.setObjectName("clippingMethodTitle")
        header_row.addWidget(title)

        header_row.addSpacing(8)

        self._manual_radio = QRadioButton("手动选择")
        self._manual_radio.setObjectName("modeRadio")
        self._manual_radio.setChecked(self._project.clipping_mode == "manual")
        self._manual_radio.toggled.connect(self._on_mode_changed)
        header_row.addWidget(self._manual_radio)

        self._ai_radio = QRadioButton("AI智能识别")
        self._ai_radio.setObjectName("modeRadio")
        self._ai_radio.setChecked(self._project.clipping_mode == "ai")
        self._ai_radio.toggled.connect(self._on_mode_changed)
        header_row.addWidget(self._ai_radio)

        header_row.addStretch()
        layout.addLayout(header_row)

        self._stack = QFrame()
        self._stack.setObjectName("clippingStack")
        stack_layout = QVBoxLayout(self._stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)

        self._manual_widget = QFrame()
        self._manual_widget.setObjectName("manualWidget")
        self._build_manual_ui()
        stack_layout.addWidget(self._manual_widget)

        self._ai_widget = QFrame()
        self._ai_widget.setObjectName("aiWidget")
        self._build_ai_ui()
        stack_layout.addWidget(self._ai_widget)

        layout.addWidget(self._stack, stretch=1)

        self._update_visible()

    def _build_manual_ui(self):
        layout = QVBoxLayout(self._manual_widget)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setObjectName("manualScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QFrame()
        scroll_content.setObjectName("manualScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(8)

        for video_state in self._project.videos:
            video_name = video_state.video_path.split("/")[-1].split("\\")[-1]
            video_header = QLabel(f"📹 {video_name}")
            video_header.setObjectName("manualVideoHeader")
            scroll_layout.addWidget(video_header)

            for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                segments = video_state.segments.get(seg_type, [])
                if not segments:
                    continue
                group = TypeSegmentGroup(seg_type, segments)
                self._type_groups.append(group)
                scroll_layout.addWidget(group)

            scroll_layout.addSpacing(8)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def _build_ai_ui(self):
        layout = QVBoxLayout(self._ai_widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        style_title = QLabel("选择AI剪辑手法")
        style_title.setObjectName("aiStyleTitle")
        layout.addWidget(style_title)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(0)

        self._style_combo = QComboBox()
        self._style_combo.setObjectName("styleCombo")
        self._style_combo.addItem("🔥 高燃混剪", "high_energy")
        self._style_combo.addItem("⚡ 热点前置", "hot_prelude")
        self._style_combo.addItem("🏆 黄金三段", "golden_three")
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)
        combo_row.addWidget(self._style_combo, stretch=1)

        combo_icon = QLabel("▼")
        combo_icon.setObjectName("comboArrow")
        combo_icon.setFixedWidth(28)
        combo_icon.setAlignment(Qt.AlignCenter)
        combo_row.addWidget(combo_icon)

        layout.addLayout(combo_row)

        self._style_desc = QLabel()
        self._style_desc.setObjectName("styleDesc")
        self._style_desc.setWordWrap(True)
        layout.addWidget(self._style_desc)

        if self._project.clipping_style:
            idx = self._style_combo.findData(self._project.clipping_style)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
        self._update_style_desc()

        self._start_ai_btn = QPushButton(" 开始AI分析 ")
        self._start_ai_btn.setObjectName("startAiAnalysisBtn")
        self._start_ai_btn.setCursor(Qt.PointingHandCursor)
        self._start_ai_btn.clicked.connect(self._on_start_ai_analysis)
        layout.addWidget(self._start_ai_btn)

        self._ai_loading = QFrame()
        self._ai_loading.setObjectName("aiLoadingFrame")
        loading_layout = QVBoxLayout(self._ai_loading)
        loading_layout.setContentsMargins(0, 20, 0, 20)
        loading_layout.setAlignment(Qt.AlignCenter)

        loading_label = QLabel("AI正在分析片段，请稍候...")
        loading_label.setObjectName("aiLoadingLabel")
        loading_layout.addWidget(loading_label)

        self._ai_progress = QFrame()
        self._ai_progress.setObjectName("aiProgressBar")
        self._ai_progress.setFixedHeight(6)
        self._ai_progress.setMaximumWidth(400)
        loading_layout.addWidget(self._ai_progress)

        layout.addWidget(self._ai_loading)
        self._ai_loading.hide()

        self._ai_results_area = QScrollArea()
        self._ai_results_area.setObjectName("aiResultsArea")
        self._ai_results_area.setWidgetResizable(True)
        self._ai_results_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._ai_results_content = QFrame()
        self._ai_results_content.setObjectName("aiResultsContent")
        self._ai_results_layout = QVBoxLayout(self._ai_results_content)
        self._ai_results_layout.setContentsMargins(0, 0, 0, 0)
        self._ai_results_layout.setSpacing(6)
        self._ai_results_layout.addStretch()
        self._ai_results_area.setWidget(self._ai_results_content)

        layout.addWidget(self._ai_results_area, stretch=1)
        self._ai_results_area.hide()

        self._re_analyze_btn = QPushButton("重新生成")
        self._re_analyze_btn.setObjectName("reAnalyzeBtn")
        self._re_analyze_btn.setCursor(Qt.PointingHandCursor)
        self._re_analyze_btn.clicked.connect(self._on_re_analyze)
        self._re_analyze_btn.hide()
        layout.addWidget(self._re_analyze_btn)

    def _collect_all_segments(self):
        self._all_segments = {}
        for video_state in self._project.videos:
            for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
                for seg in video_state.segments.get(seg_type, []):
                    self._all_segments[seg.id] = seg

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

    def _on_start_ai_analysis(self):
        if not self._analysis_service:
            logger.error("AI分析服务未配置")
            return

        style_key = self._style_combo.currentData()
        if not style_key:
            return

        self._start_ai_btn.setEnabled(False)
        self._start_ai_btn.setText("分析中...")
        self._ai_results_area.hide()
        self._re_analyze_btn.hide()
        self._ai_loading.show()

        segments_by_video = self._collect_segments_by_video()

        self._worker = ClippingAnalysisWorker(
            self._analysis_service, segments_by_video, style_key, self,
        )
        self._worker.finished.connect(self._on_analysis_finished)
        self._worker.error.connect(self._on_analysis_error)
        self._worker.start()

    def _restore_ai_results(self):
        if not self._project.ai_analysis_results:
            return
        self._ai_results = [
            (r["seg_id"], r["reason"])
            for r in self._project.ai_analysis_results
        ]
        self._clear_ai_results()
        for seg_id, reason in self._ai_results:
            seg = self._all_segments.get(seg_id)
            if seg:
                item = AiResultItem(seg, reason)
                self._ai_results_layout.insertWidget(
                    self._ai_results_layout.count() - 1, item
                )
        if self._ai_results:
            summary = QLabel(f"AI已选择 {len(self._ai_results)} 个片段")
            summary.setObjectName("aiResultSummary")
            self._ai_results_layout.insertWidget(0, summary)
            self._start_ai_btn.hide()
            self._ai_results_area.show()
            self._re_analyze_btn.show()

    def _on_analysis_finished(self, results: List[Tuple[str, str]]):
        self._ai_results = results
        self._ai_loading.hide()
        self._start_ai_btn.setText(" 开始AI分析 ")
        self._start_ai_btn.setEnabled(True)
        self._start_ai_btn.hide()

        self._project.ai_analysis_results = [
            {"seg_id": seg_id, "reason": reason}
            for seg_id, reason in results
        ]
        if self._project_state_service:
            self._project_state_service.save_project(self._project)

        self._clear_ai_results()

        for seg_id, reason in results:
            seg = self._all_segments.get(seg_id)
            if seg:
                item = AiResultItem(seg, reason)
                self._ai_results_layout.insertWidget(
                    self._ai_results_layout.count() - 1, item
                )

        if results:
            summary = QLabel(f"AI已选择 {len(results)} 个片段")
            summary.setObjectName("aiResultSummary")
            self._ai_results_layout.insertWidget(0, summary)

        self._ai_results_area.show()
        self._re_analyze_btn.show()
        self._update_ready_state()

    def _on_analysis_error(self, error_msg: str):
        self._ai_loading.hide()
        self._start_ai_btn.setText(" 开始AI分析 ")
        self._start_ai_btn.setEnabled(True)
        self._start_ai_btn.show()

        error_label = QLabel(f"分析失败：{error_msg}")
        error_label.setObjectName("aiErrorLabel")
        error_label.setWordWrap(True)
        self._clear_ai_results()
        self._ai_results_layout.insertWidget(0, error_label)
        self._ai_results_area.show()
        self._re_analyze_btn.show()

    def _clear_ai_results(self):
        while self._ai_results_layout.count() > 1:
            item = self._ai_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_re_analyze(self):
        self._ai_results_area.hide()
        self._re_analyze_btn.hide()
        self._start_ai_btn.show()
        self._clear_ai_results()
        self._on_start_ai_analysis()

    def _on_mode_changed(self):
        self._update_visible()
        self._update_ready_state()

    def _update_visible(self):
        is_manual = self._manual_radio.isChecked()
        self._manual_widget.setVisible(is_manual)
        self._ai_widget.setVisible(not is_manual)

    def _update_ready_state(self):
        if self._manual_radio.isChecked():
            all_selected = all(g.has_selection() for g in self._type_groups)
            self.ready_for_next.emit(all_selected)
        else:
            has_results = len(self._ai_results) > 0
            self.ready_for_next.emit(has_results)

    def save_state(self):
        self._project.clipping_mode = "manual" if self._manual_radio.isChecked() else "ai"
        if self._ai_radio.isChecked():
            self._project.clipping_style = self._style_combo.currentData()
        self._project.ai_analysis_results = [
            {"seg_id": seg_id, "reason": reason}
            for seg_id, reason in self._ai_results
        ]
