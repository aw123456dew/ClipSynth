import json
import logging
import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.ai_service import AIModelConfig
from clip_synth.ui.pages.narrate_v2.script_generation_worker import (
    ScriptGenerationWorker,
    SegmentationWorker,
)
from clip_synth.ui.pages.narrate_v2.script_prompts import (
    SEGMENTATION_SYSTEM_PROMPT,
    SHORT_DRAMA_SYSTEM_PROMPT,
    EASY_TALK_SYSTEM_PROMPT,
    PLAIN_NARRATION_SYSTEM_PROMPT,
    SPICY_ROAST_SYSTEM_PROMPT,
    build_segmentation_prompt,
    build_script_generation_prompt,
)

logger = logging.getLogger("clip_synth.narrate_v2")

NARRATION_STYLES = [
    "轻松口语化",
    "平铺叙事",
    "短剧爽文",
    "辛辣吐槽",
]

PERSPECTIVES = [
    "第三人称",
    "第一人称",
]

DURATIONS = [
    "无限制",
    "1-3分钟",
    "3-5分钟",
    "5分钟以上",
]

_STYLE_PROMPT_MAP = {
    "轻松口语化": EASY_TALK_SYSTEM_PROMPT,
    "平铺叙事": PLAIN_NARRATION_SYSTEM_PROMPT,
    "短剧爽文": SHORT_DRAMA_SYSTEM_PROMPT,
    "辛辣吐槽": SPICY_ROAST_SYSTEM_PROMPT,
}


class GenerateScriptPage(QFrame):
    script_edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._segmentation_worker: SegmentationWorker | None = None
        self._script_worker: ScriptGenerationWorker | None = None
        self._utterances = []
        self._speaker_aliases = {}
        self._ai_config: AIModelConfig | None = None
        self._segments = []
        self._collected_text = ""
        self._pending_chunks = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(80)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        self._setup_ui()

    def configure(self, ai_config: AIModelConfig, utterances: list, speaker_aliases: dict):
        self._ai_config = ai_config
        self._utterances = utterances
        self._speaker_aliases = speaker_aliases
        self._segments = []
        self._collected_text = ""

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("生成解说文案")
        title.setObjectName("aiStyleTitle")
        layout.addWidget(title)

        content_row = QHBoxLayout()
        content_row.setSpacing(20)

        left_panel = QFrame()
        left_panel.setObjectName("scriptInputPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        input_header = QHBoxLayout()
        input_label = QLabel("解说文案输入")
        input_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        input_header.addWidget(input_label)
        input_header.addStretch()

        self._generate_btn = QPushButton("生成解说文案")
        self._generate_btn.setObjectName("generateScriptBtn")
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.clicked.connect(self._on_generate)
        input_header.addWidget(self._generate_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #60a5fa; font-size: 12px;")
        input_header.addWidget(self._status_label)

        left_layout.addLayout(input_header)

        self._script_input = QPlainTextEdit()
        self._script_input.setObjectName("scriptTextEdit")
        self._script_input.setPlaceholderText("点击「生成解说文案」由 AI 自动生成...")
        self._script_input.textChanged.connect(self.script_edited.emit)
        left_layout.addWidget(self._script_input, stretch=1)

        content_row.addWidget(left_panel, stretch=1)

        right_panel = QFrame()
        right_panel.setObjectName("scriptConfigPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 0, 0, 0)
        right_layout.setSpacing(20)

        style_label = QLabel("解说风格")
        style_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        right_layout.addWidget(style_label)

        self._style_combo = QComboBox()
        self._style_combo.setObjectName("styleCombo")
        for s in NARRATION_STYLES:
            self._style_combo.addItem(s)
        self._style_combo.setCurrentIndex(2)
        right_layout.addWidget(self._style_combo)

        perspective_label = QLabel("讲述角度")
        perspective_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        right_layout.addWidget(perspective_label)

        self._perspective_combo = QComboBox()
        self._perspective_combo.setObjectName("styleCombo")
        for p in PERSPECTIVES:
            self._perspective_combo.addItem(p)
        self._perspective_combo.setCurrentIndex(0)
        right_layout.addWidget(self._perspective_combo)

        duration_label = QLabel("解说时长")
        duration_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        right_layout.addWidget(duration_label)

        self._duration_combo = QComboBox()
        self._duration_combo.setObjectName("styleCombo")
        for d in DURATIONS:
            self._duration_combo.addItem(d)
        right_layout.addWidget(self._duration_combo)

        requirement_label = QLabel("解说要求")
        requirement_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        right_layout.addWidget(requirement_label)

        self._requirement_input = QPlainTextEdit()
        self._requirement_input.setObjectName("scriptRequirementEdit")
        self._requirement_input.setPlaceholderText("对解说文案的额外要求...")
        self._requirement_input.setMaximumHeight(120)
        right_layout.addWidget(self._requirement_input)

        right_layout.addStretch()

        content_row.addWidget(right_panel, stretch=1)

        layout.addLayout(content_row, stretch=1)

    def _on_generate(self):
        style = self._style_combo.currentText()
        logger.info("点击生成解说文案: style=%s", style)

        if not self._ai_config or not self._ai_config.is_configured:
            self._status_label.setText("请在设置中配置文案生成模型")
            self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
            return

        if not self._utterances:
            self._status_label.setText("缺少字幕数据，请先完成字幕识别")
            self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
            return

        self._script_input.clear()
        self._pending_chunks = ""
        self._flush_timer.stop()
        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("生成中...")
        self._status_label.setText("正在分析视频片段...")
        self._status_label.setStyleSheet("color: #60a5fa; font-size: 12px;")
        logger.info("开始生成: 启动片段分割")

        duration = self._duration_combo.currentText()

        segmentation_prompt = build_segmentation_prompt(
            self._utterances, self._speaker_aliases, duration,
        )

        self._segmentation_worker = SegmentationWorker(
            system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            user_prompt=segmentation_prompt,
            api_key=self._ai_config.api_key,
            base_url=self._ai_config.base_url,
            model_name=self._ai_config.model_name,
        )
        self._segmentation_worker.finished.connect(self._on_segmentation_done)
        self._segmentation_worker.error.connect(self._on_segmentation_error)
        self._segmentation_worker.start()

    def _on_segmentation_done(self, segments: list):
        self._segmentation_worker = None
        self._segments = segments
        logger.info("片段分割完成: %d 个片段", len(segments))

        self._status_label.setText("正在生成解说文案...")
        self._status_label.setStyleSheet("color: #60a5fa; font-size: 12px;")

        style = self._style_combo.currentText()
        system_prompt = _STYLE_PROMPT_MAP.get(style, SHORT_DRAMA_SYSTEM_PROMPT)

        perspective = self._perspective_combo.currentText()
        extra = self._requirement_input.toPlainText()

        script_prompt = build_script_generation_prompt(
            self._utterances, self._speaker_aliases, segments, perspective, extra,
        )
        logger.info("解说文 prompt 长度=%d", len(script_prompt))

        self._script_worker = ScriptGenerationWorker(
            system_prompt=system_prompt,
            user_prompt=script_prompt,
            api_key=self._ai_config.api_key,
            base_url=self._ai_config.base_url,
            model_name=self._ai_config.model_name,
            temperature=0.7,
        )
        self._script_worker.chunk.connect(self._on_chunk)
        self._script_worker.finished.connect(self._on_script_finished)
        self._script_worker.error.connect(self._on_script_error)
        self._script_worker.start()

    def _on_segmentation_error(self, error_msg: str):
        self._segmentation_worker = None
        self._pending_chunks = ""
        self._flush_timer.stop()
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("生成解说文案")
        self._status_label.setText("片段分析失败")
        self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        logger.error("片段分割失败: %s", error_msg)

    def _on_chunk(self, text: str):
        self._pending_chunks += text
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_pending_chunks(self):
        if not self._pending_chunks:
            self._flush_timer.stop()
            return
        text = self._pending_chunks
        self._pending_chunks = ""
        cursor = self._script_input.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._script_input.setTextCursor(cursor)
        self._script_input.insertPlainText(text)

    def _on_script_finished(self, full_text: str):
        self._script_worker = None
        self._flush_timer.stop()

        parsed_segments = self._parse_json_segments(full_text)
        if parsed_segments:
            self._segments = parsed_segments
            clean_json = self._clean_json_text(full_text)
            if not clean_json:
                clean_json = self._clean_markdown_fences(full_text)

        self._flush_pending_chunks()

        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("开始生成解说文案")

        if parsed_segments:
            self._script_input.setPlainText(clean_json)
            self._status_label.setText(f"生成完成（{len(parsed_segments)} 个片段，共 {len(full_text)} 字）")
            self._status_label.setStyleSheet("color: #22c55e; font-size: 12px;")
            logger.info("解说文案生成完成: %d 个片段", len(parsed_segments))
        else:
            self._status_label.setText(f"生成完成（共 {len(full_text)} 字）")
            self._status_label.setStyleSheet("color: #22c55e; font-size: 12px;")
            logger.info("解说文案生成完成: %d 字（原始）", len(full_text))

    @staticmethod
    def _clean_markdown_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            idx = text.find("\n")
            if idx != -1:
                text = text[idx + 1:]
            else:
                text = text[3:]
        end_idx = text.rfind("```")
        if end_idx != -1:
            text = text[:end_idx].rstrip()
        return text.strip()

    @staticmethod
    def _clean_json_text(text: str) -> str:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        return text

    def _parse_json_segments(self, text: str) -> list:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return []
        validated = []
        for seg in segments:
            if isinstance(seg, dict) and "script" in seg:
                validated.append(seg)
        return validated

    def _on_script_error(self, error_msg: str):
        self._script_worker = None
        self._flush_timer.stop()
        self._pending_chunks = ""
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("开始生成解说文案")
        self._status_label.setText("生成失败")
        self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        logger.error("解说文案生成失败: %s", error_msg)

    def get_script_content(self) -> str:
        return self._script_input.toPlainText().strip()

    def set_script_content(self, text: str):
        if text:
            self._script_input.setPlainText(text)
            self._collected_text = text

    def is_script_valid(self) -> bool:
        text = self._script_input.toPlainText().strip()
        if not text:
            return False

        time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
        required_keys = {"summary", "start_time", "end_time", "script"}

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if isinstance(self._segments, list) and self._segments:
                return True
            return False

        segments = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(segments, list) or not segments:
            if isinstance(self._segments, list) and self._segments:
                return True
            return False

        for seg in segments:
            if not isinstance(seg, dict):
                return False
            if not required_keys.issubset(seg.keys()):
                return False
            st = str(seg.get("start_time", ""))
            et = str(seg.get("end_time", ""))
            if not time_pattern.match(st) or not time_pattern.match(et):
                return False

        self._segments = segments
        return True

    def validate_script(self) -> tuple:
        clean_json = self._clean_json_text(self._script_input.toPlainText())
        if not clean_json:
            if self._segments and any(seg.get("script", "").strip() for seg in self._segments):
                return True, ""
            return False, "输入框为空，请先点击「开始生成解说文案」按钮生成文案。"

        time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
        required_keys = {"summary", "start_time", "end_time", "script"}

        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError as e:
            if self._segments and any(seg.get("script", "").strip() for seg in self._segments):
                return True, ""
            return False, f"JSON 格式错误：{e}"

        if not isinstance(data, dict):
            return False, "JSON 根元素必须是一个对象（以 {{ 开头），不能是数组或其他类型。"

        segments = data.get("segments") if isinstance(data, dict) else None
        if not isinstance(segments, list):
            return False, "缺少 \"segments\" 字段，或该字段不是数组格式。"
        if not segments:
            return False, "\"segments\" 数组为空，至少需要包含一个片段。"

        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                return False, f"第 {i+1} 个片段不是对象格式，每个片段必须是 {{}} 包裹的字典。"

            missing = required_keys - seg.keys()
            if missing:
                missing_str = "、".join(sorted(missing))
                return False, f"第 {i+1} 个片段缺少字段：{missing_str}"

            st = str(seg.get("start_time", ""))
            et = str(seg.get("end_time", ""))
            if not time_pattern.match(st):
                return False, f"第 {i+1} 个片段的 start_time 格式错误：\"{st}\"，正确格式如 00:00:04.633"
            if not time_pattern.match(et):
                return False, f"第 {i+1} 个片段的 end_time 格式错误：\"{et}\"，正确格式如 00:00:06.200"

        self._segments = segments
        return True, ""

    def get_script_segments(self) -> list:
        return self._segments