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
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.ai_service import AIModelConfig
from clip_synth.ui.pages.narrate_v2.episode_script_worker import (
    EpisodeScriptWorker,
    get_total_duration_seconds,
    split_utterances_into_episodes,
    EPISODE_THRESHOLD_SECONDS,
)
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

ORIGINAL_SOUND_OPTIONS = [
    "关闭",
    "开启",
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
        self._episode_worker: EpisodeScriptWorker | None = None
        self._utterances = []
        self._speaker_aliases = {}
        self._ai_config: AIModelConfig | None = None
        self._segments = []
        self._collected_text = ""
        self._pending_chunks = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(80)
        self._flush_timer.timeout.connect(self._flush_pending_chunks)
        # 分集模式状态
        self._episode_mode = False
        self._episodes: list[list] = []
        self._current_episode_idx = 0
        self._episode_segments: list[list] = []  # 每集已生成的 segments
        self._episode_labels: list[QLabel] = []  # 每集状态标签
        self._setup_ui()

    def configure(self, ai_config: AIModelConfig, utterances: list, speaker_aliases: dict):
        self._ai_config = ai_config
        self._utterances = utterances
        self._speaker_aliases = speaker_aliases
        self._segments = []
        self._collected_text = ""
        # 检测是否需要分集模式
        total_sec = get_total_duration_seconds(utterances)
        self._episode_mode = total_sec > EPISODE_THRESHOLD_SECONDS
        if self._episode_mode:
            self._episodes = split_utterances_into_episodes(utterances)
            self._episode_segments = [[] for _ in self._episodes]
            logger.info(
                "分集模式启动: 总时长=%.0f秒, 分为 %d 集",
                total_sec, len(self._episodes),
            )
            self._show_episode_banner(total_sec, len(self._episodes))
        else:
            self._episode_mode = False
            self._episodes = []
            self._episode_segments = []
            self._hide_episode_banner()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # 顶部标题行
        title_row = QHBoxLayout()
        title = QLabel("生成解说文案")
        title.setObjectName("aiStyleTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self._generate_btn = QPushButton("生成解说文案")
        self._generate_btn.setObjectName("generateScriptBtn")
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.clicked.connect(self._on_generate)
        title_row.addWidget(self._generate_btn)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #60a5fa; font-size: 12px;")
        self._status_label.setMinimumWidth(200)
        title_row.addWidget(self._status_label)
        root.addLayout(title_row)

        # 主体：左侧内容区 + 右侧配置栏
        body = QHBoxLayout()
        body.setSpacing(20)

        # ── 左侧 ──────────────────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(12)

        # 分集进度面板（默认隐藏，出现在编辑器上方）
        self._episode_panel = QFrame()
        self._episode_panel.setObjectName("episodePanel")
        self._episode_panel.setStyleSheet(
            "QFrame#episodePanel {"
            "  background: #0f1f35;"
            "  border: 1px solid #2563eb;"
            "  border-radius: 8px;"
            "}"
        )
        ep_layout = QVBoxLayout(self._episode_panel)
        ep_layout.setContentsMargins(16, 12, 16, 12)
        ep_layout.setSpacing(10)

        # 面板标题行
        ep_header = QHBoxLayout()
        self._ep_icon = QLabel("⚡")
        self._ep_icon.setStyleSheet("font-size: 14px;")
        ep_header.addWidget(self._ep_icon)
        self._ep_title = QLabel("")
        self._ep_title.setStyleSheet(
            "color: #93c5fd; font-size: 13px; font-weight: bold;"
        )
        ep_header.addWidget(self._ep_title, stretch=1)
        ep_layout.addLayout(ep_header)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1e3a5f;")
        ep_layout.addWidget(sep)

        # 集列表容器（用 QScrollArea 包裹，固定最大高度）
        self._ep_list_container = QWidget()
        self._ep_list_container.setStyleSheet("background: transparent;")
        self._ep_list_layout = QVBoxLayout(self._ep_list_container)
        self._ep_list_layout.setContentsMargins(0, 0, 0, 0)
        self._ep_list_layout.setSpacing(2)

        ep_scroll = QScrollArea()
        ep_scroll.setWidget(self._ep_list_container)
        ep_scroll.setWidgetResizable(True)
        ep_scroll.setMaximumHeight(180)
        ep_scroll.setMinimumHeight(40)
        ep_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ep_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: #1e293b; border-radius: 3px; }"
            "QScrollBar::handle:vertical { background: #334155; border-radius: 3px; }"
        )
        ep_layout.addWidget(ep_scroll)

        self._episode_panel.hide()
        left.addWidget(self._episode_panel)

        # 文案编辑器
        editor_label = QLabel("解说文案")
        editor_label.setStyleSheet("color: #64748b; font-size: 12px;")
        left.addWidget(editor_label)

        self._script_input = QPlainTextEdit()
        self._script_input.setObjectName("scriptTextEdit")
        self._script_input.setPlaceholderText("点击右上角「生成解说文案」由 AI 自动生成...")
        self._script_input.textChanged.connect(self.script_edited.emit)
        left.addWidget(self._script_input, stretch=1)

        body.addLayout(left, stretch=3)

        # ── 右侧配置栏 ────────────────────────────────────────────────────
        right_panel = QFrame()
        right_panel.setObjectName("scriptConfigPanel")
        right_panel.setMaximumWidth(220)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(14)

        def _section_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;")
            return lbl

        right_layout.addWidget(_section_label("解说风格"))
        self._style_combo = QComboBox()
        self._style_combo.setObjectName("styleCombo")
        for s in NARRATION_STYLES:
            self._style_combo.addItem(s)
        self._style_combo.setCurrentIndex(2)
        right_layout.addWidget(self._style_combo)

        right_layout.addWidget(_section_label("讲述角度"))
        self._perspective_combo = QComboBox()
        self._perspective_combo.setObjectName("styleCombo")
        for p in PERSPECTIVES:
            self._perspective_combo.addItem(p)
        right_layout.addWidget(self._perspective_combo)

        right_layout.addWidget(_section_label("解说时长"))
        self._duration_combo = QComboBox()
        self._duration_combo.setObjectName("styleCombo")
        for d in DURATIONS:
            self._duration_combo.addItem(d)
        right_layout.addWidget(self._duration_combo)

        right_layout.addWidget(_section_label("是否开启原声"))
        self._original_sound_combo = QComboBox()
        self._original_sound_combo.setObjectName("styleCombo")
        for opt in ORIGINAL_SOUND_OPTIONS:
            self._original_sound_combo.addItem(opt)
        right_layout.addWidget(self._original_sound_combo)

        right_layout.addWidget(_section_label("附加要求"))
        self._requirement_input = QPlainTextEdit()
        self._requirement_input.setObjectName("scriptRequirementEdit")
        self._requirement_input.setPlaceholderText("对解说文案的额外要求...")
        self._requirement_input.setMaximumHeight(100)
        right_layout.addWidget(self._requirement_input)

        right_layout.addStretch()

        body.addWidget(right_panel, stretch=0)

        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------------ #
    #  分集进度面板
    # ------------------------------------------------------------------ #

    def _show_episode_banner(self, total_sec: float, episode_count: int):
        mins = int(total_sec // 60)
        secs = int(total_sec % 60)
        time_str = f"{mins}分{secs}秒" if secs else f"{mins}分钟"
        self._ep_title.setText(
            f"长视频模式  ·  总时长 {time_str}  ·  共 {episode_count} 集"
        )

        # 清空旧行（删除 layout 中所有子 widget）
        for lbl in self._episode_labels:
            lbl.deleteLater()
        self._episode_labels.clear()
        while self._ep_list_layout.count():
            item = self._ep_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i in range(episode_count):
            row = self._make_episode_row(i, "等待中", "pending")
            self._ep_list_layout.addWidget(row)
            # 取行内的状态标签（第二个子 widget）
            status_lbl = row.findChild(QLabel, f"ep_status_{i}")
            self._episode_labels.append(status_lbl)

        self._ep_list_layout.addStretch()
        self._episode_panel.show()

    def _make_episode_row(self, idx: int, status_text: str, state: str) -> QFrame:
        """创建单集进度行"""
        row = QFrame()
        row.setStyleSheet(
            "QFrame { background: #0d1b2e; border-radius: 4px; padding: 2px; }"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 8, 4)
        row_layout.setSpacing(10)

        # 集号标签
        num_lbl = QLabel(f"第 {idx + 1} 集")
        num_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; min-width: 44px;")
        row_layout.addWidget(num_lbl)

        # 状态点
        dot = QLabel("●")
        dot.setObjectName(f"ep_dot_{idx}")
        dot.setStyleSheet(f"color: {self._state_color(state)}; font-size: 8px;")
        row_layout.addWidget(dot)

        # 状态文字
        status_lbl = QLabel(status_text)
        status_lbl.setObjectName(f"ep_status_{idx}")
        status_lbl.setStyleSheet(f"color: {self._state_color(state)}; font-size: 12px;")
        row_layout.addWidget(status_lbl, stretch=1)

        return row

    @staticmethod
    def _state_color(state: str) -> str:
        return {
            "pending":    "#475569",
            "running":    "#60a5fa",
            "done":       "#22c55e",
            "error":      "#ef4444",
        }.get(state, "#94a3b8")

    def _hide_episode_banner(self):
        self._episode_panel.hide()

    def _update_episode_label(self, ep_idx: int, text: str, color: str = "#60a5fa"):
        if 0 <= ep_idx < len(self._episode_labels):
            lbl = self._episode_labels[ep_idx]
            if lbl:
                lbl.setText(text)
                lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
                # 同步更新状态点颜色
                dot = self._ep_list_container.findChild(QLabel, f"ep_dot_{ep_idx}")
                if dot:
                    dot.setStyleSheet(f"color: {color}; font-size: 8px;")

    # ------------------------------------------------------------------ #
    #  生成入口
    # ------------------------------------------------------------------ #

    def _on_generate(self):
        style = self._style_combo.currentText()
        logger.info("点击生成解说文案: style=%s, episode_mode=%s", style, self._episode_mode)

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
        self._status_label.setStyleSheet("color: #60a5fa; font-size: 12px;")

        if self._episode_mode:
            self._start_episode_generation()
        else:
            self._start_single_generation()

    # ------------------------------------------------------------------ #
    #  单集模式（原有逻辑）
    # ------------------------------------------------------------------ #

    def _start_single_generation(self):
        self._generate_btn.setText("生成中...")
        self._status_label.setText("正在分析视频片段...")
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
        enable_original_sound = self._original_sound_combo.currentText() == "开启"

        script_prompt = build_script_generation_prompt(
            self._utterances, self._speaker_aliases, segments, perspective, extra,
            enable_original_sound=enable_original_sound,
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

    # ------------------------------------------------------------------ #
    #  分集模式
    # ------------------------------------------------------------------ #

    def _start_episode_generation(self):
        total = len(self._episodes)
        self._episode_segments = [[] for _ in self._episodes]
        self._generate_btn.setText(f"生成中 (0/{total})...")
        self._status_label.setText(f"正在生成第 1/{total} 集...")

        for i in range(total):
            self._update_episode_label(i, "等待中", self._state_color("pending"))

        style = self._style_combo.currentText()
        system_prompt = _STYLE_PROMPT_MAP.get(style, SHORT_DRAMA_SYSTEM_PROMPT)
        duration = self._duration_combo.currentText()
        perspective = self._perspective_combo.currentText()
        extra = self._requirement_input.toPlainText()
        enable_original_sound = self._original_sound_combo.currentText() == "开启"

        self._episode_worker = EpisodeScriptWorker(
            episodes=self._episodes,
            speaker_aliases=self._speaker_aliases,
            segmentation_system_prompt=SEGMENTATION_SYSTEM_PROMPT,
            script_system_prompt=system_prompt,
            duration=duration,
            perspective=perspective,
            extra_requirements=extra,
            api_key=self._ai_config.api_key,
            base_url=self._ai_config.base_url,
            model_name=self._ai_config.model_name,
            enable_original_sound=enable_original_sound,
        )
        self._episode_worker.episode_started.connect(self._on_episode_started)
        self._episode_worker.episode_chunk.connect(self._on_episode_chunk)
        self._episode_worker.episode_done.connect(self._on_episode_done)
        self._episode_worker.episode_error.connect(self._on_episode_error)
        self._episode_worker.all_done.connect(self._on_all_episodes_done)
        self._episode_worker.error.connect(self._on_episode_global_error)
        self._episode_worker.start()

    def _on_episode_started(self, ep_idx: int, total: int):
        self._current_episode_idx = ep_idx
        self._generate_btn.setText(f"生成中 ({ep_idx + 1}/{total})...")
        self._status_label.setText(f"正在生成第 {ep_idx + 1}/{total} 集...")
        self._update_episode_label(ep_idx, "生成中...", self._state_color("running"))
        # 非最后一集：清空编辑器只显示当前集的流式输出，完成后不保留
        # 最后一集：流式输出后保留，等 all_done 合并替换
        self._script_input.clear()
        self._pending_chunks = ""

    def _on_episode_chunk(self, ep_idx: int, text: str):
        # 只显示当前正在生成的集的流式内容
        if ep_idx == self._current_episode_idx:
            self._pending_chunks += text
            if not self._flush_timer.isActive():
                self._flush_timer.start()

    def _on_episode_done(self, ep_idx: int, segments: list):
        self._flush_timer.stop()
        self._flush_pending_chunks()
        self._episode_segments[ep_idx] = segments
        self._update_episode_label(
            ep_idx, f"完成（{len(segments)} 个片段）", self._state_color("done")
        )
        logger.info("第 %d 集完成: %d 个片段", ep_idx + 1, len(segments))
        # 非最后一集：清空编辑器，等待下一集流式输出
        total = len(self._episodes)
        if ep_idx < total - 1:
            self._script_input.clear()

    def _on_episode_error(self, ep_idx: int, error_msg: str):
        self._flush_timer.stop()
        self._episode_worker = None
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("重新生成")
        self._update_episode_label(ep_idx, "生成失败", self._state_color("error"))
        self._status_label.setText(f"第 {ep_idx + 1} 集生成失败")
        self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        logger.error("第 %d 集生成失败: %s", ep_idx + 1, error_msg)

    def _on_all_episodes_done(self, all_segments: list):
        self._flush_timer.stop()
        self._episode_worker = None
        self._segments = all_segments

        # 合并所有集的 segments，输出纯 JSON（不带 markdown 代码块）
        merged_json = json.dumps({"segments": all_segments}, ensure_ascii=False, indent=2)
        self._script_input.setPlainText(merged_json)

        total = len(self._episodes)
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("重新生成")
        self._status_label.setText(
            f"全部 {total} 集生成完成（共 {len(all_segments)} 个片段）"
        )
        self._status_label.setStyleSheet("color: #22c55e; font-size: 12px;")
        logger.info("分集生成全部完成: %d 集, %d 个片段", total, len(all_segments))

    def _on_episode_global_error(self, error_msg: str):
        self._flush_timer.stop()
        self._episode_worker = None
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("重新生成")
        self._status_label.setText("生成失败")
        self._status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        logger.error("分集生成全局错误: %s", error_msg)

    # ------------------------------------------------------------------ #
    #  流式输出（单集模式）
    # ------------------------------------------------------------------ #

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
            if isinstance(seg, dict) and "parts" in seg:
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

    # ------------------------------------------------------------------ #
    #  公共接口
    # ------------------------------------------------------------------ #

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
            st = str(seg.get("start_time", ""))
            et = str(seg.get("end_time", ""))
            if not time_pattern.match(st) or not time_pattern.match(et):
                return False
            if "summary" not in seg:
                return False
            parts = seg.get("parts")
            if not isinstance(parts, list) or len(parts) == 0:
                return False
            for part in parts:
                ptype = part.get("type", "")
                if ptype == "narration" and not isinstance(part.get("script"), str):
                    return False
                if ptype == "original_sound":
                    if not isinstance(part.get("start_time"), str) or not isinstance(part.get("end_time"), str):
                        return False

        self._segments = segments
        return True

    def validate_script(self) -> tuple:
        clean_json = self._clean_json_text(self._script_input.toPlainText())
        if not clean_json:
            if self._segments and any(
                any(part.get("script", "").strip() for part in seg.get("parts", []))
                for seg in self._segments
            ):
                return True, ""
            return False, "输入框为空，请先点击「开始生成解说文案」按钮生成文案。"

        time_pattern = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")

        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError as e:
            if self._segments and any(
                any(part.get("script", "").strip() for part in seg.get("parts", []))
                for seg in self._segments
            ):
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

            if "summary" not in seg:
                return False, f"第 {i+1} 个片段缺少 summary 字段"

            st = str(seg.get("start_time", ""))
            et = str(seg.get("end_time", ""))
            if not time_pattern.match(st):
                return False, f"第 {i+1} 个片段的 start_time 格式错误：\"{st}\"，正确格式如 00:00:04.633"
            if not time_pattern.match(et):
                return False, f"第 {i+1} 个片段的 end_time 格式错误：\"{et}\"，正确格式如 00:00:06.200"

            parts = seg.get("parts")
            if not isinstance(parts, list) or len(parts) == 0:
                return False, f"第 {i+1} 个片段的 parts 数组为空，至少需要包含 1 个元素"

            for j, part in enumerate(parts):
                if not isinstance(part, dict):
                    return False, f"第 {i+1} 个片段 parts[{j}] 不是对象格式"
                ptype = part.get("type", "")
                if ptype not in ("narration", "original_sound"):
                    return False, f"第 {i+1} 个片段 parts[{j}] 的 type 无效: \"{ptype}\"，应为 narration 或 original_sound"
                if ptype == "narration":
                    if "script" not in part:
                        return False, f"第 {i+1} 个片段 parts[{j}] 的 narration 缺少 script 字段"
                    if not isinstance(part["script"], str):
                        return False, f"第 {i+1} 个片段 parts[{j}] 的 script 必须是字符串"
                if ptype == "original_sound":
                    if "start_time" not in part or "end_time" not in part:
                        return False, f"第 {i+1} 个片段 parts[{j}] 的 original_sound 缺少 start_time 或 end_time 字段"

        self._segments = segments
        return True, ""

    def get_script_segments(self) -> list:
        return self._segments