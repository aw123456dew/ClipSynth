import logging
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.ai_service import AIModelConfig, AIService, MultiRoundChatManager
from clip_synth.services.novel_comic_state_service import NovelComicStateService
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger("clip_synth.comic_video")

DOUBAO_VOICE_OPTIONS = {
    "BV700_V2_streaming": "灿灿 2.0",
    "BV705_streaming": "炀炀",
    "BV701_V2_streaming": "擎苍 2.0",
    "BV001_V2_streaming": "通用女声 2.0",
    "BV700_streaming": "灿灿",
    "BV406_V2_streaming": "超自然音色-梓梓2.0",
    "BV406_streaming": "超自然音色-梓梓",
    "BV407_V2_streaming": "超自然音色-燃燃2.0",
    "BV407_streaming": "超自然音色-燃燃",
    "BV001_streaming": "通用女声",
    "BV002_streaming": "通用男声",
    "BV701_streaming": "擎苍",
    "BV123_streaming": "阳光青年",
    "BV120_streaming": "反卷青年",
    "BV119_streaming": "通用赘婿",
    "BV115_streaming": "古风少御",
    "BV107_streaming": "霸气青叔",
    "BV100_streaming": "质朴青年",
    "BV104_streaming": "温柔淑女",
    "BV004_streaming": "开朗青年",
    "BV113_streaming": "甜宠少御",
    "BV102_streaming": "儒雅青年",
    "BV405_streaming": "甜美小源",
    "BV007_streaming": "亲切女声",
    "BV009_streaming": "知性女声",
    "BV419_streaming": "诚诚",
    "BV415_streaming": "童童",
    "BV008_streaming": "亲切男声",
    "BV408_streaming": "译制片男声",
    "BV426_streaming": "懒小羊",
    "BV428_streaming": "清新文艺女声",
    "BV403_streaming": "鸡汤女声",
    "BV158_streaming": "智慧老者",
    "BV157_streaming": "慈爱姥姥",
    "BR001_streaming": "说唱小哥",
    "BV410_streaming": "活力解说男",
    "BV411_streaming": "影视解说小帅",
    "BV437_streaming": "解说小帅-多情感",
    "BV412_streaming": "影视解说小美",
    "BV159_streaming": "纨绔青年",
    "BV418_streaming": "直播一姐",
    "BV142_streaming": "沉稳解说男",
    "BV143_streaming": "潇洒青年",
    "BV056_streaming": "阳光男声",
    "BV005_streaming": "活泼女声",
    "BV064_streaming": "小萝莉",
    "BV051_streaming": "奶气萌娃",
    "BV063_streaming": "动漫海绵",
    "BV417_streaming": "动漫海星",
    "BV050_streaming": "动漫小新",
    "BV061_streaming": "天才童声",
    "BV401_streaming": "促销男声",
    "BV402_streaming": "促销女声",
    "BV006_streaming": "磁性男声",
    "BV011_streaming": "新闻女声",
    "BV012_streaming": "新闻男声",
    "BV034_streaming": "知性姐姐-双语",
    "BV033_streaming": "温柔小哥",
    "BV511_streaming": "慵懒女声-Ava",
    "BV505_streaming": "议论女声-Alicia",
    "BV138_streaming": "情感女声-Lawrence",
    "BV027_streaming": "美式女声-Amelia",
    "BV502_streaming": "讲述女声-Amanda",
    "BV503_streaming": "活力女声-Ariana",
    "BV504_streaming": "活力男声-Jackson",
    "BV421_streaming": "天才少女",
    "BV702_streaming": "Stefan",
    "BV506_streaming": "天真萌娃-Lily",
    "BV040_streaming": "亲切女声-Anna",
    "BV516_streaming": "澳洲男声-Henry",
    "BV520_streaming": "元气少女",
    "BV521_streaming": "萌系少女",
    "BV522_streaming": "气质女声",
    "BV524_streaming": "日语男声",
    "BV531_streaming": "活力男声Carlos（巴西地区）",
    "BV530_streaming": "活力女声（巴西地区）",
    "BV065_streaming": "气质御姐（墨西哥地区）",
    "BV021_streaming": "东北老铁",
    "BV020_streaming": "东北丫头",
    "BV704_streaming": "方言灿灿",
    "BV210_streaming": "西安佟掌柜",
    "BV217_streaming": "沪上阿姐",
    "BV213_streaming": "广西表哥",
    "BV025_streaming": "甜美台妹",
    "BV227_streaming": "台普男声",
    "BV026_streaming": "港剧男神",
    "BV424_streaming": "广东女仔",
    "BV212_streaming": "相声演员",
    "BV019_streaming": "重庆小伙",
    "BV221_streaming": "四川甜妹儿",
    "BV423_streaming": "重庆幺妹儿",
    "BV214_streaming": "乡村企业家",
    "BV226_streaming": "湖南妹坨",
    "BV216_streaming": "长沙靓女",
}

DOUBAO_EMOTION_OPTIONS = {
    "": "默认",
    "customer_service": "客服",
    "professional": "专业",
    "serious": "严肃",
    "narrator": "旁白-舒缓",
    "narrator_immersive": "旁白-沉浸",
    "comfort": "安慰鼓励",
    "lovey-dovey": "撒娇",
    "energetic": "可爱元气",
    "conniving": "绿茶",
    "tsundere": "傲娇",
    "charming": "娇媚",
    "storytelling": "讲故事",
    "radio": "情感电台",
    "yoga": "瑜伽",
    "advertising": "广告",
    "assistant": "助手",
    "chat": "自然对话",
    "pleased": "愉悦",
    "sorry": "抱歉",
    "annoyed": "嗔怪",
    "happy": "开心",
    "sad": "悲伤",
    "angry": "愤怒",
    "scare": "害怕",
    "hate": "厌恶",
    "surprise": "惊讶",
    "tear": "哭腔",
    "novel_dialog": "平和",
}

DOUBAO_LANGUAGE_OPTIONS = {
    "cn": "中文",
    "en": "英语",
    "ja": "日语",
    "thth": "泰语",
    "vivn": "越南语",
    "id": "印尼语",
    "ptbr": "葡萄牙语",
    "esmx": "西班牙语",
}


class TTSWorker(QThread):
    progress = Signal(str)
    tts_finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        text: str,
        voice_type: str,
        speed: float,
        pitch: float,
        volume: float,
        emotion: str,
        language: str,
        doubao_settings,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._voice_type = voice_type
        self._speed = speed
        self._pitch = pitch
        self._volume = volume
        self._emotion = emotion
        self._language = language
        self._doubao_settings = doubao_settings
        self._output_dir = output_dir
        self._audio_files = []

    def run(self):
        try:
            from clip_synth.services.doubao_tts_service import DoubaoTTSWorker, split_text_by_length

            worker = DoubaoTTSWorker(self._doubao_settings)
            os.makedirs(self._output_dir, exist_ok=True)

            chunks = split_text_by_length(self._text, 1000)
            total = len(chunks)

            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                self.progress.emit(f"正在生成配音 ({i+1}/{total})...")
                audio_path = os.path.join(self._output_dir, f"novel_dub_{i:04d}.mp3")

                success, msg, timestamps = worker.tts_single_with_timestamps(
                    text=chunk,
                    voice_type=self._voice_type,
                    output_path=audio_path,
                    speed=self._speed,
                    pitch=self._pitch,
                    volume=self._volume,
                    emotion=self._emotion,
                    language=self._language,
                )

                if not success:
                    self.error.emit(f"第 {i+1} 段配音生成失败: {msg}")
                    return

                from clip_synth.services.narrate_export_service import _get_media_duration
                duration = _get_media_duration(audio_path)

                self._audio_files.append({
                    "index": i,
                    "path": audio_path,
                    "text": chunk,
                    "duration": duration,
                    "timestamps": timestamps,
                })

            self.progress.emit("配音生成完成")
            self.tts_finished.emit()
        except Exception as e:
            logger.error("TTS生成异常: %s", e, exc_info=True)
            self.error.emit(str(e))

    def get_audio_files(self):
        return self._audio_files


COMIC_VIDEO_DESC_SYSTEM_PROMPT = """\
你是一个顶级的漫画分镜师和漫画编辑，擅长为单页漫画画面生成详细的画面描述。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "description": "景别/角度：...\\n场景：资产名称-版本\\n人物：角色名1, 角色名2\\n动作/表情：...\\n构图/氛围：..."
    }
  ]
}

传入的分镜数据包含原文(text)、已绑定的场景资产和人物道具资产。必须为每个传入的分镜生成对应的 description，不要合并、不要跳过、不要遗漏。

你的任务是为每个分镜生成一个单页漫画的画面描述。重要区别：**每个分镜只生成一个画面（一页漫画 = 一个格）**，不要分格、不要多格、不要整体排版描述。
每个分镜只输出一个完整的画面描述，包含以下字段：

- 景别/角度：全景/中景/近景/特写/大特写 等，仰视/俯视/平视/倾斜 等
- 场景：从分镜绑定的场景资产列表中选一个，直接写资产名称
- 人物：直接列出角色名即可，不要描述外观着装（如果画面中没有人物则填写"无"）
- 动作/表情：角色的肢体动作和面部表情细节（如果画面中无人则省略）
- 构图/氛围：画面的构图布局、色调、光影、氛围

场景与人物精简示例（非常重要，请严格按照此风格生成）：
- 原文「我只是叫了男人一声，啊燕。男人就红了眼眶」→ 景别/角度：特写；场景：房间内；人物：啊燕；动作/表情：啊燕垂着眼睫，眼眶泛红，视线落在虚空中；构图/氛围：暖色调柔光，聚焦在人物面部微表情上
- 原文「我看到茶已经凉了」→ 景别/角度：大特写；场景：客厅；人物：无；动作/表情：省略；构图/氛围：冷色调，一杯孤零零的茶杯，茶水表面静止无热气，背景虚化
- 原文「她推开门，外面空无一人」→ 景别/角度：中景；场景：门前街道；人物：她；动作/表情：她站在门框内，视线望向门外空旷的街道，手还搭在门把手上；构图/氛围：门框构成画框构图，门外冷清的光线照进来，拉出长长的影子

核心规则：
1. 仅生成一个画面，不要分格、不要整体排版描述，不要"格1""格2"等字样
2. 场景从绑定的场景资产中选择
3. 人物和道具只从绑定的资产列表中选择
4. **严禁画面中出现任何文字**：不要气泡、不要说明框、不要对话框、不要旁白文字、不要页码、不要标题。画面是纯视觉图像，所有信息通过构图、动作、表情、场景来传达
5. **人物视线必须在动作/表情中明确说明**：每个角色都要写明视线方向——看向另一个角色、看向某个方向、看向地面、看向某物体、闭眼等。**严禁角色直视镜头/看向观众**，保持自然的人物互动和视线方向。
6. **精简画面人物，优先用特写传达情绪**：不强制让原文所有角色都出现在画面中。可以选择最能传达情绪的一个角色给特写/近景，甚至可以用道具/场景的空镜头特写来代替人物画面。**原则是能用特写就用特写，能用一个角色就不用两个，能用空镜头就不用人物**。
7. 输出合法 JSON：所有字符串值内的英文双引号必须用反斜杠转义；所有换行符必须用 \\n 表示"""


class ComicVideoDescWorker(QThread):
    finished = Signal(list)
    progress = Signal(int, int, int, str)
    error = Signal(str)

    def __init__(
        self,
        storyboards: list[dict],
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
        parent=None,
    ):
        super().__init__(parent)
        self._storyboards = storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._single_batch = False

    def run(self) -> None:
        try:
            from clip_synth.ui.pages.novel_comic_generate_page import _make_chat_caller, _parse_json
            from clip_synth.services.ai_service import AIModelConfig
            import concurrent.futures, threading
            import logging as _lg
            _logger = _lg.getLogger("clip_synth.comic_video")

            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            total = len(self._storyboards)
            results: dict[int, dict] = {}
            errors: list[str] = []
            lock = threading.Lock()
            done_ctr = [0]

            chat_caller = _make_chat_caller(config, None)

            if self._single_batch:
                batches = [self._storyboards]
            else:
                batch_size = 5
                batches: list[list[dict]] = []
                for i in range(0, total, batch_size):
                    batches.append(self._storyboards[i:i + batch_size])

            def process_batch(batch: list[dict]) -> None:
                try:
                    batch_parts: list[str] = []
                    for sb in batch:
                        scenes_list = sb.get("scenes_list", []) or []
                        assets = sb.get('assets', [])
                        scene_name = ', '.join(scenes_list) if scenes_list else (assets[0] if assets else "")
                        chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'

                        lines = [
                            f"--- 分镜 #{sb['index']} ---",
                            f"原文: {sb.get('text', '')}",
                        ]
                        chars = sb.get("present_characters", [])
                        if chars:
                            lines.append(f"出场人物: {', '.join(chars)}")
                        lines.append(f"绑定场景资产: {scene_name}")
                        lines.append(f"已匹配人物/道具资产: {chars_and_props}")
                        batch_parts.append("\n".join(lines))
                    full_input = "\n\n".join(batch_parts)

                    prompt = (
                        "请为以下 %d 个分镜分别生成单页漫画画面描述，"
                        "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。"
                        "必须为每个分镜生成独立的 description。\n\n%s"
                    ) % (len(batch), full_input)

                    content = chat_caller(
                        COMIC_VIDEO_DESC_SYSTEM_PROMPT, prompt,
                        temperature=0.7, response_format={"type": "json_object"},
                    )
                    indices = [sb["index"] for sb in batch]
                    _logger.info("批次分镜 %s 描述AI返回: %s", indices, content[:300])

                    data = _parse_json(content)
                    desc_map: dict[int, str] = {}
                    if data:
                        items = data.get("storyboards", data) if isinstance(data, dict) else data
                        if isinstance(items, list):
                            used: set[int] = set()
                            for item in items:
                                if isinstance(item, dict):
                                    idx = item.get("index", 0)
                                    desc = item.get("description", "")
                                    if idx and desc and any(sb["index"] == idx for sb in batch):
                                        desc_map[idx] = desc
                                        used.add(idx)
                            for i, item in enumerate(items):
                                if isinstance(item, dict) and i < len(batch):
                                    idx = batch[i]["index"]
                                    desc = item.get("description", "")
                                    if desc and idx not in used:
                                        desc_map[idx] = desc

                    with lock:
                        for sb in batch:
                            idx = sb["index"]
                            desc = desc_map.get(idx, "")
                            results[idx] = {"index": idx, "description": desc}
                            sb["description"] = desc
                            done_ctr[0] += 1
                            self.progress.emit(idx, done_ctr[0], total, desc)
                except Exception as e:
                    indices_str = str([sb["index"] for sb in batch])
                    _logger.error("批次分镜 %s 描述生成异常: %s", indices_str, str(e), exc_info=True)
                    with lock:
                        errors.append(str(e))
                        for sb in batch:
                            sb["description"] = ""
                            results[sb["index"]] = {"index": sb["index"], "description": ""}
                            done_ctr[0] += 1
                            self.progress.emit(sb["index"], done_ctr[0], total, "")

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(process_batch, b) for b in batches]
                concurrent.futures.wait(futures)

            if errors:
                raise RuntimeError(f"{len(errors)}/{total} 个分镜生成失败: {errors[0]}")

            self.finished.emit(list(results.values()))

        except Exception as e:
            _logger.error("分镜描述生成失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))


class ComicVideoSingleDescWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        storyboard: dict,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        parent=None,
    ):
        super().__init__(parent)
        self._storyboard = storyboard
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service

    def run(self) -> None:
        try:
            from clip_synth.ui.pages.novel_comic_generate_page import _make_chat_caller, _parse_json
            from clip_synth.services.ai_service import AIModelConfig
            import logging as _lg
            _logger = _lg.getLogger("clip_synth.comic_video")

            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            sb = self._storyboard
            scenes_list = sb.get("scenes_list", []) or []
            assets = sb.get('assets', [])
            scene_name = ', '.join(scenes_list) if scenes_list else (assets[0] if assets else "")
            chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'

            lines = [f"原文: {sb.get('text', '')}"]
            chars = sb.get("present_characters", [])
            if chars:
                lines.append(f"出场人物: {', '.join(chars)}")
            lines.append(f"绑定场景资产: {scene_name}")
            lines.append(f"已匹配人物/道具资产: {chars_and_props}")
            full_input = "\n".join(lines)

            prompt = (
                "请为以下分镜生成单页漫画画面描述，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"{full_input}"
            )

            chat_caller = _make_chat_caller(config, None)
            content = chat_caller(
                COMIC_VIDEO_DESC_SYSTEM_PROMPT, prompt,
                temperature=0.7, response_format={"type": "json_object"},
            )
            _logger.info("单条分镜描述AI返回: %s", content[:200])

            data = _parse_json(content)
            desc = ""
            if data:
                if isinstance(data, dict) and "description" in data:
                    desc = data.get("description", "")
                else:
                    items = data.get("storyboards", data) if isinstance(data, dict) else data
                    if isinstance(items, list) and items and isinstance(items[0], dict):
                        desc = items[0].get("description", "")

            if desc:
                self._storyboard["description"] = desc

            self.finished.emit(desc)

        except Exception as e:
            _logger.error("单条描述生成失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))


class _ComicVideoDubModeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.selected_mode = ""
        self.setWindowTitle("选择配音方式")
        self.setFixedSize(480, 280)
        self.setObjectName("dubModeDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("选择配音方式")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(16)

        system_card = QPushButton()
        system_card.setObjectName("dubModeCard")
        system_card.setCursor(Qt.PointingHandCursor)
        system_card.setMinimumHeight(140)
        system_card.clicked.connect(lambda: self._select("system"))
        system_card_layout = QVBoxLayout(system_card)
        system_card_layout.setAlignment(Qt.AlignCenter)
        system_card_layout.setSpacing(8)
        icon1 = QLabel("\U0001f399")
        icon1.setAlignment(Qt.AlignCenter)
        icon1.setStyleSheet("font-size: 32px;")
        system_card_layout.addWidget(icon1)
        title1 = QLabel("系统配音")
        title1.setObjectName("dubModeCardTitle")
        title1.setAlignment(Qt.AlignCenter)
        system_card_layout.addWidget(title1)
        desc1 = QLabel("使用豆包TTS自动生成\n配音和字幕")
        desc1.setObjectName("dubModeCardDesc")
        desc1.setAlignment(Qt.AlignCenter)
        desc1.setWordWrap(True)
        system_card_layout.addWidget(desc1)
        cards_layout.addWidget(system_card)

        self_card = QPushButton()
        self_card.setObjectName("dubModeCard")
        self_card.setMinimumHeight(140)
        self_card.setEnabled(False)
        self_card_layout = QVBoxLayout(self_card)
        self_card_layout.setAlignment(Qt.AlignCenter)
        self_card_layout.setSpacing(8)
        icon2 = QLabel("\U0001f3b5")
        icon2.setAlignment(Qt.AlignCenter)
        icon2.setStyleSheet("font-size: 32px;")
        self_card_layout.addWidget(icon2)
        title2 = QLabel("自行配音")
        title2.setObjectName("dubModeCardTitle")
        title2.setAlignment(Qt.AlignCenter)
        self_card_layout.addWidget(title2)
        desc2 = QLabel("开发中")
        desc2.setObjectName("dubModeCardDesc")
        desc2.setAlignment(Qt.AlignCenter)
        desc2.setWordWrap(True)
        self_card_layout.addWidget(desc2)
        cards_layout.addWidget(self_card)

        layout.addLayout(cards_layout)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(lambda: self.reject())
        layout.addWidget(cancel_btn, alignment=Qt.AlignCenter)

    def _select(self, mode: str) -> None:
        self.selected_mode = mode
        self.accept()


class ComicVideoDubPage(QFrame):
    next_page = Signal(str, int, str)
    back_to_chapters = Signal()

    def __init__(
        self,
        project_id: str,
        episode_num: int,
        chapter_text: str,
        settings_service: SettingsService,
        state_service: NovelComicStateService | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._episode_num = episode_num
        self._chapter_text = chapter_text
        self._settings_service = settings_service
        self._state_service = state_service
        self._tts_worker: TTSWorker | None = None
        self._generated_audio_files: list | None = None
        self._dub_done = False
        self._dubbed_text: str = ""
        self.setObjectName("comicVideoDubPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("mixScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(scroll)

        content = QWidget()
        content.setObjectName("mixScrollContent")
        scroll.setWidget(content)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(10)

        title = QLabel("配音设置")
        title.setObjectName("wizardStepTitle")
        outer.addWidget(title)

        main_row = QHBoxLayout()
        main_row.setSpacing(20)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)

        text_header = QHBoxLayout()
        text_header.setSpacing(12)
        text_label = QLabel("文案内容")
        text_label.setObjectName("sectionTitle")
        text_header.addWidget(text_label)
        text_header.addStretch()
        self._import_btn = QPushButton("导入 TXT 文件")
        self._import_btn.setObjectName("importTxtBtn")
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn.clicked.connect(self._on_import_txt)
        text_header.addWidget(self._import_btn)
        left_panel.addLayout(text_header)

        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("novelMixTextEdit")
        self._text_edit.setPlaceholderText("在这里输入或粘贴小说/故事文本...")
        self._text_edit.setPlainText(self._chapter_text)
        left_panel.addWidget(self._text_edit, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self._char_count_label = QLabel("")
        self._char_count_label.setObjectName("charCountLabel")
        bottom_row.addWidget(self._char_count_label)

        bottom_row.addStretch()

        self._format_btn = QPushButton("格式化文本")
        self._format_btn.setObjectName("formatTextBtn")
        self._format_btn.setCursor(Qt.PointingHandCursor)
        self._format_btn.clicked.connect(self._on_format_text)
        bottom_row.addWidget(self._format_btn)

        left_panel.addLayout(bottom_row)

        self._text_edit.textChanged.connect(self._on_text_changed)
        self._on_text_changed()

        main_row.addLayout(left_panel, stretch=1)

        right_panel = QGroupBox("配音参数")
        right_panel.setObjectName("ttsEngineGroup")
        right_panel.setFixedWidth(340)
        engine_layout = QVBoxLayout(right_panel)
        engine_layout.setContentsMargins(16, 4, 16, 10)
        engine_layout.setSpacing(6)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(10)

        voice_layout = QVBoxLayout()
        voice_label = QLabel("音色选择")
        voice_label.setObjectName("paramLabel")
        voice_layout.addWidget(voice_label)
        self._voice_combo = QComboBox()
        self._voice_combo.setObjectName("voiceCombo")
        self._voice_combo.setMinimumHeight(32)
        for voice_id, voice_name in DOUBAO_VOICE_OPTIONS.items():
            self._voice_combo.addItem(voice_name, voice_id)
        self._voice_combo.setCurrentIndex(0)
        voice_layout.addWidget(self._voice_combo)
        combo_row.addLayout(voice_layout, stretch=1)

        emotion_layout = QVBoxLayout()
        emotion_label = QLabel("风格/情感")
        emotion_label.setObjectName("paramLabel")
        emotion_layout.addWidget(emotion_label)
        self._emotion_combo = QComboBox()
        self._emotion_combo.setObjectName("styleCombo")
        self._emotion_combo.setMinimumHeight(32)
        for emotion_id, emotion_name in DOUBAO_EMOTION_OPTIONS.items():
            self._emotion_combo.addItem(emotion_name, emotion_id)
        self._emotion_combo.setCurrentIndex(0)
        emotion_layout.addWidget(self._emotion_combo)
        combo_row.addLayout(emotion_layout, stretch=1)

        engine_layout.addLayout(combo_row)

        lang_layout = QVBoxLayout()
        lang_label = QLabel("语种")
        lang_label.setObjectName("paramLabel")
        lang_layout.addWidget(lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.setMinimumHeight(32)
        for lang_id, lang_name in DOUBAO_LANGUAGE_OPTIONS.items():
            self._lang_combo.addItem(lang_name, lang_id)
        self._lang_combo.setCurrentIndex(0)
        lang_layout.addWidget(self._lang_combo)
        engine_layout.addLayout(lang_layout)

        slider_row = QVBoxLayout()
        slider_row.setSpacing(6)

        self._speed_slider = self._create_slider_row("语速", 0.2, 3.0, 1.0, "rateSlider", slider_row)
        self._pitch_slider = self._create_slider_row("音调", 0.2, 3.0, 1.0, "pitchSlider", slider_row)
        self._volume_slider = self._create_slider_row("音量", 0.2, 3.0, 1.0, "volumeSlider", slider_row)

        engine_layout.addLayout(slider_row)

        engine_layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch()
        self._generate_btn = QPushButton("生成全部配音")
        self._generate_btn.setObjectName("generateAllBtn")
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.clicked.connect(self._on_generate_all)
        btn_row.addWidget(self._generate_btn)
        engine_layout.addLayout(btn_row)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("ttsProgressLabel")
        self._progress_label.setAlignment(Qt.AlignCenter)
        self._progress_label.hide()
        engine_layout.addWidget(self._progress_label)

        main_row.addWidget(right_panel)

        outer.addLayout(main_row, stretch=1)

        footer = QFrame()
        footer.setObjectName("wizardFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(32, 12, 32, 16)

        self._back_btn = QPushButton("\u2190 返回")
        self._back_btn.setObjectName("chapterBackBtn")
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.clicked.connect(self.back_to_chapters.emit)
        footer_layout.addWidget(self._back_btn)

        footer_layout.addStretch()

        self._next_btn = QPushButton("下一步 \u2192")
        self._next_btn.setObjectName("wizardNextBtn")
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(lambda: self.next_page.emit(self._project_id, self._episode_num, self._dubbed_text))
        footer_layout.addWidget(self._next_btn)

        outer.addWidget(footer)

    def _create_slider_row(self, label_text: str, min_val: float, max_val: float, default: float,
                           slider_name: str, parent_layout) -> QSlider:
        row = QHBoxLayout()
        row.setSpacing(8)

        label = QLabel(label_text)
        label.setObjectName("paramLabel")
        label.setMinimumWidth(40)
        row.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setObjectName(slider_name)
        slider.setRange(int(min_val * 10), int(max_val * 10))
        slider.setValue(int(default * 10))
        row.addWidget(slider, stretch=1)

        value_label = QLabel(f"{default:.1f}x")
        value_label.setObjectName("sliderValueLabel")
        value_label.setMinimumWidth(40)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(value_label)

        slider.valueChanged.connect(
            lambda v, lbl=value_label: lbl.setText(f"{v / 10.0:.1f}x")
        )

        parent_layout.addLayout(row)
        return slider

    def _on_text_changed(self) -> None:
        text = self._text_edit.toPlainText()
        char_count = len(text.replace("\n", "").replace("\r", "").replace(" ", ""))
        self._char_count_label.setText(f"已输入 {char_count} 字")

    def _on_import_txt(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入文本文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._text_edit.setPlainText(content)
            except Exception as e:
                logger.error("读取文本文件失败: %s", e)

    def _on_format_text(self) -> None:
        text = self._text_edit.toPlainText()
        if not text.strip():
            return

        text = re.sub(r'[\[\]「」""]', '', text)
        text = re.sub(r'[！？。!?.]', ',', text)
        text = re.sub(r',+', ',', text)
        text = re.sub(r'，+', ',', text)

        self._text_edit.setPlainText(text)

    def _on_generate_all(self) -> None:
        if self._tts_worker is not None and self._tts_worker.isRunning():
            return

        settings = self._settings_service.load()
        doubao_settings = settings.doubao_voice
        if not doubao_settings.is_configured:
            QMessageBox.warning(self, "提示", "请先在设置中配置豆包语音")
            return

        text = self._text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "没有文本内容可生成配音")
            return

        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "cache", "comic_video_dub")
        output_dir = os.path.abspath(output_dir)

        voice_type = self._voice_combo.currentData()
        emotion = self._emotion_combo.currentData()
        language = self._lang_combo.currentData()
        speed = self._speed_slider.value() / 10.0
        pitch = self._pitch_slider.value() / 10.0
        volume = self._volume_slider.value() / 10.0

        self._progress_label.setText("准备生成配音...")
        self._progress_label.show()
        self._generate_btn.setEnabled(False)

        self._tts_worker = TTSWorker(
            text=text,
            voice_type=voice_type,
            speed=speed,
            pitch=pitch,
            volume=volume,
            emotion=emotion,
            language=language,
            doubao_settings=doubao_settings,
            output_dir=output_dir,
        )
        self._tts_worker.progress.connect(self._on_tts_progress)
        self._tts_worker.tts_finished.connect(self._on_tts_finished)
        self._tts_worker.error.connect(self._on_tts_error)
        self._tts_worker.start()
        self._generated_audio_files = None

    def _on_tts_progress(self, msg: str) -> None:
        self._progress_label.setText(msg)

    def _on_tts_finished(self) -> None:
        self._progress_label.setText("配音生成完成")
        self._generate_btn.setEnabled(True)
        self._dub_done = True
        self._next_btn.setEnabled(True)
        if self._tts_worker:
            self._generated_audio_files = self._tts_worker.get_audio_files()
            text_parts = [af.get("text", "") for af in (self._generated_audio_files or []) if af.get("text", "").strip()]
            self._dubbed_text = "\n".join(text_parts)

        if self._state_service:
            project = self._state_service.load_project(self._project_id)
            if project:
                project.extra_data[f"comic_video_dub_done_ep{self._episode_num}"] = True
                project.extra_data[f"comic_video_dubbed_text_ep{self._episode_num}"] = self._dubbed_text
                self._state_service.save_project(project)

    def _on_tts_error(self, msg: str) -> None:
        self._progress_label.setText(f"错误: {msg}")
        self._generate_btn.setEnabled(True)


class ComicVideoAssetExtractWorker(QThread):
    finished = Signal(list, list, list)
    error = Signal(str)

    def __init__(
        self,
        dubbed_text: str,
        settings_service: SettingsService,
        chat_manager: MultiRoundChatManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._dubbed_text = dubbed_text
        self._settings_service = settings_service
        self._chat_manager = chat_manager

    def run(self) -> None:
        try:
            from clip_synth.ui.pages.novel_comic_generate_page import ASSET_EXTRACT_SYSTEM_PROMPT, _parse_json, _make_chat_caller
            import logging as _logging
            _logger = _logging.getLogger("clip_synth.comic_video")

            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            prompt = (
                "请分析以下配音文案文本，提取人物、场景、道具资产，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"配音文案：\n{self._dubbed_text}"
            )

            caller = _make_chat_caller(config, self._chat_manager)
            content = caller(
                ASSET_EXTRACT_SYSTEM_PROMPT, prompt,
                temperature=0.3, response_format={"type": "json_object"},
            )
            _logger.info("资产提取AI返回: %s", content[:300])

            data = _parse_json(content)

            characters = [
                {"name": c.get("name", ""), "desc": c.get("desc", "")}
                for c in data.get("characters", [])
            ] if isinstance(data, dict) else []
            scenes = [
                {"name": s.get("name", ""), "desc": s.get("desc", "")}
                for s in data.get("scenes", [])
            ] if isinstance(data, dict) else []
            props = [
                {"name": p.get("name", ""), "desc": p.get("desc", "")}
                for p in data.get("props", [])
            ] if isinstance(data, dict) else []

            self.finished.emit(characters, scenes, props)

        except Exception as e:
            _logger.error("资产提取失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))


class _ComicVideoSplitModeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("选择拆分模式")
        self.setFixedSize(420, 300)
        self.setObjectName("splitModeDialog")
        self._lines_per_storyboard: int = 2
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("分镜拆分方式")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        ai_radio = QRadioButton("AI智能分析")
        ai_radio.setObjectName("splitModeRadio")
        ai_radio.setEnabled(False)
        ai_desc = QLabel("开发中，敬请期待")
        ai_desc.setObjectName("dialogFieldLabel")
        ai_desc.setStyleSheet("color: #4a5568; font-size: 12px; padding-left: 24px;")
        layout.addWidget(ai_radio)
        layout.addWidget(ai_desc)

        manual_radio = QRadioButton("手动设置")
        manual_radio.setObjectName("splitModeRadio")
        manual_radio.setChecked(True)
        layout.addWidget(manual_radio)

        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(20, 0, 0, 0)
        manual_row.setSpacing(8)

        manual_hint = QLabel("合并规则：")
        manual_hint.setObjectName("dialogFieldLabel")
        manual_row.addWidget(manual_hint)

        self._lines_combo = QComboBox()
        self._lines_combo.setObjectName("splitModeCombo")
        self._lines_combo.addItems(["2句1行", "3句1行", "4句1行", "5句1行"])
        self._lines_combo.setFixedWidth(120)
        manual_row.addWidget(self._lines_combo)

        layout.addLayout(manual_row)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    @property
    def lines_per_storyboard(self) -> int:
        text = self._lines_combo.currentText()
        return {"2句1行": 2, "3句1行": 3, "4句1行": 4, "5句1行": 5}.get(text, 2)


class ComicVideoImagePage(QFrame):
    back_to_chapters = Signal()
    re_dub_requested = Signal(str, int)
    comic_image_generated = Signal(int, str)

    def __init__(
        self,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService | None = None,
        dubbed_text: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._dubbed_text = dubbed_text
        self._storyboards: list[dict] = []
        self._storyboard_cards: dict[int, "QFrame"] = {}
        self._match_worker: QThread | None = None
        self._desc_worker: QThread | None = None
        self._single_desc_worker: QThread | None = None
        self._gen_settings: dict = {}
        self._batch_comic_total = 0
        self._batch_comic_ctr: list[int] = [0]
        self._comic_poll_timer: QTimer | None = None
        self.setObjectName("comicVideoImagePage")
        self._load_storyboards()
        self._load_gen_settings()
        self._setup_ui()
        self.comic_image_generated.connect(self._on_comic_image_generated)

    def _storyboards_key(self) -> str:
        return f"comic_video_storyboards_ep{self._episode_num}"

    def _save_storyboards(self) -> None:
        if not self._state_service:
            return
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        project.extra_data[self._storyboards_key()] = self._storyboards
        self._state_service.save_project(project)

    def _load_storyboards(self) -> None:
        if not self._state_service:
            return
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        saved = project.extra_data.get(self._storyboards_key())
        if saved and isinstance(saved, list):
            self._storyboards = saved

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = self._build_toolbar()
        layout.addWidget(toolbar)

        action_bar = self._build_action_bar()
        layout.addWidget(action_bar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("comicGenScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll_area, stretch=1)

        content = QWidget()
        content.setObjectName("comicGenScrollContent")
        self._sb_layout = QVBoxLayout(content)
        self._sb_layout.setContentsMargins(24, 16, 24, 16)
        self._sb_layout.setSpacing(12)
        self._sb_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(content)

        self._refresh_storyboard_list()

    def _refresh_storyboard_list(self) -> None:
        if not hasattr(self, '_sb_layout'):
            return
        while self._sb_layout.count():
            item = self._sb_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
        self._storyboard_cards.clear()

        if not self._storyboards:
            empty_label = QLabel("暂无分镜数据，请点击「生成分镜」开始制作")
            empty_label.setObjectName("storyboardEmptyLabel")
            empty_label.setAlignment(Qt.AlignCenter)
            self._sb_layout.addWidget(empty_label)
            return

        from clip_synth.ui.pages.novel_comic_generate_page import _StoryboardCard

        for sb in self._storyboards:
            card = _StoryboardCard(sb)
            card.add_asset_clicked.connect(lambda idx: self._placeholder_clicked())
            card.remove_asset.connect(lambda name: self._placeholder_clicked())
            card.generate_image_clicked.connect(self._on_generate_image)
            card.history_clicked.connect(self._on_history_images)
            card.gen_desc_clicked.connect(self._on_gen_single_desc)
            card.desc_edit_requested.connect(self._on_edit_desc)
            card.text_edit_requested.connect(lambda idx, text: self._placeholder_clicked())
            card.preview_clicked.connect(self._on_preview_comic)
            card.delete_clicked.connect(self._on_delete_storyboard)
            self._storyboard_cards[sb["index"]] = card
            self._sb_layout.addWidget(card)

    def _refresh_single_card(self, storyboard_index: int) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card is None:
            return
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                card.update_data(sb)
                break

    def _on_delete_storyboard(self, storyboard_index: int) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _ConfirmDialog
        dlg = _ConfirmDialog(f"确定删除分镜 #{storyboard_index} 吗？\n删除后分镜序号将重新排列。", self)
        dlg.setWindowTitle("删除分镜")
        if dlg.exec() != QDialog.Accepted:
            return

        self._storyboards = [sb for sb in self._storyboards if sb["index"] != storyboard_index]
        for i, sb in enumerate(self._storyboards, start=1):
            old_index = sb["index"]
            sb["index"] = i
        self._save_storyboards()
        self._refresh_storyboard_list()

    def _on_desc_gen_menu(self) -> None:
        if not self._storyboards:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setObjectName("batchGenMenu")

        all_action = menu.addAction("全部生成")
        all_action.triggered.connect(lambda: self._on_generate_descriptions(False))

        missing_action = menu.addAction("仅缺失")
        missing_action.triggered.connect(lambda: self._on_generate_descriptions(True))

        pos = self._desc_gen_btn.mapToGlobal(self._desc_gen_btn.rect().bottomLeft())
        menu.exec(pos)

    def _on_generate_descriptions(self, missing_only: bool = False) -> None:
        if not self._storyboards:
            return

        target = [sb for sb in self._storyboards if not missing_only or not sb.get("description")]
        if not target:
            self._desc_status.setText("所有分镜已有描述，无需生成")
            self._desc_status.setStyleSheet("color: #f87171;")
            return

        self._desc_status.setText("生成中...")
        self._desc_status.setStyleSheet("color: #4fc3f7;")

        worker = ComicVideoDescWorker(
            target,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
        )
        self._desc_worker = worker
        worker.progress.connect(self._on_desc_progress)
        worker.finished.connect(self._on_desc_finished)
        worker.error.connect(self._on_desc_error)
        worker.start()

    def _on_desc_progress(self, storyboard_index: int, current: int, total: int, description: str) -> None:
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                sb["description"] = description
                break
        self._save_storyboards()
        self._refresh_single_card(storyboard_index)
        self._desc_status.setText(f"生成中... {current}/{total}")
        self._desc_status.setStyleSheet("color: #4fc3f7;")

    def _on_desc_finished(self, descriptions: list[dict]) -> None:
        self._desc_status.setText(f"生成完成，{len(self._storyboards)} 个分镜")
        self._desc_status.setStyleSheet("color: #4ade80;")
        self._save_storyboards()
        for sb in self._storyboards:
            self._refresh_single_card(sb["index"])

    def _on_desc_error(self, error_msg: str) -> None:
        self._desc_status.setText(f"生成失败: {error_msg}")
        self._desc_status.setStyleSheet("color: #f87171;")

    def _on_gen_single_desc(self, storyboard_index: int) -> None:
        sb = next((s for s in self._storyboards if s["index"] == storyboard_index), None)
        if not sb:
            return

        card = self._storyboard_cards.get(storyboard_index)
        if card and hasattr(card, 'set_desc_gen_status'):
            card.set_desc_gen_status("generating")

        worker = ComicVideoSingleDescWorker(
            sb,
            self._project_id, self._episode_num,
            self._settings_service,
        )
        worker.finished.connect(lambda desc: self._on_single_desc_finished(storyboard_index, desc))
        worker.error.connect(lambda msg: self._on_single_desc_error(storyboard_index, msg))
        self._single_desc_worker = worker
        worker.start()

    def _on_single_desc_finished(self, storyboard_index: int, description: str) -> None:
        self._save_storyboards()
        self._refresh_single_card(storyboard_index)

    def _on_single_desc_error(self, storyboard_index: int, error_msg: str) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card and hasattr(card, 'set_desc_gen_status'):
            card.set_desc_gen_status("error")
        self._desc_status.setText(f"分镜 #{storyboard_index} 描述生成失败: {error_msg}")
        self._desc_status.setStyleSheet("color: #f87171;")

    def _on_edit_desc(self, storyboard_index: int, desc: str) -> None:
        from PySide6.QtWidgets import QDialog as _QDialog, QVBoxLayout, QTextEdit, QHBoxLayout
        dialog = _QDialog(self.window())
        dialog.setWindowTitle(f"编辑分镜 #{storyboard_index} 描述")
        dialog.setFixedSize(600, 400)
        dialog.setObjectName("editDescDialog")
        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(24, 24, 24, 24)
        dlg_layout.setSpacing(16)
        text_edit = QTextEdit()
        text_edit.setObjectName("storyboardDescEdit")
        text_edit.setPlainText(desc)
        dlg_layout.addWidget(text_edit, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(confirm_btn)
        dlg_layout.addLayout(btn_row)
        if dialog.exec() == _QDialog.Accepted:
            new_desc = text_edit.toPlainText()
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["description"] = new_desc
                    break
            self._save_storyboards()
            self._refresh_single_card(storyboard_index)

    def _placeholder_clicked(self) -> None:
        QMessageBox.information(self, "提示", "该功能开发中，敬请期待！")

    def _load_gen_settings(self) -> None:
        if not self._state_service:
            return
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        self._gen_settings = project.extra_data.get("comic_video_gen_settings", {})

    def _save_gen_settings(self) -> None:
        if not self._state_service:
            return
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        project.extra_data["comic_video_gen_settings"] = self._gen_settings
        self._state_service.save_project(project)

    def _images_dir(self):
        if not self._state_service:
            return Path(".")
        images_dir = (
            self._state_service.get_project_images_dir(self._project_id)
            / f"comic_video_ep_{self._episode_num}"
        )
        images_dir.mkdir(parents=True, exist_ok=True)
        return images_dir

    def _build_comic_video_prompt(self, sb: dict, project, gen_settings: dict, page_num: int = 0):
        from clip_synth.ui.pages.novel_comic_generate_page import _get_effective_prefix, _image_size_from_settings

        desc = sb.get("description", "")
        asset_names = sb.get("assets", [])
        asset_descs: list[str] = []
        reference_paths: list[str] = []

        if project:
            for a in project.assets:
                if a.name in asset_names:
                    asset_descs.append(f"「{a.name}」: {a.desc}")
                    if a.image_path and Path(a.image_path).exists():
                        reference_paths.append(a.image_path)

        global_prefix = _get_effective_prefix(gen_settings)
        prompt = desc
        if global_prefix:
            prompt = global_prefix + "，分镜内容：" + prompt
        if asset_descs:
            prompt += "。参考资产形象：" + "；".join(asset_descs)

        ratio = gen_settings.get("aspect_ratio", "3:4")
        resolution = gen_settings.get("resolution", "1K")
        size = _image_size_from_settings(ratio, resolution)
        prompt += f"，{resolution}分辨率，图片比例{ratio}，尺寸{size}"
        if page_num:
            prompt += f"。请在画面底部居中位置用白色小字生成页码 {page_num}"
            prompt += f"。强制要求：文字清晰锐利，无模糊乱码；画面干净无噪点，主体完整无缺陷，画面中的字体加粗"

        return prompt, size, reference_paths

    def _on_gen_settings(self) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _GenerateSettingsDialog
        dialog = _GenerateSettingsDialog(self._gen_settings, self.window())
        if dialog.exec() == QDialog.Accepted:
            self._gen_settings = dialog.result
            self._save_gen_settings()

    def _on_generate_image(self, storyboard_index: int) -> None:
        sb = next((s for s in self._storyboards if s["index"] == storyboard_index), None)
        if sb is None:
            return

        if not sb.get("description"):
            QMessageBox.warning(self, "提示", "请先生成分镜描述")
            return

        settings = self._settings_service.load()
        from clip_synth.services.ai_service import AIModelConfig
        image_config = AIModelConfig(
            model_name=settings.image_model.model_name,
            api_key=settings.image_model.api_key,
            base_url=settings.image_model.base_url,
            api_type=settings.image_model.api_type,
            api_provider=settings.image_model.api_provider,
        )
        image_text_config = AIModelConfig(
            model_name=settings.text_model.model_name,
            api_key=settings.text_model.api_key,
            base_url=settings.text_model.base_url,
        )
        if not image_config.is_configured:
            QMessageBox.warning(self, "提示", "请先在系统配置中设置图片生成模型")
            return

        project = self._state_service.load_project(self._project_id)
        prompt, size, reference_paths = self._build_comic_video_prompt(sb, project, self._gen_settings, page_num=sb.get("index", 1))

        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_generating()

        review_mode = card.is_review_mode() if card else False
        if review_mode:
            reference_paths = []

        from clip_synth.services.image_gen_service import ImageGenService
        from clip_synth.ui.pages.novel_comic_generate_page import _make_comic_on_done, _make_comic_on_error

        batch_counter = [0, 0]
        ImageGenService.instance().submit(
            image_config, prompt,
            _make_comic_on_done(
                self._state_service, self._project_id, self._episode_num,
                storyboard_index, self.comic_image_generated, batch_counter, lambda: None,
            ),
            _make_comic_on_error(storyboard_index, self.comic_image_generated, batch_counter, lambda: None),
            size=size,
            reference_images=reference_paths if reference_paths else None,
            resolution=self._gen_settings.get("resolution"),
            aspect_ratio=self._gen_settings.get("aspect_ratio", "3:4"),
            text_model_config=image_text_config,
            prompt_rewrite=self._gen_settings.get("prompt_rewrite", True),
        )

    def _on_history_images(self, storyboard_index: int) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _HistoryImagesDialog
        dialog = _HistoryImagesDialog(
            self._state_service, self._project_id, self._episode_num,
            storyboard_index, self.window(),
        )
        if dialog.exec() == QDialog.Accepted and dialog.selected_path:
            selected = dialog.selected_path
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["generated_image"] = selected
                    break
            self._save_storyboards()
            self._refresh_single_card(storyboard_index)

    def _on_preview_comic(self, storyboard_index: int) -> None:
        sb = next((s for s in self._storyboards if s["index"] == storyboard_index), None)
        if not sb:
            return
        img = sb.get("generated_image", "")
        if img and Path(img).exists():
            from clip_synth.ui.widgets.image_viewer import show_image_viewer
            show_image_viewer(img, f"分镜 #{storyboard_index} 预览", self)

    def _on_export_comics(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return

        from clip_synth.ui.pages.novel_comic_generate_page import _ExportDialog
        dialog = _ExportDialog(len(self._storyboards), self.window())
        if dialog.exec() != QDialog.Accepted:
            return

        images_dir = self._images_dir()

        target_indices: set[int] = set()
        if dialog.is_all:
            target_indices = {sb["index"] for sb in self._storyboards}
        else:
            target_indices = set(range(dialog.start_index, dialog.end_index + 1))

        missing_indices: list[int] = []
        for idx in sorted(target_indices):
            sb = next((s for s in self._storyboards if s["index"] == idx), None)
            img = sb.get("generated_image", "") if sb else ""
            if img and Path(img).exists():
                continue
            base = images_dir / f"comic_panel_{idx}"
            matches = sorted(images_dir.glob(f"comic_panel_{idx}_*.png"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if not matches:
                missing_indices.append(idx)

        if missing_indices:
            names = "、".join(f"#{i}" for i in missing_indices)
            from clip_synth.ui.pages.novel_comic_generate_page import _ConfirmDialog
            dlg = _ConfirmDialog(
                f"以下分镜没有已生成的漫画图片：\n{names}\n\n请先生成图片再导出。",
                self,
            )
            dlg.setWindowTitle("缺少图片")
            dlg.exec()
            return

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        export_name = f"comic_video_ep{self._episode_num}_{timestamp}"
        export_dir = images_dir / export_name
        export_dir.mkdir(parents=True, exist_ok=True)

        exported = 0
        for idx in sorted(target_indices):
            sb = next((s for s in self._storyboards if s["index"] == idx), None)
            if not sb:
                continue
            img = sb.get("generated_image", "")
            if img and Path(img).exists():
                import shutil
                ext = Path(img).suffix
                shutil.copy2(img, str(export_dir / f"comic_panel_{idx}{ext}"))
                exported += 1
            else:
                base = images_dir / f"comic_panel_{idx}"
                matches = sorted(images_dir.glob(f"comic_panel_{idx}_*.png"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if matches:
                    import shutil
                    shutil.copy2(str(matches[0]), str(export_dir / f"comic_panel_{idx}.png"))
                    exported += 1

        self._desc_status.setText(f"导出完成，共 {exported} 张图片到 {export_name}")
        self._desc_status.setStyleSheet("color: #4ade80;")
        QMessageBox.information(self, "导出完成", f"共导出 {exported} 张图片到：\n{export_dir}")

    def _on_batch_generate_menu(self) -> None:
        if not self._storyboards:
            QMessageBox.warning(self, "提示", "请先生成分镜后再生成漫画图。")
            return

        no_desc = [sb["index"] for sb in self._storyboards if not sb.get("description")]
        if no_desc:
            QMessageBox.warning(
                self, "分镜描述缺失",
                f"以下 {len(no_desc)} 个分镜尚未生成描述：\n\n{', '.join(f'#{i}' for i in no_desc)}\n\n"
                f"请先生成分镜描述后再尝试。",
            )
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setObjectName("batchGenMenu")

        all_action = menu.addAction("全部生成（包含已有图片）")
        all_action.triggered.connect(self._on_batch_generate_all)

        missing_action = menu.addAction("仅生成缺失图片")
        missing_action.triggered.connect(self._on_batch_generate_missing)

        range_action = menu.addAction("指定区域")
        range_action.triggered.connect(self._on_batch_generate_range)

        pos = self._batch_btn.mapToGlobal(self._batch_btn.rect().bottomLeft())
        menu.exec(pos)

    def _on_batch_generate_all(self) -> None:
        self._run_batch_comic_gen(missing_only=False)

    def _on_batch_generate_missing(self) -> None:
        self._run_batch_comic_gen(missing_only=True)

    def _on_batch_generate_range(self) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _BatchRangeDialog
        dialog = _BatchRangeDialog(len(self._storyboards), self.window())
        if dialog.exec() == QDialog.Accepted:
            self._run_batch_comic_gen(missing_only=False, index_range=(dialog.start_index, dialog.end_index))

    def _run_batch_comic_gen(self, missing_only: bool, index_range: tuple[int, int] | None = None) -> None:
        project = self._state_service.load_project(self._project_id)

        from clip_synth.services.ai_service import AIModelConfig
        settings = self._settings_service.load()
        image_config = AIModelConfig(
            model_name=settings.image_model.model_name,
            api_key=settings.image_model.api_key,
            base_url=settings.image_model.base_url,
            api_type=settings.image_model.api_type,
            api_provider=settings.image_model.api_provider,
        )
        image_text_config = AIModelConfig(
            model_name=settings.text_model.model_name,
            api_key=settings.text_model.api_key,
            base_url=settings.text_model.base_url,
        )
        if not image_config.is_configured:
            QMessageBox.warning(self, "提示", "请先在系统配置中设置图片生成模型")
            return

        targets: list[dict] = []
        for sb in self._storyboards:
            if not sb.get("description"):
                continue
            if missing_only and sb.get("generated_image") and Path(sb["generated_image"]).exists():
                continue
            if index_range is not None and (sb["index"] < index_range[0] or sb["index"] > index_range[1]):
                continue
            targets.append(sb)

        if not targets:
            self._desc_status.setText("没有需要生成的漫画图")
            self._desc_status.setStyleSheet("color: #f87171;")
            return

        from clip_synth.services.image_gen_service import ImageGenService
        from clip_synth.ui.pages.novel_comic_generate_page import _make_comic_on_done, _make_comic_on_error, _comic_batch_counter

        gen_settings = self._gen_settings
        concurrency = gen_settings.get("concurrency", 3)
        ImageGenService.instance().set_concurrency(concurrency)

        count = len(targets)
        key = (self._project_id, self._episode_num)
        ctr = _comic_batch_counter.get(key)
        if ctr is None:
            ctr = [0, 0]
            _comic_batch_counter[key] = ctr
        ctr[0] += count
        self._batch_comic_total = ctr[0]
        self._batch_comic_ctr = ctr
        self._desc_status.setText(
            f"漫画队列: {ctr[0]} 张 | 并发: {concurrency} | 进行中: 0 | 等待: {ctr[0] - ctr[1]}"
        )
        self._desc_status.setStyleSheet("color: #4fc3f7;")

        if self._comic_poll_timer is None:
            self._comic_poll_timer = QTimer(self)
            self._comic_poll_timer.timeout.connect(self._update_comic_batch_status)
            self._comic_poll_timer.start(2000)

        for sb in targets:
            idx = sb["index"]
            card = self._storyboard_cards.get(idx)
            if card:
                card.set_generating()

            prompt, size, reference_paths = self._build_comic_video_prompt(sb, project, gen_settings, page_num=idx)

            review_mode = card.is_review_mode() if card else False
            if review_mode:
                batch_refs = None
            else:
                batch_refs = reference_paths if reference_paths else None

            ImageGenService.instance().submit(
                image_config, prompt,
                _make_comic_on_done(
                    self._state_service, self._project_id, self._episode_num,
                    idx, self.comic_image_generated, self._batch_comic_ctr,
                    self._update_comic_batch_status,
                ),
                _make_comic_on_error(
                    idx, self.comic_image_generated, self._batch_comic_ctr,
                    self._update_comic_batch_status,
                ),
                size=size,
                reference_images=batch_refs,
                resolution=gen_settings.get("resolution"),
                aspect_ratio=gen_settings.get("aspect_ratio", "3:4"),
                text_model_config=image_text_config,
                prompt_rewrite=gen_settings.get("prompt_rewrite", True),
            )

    def _update_comic_batch_status(self) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _comic_batch_counter
        if not self.isVisible():
            return
        key = (self._project_id, self._episode_num)
        ctr = _comic_batch_counter.get(key)
        if ctr is None:
            return
        from clip_synth.services.image_gen_service import ImageGenService
        total, done = ctr[0], ctr[1]
        pending = ImageGenService.instance().pending_count
        running = total - done - pending
        if done >= total and pending == 0:
            self._desc_status.setText(f"漫画批量生成完成！共 {total} 张")
            self._desc_status.setStyleSheet("color: #4ade80;")
            _comic_batch_counter.pop(key, None)
            if self._comic_poll_timer:
                self._comic_poll_timer.stop()
                self._comic_poll_timer = None
        else:
            self._desc_status.setText(
                f"漫画队列: {total} 张 | 已完成: {done} | 进行中: {running} | 等待: {pending}"
            )
            self._desc_status.setStyleSheet("color: #4fc3f7;")

    def _on_comic_image_generated(self, storyboard_index: int, image_path: str) -> None:
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                if image_path:
                    sb["generated_image"] = image_path
                break
        self._save_storyboards()
        self._refresh_single_card(storyboard_index)

    def _on_asset_management(self) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import _AssetManagementDialog
        dialog = _AssetManagementDialog(
            self._project_id, self._episode_num, "",
            self._state_service, self._settings_service,
            self.window(),
            dubbed_text=self._dubbed_text,
        )
        dialog.exec()

    def _on_generate_storyboard(self) -> None:
        dialog = _ComicVideoSplitModeDialog(self.window())
        if dialog.exec() != QDialog.Accepted:
            return

        text = self._dubbed_text.strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先生成配音")
            return

        import re as _re
        sentences = _re.split(r'(?<=[。！？\n])\s*', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            QMessageBox.warning(self, "提示", "配音文案中没有找到有效句子")
            return

        lines_per_sb = dialog.lines_per_storyboard
        groups: list[list[str]] = []
        for i in range(0, len(sentences), lines_per_sb):
            group = sentences[i:i + lines_per_sb]
            groups.append(group)

        storyboards: list[dict] = []
        for idx, group in enumerate(groups, start=1):
            cleaned = [s.rstrip("。！？，、；：，.!?,;:…~\n\r ") for s in group]
            merged_text = "。".join(cleaned).rstrip("。！？，、；：，.!?,;:…~")
            storyboards.append({
                "index": idx,
                "text": merged_text,
                "panel_count_suggestion": 2,
                "present_characters": [],
                "bubbles": [],
                "narrative": [],
                "summary": "",
                "description": "",
                "assets": [],
            })

        self._storyboards = storyboards
        self._save_storyboards()
        self._refresh_storyboard_list()
        self._split_status.setText(f"拆分完成，共 {len(storyboards)} 个分镜")
        self._split_status.setStyleSheet("color: #4ade80;")

    def _get_flattened_asset_names(self) -> dict:
        if not self._state_service:
            return {"characters": [], "scenes": [], "props": []}
        project = self._state_service.load_project(self._project_id)
        if not project:
            return {"characters": [], "scenes": [], "props": []}
        result: dict = {"characters": [], "scenes": [], "props": []}
        for a in project.assets:
            key = a.asset_type
            if key == "character":
                key = "characters"
            elif key == "scene":
                key = "scenes"
            elif key == "prop":
                key = "props"
            if key in result:
                result[key].append(a.name)
        return result

    def _on_match_assets(self) -> None:
        if not self._storyboards:
            return
        asset_names = self._get_flattened_asset_names()
        total = len(asset_names["characters"]) + len(asset_names["scenes"]) + len(asset_names["props"])
        if total == 0:
            self._match_status.setText("请先提取资产")
            self._match_status.setStyleSheet("color: #f87171;")
            return

        self._match_status.setText("匹配中...")
        self._match_status.setStyleSheet("color: #4fc3f7;")

        from clip_synth.ui.pages.novel_comic_generate_page import MatchAssetsWorker
        worker = MatchAssetsWorker(
            self._storyboards, asset_names,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
        )
        worker.finished.connect(self._on_match_finished)
        worker.error.connect(self._on_match_error)
        self._match_worker = worker
        worker.start()

    def _on_match_finished(self, assets_map: dict) -> None:
        for sb in self._storyboards:
            match = assets_map.get(sb["index"], {})
            parts = []
            scenes = match.get("scenes", [])
            scene = match.get("scene", "")
            scene_list: list[str] = []
            if isinstance(scenes, list) and scenes:
                scene_list = [s for s in scenes if s]
            elif scene:
                scene_list = [scene]
            for s in scene_list:
                parts.append(s)
            char_list = [name for name in match.get("characters", []) if name]
            for name in char_list:
                parts.append(name)
            for name in match.get("props", []):
                if name:
                    parts.append(name)
            sb["assets"] = parts
            sb["scenes_list"] = scene_list
        self._save_storyboards()
        self._refresh_storyboard_list()
        matched = sum(1 for sb in self._storyboards if sb["assets"])
        self._match_status.setText(f"匹配完成，{matched}/{len(self._storyboards)} 个分镜已匹配")
        self._match_status.setStyleSheet("color: #4ade80;")

    def _on_match_error(self, error_msg: str) -> None:
        self._match_status.setText(f"匹配失败: {error_msg}")
        self._match_status.setStyleSheet("color: #f87171;")

    def _build_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        back_btn = QPushButton("\u2190 返回章节列表")
        back_btn.setObjectName("chapterBackBtn")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back_to_chapters.emit)
        toolbar_layout.addWidget(back_btn)

        title_label = QLabel(f"漫画视频 - 第{self._episode_num}集")
        title_label.setObjectName("mixTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()

        re_dub_btn = QPushButton("\U0001f3b5  重新配音")
        re_dub_btn.setObjectName("chapterGenBtn")
        re_dub_btn.setCursor(Qt.PointingHandCursor)
        re_dub_btn.clicked.connect(self._on_re_dub)
        toolbar_layout.addWidget(re_dub_btn)

        return toolbar

    def _on_re_dub(self) -> None:
        if self._state_service:
            project = self._state_service.load_project(self._project_id)
            if project:
                cache_key = f"comic_video_dub_done_ep{self._episode_num}"
                text_key = f"comic_video_dubbed_text_ep{self._episode_num}"
                project.extra_data.pop(cache_key, None)
                project.extra_data.pop(text_key, None)
                self._state_service.save_project(project)
        self.re_dub_requested.emit(self._project_id, self._episode_num)

    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("comicGenActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(24, 10, 24, 10)
        bar_layout.setSpacing(12)

        asset_btn = QPushButton("\U0001f4e6  资产管理")
        asset_btn.setObjectName("comicGenActionBtn")
        asset_btn.setCursor(Qt.PointingHandCursor)
        asset_btn.clicked.connect(self._on_asset_management)
        bar_layout.addWidget(asset_btn)

        sb_gen_btn = QPushButton("\U0001f4dd  生成分镜")
        sb_gen_btn.setObjectName("comicGenActionBtn")
        sb_gen_btn.setCursor(Qt.PointingHandCursor)
        sb_gen_btn.clicked.connect(self._on_generate_storyboard)
        bar_layout.addWidget(sb_gen_btn)

        self._split_status = QLabel("")
        self._split_status.setObjectName("assetExtractStatus")
        bar_layout.addWidget(self._split_status)

        self._match_btn = QPushButton("\U0001f517  匹配资产")
        self._match_btn.setObjectName("comicGenActionBtn")
        self._match_btn.setCursor(Qt.PointingHandCursor)
        self._match_btn.clicked.connect(self._on_match_assets)
        bar_layout.addWidget(self._match_btn)

        self._match_status = QLabel("")
        self._match_status.setObjectName("assetExtractStatus")
        bar_layout.addWidget(self._match_status)

        self._desc_gen_btn = QPushButton("\U0001f4c4  生成分镜描述 \u25be")
        self._desc_gen_btn.setObjectName("comicGenActionBtn")
        self._desc_gen_btn.setCursor(Qt.PointingHandCursor)
        self._desc_gen_btn.clicked.connect(self._on_desc_gen_menu)
        bar_layout.addWidget(self._desc_gen_btn)

        self._desc_status = QLabel("")
        self._desc_status.setObjectName("assetExtractStatus")
        bar_layout.addWidget(self._desc_status)

        bar_layout.addStretch()

        self._batch_btn = QPushButton("\U0001f3a8  批量生成漫画 \u25be")
        self._batch_btn.setObjectName("comicGenBatchBtn")
        self._batch_btn.setCursor(Qt.PointingHandCursor)
        self._batch_btn.clicked.connect(self._on_batch_generate_menu)
        bar_layout.addWidget(self._batch_btn)

        self._gen_settings_btn = QPushButton("\u2699  生图设置")
        self._gen_settings_btn.setObjectName("comicGenActionBtn")
        self._gen_settings_btn.setCursor(Qt.PointingHandCursor)
        self._gen_settings_btn.clicked.connect(self._on_gen_settings)
        bar_layout.addWidget(self._gen_settings_btn)

        self._export_btn = QPushButton("\U0001f4e6  导出")
        self._export_btn.setObjectName("comicGenActionBtn")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export_comics)
        bar_layout.addWidget(self._export_btn)

        return bar
