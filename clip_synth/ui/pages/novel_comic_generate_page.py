import concurrent.futures
import json
import logging
import re
import struct
import threading
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QSize, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.ai_service import AIModelConfig, AIService, MultiRoundChatManager
from clip_synth.services.image_gen_service import ImageGenService
from clip_synth.services.novel_comic_state_service import NovelComicStateService
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.widgets.image_viewer import show_image_viewer

logger = logging.getLogger("clip_synth.novel_comic_generate")


def _strip_code_block(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        return m.group(1).strip()
    m = re.search(r"[\{\[][\s\S]*[\}\]]", t)
    if m:
        return m.group().strip()
    return t


def _parse_json(text: str) -> dict | list | None:
    if not text or not text.strip():
        logger.warning("_parse_json: 输入文本为空")
        return None
    t = text.strip()
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(t)
        return obj
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        logger.error("_parse_json: JSON解析失败 - %s", str(e))
        return None


_WINDOWS_EPOCH = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def _make_zip_ntfs_extra(unix_ts: float) -> bytes:
    ntfs_ts = int((unix_ts + _WINDOWS_EPOCH) * 10000000)
    data = struct.pack("<HH", 0x000a, 32)  # tag=NTFS(10), size=32
    data += struct.pack("<I", 0)  # reserved
    data += struct.pack("<HH", 1, 24)  # tag1=NTFS(1), size1=24
    data += struct.pack("<Q", ntfs_ts)  # mtime
    data += struct.pack("<Q", ntfs_ts)  # atime
    data += struct.pack("<Q", ntfs_ts)  # ctime
    return data


_running_workers: dict[tuple[str, int, str], QThread] = {}


def _make_chat_caller(
    config: AIModelConfig,
    chat_manager: MultiRoundChatManager | None,
) -> callable:
    svc = AIService(config)

    def _call(system_prompt: str, user_content: str, temperature: float = 0.3, response_format: dict | None = None) -> str:
        if chat_manager and chat_manager.is_multi_round_enabled(config):
            return svc.chat_completion_with_history(
                chat_manager, system_prompt, user_content,
                temperature=temperature,
                response_format=response_format,
            )

        logger.info("Using single-round chat (model: %s)", config.model_name)
        client = svc._ensure_client()
        is_gemini = config.model_name.lower().startswith("gemini")
        kwargs: dict = dict(
            model=config.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            timeout=900,
            response_format=response_format or {"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        if is_gemini:
            kwargs["extra_body"] = AIService._get_gemini_extra_body()
        response = client.chat.completions.create(**kwargs)

        return response.choices[0].message.content or ""

    return _call


def _worker_key(project_id: str, episode_num: int, task_type: str) -> tuple[str, int, str]:
    return (project_id, episode_num, task_type)


def _has_running_task(project_id: str, episode_num: int, task_type: str) -> bool:
    return _worker_key(project_id, episode_num, task_type) in _running_workers


_asset_batch_counter: dict[str, list[int]] = {}
_comic_batch_counter: dict[tuple[str, int], list[int]] = {}


ASSET_EXTRACT_SYSTEM_PROMPT = """\
你是一个专业的漫画分镜策划师，擅长从小说文本中提取漫画创作所需的资产信息。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "characters": [
    {"name": "角色名", "desc": "详细描述"}
  ],
  "scenes": [
    {"name": "场景名", "desc": "详细描述"}
  ],
  "props": [
    {"name": "道具名", "desc": "详细描述"}
  ]
}

资产分类说明：
1. 人物（characters）：文本中出现的所有具名角色或有明确身份的角色
2. 场景（scenes）：文本中描述的所有地点、环境、空间
3. 道具（props）：文本中出现的所有物品、装备、工具、武器等

要求：
1. 每个资产必须包含名称(name)和详细描述(desc)
2. 只提取文本中明确出现或强烈暗示的资产，不要凭空捏造
3. 如果某类资产不存在，返回空数组
4. 描述必须具体到可以用于 AI 生图的程度，不同类型有不同的描述重点：

   - 人物（characters）：描述性别、年龄、外貌（发型、脸型、五官特点、身材）、着装（衣服款式、颜色、材质）、气质/神态。正文未明确写到的部分，可根据上下文合理预测，不要编造离谱特征
   
   - 场景（scenes）：只描述环境本身——空间大小、建筑风格、光线氛围、色调、时间、天气、装饰、设施。可以写"街上偶有行人""有几盏路灯"这类背景元素，但绝对不能说具体角色（角色名、他、她等）在场景中做什么
   
   - 道具（props）：描述物品的外观、材质、颜色、尺寸、状态（新旧/破损/脏污等）。和场景一样，只描述物品本身，不能说角色在使用它

5. 描述中严禁使用双引号、单引号、破折号、省略号、书名号等标点符号，只能使用逗号、句号、感叹号、问号、顿号
6. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。"""


class AssetExtractWorker(QThread):
    finished = Signal(list, list, list)
    error = Signal(str)

    def __init__(
        self,
        chapter_text: str,
        settings_service: SettingsService,
        chat_manager: MultiRoundChatManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._chapter_text = chapter_text
        self._settings_service = settings_service
        self._chat_manager = chat_manager

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            prompt = (
                "请分析以下小说文本，提取人物、场景、道具资产，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"小说文本：\n{self._chapter_text}"
            )

            caller = _make_chat_caller(config, self._chat_manager)
            content = caller(
                ASSET_EXTRACT_SYSTEM_PROMPT, prompt,
                temperature=0.3, response_format={"type": "json_object"},
            )
            logger.info("资产提取AI返回: %s", content[:300])

            characters, scenes, props = self._parse_response(content)
            self.finished.emit(characters, scenes, props)

        except Exception as e:
            logger.error("资产提取失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))

    def _parse_response(self, content: str) -> tuple:
        data = _parse_json(content)
        characters = [
            {"name": c.get("name", ""), "desc": c.get("desc", "")}
            for c in data.get("characters", [])
        ]
        scenes = [
            {"name": s.get("name", ""), "desc": s.get("desc", "")}
            for s in data.get("scenes", [])
        ]
        props = [
            {"name": p.get("name", ""), "desc": p.get("desc", "")}
            for p in data.get("props", [])
        ]
        return characters, scenes, props


STORYBOARD_SEGMENT_SYSTEM_PROMPT = """\
你是一个专业的文本分段专家。请将以下小说文本按自然场景转换或情节节点切分成大片段。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 数组，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 [ 开始，到 ] 结束。

["第一段原文...", "第二段原文...", "第三段原文..."]

核心要求：
1. 按场景转换、时间流逝、情节推进的自然断点来切分
2. 每段约 1000-1500 字，原文较短则适当缩小段数和每段字数
3. 严格保留原文文字，不要做任何修改、润色、删减或添加
4. 所有片段按原文顺序排列，覆盖全文"""


STORYBOARD_SPLIT_SYSTEM_PROMPT = """\
你是一个专业的漫画分镜师，擅长将小说文本拆分为漫画单页的分镜。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "text": "第一页漫画的原文文字",
      "panel_count_suggestion": 2,
      "present_characters": ["张三", "李四"],
      "bubbles": [{"speaker": "张三", "text": "你终于来了。"}, {"speaker": "李四", "text": "没错。"}],
      "narrative": ["一阵阴风吹过，伴随着血腥味。", "两人对视，空气仿佛凝固。"]
    },
    {
      "text": "第二页漫画的原文文字",
      "panel_count_suggestion": 1,
      "present_characters": ["张三"],
      "bubbles": [{"speaker": "张三", "text": "这话是什么意思？"}],
      "narrative": ["张三独自站在月光下，影子拖得很长。"]
    }
  ]
}

核心要求：
1. text 为该分镜对应的原文片段，保留原文文字，不要做任何修改、润色、删减或添加，并且text 不能一次性超过150个汉字
2. 每个分镜对应一页漫画，一页漫画 1-3 格。一个格能表达清楚就只用 1 格（大画面），内容较多时用 2 格，复杂时才用 3-4 格。
3. panel_count_suggestion 是推荐格数，根据本页内容的节奏和复杂度给出合理建议（整数 1-4）
4. present_characters 列出本页出场的人物名称，不要遗漏
5. bubbles 列出本页中所有角色的对话，speaker 是说话人，text 是对话内容。text 字段中凡是对话部分都要提取到 bubbles 中
6. narrative 仅保留角色的内心独白、内心想法或主观看法（心理活动、内心感受、主观评价等），按顺序放入数组。其他所有的叙事描述（场景描写、环境交代、角色动作、面部表情等）**一律省略不放入 narrative**，以精简内容
7. 分镜之间要有清晰的叙事断点，如场景切换、视角转换、对话回合、动作节拍
8. 所有分镜按原文顺序排列，覆盖全文，不要遗漏原文内容
9. **字数限制**：每页气泡（bubbles 中所有 text 内容）和旁白（narrative 中所有字符串）的总字数不得超过 80 个汉字。如果原文段落较长，必须对原文进行浓缩概括，只保留最核心的信息（不得转写语言，原文是中文就要全中文，原文是英文就要全英文）
10. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符
11. 所有字段必须使用与原文一致的语言（原文是中文则用中文，英文则用英文），**严禁出现英文的人称代词(he/she/him/her等)或英文单词，中文原文必须全部用中文表达**
12. 旁白和对话的文字中必须移除无意义的标点符号（如破折号、省略号、书名号、单双引号等），只保留逗号、句号、感叹号、问号、顿号"""


def _save_project_storyboards(
    state_service: NovelComicStateService, project_id: str, episode_num: int, storyboards: list[dict],
) -> None:
    project = state_service.load_project(project_id)
    if not project:
        return
    project.extra_data[f"storyboards_ep{episode_num}"] = storyboards
    state_service.save_project(project)


class StoryboardSplitWorker(QThread):
    finished = Signal(list)
    progress = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        chapter_text: str,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
        chat_manager: MultiRoundChatManager | None = None,
    ):
        super().__init__()
        self._chapter_text = chapter_text
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager

    def _call_ai(self, config: AIModelConfig, system_prompt: str, user_text: str) -> str:
        caller = _make_chat_caller(config, self._chat_manager)
        return caller(system_prompt, user_text, temperature=0.3, response_format={"type": "json_object"})

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            # Phase 1: 按自然场景/情节节点切成 6-8 个片段
            self.progress.emit("正在分析章节结构...")
            logger.info("分镜拆分 Phase 1: 场景/情节分段")

            seg_prompt = (
                "请将以下小说文本按自然场景或情节节点切成 6-8 个片段，"
                "每个片段约 1000-1500 字。"
                f"\n\n小说文本：\n{self._chapter_text}"
            )

            seg_response = self._call_ai(config, STORYBOARD_SEGMENT_SYSTEM_PROMPT, seg_prompt)
            logger.info("分段AI返回: %s", seg_response[:300])

            segments = _parse_json(seg_response)
            if isinstance(segments, dict) and "storyboards" in segments:
                items = segments["storyboards"]
                segments = [s.get("text", "") for s in items if isinstance(s, dict) and s.get("text", "").strip()]
            if not isinstance(segments, list) or len(segments) == 0:
                raise ValueError("分镜拆分失败：AI返回的分段结果无效")
            segments = [s.strip() for s in segments if isinstance(s, str) and s.strip()]
            if len(segments) == 0:
                raise ValueError("分镜拆分失败：分段后没有有效内容")

            total_segments = len(segments)
            logger.info("分镜拆分 Phase 1 完成: %d 个片段", total_segments)

            seg_text_map = {i: s for i, s in enumerate(segments)}

            # Phase 2: 并发处理每个片段，生成分镜
            results_per_seg: dict[int, list[dict]] = {}
            errors_per_seg: dict[int, str] = {}
            done_ctr = [0]
            lock = threading.Lock()

            def process_segment(seg_idx: int, seg_text: str) -> None:
                last_error = ""
                for attempt in range(3):
                    try:
                        logger.info("分镜拆分 Phase 2: 片段 #%d (尝试 %d/3)", seg_idx + 1, attempt + 1)
                        sb_response = self._call_ai(
                            config,
                            STORYBOARD_SPLIT_SYSTEM_PROMPT,
                            f"请为以下小说片段生成分镜数据，将文本拆分为漫画单页分镜：\n\n{seg_text}",
                        )
                        logger.info("片段 #%d AI返回: %s", seg_idx + 1, sb_response[:300])

                        sb_data = _parse_json(sb_response)
                        if sb_data is None:
                            raise ValueError("JSON解析失败")

                        if isinstance(sb_data, dict) and "storyboards" in sb_data:
                            items = sb_data["storyboards"]
                        elif isinstance(sb_data, list):
                            items = sb_data
                        else:
                            raise ValueError("返回格式不正确")

                        if not isinstance(items, list) or len(items) == 0:
                            raise ValueError("返回的storyboards为空")

                        seg_items: list[dict] = []
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            text = item.get("text", "").strip()
                            if not text:
                                continue
                            seg_items.append({
                                "text": text,
                                "panel_count_suggestion": item.get("panel_count_suggestion", 1),
                                "present_characters": item.get("present_characters", []),
                                "bubbles": item.get("bubbles", []),
                                "narrative": item.get("narrative", []) if isinstance(item.get("narrative"), list) else ([item["narrative"]] if item.get("narrative", "") else []),
                                "summary": item.get("summary", ""),
                            })

                        if len(seg_items) == 0:
                            raise ValueError("片段解析后没有有效的分镜内容")

                        with lock:
                            results_per_seg[seg_idx] = seg_items
                            done_ctr[0] += 1
                            self.progress.emit(f"正在生成分镜 {done_ctr[0]}/{total_segments}...")
                        return

                    except (ValueError, json.JSONDecodeError) as e:
                        last_error = str(e)
                        logger.warning("片段 #%d 解析失败, 重试 %d/3: %s", seg_idx + 1, attempt + 1, last_error)
                        if attempt < 2:
                            continue
                        with lock:
                            errors_per_seg[seg_idx] = last_error
                            done_ctr[0] += 1
                            self.progress.emit(f"正在生成分镜 {done_ctr[0]}/{total_segments}...")
                        return

                    except Exception as e:
                        logger.error("片段 #%d 生成失败: %s", seg_idx + 1, str(e), exc_info=True)
                        with lock:
                            errors_per_seg[seg_idx] = str(e)
                            done_ctr[0] += 1
                            self.progress.emit(f"正在生成分镜 {done_ctr[0]}/{total_segments}...")
                        return

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = [
                    pool.submit(process_segment, seg_idx, seg_text)
                    for seg_idx, seg_text in enumerate(segments)
                ]
                concurrent.futures.wait(futures)

            if errors_per_seg:
                failed = sorted(errors_per_seg.keys())
                first = errors_per_seg[failed[0]]
                raise ValueError(f"分镜拆分失败：{len(errors_per_seg)}/{total_segments} 个片段处理失败（#{[i+1 for i in failed]}），首个错误: {first}")

            all_storyboards: list[dict] = []
            for seg_idx in sorted(results_per_seg.keys()):
                for item in results_per_seg[seg_idx]:
                    all_storyboards.append({
                        "index": len(all_storyboards) + 1,
                        "text": item["text"],
                        "panel_count_suggestion": item["panel_count_suggestion"],
                        "present_characters": item["present_characters"],
                        "bubbles": item["bubbles"],
                        "narrative": item["narrative"],
                        "summary": item["summary"],
                        "segment_text": seg_text_map.get(seg_idx, ""),
                        "description": "",
                        "assets": [],
                    })

            if len(all_storyboards) == 0:
                raise ValueError("分镜拆分失败：所有片段处理后没有有效分镜数据")

            logger.info("分镜拆分完成: %d 个分镜", len(all_storyboards))
            _save_project_storyboards(
                self._state_service, self._project_id, self._episode_num, all_storyboards,
            )
            self.finished.emit(all_storyboards)

        except Exception as e:
            logger.error("分镜拆分失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))


MATCH_ASSETS_SYSTEM_PROMPT = """\
你是一个专业的漫画分镜策划师，负责为每个分镜匹配所需的资产（人物、场景、道具）。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "scenes": ["场景A", "场景B"],
      "characters": ["人物A", "人物B"],
      "props": ["道具X"]
    }
  ]
}

你将收到：
1. 分镜列表，每个分镜包含编号、原文文本、出场人物列表、对话气泡、旁白叙述等详细信息
2. 资产池，分为人物(characters)、场景(scenes)、道具(props)三类，每项有名称

核心规则：
1. 每个分镜可以选择零个、一个或多个场景（scenes），根据分镜内容判断发生在哪些场景中。场景字段为数组，哪怕只有一个场景也要用数组格式
2. 每个分镜可以选择零个或多个人物（characters），优先参考分镜已有的 present_characters 字段
3. 每个分镜可以选择零个或多个道具（props），根据分镜的文本、旁白叙述判断使用了哪些道具
4. 只从提供的资产池中选择，不要编造不存在的资产名称。如果资产池中没有匹配的人物，则从 present_characters 中选择已有角色名
5. 根据分镜的原文文本、出场人物、对话气泡和旁白叙述综合判断该分镜发生在哪些场景、出现了哪些人物、使用了哪些道具
6. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。
7. **上下文延续规则**：道具分为两类——
   - **外观类（需延续）**：服装、配饰、帽子、眼镜、首饰、背包等影响角色外观的物品。一旦某个分镜中出现，后续没有切换场景/时间跳跃/或明确说明脱下/放下/换掉之前，必须一直保留在该角色的 props 中。
   - **使用类（不需延续）**：手机、证件、书本、工具、武器、食物等功能性物品。仅在原文明确提到使用时添加，提到放下/收起后移除，不自动延续到后续分镜。"""


def _get_effective_prefix(gen_settings: dict) -> str:
    prefix = gen_settings.get("prefix", "").strip()
    if prefix:
        return prefix
    return COMIC_STYLE_PRESETS[0]["prompt"]


class MatchAssetsWorker(QThread):
    finished = Signal(object)
    error = Signal(str)

    def __init__(
        self,
        storyboards: list[dict],
        asset_names: dict,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
        chat_manager: MultiRoundChatManager | None = None,
    ):
        super().__init__()
        self._storyboards = storyboards
        self._asset_names = asset_names
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            sb_texts = "\n\n".join(
                self._format_storyboard_for_match(sb)
                for sb in self._storyboards
            )
            characters = ", ".join(self._asset_names.get("characters", []))
            scenes = ", ".join(self._asset_names.get("scenes", []))
            props = ", ".join(self._asset_names.get("props", []))

            prompt = (
                "请为以下分镜匹配资产，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"=== 分镜列表 ===\n{sb_texts}\n\n"
                f"=== 资产池 ===\n"
                f"人物(characters): {characters if characters else '(无)'}\n"
                f"场景(scenes): {scenes if scenes else '(无)'}\n"
                f"道具(props): {props if props else '(无)'}"
            )

            caller = _make_chat_caller(config, self._chat_manager)
            content = caller(
                MATCH_ASSETS_SYSTEM_PROMPT, prompt,
                temperature=0.3, response_format={"type": "json_object"},
            )
            logger.info("资产匹配AI返回: %s", content[:300])

            assets_map = self._parse_response(content)
            self._apply_and_save(assets_map)
            self.finished.emit(assets_map)

        except Exception as e:
            logger.error("资产匹配失败: %s", str(e), exc_info=True)
            try:
                logger.info("资产匹配AI返回: %s", content[:300])
            except NameError:
                pass
            self.error.emit(str(e))

    @staticmethod
    def _format_storyboard_for_match(sb: dict) -> str:
        parts = [f"--- 分镜 #{sb['index']} ---"]
        parts.append(f"原文: {sb.get('text', '')[:200]}")
        chars = sb.get("present_characters", [])
        if chars:
            parts.append(f"出场人物: {', '.join(chars)}")
        bubbles = sb.get("bubbles", [])
        if bubbles:
            bubble_texts = "; ".join(
                f"{b.get('speaker', '')}:{b.get('text', '')}"
                for b in bubbles if isinstance(b, dict)
            )
            if bubble_texts:
                parts.append(f"对话: {bubble_texts}")
        narrative = sb.get("narrative", [])
        if narrative:
            if isinstance(narrative, list):
                parts.append(f"旁白: {' | '.join(narrative)}")
            elif narrative:
                parts.append(f"旁白: {narrative}")
        return "\n".join(parts)

    def _apply_and_save(self, assets_map: dict) -> None:
        for sb in self._storyboards:
            idx = sb["index"]
            if idx in assets_map:
                entry = assets_map[idx]
                parts = []
                if entry.get("scene"):
                    parts.append(entry["scene"])
                for c in entry.get("characters", []):
                    parts.append(c)
                for p in entry.get("props", []):
                    parts.append(p)
                sb["assets"] = parts
        _save_project_storyboards(
            self._state_service, self._project_id, self._episode_num, self._storyboards,
        )

    def _parse_response(self, content: str) -> dict:
        data = _parse_json(content)
        items = data.get("storyboards", data) if isinstance(data, dict) else data
        result = {}
        for item in items:
            idx = item.get("index", 0)
            result[idx] = {
                "scene": item.get("scene", ""),
                "characters": item.get("characters", []) or [],
                "props": item.get("props", []) or [],
            }
        return result


STORYBOARD_DESC_SYSTEM_PROMPT = """\
你是一个顶级的漫画分镜师和漫画编辑，擅长将小说片段转化为极致专业的漫画分镜脚本。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "description": "整体排版：...\\n\\n格1 (...)\\n- 景别/角度：...\\n- 场景：资产名称-版本\\n- 人物：角色名1, 角色名2\\n- 动作/表情：...\\n- 气泡：...\\n- 说明框：...\\n\\n格2 (...)\\n- 景别/角度：...\\n\\n格N (...)"
    },
    {
      "index": 2,
      "description": "..."
    }
  ]
}

传入的分镜数据会包含每个分镜的完整数据：原文(text)、旁白(narrative)、对话(bubbles)、出场人物(present_characters)、推荐格数(panel_count_suggestion)、所属段落原文(segment_text)、以及已绑定的场景资产和人物道具资产，**必须为每个传入的分镜生成对应的 description**。返回的 storyboards 数组必须包含所有传入分镜的条目，每个的描述独立生成，不要合并、不要跳过、不要遗漏。

你的任务是为每个分镜段落生成一页漫画的详细分镜描述。每个分镜可能包含多个格（panel），格数参考 panel_count_suggestion，但你可根据内容节奏灵活调整。

传入的数据可能包含原文(text)、对话(bubbles)、旁白(narrative)、出场人物(present_characters)、已绑定的场景资产和人物道具资产。如果 bubbles/narrative/present_characters 字段为空，请从原文(text)自行分析：识别对话内容放入气泡，识别角色的内心独白/主观看法放入说明框，其他叙事描写不放入说明框。

输出格式规范：

整体排版：
用一段话概述本页的版面布局方案。例如：采用左右对切、上下结构、三格阶梯式、出格效果等。说明格的数量、大致形状比例（横格/竖格/方格的宽窄高矮）和排列逻辑。

格N (形状描述，如：窄长横格 / 大方格 / 竖长格左半页 / 满版出血格 / 三小格并列 等)
- 景别/角度：全景/中景/近景/特写/大特写 等，仰视/俯视/平视/倾斜 等
- 场景：从分镜绑定的场景资产列表中选一个，直接写资产名称。不同格可选择不同场景
- 人物：直接列出角色名即可，例如"张三, 李四"，不要描述外观着装
- 动作/表情：角色的肢体动作和面部表情细节
- 气泡：每个气泡独立一行，格式：角色名的气泡："文字内容"。不要写气泡序号和气泡类型，不要加额外前缀。每个气泡文字控制在 1-2 行以内，长文本拆成多个气泡依次排列，例如：念念的气泡："好。"\n念念的气泡："妈，我答应你，明天我不去考试了。"\n十年后的妈妈的气泡："真的？"。气泡的语言必须与原文一致：原文是中文则用中文，原文是英文则用英文。
- 说明框：直接放置文字内容，不要标注角色名，格式：说明框："文字内容"。每段控制在 1-2 行以内，用原文原句。说明框的内容来自角色的内心独白、内心想法或主观看法，但**不需要标注是谁的**，直接呈现文字即可。除内心独白外，其他叙事描述（场景描写、环境交代、角色动作等）**不放入说明框**。如有传入 narrative 字段则优先使用，否则从原文自行判断。

说明框与气泡的分工铁律：
- 气泡负责一切对话。原文中的对话内容必须全部进入气泡。
- 说明框负责承载角色的内心独白与主观看法。如有传入 narrative 字段则从中提取，否则从原文自行判断。
- 动作/表情负责描述画师需要画的视觉内容（肢体动作、面部表情、画面构图），不承载任何文字。
- 同一句话**严禁同时出现在气泡和说明框中**，每句话唯一归属。

核心规则：
1. 一个分镜对应一页漫画，不要拆分到多个分镜描述
2. 格的数量：一个格能表现到位就大胆用 1 格（满版/出血大画面），内容稍多时用 2 格，只有在气泡数量+说明框数量超过 4 个以上时才考虑使用 3 格，超过 6 个以上才使用 4 格。在满足内容容量的前提下尽量用更少的格数来保证大画面表现力
3. 场景使用规则：每个分镜已绑定了场景资产列表，每个格可从列表中自由选择一个场景。可以在不同格使用不同场景
4. 人物和道具只能从已匹配的资产列表中选择，不要自行编造或添加未匹配的角色和物品
5. 每个格必须有明确的景别和角度
6. **唯一归属原则**：原文text中的每一句话**有且只有一个归属**——要么放入气泡，要么放入说明框，严禁同一句话同时出现在气泡和说明框中。对话进气泡，旁白/叙事/心理描述等进说明框。如果一个格全是对话可以只有气泡，全是旁白可以只有说明框。
7. 对话气泡和内心独白要标注气泡类型，每个气泡文字不超过 2 行，长文本拆成多个气泡依次排列，尽量减少气泡文字数量。你也可以不使用原文文案，但是意思表达出来就行。你也可以加一些声响词，比如 砰， 咚， 咔嚓， 之类的声响词
8. 描述语言要有画面感，让画师能直接照着画。优先用画面构图、人物微表情、肢体语言来传递情绪和叙事
9. 手机屏幕 / 电脑屏幕 / 平板 / 纸条 / 书本等媒介上显示的文字：这些不是气泡也不是说明框，而是画面内的视觉元素，应在动作/表情或场景描述中直接描述屏幕上的文字内容，例如：动作/表情：陆宴知手指微微收紧，手机屏幕冷光照亮指节，聊天界面上赫然显示谢依璇刚刚发送的信息：「今晚有空吗？」。严禁为此类媒介文字使用气泡或说明框
10. 气泡和说明框的文字内容必须使用中文双引号（""）包裹，除此之外的其他位置（场景描述、人物描述、动作表情等）严禁使用双引号、单引号、破折号、书名号等标点，只使用逗号、句号、感叹号、问号、顿号、冒号
11. 气泡和说明框内部的文字中，必须移除原文中的无意义标点（破折号、省略号、书名号、单双引号等），只保留逗号、句号、感叹号、问号、顿号
12. 输出合法 JSON：只输出一个 JSON 对象，不要输出任何其他内容。描述文本中出现的所有英文双引号（"）必须用反斜杠转义（\\"），中文双引号（""）无需转义。所有换行符必须用 \\n 表示，不得出现真正的换行符。JSON 对象内的 description 字符串本身可以包含 \\n 来表示换行。
13. **违禁内容处理**：生成描述时，必须自动检测动作/表情、气泡、说明框中的所有文字。如果发现任何可能涉及敏感、违规、不适的内容（包括但不限于血腥、暴力、色情、粗俗用语等），必须自动替换为合规的表达方式，或改为暗示/含蓄表达。例如"他裸露的身体"改为"他衣衫不整地"；"一刀砍下他的头"改为"刀光闪过"；"色情描写"改为"暧昧的氛围"。禁止直接输出任何可能被内容审核拦截的文字，确保所有描述都能通过安全审核。"""


class StoryboardDescriptionWorker(QThread):
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
        chat_manager: MultiRoundChatManager | None = None,
        single_batch: bool = False,
    ):
        super().__init__()
        self._storyboards = storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager
        self._single_batch = single_batch

    def run(self) -> None:
        try:
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

            chat_mgr = self._chat_manager
            chat_caller = _make_chat_caller(config, chat_mgr)

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
                        assets = sb.get('assets', [])
                        scene_name = assets[0] if assets else ""
                        chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'

                        lines = [
                            f"--- 分镜 #{sb['index']} ---",
                            f"原文: {sb.get('text', '')}",
                        ]
                        conversation = sb.get("bubbles", [])
                        if conversation:
                            cb = "; ".join(
                                f"{b.get('speaker','')}: {b.get('text','')}"
                                for b in conversation if isinstance(b, dict)
                            )
                            lines.append(f"对话: {cb}")
                        narrative = sb.get("narrative", [])
                        if narrative:
                            if isinstance(narrative, list):
                                lines.append(f"旁白: {' | '.join(narrative)}")
                            elif narrative:
                                lines.append(f"旁白: {narrative}")
                        chars = sb.get("present_characters", [])
                        if chars:
                            lines.append(f"出场人物: {', '.join(chars)}")
                        lines.append(f"建议格数: {sb.get('panel_count_suggestion', 1)}")
                        lines.append(f"绑定场景资产: {scene_name}")
                        lines.append(f"已匹配人物/道具资产: {chars_and_props}")
                        seg_text = sb.get("segment_text", "")
                        if seg_text:
                            lines.append(f"所属段落原文: {seg_text[:500]}")
                        batch_parts.append("\n".join(lines))
                    full_input = "\n\n".join(batch_parts)

                    prompt = (
                        "请为以下 %d 个分镜分别生成详细的一页漫画分镜描述，"
                        "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。"
                        "必须为每个分镜生成独立的 description。\n\n%s"
                    ) % (len(batch), full_input)

                    content = chat_caller(
                        STORYBOARD_DESC_SYSTEM_PROMPT, prompt,
                        temperature=0.7, response_format={"type": "json_object"},
                    )
                    indices = [sb["index"] for sb in batch]
                    logger.info("批次分镜 %s 描述AI返回: %s", indices, content[:300])

                    desc_map = self._parse_batch(content, batch)
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
                    logger.error("批次分镜 %s 描述生成异常: %s", indices_str, str(e), exc_info=True)
                    try:
                        logger.info("批次分镜 %s 描述AI返回: %s", indices_str, content[:200])
                    except NameError:
                        pass
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
            logger.error("分镜描述生成失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))

    def _parse_batch(self, content: str, batch: list[dict]) -> dict[int, str]:
        data = _parse_json(content)
        
        if data is None:
            logger.warning("批次分镜描述解析失败：AI返回的JSON数据为空或无效")
            return {}
        
        if not isinstance(data, (dict, list)):
            logger.warning(f"批次分镜描述解析失败：AI返回的数据格式错误，期望dict或list，实际为{type(data).__name__}")
            return {}
        
        items = data.get("storyboards", data) if isinstance(data, dict) else data
        
        if isinstance(items, dict):
            items = [items]
        
        if not isinstance(items, list):
            logger.warning(f"批次分镜描述解析失败：storyboards不是列表，实际为{type(items).__name__}")
            return {}
        
        if len(items) == 0:
            logger.warning("批次分镜描述解析失败：AI返回的分镜列表为空")
            return {}
        
        result: dict[int, str] = {}
        used: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                logger.warning("分镜项不是字典类型，跳过")
                continue
            idx = item.get("index", 0)
            desc = item.get("description", "")
            if idx and desc and any(sb["index"] == idx for sb in batch):
                result[idx] = desc
                used.add(idx)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if i < len(batch):
                idx = batch[i]["index"]
                desc = item.get("description", "")
                if desc and idx not in used:
                    result[idx] = desc
        return result


class SingleDescWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        storyboard: dict,
        all_storyboards: list[dict],
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
        chat_manager: MultiRoundChatManager | None = None,
    ):
        super().__init__()
        self._storyboard = storyboard
        self._all_storyboards = all_storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            chat_caller = _make_chat_caller(config, self._chat_manager)

            context_parts: list[str] = []
            for s in sorted(self._all_storyboards, key=lambda x: x.get("index", 0)):
                tag = ">>> 当前要生成的分镜 <<<" if s["index"] == self._storyboard["index"] else ""
                s_lines = [f"--- 分镜 #{s['index']} {tag}---"]
                if s["index"] == self._storyboard["index"]:
                    s_lines.append(f"原文: {s.get('text', '')}")
                    bubbles = s.get("bubbles", [])
                    if bubbles:
                        cb = "; ".join(f"{b.get('speaker','')}: {b.get('text','')}" for b in bubbles if isinstance(b, dict))
                        s_lines.append(f"对话: {cb}")
                    narrative = s.get("narrative", [])
                    if narrative and isinstance(narrative, list):
                        s_lines.append(f"旁白: {' | '.join(narrative)}")
                    elif narrative:
                        s_lines.append(f"旁白: {narrative}")
                    chars = s.get("present_characters", [])
                    if chars:
                        s_lines.append(f"出场人物: {', '.join(chars)}")
                    s_lines.append(f"建议格数: {s.get('panel_count_suggestion', 1)}")
                    seg_text = s.get("segment_text", "")
                    if seg_text:
                        s_lines.append(f"所属段落原文: {seg_text[:500]}")
                else:
                    s_lines.append(f"摘要: {s.get('summary', '') or s.get('text', '')[:100]}")
                context_parts.append("\n".join(s_lines))
            full_context = "\n\n".join(context_parts)

            sb = self._storyboard
            assets = sb.get('assets', [])
            scene_name = assets[0] if assets else ""
            chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'
            prompt = (
                "以下是一页漫画的全部分镜摘要，请为标记为「当前要生成的分镜」的那个分镜生成详细的一页漫画分镜描述，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。"
                "你可以参考前后分镜的上下文来理解叙事节奏和人物状态。\n\n"
                f"{full_context}\n\n"
                f"--- 当前分镜额外信息 ---\n"
                f"绑定场景: {scene_name}\n"
                f"已匹配人物/道具: {chars_and_props}"
            )

            content = chat_caller(
                STORYBOARD_DESC_SYSTEM_PROMPT, prompt,
                temperature=0.7, response_format={"type": "json_object"},
            )
            logger.info("分镜 #%d 单条描述AI返回: %s", sb['index'], content[:200])

            data = _parse_json(content)
            
            if data is None:
                logger.warning(f"分镜 #%d 描述解析失败：AI返回的JSON数据为空或无效", sb['index'])
                desc = ""
            elif isinstance(data, dict) and "description" in data:
                desc = data["description"]
                if not isinstance(desc, str):
                    logger.warning(f"分镜 #%d 描述解析失败：description不是字符串类型", sb['index'])
                    desc = ""
            else:
                items = data.get("storyboards", data) if isinstance(data, dict) else data
                if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                    desc = items[0].get("description", "")
                    if not isinstance(desc, str):
                        logger.warning(f"分镜 #%d 描述解析失败：description不是字符串类型", sb['index'])
                        desc = ""
                else:
                    logger.warning(f"分镜 #%d 描述解析失败：数据格式不正确", sb['index'])
                    desc = ""

            if desc.strip():
                sb["description"] = desc
                project = self._state_service.load_project(self._project_id)
                if project:
                    stored = project.extra_data.get(f"storyboards_ep{self._episode_num}")
                    if isinstance(stored, list):
                        for s in stored:
                            if s.get("index") == sb["index"]:
                                s["description"] = desc
                                break
                        project.extra_data[f"storyboards_ep{self._episode_num}"] = stored
                        self._state_service.save_project(project)
            else:
                logger.warning(f"分镜 #%d 描述为空，不保存", sb['index'])

            self.finished.emit(desc)

        except Exception as e:
            logger.error("单条描述生成失败: %s", str(e), exc_info=True)
            try:
                logger.info("分镜 #%d 单条描述AI返回: %s", sb['index'], content[:200])
            except NameError:
                pass
            self.error.emit(str(e))


def _image_size_from_settings(ratio: str, resolution: str) -> str:
    factors = {"1K": 1, "2K": 2, "4K": 4}
    factor = factors.get(resolution, 1)
    if ratio == "9:16":
        w = 576 * factor
        h = 1024 * factor
    else:
        w = 768 * factor
        h = 1024 * factor
    return f"{w}x{h}"


def _square_size_from_resolution(resolution: str) -> str:
    factors = {"1K": 1024, "2K": 2048, "4K": 4096}
    return f"{factors.get(resolution, 1024)}x{factors.get(resolution, 1024)}"


def _make_comic_on_done(
    state_service: NovelComicStateService,
    project_id: str,
    episode_num: int,
    storyboard_index: int,
    signal: Signal,
    batch_counter: list[int],
    update_cb,
) -> object:
    def on_done(_task_id: int, image_data: bytes) -> None:
        try:
            project = state_service.load_project(project_id)
            images_dir = state_service.get_project_images_dir(project_id)
            images_dir = images_dir / f"ep_{episode_num}"
            images_dir.mkdir(parents=True, exist_ok=True)
            file_path = str(images_dir / f"comic_panel_{storyboard_index}_{int(time.time())}.png")
            Path(file_path).write_bytes(image_data)
            logger.info("分镜图片保存成功 #%d: %s (%d bytes)", storyboard_index, file_path, len(image_data))
            if project:
                key = f"storyboards_ep{episode_num}"
                stored = project.extra_data.get(key)
                if isinstance(stored, list):
                    for s in stored:
                        if s.get("index") == storyboard_index:
                            s["generated_image"] = file_path
                            break
                    project.extra_data[key] = stored
                    state_service.save_project(project)
            try:
                signal.emit(storyboard_index, file_path)
            except RuntimeError:
                pass
        except Exception as e:
            logger.error("保存分镜图片失败: %s", e, exc_info=True)
            try:
                signal.emit(storyboard_index, "")
            except RuntimeError:
                pass
        finally:
            batch_counter[1] += 1
            update_cb()
    return on_done


def _make_comic_on_error(
    storyboard_index: int,
    signal: Signal,
    batch_counter: list[int],
    update_cb,
) -> object:
    def on_error(_task_id: int, error_msg: str) -> None:
        logger.error("分镜图片生成失败 [#%d]: %s", storyboard_index, error_msg)
        try:
            signal.emit(storyboard_index, "")
        except RuntimeError:
            pass
        batch_counter[1] += 1
        update_cb()
    return on_error


def _save_asset_image_to_project(
    state_service: NovelComicStateService,
    project_id: str,
    episode_num: int,
    asset_name: str,
    file_path: str,
) -> None:
    project = state_service.load_project(project_id)
    if not project:
        return
    ep_key = f"ep_assets_{episode_num}"
    ep_data = project.extra_data.get(ep_key, [])
    found = False
    for item in ep_data:
        if item.get("name") == asset_name:
            item["image_path"] = file_path
            found = True
            break
    if found:
        project.extra_data[ep_key] = ep_data
        from clip_synth.models.novel_comic_project_state import NovelComicAsset
        seen: set[str] = set()
        merged: list = []
        for ep in range(1, episode_num + 1):
            for a in project.extra_data.get(f"ep_assets_{ep}", []):
                n = a.get("name", "")
                if n and n not in seen:
                    seen.add(n)
                    merged.append(NovelComicAsset(
                        name=n, desc=a.get("desc", ""),
                        asset_type=a.get("asset_type", "prop"),
                        image_path=a.get("image_path", ""),
                    ))
        project.assets = merged
        state_service.save_project(project)


def _make_asset_on_done(
    state_service: NovelComicStateService,
    project_id: str,
    episode_num: int,
    name: str,
    signal,
) -> object:
    def on_done(_task_id: int, image_data: bytes) -> None:
        try:
            images_dir = state_service.get_project_images_dir(project_id)
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
            file_path = str(images_dir / f"asset_{safe_name}.png")
            Path(file_path).write_bytes(image_data)
            _save_asset_image_to_project(state_service, project_id, episode_num, name, file_path)
            try:
                signal.emit(name, file_path)
            except RuntimeError:
                pass
        except Exception as e:
            logger.error("保存资产图片失败: %s", e, exc_info=True)
            try:
                signal.emit(name, "")
            except RuntimeError:
                pass
        finally:
            ctr = _asset_batch_counter.get(project_id)
            if ctr:
                ctr[1] += 1
    return on_done


def _make_asset_on_error(
    project_id: str,
    name: str,
    signal,
) -> object:
    def on_error(_task_id: int, error_msg: str) -> None:
        logger.error("资产图片生成失败 [%s]: %s", name, error_msg)
        try:
            signal.emit(name, "")
        except RuntimeError:
            pass
        ctr = _asset_batch_counter.get(project_id)
        if ctr:
            ctr[1] += 1
    return on_error


COMIC_STYLE_PRESETS = [
    {
        "name": "韩式条漫风格-清新少女风",
        "prompt": (
            "韩式条漫风格, 清新少女风，色彩明亮柔和，线条流畅。多用于校园、纯爱等题材。"
        ),
    },
    {
        "name": "韩式条漫风格-写实厚涂风",
        "prompt": (
            "韩式条漫风格, 写实厚涂风，接近真实人体比例，强调肌肉、骨骼、光影和衣物质感，色彩厚重，视觉冲击力强。"
        ),
    },
    {
        "name": "韩式条漫风格-西幻华丽风",
        "prompt": (
            "韩式条漫风格, 西幻华丽风，极尽奢华，细节丰富。"
        ),
    },
    {
        "name": "国漫仙侠风格1",
        "prompt": (
            "国漫仙侠风格，用3D模型还原工笔画般的精绘线条感。布料纹理贴图直接画出衣褶与云纹，边缘光清晰；头发是一条条飘带模型，动画感强。场景山石带描边，云雾是分层半透明片，整体像活过来的国风插画。"
        ),
    },
    {
        "name": "国漫仙侠风格2",
        "prompt": (
            "国漫仙侠风格，PBR厚涂写实仙侠风，追求真实物理光影，皮肤有半透明散射，纱衣有丝绸高光与菲涅尔反射。场景使用大气体积雾，通过光线穿透云层、剑刃高光反射来营造层次。毛孔与刺绣纹理清晰，像电影预演或S级游戏CG。"
        ),
    },
    {
        "name": "日式少年漫画风",
        "prompt": (
            "日式漫画风格, 少年漫画风，线条肯定有力，动态感强，注重“跃动感”和速度线。角色眼睛大而有神，身体结构概括，背景常使用集中线来烘托气势"
        ),
    },
    {
        "name": "日式少女漫画风",
        "prompt": (
            "日式漫画风格, 少女漫画风，画面唯美、装饰性强，强调氛围。标志性的“星光眼”（瞳孔里有大量高光和星星），纤细的睫毛，身材修长。擅长使用花卉、网点纸和流线型的头发来渲染情绪。"
        ),
    },
    {
        "name": "超级英雄漫画",
        "prompt": (
            "美式漫画风格，超级英雄漫画风，角色眼睛大而有神，身体结构概括，背景常使用集中线来烘托气势。"
        ),
    },
    {
        "name": "写实厚涂",
        "prompt": (
            "semi-realistic digital painting, detailed textures, cinematic lighting, "
            "soft brush blending, atmospheric depth of field, rich shadows and highlights, "
            "concept art quality, highly detailed"
        ),
    },
    {
        "name": "水墨国风",
        "prompt": (
            "Chinese ink wash painting style, sumi-e brush strokes, "
            "elegant flowing lines, misty atmosphere, traditional Chinese aesthetic, "
            "soft watercolor washes, poetic composition, rice paper texture"
        ),
    },
    {
        "name": "赛博朋克",
        "prompt": (
            "cyberpunk style, neon lights, dark rainy streets, holographic displays, "
            "chrome and metal surfaces, high-tech low-life atmosphere, "
            "purple and cyan color palette, futuristic dystopian cityscape"
        ),
    },
    {
        "name": "Q版可爱",
        "prompt": (
            "chibi style, super deformed, cute and playful, "
            "large head small body, bright pastel colors, simple clean lines, "
            "kawaii aesthetic, cheerful expressions, rounded shapes"
        ),
    },
    {
        "name": "暗黑恐怖",
        "prompt": (
            "dark horror comic style, heavy shadows, disturbing atmosphere, "
            "muted desaturated colors, gritty textures, psychological thriller aesthetic, "
            "low key lighting, stark contrast black and white with splashes of red"
        ),
    },
]


class _SplitModeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("选择拆分模式")
        self.setFixedSize(420, 280)
        self.setObjectName("splitModeDialog")
        self._mode: str = "ai"
        self._lines_per_storyboard: int = 2
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("分镜拆分方式")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self._ai_radio = QRadioButton("AI智能分析")
        self._ai_radio.setObjectName("splitModeRadio")
        self._ai_radio.setChecked(True)
        self._ai_radio.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._ai_radio)

        ai_desc = QLabel("AI自动分析章节结构，按场景/情节智能拆分分镜")
        ai_desc.setObjectName("dialogFieldLabel")
        ai_desc.setStyleSheet("color: #94a3b8; font-size: 12px; padding-left: 24px;")
        layout.addWidget(ai_desc)

        self._manual_radio = QRadioButton("手动设置")
        self._manual_radio.setObjectName("splitModeRadio")
        self._manual_radio.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._manual_radio)

        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(24, 0, 0, 0)
        manual_row.setSpacing(8)

        manual_hint = QLabel("合并规则：")
        manual_hint.setObjectName("dialogFieldLabel")
        manual_row.addWidget(manual_hint)

        self._lines_combo = QComboBox()
        self._lines_combo.setObjectName("splitModeCombo")
        self._lines_combo.addItems(["2句1行", "3句1行", "4句1行", "5句1行"])
        self._lines_combo.setFixedWidth(120)
        self._lines_combo.setEnabled(False)
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

    def _on_mode_changed(self) -> None:
        is_manual = self._manual_radio.isChecked()
        self._lines_combo.setEnabled(is_manual)

    @property
    def mode(self) -> str:
        return "manual" if self._manual_radio.isChecked() else "ai"

    @property
    def lines_per_storyboard(self) -> int:
        text = self._lines_combo.currentText()
        return {"2句1行": 2, "3句1行": 3, "4句1行": 4, "5句1行": 5}.get(text, 2)


class _GenerateSettingsDialog(QDialog):
    def __init__(self, current_settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self.setWindowTitle("生图设置")
        self.setFixedSize(560, 700)
        self.setObjectName("genSettingsDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("生图设置")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        concurrency_label = QLabel("生图并发数 (1-10)")
        concurrency_label.setObjectName("dialogFieldLabel")
        layout.addWidget(concurrency_label)

        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setObjectName("genSettingsSpin")
        self._concurrency_spin.setMinimum(1)
        self._concurrency_spin.setMaximum(10)
        self._concurrency_spin.setValue(self._settings.get("concurrency", 3))
        self._concurrency_spin.setFixedHeight(36)
        layout.addWidget(self._concurrency_spin)

        style_label = QLabel("漫画风格")
        style_label.setObjectName("dialogFieldLabel")
        layout.addWidget(style_label)

        self._style_combo = QComboBox()
        self._style_combo.setObjectName("genSettingsCombo")
        for preset in COMIC_STYLE_PRESETS:
            self._style_combo.addItem(preset["name"])
        self._style_combo.addItem("自定义画风")
        self._style_combo.setFixedHeight(36)
        saved_style = self._settings.get("style", COMIC_STYLE_PRESETS[0]["name"])
        idx = self._style_combo.findText(saved_style)
        if idx >= 0:
            self._style_combo.blockSignals(True)
            self._style_combo.setCurrentIndex(idx)
            self._style_combo.blockSignals(False)
        elif saved_style == "自定义画风":
            self._style_combo.blockSignals(True)
            self._style_combo.setCurrentIndex(self._style_combo.count() - 1)
            self._style_combo.blockSignals(False)
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)
        layout.addWidget(self._style_combo)

        ratio_label = QLabel("图片比例")
        ratio_label.setObjectName("dialogFieldLabel")
        layout.addWidget(ratio_label)

        self._ratio_combo = QComboBox()
        self._ratio_combo.setObjectName("genSettingsCombo")
        self._ratio_combo.addItems(["3:4", "9:16"])
        self._ratio_combo.setFixedHeight(36)
        saved_ratio = self._settings.get("aspect_ratio", "3:4")
        idx2 = self._ratio_combo.findText(saved_ratio)
        self._ratio_combo.setCurrentIndex(idx2 if idx2 >= 0 else 0)
        layout.addWidget(self._ratio_combo)

        resolution_label = QLabel("分辨率")
        resolution_label.setObjectName("dialogFieldLabel")
        layout.addWidget(resolution_label)

        self._resolution_combo = QComboBox()
        self._resolution_combo.setObjectName("genSettingsCombo")
        self._resolution_combo.addItems(["1K", "2K", "4K"])
        self._resolution_combo.setFixedHeight(36)
        saved_res = self._settings.get("resolution", "1K")
        idx3 = self._resolution_combo.findText(saved_res)
        self._resolution_combo.setCurrentIndex(idx3 if idx3 >= 0 else 0)
        layout.addWidget(self._resolution_combo)

        self._rewrite_cb = QCheckBox("失败时重新生成提示词重试")
        self._rewrite_cb.setObjectName("genSettingsCheckbox")
        self._rewrite_cb.setChecked(self._settings.get("prompt_rewrite", True))
        self._rewrite_cb.setToolTip("生图失败时，自动调用AI文案模型重写提示词并重试（最多2次）")
        layout.addWidget(self._rewrite_cb)

        prefix_label = QLabel("全局前缀提示词")
        prefix_label.setObjectName("dialogFieldLabel")
        layout.addWidget(prefix_label)

        self._prefix_edit = QTextEdit()
        self._prefix_edit.setObjectName("genSettingsPrefixEdit")
        self._prefix_edit.setFixedHeight(140)
        self._prefix_edit.setPlaceholderText("输入全局前缀提示词，会附加到每张生图请求的前面...")
        self._prefix_edit.setPlainText(self._settings.get("prefix", self._current_style_prompt()))
        layout.addWidget(self._prefix_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _current_style_prompt(self) -> str:
        idx = self._style_combo.currentIndex()
        if 0 <= idx < len(COMIC_STYLE_PRESETS):
            return COMIC_STYLE_PRESETS[idx]["prompt"]
        return ""

    def _on_style_changed(self, _index: int) -> None:
        if self._style_combo.currentText() == "自定义画风":
            self._prefix_edit.clear()
            return
        prompt = self._current_style_prompt()
        if prompt:
            self._prefix_edit.setPlainText(prompt)

    def _on_confirm(self) -> None:
        self._settings["concurrency"] = self._concurrency_spin.value()
        self._settings["style"] = self._style_combo.currentText()
        self._settings["prefix"] = self._prefix_edit.toPlainText().strip()
        self._settings["aspect_ratio"] = self._ratio_combo.currentText()
        self._settings["resolution"] = self._resolution_combo.currentText()
        self._settings["prompt_rewrite"] = self._rewrite_cb.isChecked()
        self.accept()

    @property
    def result(self) -> dict:
        return self._settings


class _BatchRangeDialog(QDialog):
    def __init__(self, max_index: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("指定生图区域")
        self.setFixedSize(360, 240)
        self.setObjectName("batchRangeDialog")
        self._max_index = max_index
        self._start = 1
        self._end = min(5, max_index)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("指定生图区域")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        hint = QLabel(f"分镜总数：{self._max_index} 个")
        hint.setObjectName("dialogFieldLabel")
        layout.addWidget(hint)

        start_row = QHBoxLayout()
        start_row.setSpacing(12)

        start_label = QLabel("开始分镜：")
        start_label.setObjectName("dialogFieldLabel")
        start_row.addWidget(start_label)

        self._start_spin = QSpinBox()
        self._start_spin.setObjectName("rangeSpinBox")
        self._start_spin.setMinimum(1)
        self._start_spin.setMaximum(self._max_index)
        self._start_spin.setValue(self._start)
        self._start_spin.setFixedWidth(100)
        self._start_spin.setFixedHeight(36)
        self._start_spin.valueChanged.connect(self._on_start_changed)
        start_row.addWidget(self._start_spin)

        start_row.addStretch()
        layout.addLayout(start_row)

        end_row = QHBoxLayout()
        end_row.setSpacing(12)

        end_label = QLabel("结束分镜：")
        end_label.setObjectName("dialogFieldLabel")
        end_row.addWidget(end_label)

        self._end_spin = QSpinBox()
        self._end_spin.setObjectName("rangeSpinBox")
        self._end_spin.setMinimum(1)
        self._end_spin.setMaximum(self._max_index)
        self._end_spin.setValue(self._end)
        self._end_spin.setFixedWidth(100)
        self._end_spin.setFixedHeight(36)
        self._end_spin.valueChanged.connect(self._on_end_changed)
        end_row.addWidget(self._end_spin)

        end_row.addStretch()
        layout.addLayout(end_row)

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

    def _on_start_changed(self, value: int) -> None:
        if value > self._end_spin.value():
            self._end_spin.setValue(value)

    def _on_end_changed(self, value: int) -> None:
        if value < self._start_spin.value():
            self._start_spin.setValue(value)

    @property
    def start_index(self) -> int:
        return self._start_spin.value()

    @property
    def end_index(self) -> int:
        return self._end_spin.value()


class _ExportDialog(QDialog):
    def __init__(self, max_index: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("导出漫画图片")
        self.setFixedSize(420, 300)
        self.setObjectName("exportDialog")
        self._max_index = max_index
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("导出漫画图片")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self._all_radio = QRadioButton("全部导出")
        self._all_radio.setObjectName("exportModeRadio")
        self._all_radio.setChecked(True)
        self._all_radio.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._all_radio)

        self._range_radio = QRadioButton("指定区域")
        self._range_radio.setObjectName("exportModeRadio")
        self._range_radio.toggled.connect(self._on_mode_changed)
        layout.addWidget(self._range_radio)

        range_row = QHBoxLayout()
        range_row.setContentsMargins(24, 0, 0, 0)
        range_row.setSpacing(8)

        start_label = QLabel("从")
        start_label.setObjectName("dialogFieldLabel")
        range_row.addWidget(start_label)

        self._start_spin = QSpinBox()
        self._start_spin.setObjectName("rangeSpinBox")
        self._start_spin.setMinimum(1)
        self._start_spin.setMaximum(self._max_index)
        self._start_spin.setValue(1)
        self._start_spin.setFixedWidth(120)
        self._start_spin.setFixedHeight(36)
        self._start_spin.setEnabled(False)
        self._start_spin.valueChanged.connect(self._on_start_changed)
        range_row.addWidget(self._start_spin)

        to_label = QLabel("到")
        to_label.setObjectName("dialogFieldLabel")
        range_row.addWidget(to_label)

        self._end_spin = QSpinBox()
        self._end_spin.setObjectName("rangeSpinBox")
        self._end_spin.setMinimum(1)
        self._end_spin.setMaximum(self._max_index)
        self._end_spin.setValue(min(5, self._max_index))
        self._end_spin.setFixedWidth(120)
        self._end_spin.setFixedHeight(36)
        self._end_spin.setEnabled(False)
        self._end_spin.valueChanged.connect(self._on_end_changed)
        range_row.addWidget(self._end_spin)

        range_row.addStretch()
        layout.addLayout(range_row)

        self._hint_label = QLabel(f"共 {self._max_index} 个分镜")
        self._hint_label.setObjectName("dialogFieldLabel")
        self._hint_label.setStyleSheet("color: #94a3b8; font-size: 12px; padding-left: 24px;")
        layout.addWidget(self._hint_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("导出")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_mode_changed(self) -> None:
        is_range = self._range_radio.isChecked()
        self._start_spin.setEnabled(is_range)
        self._end_spin.setEnabled(is_range)
        if is_range:
            self._hint_label.setText(f"分镜 {self._start_spin.value()} ~ {self._end_spin.value()}")
        else:
            self._hint_label.setText(f"共 {self._max_index} 个分镜")

    def _on_start_changed(self, value: int) -> None:
        if value > self._end_spin.value():
            self._end_spin.setValue(value)
        self._hint_label.setText(f"分镜 {self._start_spin.value()} ~ {self._end_spin.value()}")

    def _on_end_changed(self, value: int) -> None:
        if value < self._start_spin.value():
            self._start_spin.setValue(value)
        self._hint_label.setText(f"分镜 {self._start_spin.value()} ~ {self._end_spin.value()}")

    @property
    def is_all(self) -> bool:
        return self._all_radio.isChecked()

    @property
    def start_index(self) -> int:
        return self._start_spin.value()

    @property
    def end_index(self) -> int:
        return self._end_spin.value()


class NovelComicGeneratePage(QFrame):
    back_to_chapters = Signal()
    comic_image_generated = Signal(int, str)

    def __init__(
        self,
        project_id: str,
        episode_num: int,
        state_service: NovelComicStateService,
        settings_service: SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._episode_num = episode_num
        self._state_service = state_service
        self._settings_service = settings_service
        self._storyboards: list[dict] = []
        self._storyboard_cards: dict[int, _StoryboardCard] = {}
        self._split_worker: StoryboardSplitWorker | None = None
        self._match_worker: MatchAssetsWorker | None = None
        self._desc_worker: StoryboardDescriptionWorker | None = None
        self._gen_settings: dict = {}
        self._batch_comic_total = 0
        self._batch_comic_ctr: list[int] = [0]
        self._comic_poll_timer: object | None = None
        self._split_mode: str = "ai"
        self.setObjectName("novelComicGeneratePage")
        self.comic_image_generated.connect(self._on_comic_image_generated)
        self._setup_ui()
        self._restore_comic_batch_state()

    def _get_chapter_text(self) -> str:
        project = self._state_service.load_project(self._project_id)
        if project and self._episode_num <= len(project.chapters):
            return project.chapters[self._episode_num - 1].text
        all_text_parts = []
        if project:
            for ch in project.chapters:
                if ch.text:
                    all_text_parts.append(ch.text)
        return "\n".join(all_text_parts)

    def _get_chat_manager(self) -> MultiRoundChatManager | None:
        settings = self._settings_service.load()
        config = AIModelConfig(
            model_name=settings.text_model.model_name,
            api_key=settings.text_model.api_key,
            base_url=settings.text_model.base_url,
        )
        mgr = MultiRoundChatManager(self._project_id, self._episode_num, self._state_service)
        if mgr.is_multi_round_enabled(config):
            logger.info("启用多轮对话模式, model=%s, project=%s, ep=%d",
                        config.model_name, self._project_id, self._episode_num)
            return mgr
        return None

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

        self._scroll_area = scroll_area

        scroll_content = QWidget()
        scroll_content.setObjectName("comicGenScrollContent")
        self._storyboard_layout = QVBoxLayout(scroll_content)
        self._storyboard_layout.setContentsMargins(24, 16, 24, 16)
        self._storyboard_layout.setSpacing(12)
        self._storyboard_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._load_storyboards()
        self._load_gen_settings()
        self._refresh_storyboard_list()

    def _storyboards_key(self) -> str:
        return f"storyboards_ep{self._episode_num}"

    def _load_storyboards(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        saved = project.extra_data.get(self._storyboards_key())
        if saved and isinstance(saved, list):
            self._storyboards = saved

    def _save_storyboards(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        project.extra_data[self._storyboards_key()] = self._storyboards
        self._state_service.save_project(project)

    def _load_gen_settings(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        self._gen_settings = project.extra_data.get("gen_settings", {})

    def _save_gen_settings(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        project.extra_data["gen_settings"] = self._gen_settings
        self._state_service.save_project(project)

    def _on_gen_settings(self) -> None:
        dialog = _GenerateSettingsDialog(self._gen_settings, self.window())
        if dialog.exec() == QDialog.Accepted:
            self._gen_settings = dialog.result
            self._save_gen_settings()

    def _on_export_comics(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return

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
            dlg = _ConfirmDialog(
                f"以下分镜没有已生成的漫画图片：\n{names}\n\n请先生成图片再导出。",
                self,
            )
            dlg.setWindowTitle("缺少图片")
            dlg.exec()
            return

        zip_name = re.sub(r'[<>:"/\\|?*]', '_', project.name) or "comic"
        default_name = f"{zip_name}.zip"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "导出漫画图片", default_name,
            "ZIP 文件 (*.zip)",
        )
        if not save_path:
            return

        image_files: list[tuple[int, Path]] = []
        for idx in sorted(target_indices):
            sb = next((s for s in self._storyboards if s["index"] == idx), None)
            img = sb.get("generated_image", "") if sb else ""
            if img and Path(img).exists():
                image_files.append((idx, Path(img)))
            else:
                matches = sorted(images_dir.glob(f"comic_panel_{idx}_*.png"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if matches:
                    image_files.append((idx, matches[0]))

        image_files.sort(key=lambda x: x[0])
        try:
            base_ts = time.time() - len(image_files)
            with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for i, (idx, fp) in enumerate(image_files, start=1):
                    zi = zipfile.ZipInfo.from_file(fp, f"{i}.png")
                    ts = base_ts + idx
                    zi.date_time = time.localtime(ts)[:6]
                    zi.extra = _make_zip_ntfs_extra(ts)
                    with open(fp, "rb") as f:
                        zf.writestr(zi, f.read())
            self._desc_status.setText(f"导出完成：{len(image_files)} 张图片 → {save_path}")
            self._desc_status.setStyleSheet("color: #4ade80;")
        except Exception as e:
            self._desc_status.setText(f"导出失败: {e}")
            self._desc_status.setStyleSheet("color: #f87171;")

    def _images_dir(self) -> Path:
        images_dir = (
            self._state_service.get_project_images_dir(self._project_id)
            / f"ep_{self._episode_num}"
        )
        images_dir.mkdir(parents=True, exist_ok=True)
        return images_dir

    def _build_toolbar(self) -> QFrame:
        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        back_btn = QPushButton("\u2190 返回章节列表")
        back_btn.setObjectName("chapterBackBtn")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(lambda: self.back_to_chapters.emit())
        toolbar_layout.addWidget(back_btn)

        title_label = QLabel(f"漫画生成 - 第{self._episode_num}集")
        title_label.setObjectName("mixTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()
        return toolbar

    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("comicGenActionBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(24, 10, 24, 10)
        bar_layout.setSpacing(12)

        self._asset_btn = QPushButton("\U0001f4e6  资产管理")
        self._asset_btn.setObjectName("comicGenActionBtn")
        self._asset_btn.setCursor(Qt.PointingHandCursor)
        self._asset_btn.clicked.connect(self._on_asset_management)
        bar_layout.addWidget(self._asset_btn)

        self._storyboard_gen_btn = QPushButton("\U0001f4dd  生成分镜")
        self._storyboard_gen_btn.setObjectName("comicGenActionBtn")
        self._storyboard_gen_btn.setCursor(Qt.PointingHandCursor)
        self._storyboard_gen_btn.clicked.connect(self._on_generate_storyboard)
        bar_layout.addWidget(self._storyboard_gen_btn)

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

    def _refresh_storyboard_list(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if project:
            self._migrate_legacy_project_assets(project)
            self._rebuild_project_assets(project)
            self._state_service.save_project(project)

        scroll_value = self._scroll_area.verticalScrollBar().value()

        while self._storyboard_layout.count():
            item = self._storyboard_layout.takeAt(0)
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
            self._storyboard_layout.addWidget(empty_label)
            return

        total = len(self._storyboards)
        for i, sb in enumerate(self._storyboards):
            card = _StoryboardCard(sb)
            card.add_asset_clicked.connect(lambda idx=sb["index"]: self._on_add_asset(idx))
            card.remove_asset.connect(
                lambda asset_name, idx=sb["index"]: self._on_remove_asset(idx, asset_name),
            )
            card.generate_image_clicked.connect(lambda idx=sb["index"]: self._on_generate_image(idx))
            card.history_clicked.connect(lambda idx=sb["index"]: self._on_history_images(idx))
            card.gen_desc_clicked.connect(self._on_gen_single_desc)
            card.desc_edit_requested.connect(self._on_edit_desc)
            card.text_edit_requested.connect(self._on_edit_text)
            card.preview_clicked.connect(lambda idx=sb["index"]: self._on_preview_comic(idx))
            self._storyboard_cards[sb["index"]] = card
            self._storyboard_layout.addWidget(card)

        self._scroll_area.verticalScrollBar().setValue(scroll_value)

    def _refresh_single_card(self, storyboard_index: int) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card is None:
            return
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                card.update_data(sb)
                break

    def _on_asset_management(self) -> None:
        chapter_text = self._get_chapter_text()
        dialog = _AssetManagementDialog(
            self._project_id, self._episode_num, chapter_text,
            self._state_service, self._settings_service,
            self.window(),
        )
        dialog.exec()

    def _on_generate_storyboard(self) -> None:
        chapter_text = self._get_chapter_text()
        if not chapter_text.strip():
            self._split_status.setText("没有可拆分的文本内容")
            self._split_status.setStyleSheet("color: #f87171;")
            return

        dialog = _SplitModeDialog(self.window())
        if dialog.exec() != QDialog.Accepted:
            return

        if dialog.mode == "manual":
            self._split_mode = "manual"
            self._manual_split_storyboards(chapter_text, dialog.lines_per_storyboard)
            return

        self._split_mode = "ai"
        self._split_status.setText("拆分中...")
        self._split_status.setStyleSheet("color: #4fc3f7;")

        chat_mgr = self._get_chat_manager()
        if chat_mgr:
            chat_mgr.clear_history()

        key = _worker_key(self._project_id, self._episode_num, "split")
        worker = StoryboardSplitWorker(
            chapter_text, self._project_id, self._episode_num,
            self._settings_service, self._state_service,
            chat_manager=chat_mgr,
        )
        _running_workers[key] = worker
        worker.progress.connect(self._split_status.setText)
        worker.finished.connect(self._on_split_finished)
        worker.finished.connect(lambda: _running_workers.pop(key, None))
        worker.error.connect(self._on_split_error)
        worker.error.connect(lambda: _running_workers.pop(key, None))
        self._split_worker = worker
        worker.start()

    def _on_split_finished(self, storyboards: list[dict]) -> None:
        self._storyboards = storyboards
        self._save_storyboards()
        self._refresh_storyboard_list()
        self._split_status.setText(f"拆分完成，共 {len(storyboards)} 个分镜")
        self._split_status.setStyleSheet("color: #4ade80;")

    def _on_split_error(self, error_msg: str) -> None:
        self._split_status.setText(f"拆分失败: {error_msg}")
        self._split_status.setStyleSheet("color: #f87171;")

    def _manual_split_storyboards(self, chapter_text: str, lines_per_sb: int) -> None:
        import re as _re
        sentences = _re.split(r'(?<=[。！？\n])\s*', chapter_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences:
            self._split_status.setText("文本中没有找到有效句子")
            self._split_status.setStyleSheet("color: #f87171;")
            return

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
        self._split_status.setText(f"手动拆分完成，共 {len(storyboards)} 个分镜")
        self._split_status.setStyleSheet("color: #4ade80;")

    def _get_flattened_asset_names(self) -> dict:
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

        key = _worker_key(self._project_id, self._episode_num, "match")
        worker = MatchAssetsWorker(
            self._storyboards, asset_names,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
            chat_manager=self._get_chat_manager(),
        )
        _running_workers[key] = worker
        worker.finished.connect(self._on_match_finished)
        worker.finished.connect(lambda: _running_workers.pop(key, None))
        worker.error.connect(self._on_match_error)
        worker.error.connect(lambda: _running_workers.pop(key, None))
        self._match_worker = worker
        worker.start()

    def _on_match_finished(self, assets_map: dict) -> None:
        for sb in self._storyboards:
            match = assets_map.get(sb["index"], {})
            parts = []
            scenes = match.get("scenes", [])
            scene = match.get("scene", "")
            if isinstance(scenes, list):
                for s in scenes:
                    if s:
                        parts.append(s)
            elif scene:
                parts.append(scene)
            for name in match.get("characters", []):
                if name:
                    parts.append(name)
            for name in match.get("props", []):
                if name:
                    parts.append(name)
            sb["assets"] = parts
        self._save_storyboards()
        self._refresh_storyboard_list()
        matched = sum(1 for sb in self._storyboards if sb["assets"])
        self._match_status.setText(f"匹配完成，{matched}/{len(self._storyboards)} 个分镜已匹配")
        self._match_status.setStyleSheet("color: #4ade80;")

    def _on_match_error(self, error_msg: str) -> None:
        self._match_status.setText(f"匹配失败: {error_msg}")
        self._match_status.setStyleSheet("color: #f87171;")

    def _on_desc_gen_menu(self) -> None:
        if not self._storyboards:
            return
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

        key = _worker_key(self._project_id, self._episode_num, "desc")
        worker = StoryboardDescriptionWorker(
            target,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
            chat_manager=self._get_chat_manager(),
            single_batch=(self._split_mode == "manual"),
        )
        _running_workers[key] = worker
        self._desc_worker = worker
        worker.progress.connect(self._on_desc_progress)
        worker.finished.connect(self._on_desc_finished)
        worker.finished.connect(lambda: _running_workers.pop(key, None))
        worker.error.connect(self._on_desc_error)
        worker.error.connect(lambda: _running_workers.pop(key, None))
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

    @staticmethod
    def _rebuild_project_assets(project) -> None:
        seen: set[str] = set()
        merged: list = []
        for ep in range(1, 100):
            for a in project.extra_data.get(f"ep_assets_{ep}", []):
                n = a.get("name", "")
                if n and n not in seen:
                    seen.add(n)
                    merged.append(a)
        if merged:
            from clip_synth.models.novel_comic_project_state import NovelComicAsset
            project.assets = [
                NovelComicAsset(
                    name=a.get("name", ""), desc=a.get("desc", ""),
                    asset_type=a.get("asset_type", "prop"),
                    image_path=a.get("image_path", ""),
                )
                for a in merged
            ]

    @staticmethod
    def _migrate_legacy_project_assets(project) -> None:
        if not project.assets:
            return
        has_any_ep_asset = any(
            project.extra_data.get(f"ep_assets_{ep}")
            for ep in range(1, 100)
        )
        if has_any_ep_asset:
            return
        migrated = []
        for a in project.assets:
            atype = getattr(a, "asset_type", "prop")
            migrated.append({
                "name": a.name,
                "desc": getattr(a, "desc", ""),
                "asset_type": atype,
                "image_path": getattr(a, "image_path", "") or "",
            })
        project.extra_data["ep_assets_1"] = migrated

    def _on_add_asset(self, storyboard_index: int) -> None:
        dialog = _AddAssetDialog(self._storyboards, storyboard_index, self._state_service, self._project_id, self.window())
        if dialog.exec() == QDialog.Accepted:
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["assets"] = list(dialog.selected_assets)
                    break
            self._save_storyboards()
            self._refresh_single_card(storyboard_index)

    def _on_remove_asset(self, storyboard_index: int, asset_name: str) -> None:
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                if asset_name in sb["assets"]:
                    sb["assets"].remove(asset_name)
                break
        self._save_storyboards()
        self._refresh_single_card(storyboard_index)

    def _on_generate_image(self, storyboard_index: int) -> None:
        sb = None
        for s in self._storyboards:
            if s["index"] == storyboard_index:
                sb = s
                break
        if sb is None:
            return

        if not sb.get("description"):
            self._show_alert("无法生成", "请先生成分镜描述")
            return

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
            self._show_alert("无法生成", "请先在系统配置中设置图片生成模型")
            return

        project = self._state_service.load_project(self._project_id)
        gen_settings = self._gen_settings

        prompt, size, reference_paths = self._build_comic_prompt(sb, project, gen_settings, page_num=sb.get("index", 1))

        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_generating()

        review_mode = card.is_review_mode() if card else False

        if review_mode:
            reference_paths = []
            logger.info("过审模式：分镜 #%d 跳过参考图", storyboard_index)
        elif reference_paths:
            logger.info("分镜 #%d 找到 %d 张参考图: %s", storyboard_index, len(reference_paths), reference_paths)
        else:
            logger.info("分镜 #%d 没有参考图", storyboard_index)

        signal = self.comic_image_generated
        batch_counter = [0, 0]
        ImageGenService.instance().submit(
            image_config, prompt,
            _make_comic_on_done(
                self._state_service, self._project_id, self._episode_num,
                storyboard_index, signal, batch_counter, lambda: None,
            ),
            _make_comic_on_error(storyboard_index, signal, batch_counter, lambda: None),
            size=size,
            reference_images=reference_paths if reference_paths else None,
            resolution=gen_settings.get("resolution"),
            aspect_ratio=gen_settings.get("aspect_ratio", "3:4"),
            text_model_config=image_text_config,
            prompt_rewrite=gen_settings.get("prompt_rewrite", True),
        )

    def _on_history_images(self, storyboard_index: int) -> None:
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
        dialog = _StoryboardPreviewDialog(
            self._storyboards, storyboard_index, self.window(),
        )
        dialog.show_preview()

    def _on_comic_image_generated(self, storyboard_index: int, image_path: str) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card is None:
            return
        if image_path:
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["generated_image"] = image_path
                    break
            self._refresh_single_card(storyboard_index)
        else:
            card.set_gen_error()

    def _on_gen_single_desc(self, storyboard_index: int) -> None:
        sb = None
        for s in self._storyboards:
            if s["index"] == storyboard_index:
                sb = s
                break
        if sb is None:
            return

        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_desc_gen_status("generating")

        key = _worker_key(self._project_id, self._episode_num, f"desc_{storyboard_index}")
        worker = SingleDescWorker(
            sb, self._storyboards, self._project_id, self._episode_num,
            self._settings_service, self._state_service,
            chat_manager=self._get_chat_manager(),
        )
        _running_workers[key] = worker
        worker.finished.connect(
            lambda desc, idx=storyboard_index: self._on_single_desc_finished(idx, desc),
        )
        worker.finished.connect(lambda: _running_workers.pop(key, None))
        worker.error.connect(
            lambda err, idx=storyboard_index: self._on_single_desc_error(idx, err),
        )
        worker.error.connect(lambda: _running_workers.pop(key, None))
        worker.start()

    def _on_single_desc_finished(self, storyboard_index: int, description: str) -> None:
        for sb in self._storyboards:
            if sb["index"] == storyboard_index:
                sb["description"] = description
                break
        self._save_storyboards()
        self._refresh_single_card(storyboard_index)
        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_desc_gen_status("done")

    def _on_single_desc_error(self, storyboard_index: int, error_msg: str) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_desc_gen_status("error")
            if card._desc_edit:
                card._desc_edit.setPlaceholderText(f"生成失败: {error_msg}")

    def _on_edit_desc(self, storyboard_index: int, current_desc: str) -> None:
        dialog = _DescEditDialog(storyboard_index, current_desc, self.window())
        if dialog.exec() == QDialog.Accepted:
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["description"] = dialog.edited_desc
                    break
            self._save_storyboards()
            self._refresh_single_card(storyboard_index)

    def _on_edit_text(self, storyboard_index: int, current_text: str) -> None:
        dialog = _DescEditDialog(storyboard_index, current_text, self.window())
        dialog.setWindowTitle(f"编辑分镜 #{storyboard_index} 原文")
        if dialog.exec() == QDialog.Accepted:
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["text"] = dialog.edited_desc
                    break
            self._save_storyboards()
            self._refresh_single_card(storyboard_index)

    def _on_batch_generate_menu(self) -> None:
        if not self._storyboards:
            self._show_alert("无法生成", "请先生成分镜后再生成漫画图。")
            return

        no_desc = [sb["index"] for sb in self._storyboards if not sb.get("description")]
        if no_desc:
            self._show_alert(
                "分镜描述缺失",
                f"以下 {len(no_desc)} 个分镜尚未生成描述：\n\n{', '.join(f'#{i}' for i in no_desc)}\n\n"
                f"请先生成分镜描述后再尝试。",
            )
            return

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
        dialog = _BatchRangeDialog(len(self._storyboards), self.window())
        if dialog.exec() == QDialog.Accepted:
            self._run_batch_comic_gen(missing_only=False, index_range=(dialog.start_index, dialog.end_index))

    def _run_batch_comic_gen(self, missing_only: bool, index_range: tuple[int, int] | None = None) -> None:
        project = self._state_service.load_project(self._project_id)

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
            self._show_alert("无法生成", "请先在系统配置中设置图片生成模型")
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
        logger.info("开始批量漫画生图: 共 %d 张, 并发数 %d", ctr[0], concurrency)

        if self._comic_poll_timer is None:
            self._comic_poll_timer = self.startTimer(2000)

        for sb in targets:
            idx = sb["index"]
            card = self._storyboard_cards.get(idx)
            if card:
                card.set_generating()

            prompt, size, reference_paths = self._build_comic_prompt(sb, project, gen_settings, page_num=idx)

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

    def _build_comic_prompt(
        self, sb: dict, project, gen_settings: dict, page_num: int = 0,
    ) -> tuple[str, str, list[str]]:
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

    def _update_comic_batch_status(self) -> None:
        if not self.isVisible():
            return
        key = (self._project_id, self._episode_num)
        ctr = _comic_batch_counter.get(key)
        if ctr is None:
            return
        total, done = ctr[0], ctr[1]
        pending = ImageGenService.instance().pending_count
        running = total - done - pending
        if done >= total and pending == 0:
            self._desc_status.setText(f"漫画批量生成完成！共 {total} 张")
            self._desc_status.setStyleSheet("color: #4ade80;")
            logger.info("批量漫画生成完成: %d/%d", done, total)
            _comic_batch_counter.pop(key, None)
        else:
            self._desc_status.setText(
                f"漫画队列: {total} 张 | "
                f"已完成: {done} | "
                f"进行中: {running} | "
                f"等待: {pending}"
            )
            self._desc_status.setStyleSheet("color: #4fc3f7;")
            logger.info(
                "批量漫画进度: %d/%d 已完成, %d 进行中, %d 等待",
                done, total, running, pending,
            )

    def _restore_comic_batch_state(self) -> None:
        key = (self._project_id, self._episode_num)
        ctr = _comic_batch_counter.get(key)
        if ctr is None:
            return
        self._batch_comic_total = ctr[0]
        self._batch_comic_ctr = ctr
        if ctr[0] > ctr[1] or ImageGenService.instance().pending_count > 0:
            self._update_comic_batch_status()
            if self._comic_poll_timer is None:
                self._comic_poll_timer = self.startTimer(2000)

    def timerEvent(self, event) -> None:
        super().timerEvent(event)
        if not self.isVisible():
            if self._comic_poll_timer is not None:
                self.killTimer(self._comic_poll_timer)
                self._comic_poll_timer = None
            return
        key = (self._project_id, self._episode_num)
        ctr = _comic_batch_counter.get(key)
        if ctr is not None and ctr[0] <= ctr[1] and ImageGenService.instance().pending_count == 0:
            if self._comic_poll_timer is not None:
                self.killTimer(self._comic_poll_timer)
                self._comic_poll_timer = None
            _comic_batch_counter.pop(key, None)
            self._desc_status.setText(f"漫画批量生成完成！共 {ctr[0]} 张")
            self._desc_status.setStyleSheet("color: #4ade80;")
            return
        self._update_comic_batch_status()

    def _show_alert(self, title: str, message: str) -> None:
        box = QMessageBox(QMessageBox.Warning, title, message, QMessageBox.Ok, self.window())
        box.setObjectName("comicGenAlertBox")
        box.exec()


class _StoryboardCard(QFrame):
    add_asset_clicked = Signal(int)
    remove_asset = Signal(str)
    generate_image_clicked = Signal(int)
    history_clicked = Signal(int)
    gen_desc_clicked = Signal(int)
    desc_edit_requested = Signal(int, str)
    text_edit_requested = Signal(int, str)
    preview_clicked = Signal(int)

    def __init__(self, data: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._data = data
        self.setObjectName("storyboardCard")
        self._main_layout: QHBoxLayout | None = None
        self._right_layout: QVBoxLayout | None = None
        self._text_edit: QTextEdit | None = None
        self._desc_container: QWidget | None = None
        self._desc_edit: QTextEdit | None = None
        self._asset_row: QHBoxLayout | None = None
        self._asset_container: QWidget | None = None
        self._image_placeholder: QFrame | None = None
        self._image_label: QLabel | None = None
        self._gen_img_btn: QPushButton | None = None
        self._gen_desc_btn: QPushButton | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(16, 16, 16, 16)
        self._main_layout.setSpacing(16)

        left_col = QVBoxLayout()
        left_col.setSpacing(0)
        left_col.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        left_col.addStretch()

        self._image_placeholder = QFrame()
        self._image_placeholder.setObjectName("storyboardImagePlaceholder")
        self._image_placeholder.setFixedSize(120, 160)
        self._image_placeholder.setCursor(Qt.PointingHandCursor)
        image_layout = QVBoxLayout(self._image_placeholder)
        image_layout.setContentsMargins(4, 4, 4, 4)
        image_layout.setAlignment(Qt.AlignCenter)
        self._image_label = QLabel("+")
        self._image_label.setObjectName("storyboardImageCross")
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setScaledContents(True)
        image_layout.addWidget(self._image_label)
        self._image_placeholder.mousePressEvent = self._on_image_click
        left_col.addWidget(self._image_placeholder)

        left_col.addStretch()

        self._main_layout.addLayout(left_col)

        self._right_layout = QVBoxLayout()
        self._right_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        index_label = QLabel(f"#{self._data['index']}")
        index_label.setObjectName("storyboardIndexLabel")
        header_row.addWidget(index_label)

        self._text_edit = QTextEdit(self._data["text"])
        self._text_edit.setObjectName("storyboardTextLabel")
        self._text_edit.setReadOnly(True)
        self._text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text_edit.setFrameShape(QTextEdit.NoFrame)
        self._text_edit.setStyleSheet("QTextEdit { background: transparent; color: #e2e8f0; font-size: 13px; }")
        self._text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._text_edit.setFixedHeight(60)
        self._text_edit.viewport().installEventFilter(self)
        header_row.addWidget(self._text_edit, stretch=1)
        self._right_layout.addLayout(header_row)

        self._desc_container = QWidget()
        self._desc_container.setObjectName("storyboardDescContainer")
        self._desc_container.setVisible(False)
        self._desc_container.setFixedHeight(130)
        desc_container_layout = QVBoxLayout(self._desc_container)
        desc_container_layout.setContentsMargins(0, 0, 0, 0)
        self._desc_edit = QTextEdit()
        self._desc_edit.setObjectName("storyboardDescEdit")
        self._desc_edit.setReadOnly(True)
        self._desc_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._desc_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._desc_edit.setFrameShape(QTextEdit.NoFrame)
        self._desc_edit.viewport().installEventFilter(self)
        desc_container_layout.addWidget(self._desc_edit)
        self._right_layout.addWidget(self._desc_container)

        self._asset_container = QWidget()
        self._asset_container.setObjectName("storyboardAssetContainer")
        self._asset_container.setVisible(False)
        self._asset_row = QHBoxLayout(self._asset_container)
        self._asset_row.setContentsMargins(0, 0, 0, 0)
        self._asset_row.setSpacing(6)
        self._right_layout.addWidget(self._asset_container)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._review_btn = QPushButton("过审模式")
        self._review_btn.setObjectName("storyboardReviewBtn")
        self._review_btn.setCursor(Qt.PointingHandCursor)
        self._review_btn.setToolTip("开启后生成该分镜漫画图时不传入参考图")
        self._review_btn.setCheckable(True)
        btn_row.addWidget(self._review_btn)

        gen_img_btn = QPushButton("\U0001f5bc  生成图片")
        gen_img_btn.setObjectName("storyboardActionBtn")
        gen_img_btn.setCursor(Qt.PointingHandCursor)
        gen_img_btn.clicked.connect(
            lambda: self.generate_image_clicked.emit(self._data["index"])
        )
        self._gen_img_btn = gen_img_btn
        btn_row.addWidget(gen_img_btn)

        hist_btn = QPushButton("\U0001f4c2  历史图片")
        hist_btn.setObjectName("storyboardActionBtn")
        hist_btn.setCursor(Qt.PointingHandCursor)
        hist_btn.clicked.connect(
            lambda: self.history_clicked.emit(self._data["index"])
        )
        btn_row.addWidget(hist_btn)

        self._gen_desc_btn = QPushButton("\U0001f4c4  生成描述")
        self._gen_desc_btn.setObjectName("storyboardActionBtn")
        self._gen_desc_btn.setCursor(Qt.PointingHandCursor)
        self._gen_desc_btn.clicked.connect(
            lambda: self.gen_desc_clicked.emit(self._data["index"])
        )
        btn_row.addWidget(self._gen_desc_btn)

        btn_row.addStretch()
        self._right_layout.addLayout(btn_row)

        self._main_layout.addLayout(self._right_layout, stretch=1)

        self._apply_data()

    def _apply_data(self) -> None:
        if self._text_edit:
            self._text_edit.setPlainText(self._data["text"])

        if self._desc_edit and self._desc_container:
            desc = self._data.get("description", "")
            if desc:
                self._desc_edit.setPlainText(desc)
                self._desc_container.setVisible(True)
                if self._gen_desc_btn:
                    self._gen_desc_btn.setText("\U0001f4c4  重新生成")
                    self._gen_desc_btn.setEnabled(True)
            else:
                self._desc_container.setVisible(False)

        if self._asset_row and self._asset_container:
            self._rebuild_asset_row()

        generated = self._data.get("generated_image", "")
        if generated and Path(generated).exists():
            self._show_generated_pixmap(generated)

    def _on_image_click(self, event) -> None:
        generated = self._data.get("generated_image", "")
        if generated and Path(generated).exists():
            self.preview_clicked.emit(self._data["index"])

    def set_generating(self) -> None:
        if self._gen_img_btn:
            self._gen_img_btn.setText("生成中...")
            self._gen_img_btn.setEnabled(False)

    def is_review_mode(self) -> bool:
        return self._review_btn.isChecked()

    def set_gen_error(self) -> None:
        if self._gen_img_btn:
            self._gen_img_btn.setText("\U0001f5bc  生成失败")
            self._gen_img_btn.setEnabled(True)

    def _show_generated_pixmap(self, image_path: str) -> None:
        if self._image_label and Path(image_path).exists():
            px = QPixmap(image_path)
            if not px.isNull():
                self._image_label.setPixmap(px.scaled(110, 148, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._image_label.setText("")
                if self._gen_img_btn:
                    self._gen_img_btn.setText("\U0001f5bc  重新生成")
                    self._gen_img_btn.setEnabled(True)

    def set_desc_gen_status(self, status: str) -> None:
        if not self._gen_desc_btn:
            return
        if status == "generating":
            self._gen_desc_btn.setText("生成中...")
            self._gen_desc_btn.setEnabled(False)
        elif status == "error":
            self._gen_desc_btn.setText("\U0001f4c4  生成描述")
            self._gen_desc_btn.setEnabled(True)
        else:
            self._gen_desc_btn.setText("\U0001f4c4  重新生成")
            self._gen_desc_btn.setEnabled(True)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseButtonDblClick:
            if obj is self._desc_edit.viewport():
                current_desc = self._data.get("description", "")
                self.desc_edit_requested.emit(self._data["index"], current_desc)
                return True
            if obj is self._text_edit.viewport():
                self.text_edit_requested.emit(self._data["index"], self._data["text"])
                return True
        return super().eventFilter(obj, event)

    def _rebuild_asset_row(self) -> None:
        if self._asset_row is None:
            return
        while self._asset_row.count():
            item = self._asset_row.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()

        assets = self._data.get("assets", [])
        if not assets:
            self._asset_container.setVisible(False)
            return

        self._asset_container.setVisible(True)

        asset_label = QLabel("资产：")
        asset_label.setObjectName("storyboardAssetTagLabel")
        self._asset_row.addWidget(asset_label)

        for a in assets:
            tag = _AssetTag(a)
            tag.remove_clicked.connect(lambda name=a: self.remove_asset.emit(name))
            self._asset_row.addWidget(tag)

        add_btn = QPushButton("+ 添加")
        add_btn.setObjectName("storyboardAddAssetBtn")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(
            lambda: self.add_asset_clicked.emit(self._data["index"])
        )
        self._asset_row.addWidget(add_btn)
        self._asset_row.addStretch()

    def update_data(self, data: dict) -> None:
        self._data = data
        self._apply_data()


class _AssetTag(QFrame):
    remove_clicked = Signal()

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("assetTag")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)

        label = QLabel(name)
        label.setObjectName("assetTagLabel")
        layout.addWidget(label)

        remove_btn = QPushButton("\u00d7")
        remove_btn.setObjectName("assetTagRemoveBtn")
        remove_btn.setFixedSize(16, 16)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(self.remove_clicked.emit)
        layout.addWidget(remove_btn)


class _AddAssetDialog(QDialog):
    def __init__(
        self,
        storyboards: list[dict],
        storyboard_index: int,
        state_service: NovelComicStateService | None = None,
        project_id: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._selected_assets: list[str] = []
        self.setWindowTitle(f"为分镜 #{storyboard_index} 添加资产")
        self.setFixedSize(680, 520)
        self.setObjectName("addAssetDialog")
        self._current_tab = 0
        self._asset_checkboxes: dict[str, list[QCheckBox]] = {
            "character": [], "scene": [], "prop": [],
        }
        self._setup_ui(storyboards, storyboard_index, state_service, project_id)

    def _setup_ui(self, storyboards: list[dict], storyboard_index: int, state_service, project_id) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel(f"为分镜 #{storyboard_index} 添加资产")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        tab_bar = QFrame()
        tab_bar.setObjectName("assetTabBar")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        self._tab_btns: list[QPushButton] = []
        tab_names = ["\U0001f464  人物", "\U0001f3ed  场景", "\U0001f392  道具"]
        for i, name in enumerate(tab_names):
            btn = QPushButton(name)
            btn.setObjectName("assetTabBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))
            tab_layout.addWidget(btn)
            self._tab_btns.append(btn)
        tab_layout.addStretch()
        layout.addWidget(tab_bar)

        self._tab_stack = QStackedWidget()
        self._tab_stack.setObjectName("assetTabStack")

        project = state_service.load_project(project_id) if state_service and project_id else None
        character_data = []
        scene_data = []
        prop_data = []
        if project:
            for a in project.assets:
                entry = {"name": a.name}
                if a.asset_type == "character":
                    character_data.append(entry)
                elif a.asset_type == "scene":
                    scene_data.append(entry)
                elif a.asset_type == "prop":
                    prop_data.append(entry)

        current_assets = []
        for sb in storyboards:
            if sb["index"] == storyboard_index:
                current_assets = sb.get("assets", [])
                break

        self._character_page = self._build_asset_check_page(character_data, current_assets, "character")
        self._scene_page = self._build_asset_check_page(scene_data, current_assets, "scene")
        self._prop_page = self._build_asset_check_page(prop_data, current_assets, "prop")

        self._tab_stack.addWidget(self._character_page)
        self._tab_stack.addWidget(self._scene_page)
        self._tab_stack.addWidget(self._prop_page)

        layout.addWidget(self._tab_stack, stretch=1)

        self._switch_tab(0)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        select_all_btn = QPushButton("全选当前Tab")
        select_all_btn.setObjectName("dialogCancelBtn")
        select_all_btn.clicked.connect(self._on_select_all_current)
        btn_row.addWidget(select_all_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _build_asset_check_page(
        self, assets: list[dict], current: list[str], asset_type: str,
    ) -> QWidget:
        container = QWidget()
        container.setObjectName(f"addAssetTabPage_{asset_type}")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("assetItemScroll")
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("assetItemScrollContent")
        check_layout = QVBoxLayout(scroll_content)
        check_layout.setContentsMargins(0, 8, 0, 0)
        check_layout.setSpacing(6)
        check_layout.setAlignment(Qt.AlignTop)

        for asset in assets:
            cb = QCheckBox(asset["name"])
            cb.setObjectName("assetCheckBox")
            cb.setChecked(asset["name"] in current)
            check_layout.addWidget(cb)
            self._asset_checkboxes[asset_type].append(cb)

        check_layout.addStretch()
        scroll.setWidget(scroll_content)
        container_layout.addWidget(scroll, stretch=1)
        return container

    def _switch_tab(self, index: int) -> None:
        self._current_tab = index
        self._tab_stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_select_all_current(self) -> None:
        tab_key = ["character", "scene", "prop"][self._current_tab]
        all_checked = all(cb.isChecked() for cb in self._asset_checkboxes[tab_key])
        new_state = not all_checked
        for cb in self._asset_checkboxes[tab_key]:
            cb.setChecked(new_state)

    def _on_confirm(self) -> None:
        self._selected_assets = []
        for asset_type in ("character", "scene", "prop"):
            for cb in self._asset_checkboxes[asset_type]:
                if cb.isChecked():
                    name = cb.text().split("  ")[-1]
                    if name and name not in self._selected_assets:
                        self._selected_assets.append(name)
        self.accept()

    @property
    def selected_assets(self) -> list[str]:
        return self._selected_assets


class _AddAssetNameDialog(QDialog):
    def __init__(self, asset_type: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._name = ""
        self._desc = ""
        type_names = {"character": "人物", "scene": "场景", "prop": "道具"}
        self.setWindowTitle(f"新增{type_names.get(asset_type, '资产')}")
        self.setFixedSize(450, 320)
        self.setObjectName("addAssetNameDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("新增资产")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        name_label = QLabel("资产名称")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("例如：张三、教室、智能手机")
        layout.addWidget(self._name_input)

        desc_label = QLabel("资产描述")
        desc_label.setObjectName("dialogFieldLabel")
        layout.addWidget(desc_label)

        self._desc_input = QTextEdit()
        self._desc_input.setObjectName("chapterTextEdit")
        self._desc_input.setPlaceholderText("描述资产的外貌、材质、颜色等视觉特征，用于AI生图")
        self._desc_input.setMaximumHeight(100)
        layout.addWidget(self._desc_input)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        self._name = self._name_input.text().strip()
        if not self._name:
            return
        self._desc = self._desc_input.toPlainText().strip()
        self.accept()

    @property
    def asset_name(self) -> str:
        return self._name

    @property
    def asset_desc(self) -> str:
        return self._desc


class _ConfirmDialog(QDialog):
    def __init__(self, message: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("确认")
        self.setFixedSize(400, 150)
        self.setObjectName("confirmDialog")
        self._setup_ui(message)

    def _setup_ui(self, message: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        msg_label = QLabel(message)
        msg_label.setObjectName("dialogTitle")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

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


class _DescEditDialog(QDialog):
    def __init__(self, storyboard_index: int, current_desc: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._edited = ""
        self.setWindowTitle(f"编辑分镜 #{storyboard_index} 描述")
        self.setFixedSize(680, 500)
        self.setObjectName("descEditDialog")
        self._setup_ui(storyboard_index, current_desc)

    def _setup_ui(self, storyboard_index: int, current_desc: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel(f"分镜 #{storyboard_index} 描述编辑")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("chapterTextEdit")
        self._text_edit.setPlainText(current_desc)
        layout.addWidget(self._text_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确定")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        self._edited = self._text_edit.toPlainText()
        self.accept()

    @property
    def edited_desc(self) -> str:
        return self._edited


class _ChooseRefDialog(QDialog):
    def __init__(self, candidates: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self._candidates = candidates
        self._selected: str | None = None
        self.setWindowTitle("选择垫图人物")
        self.setFixedSize(400, 300)
        self.setObjectName("chooseRefDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("选择垫图人物")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        label = QLabel("请选择一个人物作为垫图来源:")
        label.setObjectName("dialogFieldLabel")
        layout.addWidget(label)

        self._list = QListWidget()
        self._list.setObjectName("refCharList")
        for nm, _ in self._candidates:
            self._list.addItem(f"{nm}（已有图）")
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self._list, stretch=1)

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
    def selected_image_path(self) -> str:
        row = self._list.currentRow()
        if row >= 0 and row < len(self._candidates):
            return self._candidates[row][1]
        return ""


class _StoryboardPreviewDialog(QDialog):
    def __init__(
        self,
        storyboards: list[dict],
        current_index: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent, Qt.FramelessWindowHint)
        self._storyboards = storyboards
        self._current_idx = current_index
        self._sorted = sorted(
            [s for s in storyboards if s.get("generated_image") and Path(s["generated_image"]).exists()],
            key=lambda s: s["index"],
        )
        self.setWindowTitle(f"分镜 #{current_index} 预览")
        self.setObjectName("storyboardPreviewDialog")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setStyleSheet("background: transparent;")
        self._main_layout.addWidget(self._image_label, stretch=1)

    def show_preview(self) -> None:
        if not self._sorted:
            return
        self._show_current()
        self.showFullScreen()
        self.exec()

    def _show_current(self) -> None:
        if not self._sorted:
            self.reject()
            return
        sb = self._sorted[self._current_pos()]
        path = sb["generated_image"]
        px = QPixmap(path)
        if px.isNull():
            return
        screen = self.screen().size() if self.screen() else QSize(1920, 1080)
        max_w = int(screen.width() * 0.85)
        max_h = int(screen.height() * 0.85)
        scaled = px.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(scaled)
        self.update()

    def _current_pos(self) -> int:
        for i, s in enumerate(self._sorted):
            if s["index"] == self._current_idx:
                return i
        return 0

    def _navigate(self, direction: int) -> None:
        pos = self._current_pos()
        new_pos = pos + direction
        if 0 <= new_pos < len(self._sorted):
            self._current_idx = self._sorted[new_pos]["index"]
            self._show_current()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 220))

        pos = self._current_pos()
        has_prev = pos > 0
        has_next = pos < len(self._sorted) - 1

        painter.setPen(QColor(200, 200, 200, 150))
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(18, self.height() - 16,
                         f"分镜 #{self._sorted[pos]['index']} ({pos + 1}/{len(self._sorted)})    ← / → 切换    ESC 关闭")

        painter.setFont(QFont(painter.font().family(), 32))
        ah = self.height()
        if has_prev:
            painter.fillRect(8, ah // 2 - 40, 50, 80, QColor(255, 255, 255, 40))
            painter.setPen(QColor(255, 255, 255, 200))
            painter.drawText(8, ah // 2 - 40, 50, 80, Qt.AlignCenter, "<")
        if has_next:
            w = self.width()
            painter.fillRect(w - 58, ah // 2 - 40, 50, 80, QColor(255, 255, 255, 40))
            painter.setPen(QColor(255, 255, 255, 200))
            painter.drawText(w - 58, ah // 2 - 40, 50, 80, Qt.AlignCenter, ">")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            x = event.position().toPoint().x()
            w = self.width()
            ah = self.height()
            has_prev = self._current_pos() > 0
            has_next = self._current_pos() < len(self._sorted) - 1
            if has_prev and 8 <= x <= 58 and ah // 2 - 40 <= event.position().toPoint().y() <= ah // 2 + 40:
                self._navigate(-1)
            elif has_next and w - 58 <= x <= w - 8 and ah // 2 - 40 <= event.position().toPoint().y() <= ah // 2 + 40:
                self._navigate(1)
            else:
                self.reject()
        elif event.button() == Qt.RightButton:
            self.reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Left:
            self._navigate(-1)
        elif event.key() == Qt.Key_Right:
            self._navigate(1)
        elif event.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.reject()

    def mouseMoveEvent(self, event) -> None:
        self.update()


class _HistoryImagesDialog(QDialog):
    def __init__(
        self,
        state_service: NovelComicStateService,
        project_id: str,
        episode_num: int,
        storyboard_index: int,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._state_service = state_service
        self._project_id = project_id
        self._episode_num = episode_num
        self._storyboard_index = storyboard_index
        self._selected_path: str = ""
        self.setWindowTitle(f"分镜 #{storyboard_index} 历史图片")
        self.setFixedSize(560, 480)
        self.setObjectName("historyImagesDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel(f"分镜 #{self._storyboard_index} 历史图片")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(8)
        scroll_layout.setAlignment(Qt.AlignTop)

        images_dir = self._state_service.get_project_images_dir(self._project_id)
        ep_dir = images_dir / f"ep_{self._episode_num}"
        image_files: list[Path] = []
        if ep_dir.exists():
            pattern = f"comic_panel_{self._storyboard_index}_*.png"
            image_files = sorted(ep_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

        if not image_files:
            hint = QLabel("暂无历史生成记录")
            hint.setObjectName("assetEmptyLabel")
            hint.setAlignment(Qt.AlignCenter)
            scroll_layout.addWidget(hint)
        else:
            for fp in image_files:
                row = QFrame()
                row.setObjectName("assetItemRow")
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(12, 10, 12, 10)
                row_layout.setSpacing(12)

                thumb = QLabel()
                thumb.setFixedSize(100, 100)
                thumb.setScaledContents(True)
                px = QPixmap(str(fp))
                if not px.isNull():
                    thumb.setPixmap(px.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                row_layout.addWidget(thumb)

                info = QLabel(str(fp.name))
                info.setObjectName("assetItemName")
                row_layout.addWidget(info, stretch=1)

                use_btn = QPushButton("使用")
                use_btn.setObjectName("dialogConfirmBtn")
                use_btn.setCursor(Qt.PointingHandCursor)
                use_btn.clicked.connect(lambda checked, p=str(fp): self._on_use(p))
                row_layout.addWidget(use_btn)

                scroll_layout.addWidget(row)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("dialogCancelBtn")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_use(self, image_path: str) -> None:
        self._selected_path = image_path
        self.accept()

    @property
    def selected_path(self) -> str:
        return self._selected_path


class _AssetManagementDialog(QDialog):
    asset_image_generated = Signal(str, str)

    def __init__(
        self,
        project_id: str,
        episode_num: int,
        chapter_text: str,
        state_service: NovelComicStateService,
        settings_service: SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._episode_num = episode_num
        self._chapter_text = chapter_text
        self._state_service = state_service
        self._settings_service = settings_service
        self._worker: AssetExtractWorker | None = None
        self.setWindowTitle(f"资产管理 — 第{episode_num}集")
        self.setFixedSize(680, 560)
        self.setObjectName("assetManagementDialog")
        self._current_tab = 0
        self._setup_ui()
        self._load_from_project()
        self.asset_image_generated.connect(self._on_asset_image_generated)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("资产管理")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        extract_row = QHBoxLayout()
        extract_btn = QPushButton("\U0001f9e0  提取资产")
        extract_btn.setObjectName("comicGenActionBtn")
        extract_btn.setCursor(Qt.PointingHandCursor)
        extract_btn.clicked.connect(self._on_extract_assets)
        extract_row.addWidget(extract_btn)

        self._extract_status = QLabel("")
        self._extract_status.setObjectName("assetExtractStatus")
        extract_row.addWidget(self._extract_status)
        extract_row.addStretch()
        layout.addLayout(extract_row)

        tab_bar = QFrame()
        tab_bar.setObjectName("assetTabBar")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        self._tab_btns: list[QPushButton] = []
        tab_names = ["\U0001f464  人物", "\U0001f3ed  场景", "\U0001f392  道具"]
        for i, name in enumerate(tab_names):
            btn = QPushButton(name)
            btn.setObjectName("assetTabBtn")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, idx=i: self._switch_tab(idx))
            tab_layout.addWidget(btn)
            self._tab_btns.append(btn)
        tab_layout.addStretch()
        layout.addWidget(tab_bar)

        self._tab_stack = QStackedWidget()
        self._tab_stack.setObjectName("assetTabStack")

        self._character_data = []
        self._scene_data = []
        self._prop_data = []

        self._asset_layouts: dict[str, QVBoxLayout] = {}
        self._batch_total = 0
        self._batch_done = 0

        self._character_list = self._build_asset_list_widget("character")
        self._scene_list = self._build_asset_list_widget("scene")
        self._prop_list = self._build_asset_list_widget("prop")

        self._tab_stack.addWidget(self._character_list)
        self._tab_stack.addWidget(self._scene_list)
        self._tab_stack.addWidget(self._prop_list)
        layout.addWidget(self._tab_stack, stretch=1)

        self._switch_tab(0)

        self._poll_timer = self.startTimer(2000) if self._restore_batch_state() else None

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)
        batch_gen_btn = QPushButton("\U0001f3a8  批量生成资产图 \u25be")
        batch_gen_btn.setObjectName("comicGenBatchBtn")
        batch_gen_btn.setCursor(Qt.PointingHandCursor)
        batch_gen_btn.clicked.connect(self._on_batch_gen_assets_menu)
        self._batch_asset_btn = batch_gen_btn
        bottom_row.addWidget(batch_gen_btn)
        bottom_row.addStretch()

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("dialogCancelBtn")
        close_btn.clicked.connect(self.reject)
        bottom_row.addWidget(close_btn)
        layout.addLayout(bottom_row)

    def _build_asset_list_widget(self, asset_type: str) -> QWidget:
        container = QWidget()
        container.setObjectName(f"assetListContainer_{asset_type}")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("assetItemScroll")
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("assetItemScrollContent")
        asset_layout = QVBoxLayout(scroll_content)
        asset_layout.setContentsMargins(0, 8, 0, 0)
        asset_layout.setSpacing(6)
        asset_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(scroll_content)
        self._asset_layouts[asset_type] = asset_layout

        empty = QLabel("点击「提取资产」按钮从小说文案中提取资产")
        empty.setObjectName("assetEmptyLabel")
        empty.setAlignment(Qt.AlignCenter)
        asset_layout.addWidget(empty)

        layout.addWidget(scroll, stretch=1)
        return container

    def _switch_tab(self, index: int) -> None:
        self._current_tab = index
        self._tab_stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_btns):
            btn.setProperty("active", i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _ep_asset_key(self, ep_num: int) -> str:
        return f"ep_assets_{ep_num}"

    def _load_ep_assets(self, ep_num: int) -> tuple[list[dict], list[dict], list[dict]]:
        project = self._state_service.load_project(self._project_id)
        chars: list[dict] = []
        scenes: list[dict] = []
        props: list[dict] = []
        if not project:
            return chars, scenes, props
        raw = project.extra_data.get(self._ep_asset_key(ep_num), [])
        for item in raw:
            entry = {"name": item.get("name", ""), "desc": item.get("desc", ""), "image_path": item.get("image_path", ""), "ref_image_path": item.get("ref_image_path", "")}
            t = item.get("asset_type", "")
            if t == "character":
                chars.append(entry)
            elif t == "scene":
                scenes.append(entry)
            elif t == "prop":
                props.append(entry)
        return chars, scenes, props

    def _load_from_project(self) -> None:
        self._character_data, self._scene_data, self._prop_data = self._load_ep_assets(self._episode_num)

        has_own = bool(self._character_data or self._scene_data or self._prop_data)

        if not has_own and self._episode_num > 1:
            seen: dict[str, dict] = {}
            for ep in range(1, self._episode_num):
                c, s, p = self._load_ep_assets(ep)
                for items, atype in [(c, "character"), (s, "scene"), (p, "prop")]:
                    for item in items:
                        name = item.get("name", "")
                        if name:
                            item["asset_type"] = atype
                            seen[name] = dict(item)
            for item in seen.values():
                entry = {"name": item.get("name", ""), "desc": item.get("desc", ""), "image_path": item.get("image_path", ""), "ref_image_path": item.get("ref_image_path", "")}
                t = item.get("asset_type", "")
                if t == "character":
                    self._character_data.append(entry)
                elif t == "scene":
                    self._scene_data.append(entry)
                elif t == "prop":
                    self._prop_data.append(entry)

        self._refresh_asset_list("character", self._character_data)
        self._refresh_asset_list("scene", self._scene_data)
        self._refresh_asset_list("prop", self._prop_data)
        total = len(self._character_data) + len(self._scene_data) + len(self._prop_data)
        if total:
            source = "（从前集复制）" if not has_own and self._episode_num > 1 else ""
            self._extract_status.setText(f"已加载 {total} 个资产{source}")
            self._extract_status.setStyleSheet("color: #4ade80;")

    def _save_to_project(self) -> None:
        from clip_synth.models.novel_comic_project_state import NovelComicAsset

        project = self._state_service.load_project(self._project_id)
        if not project:
            return

        def _list_to_assets(data: list[dict], atype: str) -> list:
            return [
                NovelComicAsset(
                    name=item["name"], desc=item["desc"], asset_type=atype,
                    image_path=item.get("image_path", ""),
                )
                for item in data
            ]

        ep_assets = (
            _list_to_assets(self._character_data, "character")
            + _list_to_assets(self._scene_data, "scene")
            + _list_to_assets(self._prop_data, "prop")
        )

        ep_key = self._ep_asset_key(self._episode_num)
        project.extra_data[ep_key] = [
            {
                "name": a.name, "desc": a.desc, "asset_type": a.asset_type,
                "image_path": a.image_path,
                "ref_image_path": item.get("ref_image_path", ""),
            }
            for item, a in zip(
                self._character_data + self._scene_data + self._prop_data,
                ep_assets,
            )
        ]

        merged: list[NovelComicAsset] = []
        seen_names: set[str] = set()
        for ep in range(1, self._episode_num + 1):
            for a in project.extra_data.get(self._ep_asset_key(ep), []):
                name = a.get("name", "")
                if name and name not in seen_names:
                    seen_names.add(name)
                    merged.append(NovelComicAsset(
                        name=name, desc=a.get("desc", ""),
                        asset_type=a.get("asset_type", "prop"),
                        image_path=a.get("image_path", ""),
                    ))
        project.assets = merged

        self._state_service.save_project(project)

    def _on_extract_assets(self) -> None:
        if not self._chapter_text.strip():
            self._extract_status.setText("没有可提取的文本内容")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        self._extract_status.setText("提取中...")
        self._extract_status.setStyleSheet("color: #4fc3f7;")

        cm = self._get_chat_manager()
        self._worker = AssetExtractWorker(self._chapter_text, self._settings_service, chat_manager=cm)
        self._worker.finished.connect(self._on_extract_finished)
        self._worker.error.connect(self._on_extract_error)
        self._worker.start()

    def _get_chat_manager(self) -> MultiRoundChatManager | None:
        settings = self._settings_service.load()
        config = AIModelConfig(
            model_name=settings.text_model.model_name,
            api_key=settings.text_model.api_key,
            base_url=settings.text_model.base_url,
        )
        mgr = MultiRoundChatManager(self._project_id, self._episode_num, self._state_service)
        if mgr.is_multi_round_enabled(config):
            return mgr
        return None

    def _merge_new_assets(self, existing: list[dict], new_items: list[dict]) -> list[dict]:
        existing_names = {item["name"] for item in existing}
        for item in new_items:
            name = item.get("name", "")
            if name and name not in existing_names:
                existing.append({"name": name, "desc": item.get("desc", ""), "image_path": item.get("image_path", "")})
                existing_names.add(name)
        return existing

    def _on_extract_finished(self, characters: list, scenes: list, props: list) -> None:
        self._merge_new_assets(self._character_data, characters)
        self._merge_new_assets(self._scene_data, scenes)
        self._merge_new_assets(self._prop_data, props)
        self._refresh_asset_list("character", self._character_data)
        self._refresh_asset_list("scene", self._scene_data)
        self._refresh_asset_list("prop", self._prop_data)
        self._save_to_project()
        total = len(self._character_data) + len(self._scene_data) + len(self._prop_data)
        self._extract_status.setText(f"提取完成，共 {total} 个资产")
        self._extract_status.setStyleSheet("color: #4ade80;")
        if characters:
            self._switch_tab(0)
        elif scenes:
            self._switch_tab(1)
        elif props:
            self._switch_tab(2)

    def _on_extract_error(self, error_msg: str) -> None:
        self._extract_status.setText(f"提取失败: {error_msg}")
        self._extract_status.setStyleSheet("color: #f87171;")

    def _refresh_asset_list(self, asset_type: str, data: list[dict]) -> None:
        layout = self._asset_layouts.get(asset_type)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()

        if data:
            self._asset_rows = getattr(self, '_asset_rows', {})
            self._asset_rows[asset_type] = []
            for asset in data:
                row = _AssetItemRow(asset)
                row.generate_clicked.connect(lambda n=asset["name"]: self._on_gen_asset_image(n))
                row.desc_edit_requested.connect(self._on_edit_asset_desc)
                row.image_clicked.connect(self._on_preview_asset_image)
                row.upload_clicked.connect(lambda r=row: self._on_upload_asset_image(r))
                row.delete_clicked.connect(lambda a=asset, at=asset_type: self._on_delete_asset(a, at))
                row.ref_image_clicked.connect(lambda n=asset["name"]: self._on_select_ref_image(n))
                layout.addWidget(row)
                self._asset_rows[asset_type].append(row)

        if not data:
            empty = QLabel("暂无资产数据")
            empty.setObjectName("assetEmptyLabel")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty)

        add_row = _AssetAddRow(asset_type)
        add_row.add_clicked.connect(lambda at=asset_type: self._on_add_asset(at))
        layout.addWidget(add_row)

    def _on_preview_asset_image(self, image_path: str) -> None:
        show_image_viewer(image_path, "图片预览", self)

    def _on_upload_asset_image(self, row: "_AssetItemRow") -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择资产图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*)",
        )
        if not file_path:
            return
        images_dir = self._state_service.get_project_images_dir(self._project_id)
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', row._asset.get("name", "asset"))
        dest = str(images_dir / f"asset_{safe_name}.png")
        try:
            px = QPixmap(file_path)
            if px.isNull():
                qm = _ConfirmDialog("选择的文件不是有效的图片", self)
                qm.setWindowTitle("无法读取")
                qm.exec()
                return
            px.save(dest, "PNG")
        except Exception as e:
            qm = _ConfirmDialog(f"无法保存图片: {e}", self)
            qm.setWindowTitle("保存失败")
            qm.exec()
            return
        row._asset["image_path"] = dest
        row._image_path = dest
        row._show_thumb_pixmap(dest)
        row._gen_btn.setText("重新生成")
        for data_list in (self._character_data, self._scene_data, self._prop_data):
            for item in data_list:
                if item.get("name") == row._asset.get("name"):
                    item["image_path"] = dest
                    break
        self._save_to_project()
        self.asset_image_generated.emit(row._asset.get("name", ""), dest)

    def _on_delete_asset(self, asset: dict, asset_type: str) -> None:
        name = asset.get("name", "未知")
        dialog = _ConfirmDialog(f"确定要删除资产「{name}」吗？", self)
        if dialog.exec() != QDialog.Accepted:
            return
        type_map = {"character": self._character_data, "scene": self._scene_data, "prop": self._prop_data}
        data_list = type_map.get(asset_type, [])
        if asset in data_list:
            data_list.remove(asset)
        self._save_to_project()
        self._refresh_asset_list(asset_type, data_list)

    def _on_add_asset(self, asset_type: str) -> None:
        dialog = _AddAssetNameDialog(asset_type, self)
        if dialog.exec() != QDialog.Accepted or not dialog.asset_name:
            return
        name = dialog.asset_name
        desc = dialog.asset_desc
        type_map = {"character": self._character_data, "scene": self._scene_data, "prop": self._prop_data}
        data_list = type_map[asset_type]
        for item in data_list:
            if item.get("name") == name:
                qm = _ConfirmDialog(f"资产「{name}」已存在", self)
                qm.setWindowTitle("重复名称")
                qm.exec()
                return
        item = {"name": name, "desc": desc, "image_path": ""}
        data_list.append(item)
        self._save_to_project()
        self._refresh_asset_list(asset_type, data_list)

    def _on_select_ref_image(self, asset_name: str) -> None:
        menu = QMenu(self)
        menu.setObjectName("assetRefMenu")

        upload_action = menu.addAction("上传图片")
        upload_action.triggered.connect(lambda: self._on_upload_ref_image(asset_name))

        choose_action = menu.addAction("从人物资产选择")
        choose_action.triggered.connect(lambda: self._on_choose_ref_from_assets(asset_name))

        clear_action = menu.addAction("清除垫图")
        clear_action.triggered.connect(lambda: self._on_clear_ref_image(asset_name))

        row = self._find_asset_row(asset_name)
        if row:
            btn = row._ref_btn
            pos = btn.mapToGlobal(btn.rect().bottomLeft())
            menu.exec(pos)
        else:
            menu.exec(QCursor.pos())

    def _on_upload_ref_image(self, asset_name: str) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择垫图图片", "", "图片文件 (*.png *.jpg *.jpeg *.webp);;所有文件 (*.*)",
        )
        if not file_path:
            return
        images_dir = self._state_service.get_project_images_dir(self._project_id)
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', f"ref_{asset_name}")
        dest = str(images_dir / f"{safe_name}{Path(file_path).suffix}")
        try:
            from shutil import copy2
            copy2(file_path, dest)
        except Exception as e:
            logger.error("复制垫图失败: %s", e)
            return
        row = self._find_asset_row(asset_name)
        if row:
            row.set_ref_image(dest)
        self._save_to_project()

    def _on_choose_ref_from_assets(self, asset_name: str) -> None:
        candidates = []
        for item in self._character_data:
            img = item.get("image_path", "")
            nm = item.get("name", "")
            if img and Path(img).exists():
                candidates.append((nm, img))
        if not candidates:
            QMessageBox.information(self, "无可选垫图", "没有已生成图片的人物资产可供选择")
            return

        dialog = _ChooseRefDialog(candidates, self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        img_path = dialog.selected_image_path
        if not img_path:
            return
        row = self._find_asset_row(asset_name)
        if row:
            row.set_ref_image(img_path)
        self._save_to_project()

    def _on_clear_ref_image(self, asset_name: str) -> None:
        row = self._find_asset_row(asset_name)
        if row:
            row.set_ref_image("")
        self._save_to_project()

    def _on_gen_asset_image(self, asset_name: str) -> None:
        row = self._find_asset_row(asset_name)
        if row is None:
            return

        project = self._state_service.load_project(self._project_id)
        gen_settings = {}
        if project:
            gen_settings = project.extra_data.get("gen_settings", {})

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
            self._extract_status.setText("请先在系统配置中设置图片生成模型")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        asset_type = self._get_asset_type(asset_name)
        asset_desc = self._get_asset_desc(asset_name)
        type_label = {"character": "人物图", "scene": "场景图", "prop": "道具图"}.get(asset_type, asset_type)

        global_prefix = _get_effective_prefix(gen_settings)
        prompt = f"资产名称：{asset_name}，资产类型：{type_label}，{asset_desc}"
        if global_prefix:
            prompt = f"{global_prefix}，{asset_name}，{prompt}"

        if asset_type == "character":
            prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图，需要把人物名字显示在图片上"
        elif asset_type == "scene":
            prompt += "，生成9机位的不同方向的视角图"
        elif asset_type == "prop":
            prompt += "，生成9机位的不同方向的视角图"

        row.set_generating()

        ref_image_path = None
        for data_list in (self._character_data, self._scene_data, self._prop_data):
            for item in data_list:
                if item.get("name") == asset_name:
                    ref_image_path = item.get("ref_image_path", "")
                    break
        reference_images = None
        if ref_image_path and Path(ref_image_path).exists():
            reference_images = [ref_image_path]

        resolution = gen_settings.get("resolution", "1K")
        size = _square_size_from_resolution(resolution)
        ImageGenService.instance().submit(
            image_config, prompt,
            _make_asset_on_done(
                self._state_service, self._project_id, self._episode_num,
                asset_name, self.asset_image_generated,
            ),
            _make_asset_on_error(
                self._project_id, asset_name, self.asset_image_generated,
            ),
            size=size,
            reference_images=reference_images,
            resolution=resolution,
            aspect_ratio="1:1",
            text_model_config=image_text_config,
            prompt_rewrite=gen_settings.get("prompt_rewrite", True),
        )

    def _on_asset_image_generated(self, asset_name: str, image_path: str) -> None:
        row = self._find_asset_row(asset_name)
        if row is None:
            return
        if image_path:
            row.set_image(image_path)
        else:
            row.set_gen_error()

    def _find_asset_row(self, asset_name: str) -> "_AssetItemRow | None":
        for rows in getattr(self, '_asset_rows', {}).values():
            for row in rows:
                if row._asset.get("name") == asset_name:
                    return row
        return None

    def _get_asset_type(self, asset_name: str) -> str:
        for data_list, atype in [
            (self._character_data, "character"),
            (self._scene_data, "scene"),
            (self._prop_data, "prop"),
        ]:
            for item in data_list:
                if item.get("name") == asset_name:
                    return atype
        return ""

    def _get_asset_desc(self, asset_name: str) -> str:
        for data_list in (self._character_data, self._scene_data, self._prop_data):
            for item in data_list:
                if item.get("name") == asset_name:
                    return item.get("desc", "")
        return ""

    def _on_edit_asset_desc(self, asset_name: str, current_desc: str) -> None:
        dialog = _DescEditDialog(asset_name, current_desc, self)
        dialog.setWindowTitle(f"编辑资产描述 - {asset_name}")
        if dialog.exec() == QDialog.Accepted:
            new_desc = dialog.edited_desc
            for data_list in (self._character_data, self._scene_data, self._prop_data):
                for item in data_list:
                    if item.get("name") == asset_name:
                        item["desc"] = new_desc
                        break
            self._save_to_project()
            for rows in getattr(self, '_asset_rows', {}).values():
                for row in rows:
                    if row._asset.get("name") == asset_name:
                        row.update_desc(new_desc)

    def _update_batch_status(self) -> None:
        if not self.isVisible():
            return
        ctr = _asset_batch_counter.get(self._project_id)
        if ctr is None:
            return
        total, done = ctr[0], ctr[1]
        pending = ImageGenService.instance().pending_count
        running = total - done - pending
        if done >= total:
            self._extract_status.setText(
                f"批量生成完成！共 {total} 张"
            )
            self._extract_status.setStyleSheet("color: #4ade80;")
            logger.info("批量资产图生成完成: %d/%d", done, total)
        else:
            self._extract_status.setText(
                f"队列: {total} 张 | "
                f"已完成: {done} | "
                f"进行中: {running} | "
                f"等待: {pending}"
            )
            self._extract_status.setStyleSheet("color: #4fc3f7;")
            logger.info(
                "批量资产生图进度: %d/%d 已完成, %d 进行中, %d 等待",
                done, total, running, pending,
            )

    def _restore_batch_state(self) -> bool:
        ctr = _asset_batch_counter.get(self._project_id)
        if ctr is None:
            return False
        self._batch_total = ctr[0]
        self._batch_done = ctr[1]
        if ctr[0] > ctr[1] or ImageGenService.instance().pending_count > 0:
            self._update_batch_status()
            return True
        return False

    def timerEvent(self, event) -> None:
        if not self.isVisible():
            if self._poll_timer is not None:
                self.killTimer(self._poll_timer)
                self._poll_timer = None
            return
        if self._batch_done >= self._batch_total and ImageGenService.instance().pending_count == 0:
            if self._poll_timer is not None:
                self.killTimer(self._poll_timer)
                self._poll_timer = None
            _asset_batch_counter.pop(self._project_id, None)
            return
        self._update_batch_status()

    def _on_batch_gen_assets_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("batchGenMenu")

        all_action = menu.addAction("📦  全部")
        all_action.triggered.connect(lambda: self._run_batch_asset_gen("all"))

        char_action = menu.addAction("🧑  仅人物")
        char_action.triggered.connect(lambda: self._run_batch_asset_gen("character"))

        scene_action = menu.addAction("🏞  仅场景")
        scene_action.triggered.connect(lambda: self._run_batch_asset_gen("scene"))

        prop_action = menu.addAction("🔮  仅道具")
        prop_action.triggered.connect(lambda: self._run_batch_asset_gen("prop"))

        pos = self._batch_asset_btn.mapToGlobal(self._batch_asset_btn.rect().bottomLeft())
        menu.exec(pos)

    def _run_batch_asset_gen(self, filter_type: str) -> None:
        project = self._state_service.load_project(self._project_id)
        gen_settings = {}
        if project:
            gen_settings = project.extra_data.get("gen_settings", {})

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
            self._extract_status.setText("请先在系统配置中设置图片生成模型")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        concurrency = gen_settings.get("concurrency", 3)
        ImageGenService.instance().set_concurrency(concurrency)

        all_assets: list[tuple[str, str]] = []
        skipped_count = 0
        type_map: dict[str, tuple[list, str]] = {
            "character": (self._character_data, "character"),
            "scene": (self._scene_data, "scene"),
            "prop": (self._prop_data, "prop"),
        }
        if filter_type == "all":
            for data_list, atype in type_map.values():
                for item in data_list:
                    img = item.get("image_path", "")
                    if img and Path(img).exists():
                        skipped_count += 1
                        continue
                    all_assets.append((item["name"], atype))
        else:
            data_list, atype = type_map[filter_type]
            for item in data_list:
                img = item.get("image_path", "")
                if img and Path(img).exists():
                    skipped_count += 1
                    continue
                all_assets.append((item["name"], atype))

        if not all_assets:
            msg = "所有资产已有图片，无需生成" if skipped_count > 0 else "没有需要生成的资产"
            self._extract_status.setText(msg)
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        if skipped_count > 0:
            logger.info("跳过 %d 个已有图片的资产", skipped_count)

        count = len(all_assets)
        ctr = _asset_batch_counter.get(self._project_id)
        if ctr is None:
            ctr = [0, 0]
            _asset_batch_counter[self._project_id] = ctr
        ctr[0] += count
        self._batch_total = ctr[0]
        self._batch_done = ctr[1]
        concurrency = gen_settings.get("concurrency", 3)
        pending = ImageGenService.instance().pending_count + count
        running = ctr[0] - ctr[1] - pending
        self._extract_status.setText(
            f"队列: {ctr[0]} 张 | 并发: {concurrency} | 进行中: {running} | 等待: {pending}"
        )
        self._extract_status.setStyleSheet("color: #4fc3f7;")
        logger.info(
            "追加批量生图: +%d 张 (filter=%s), 总计 %d 张, 并发数 %d",
            count, filter_type, ctr[0], concurrency,
        )

        if self._poll_timer is None:
            self._poll_timer = self.startTimer(2000)

        for asset_name, asset_type in all_assets:
            row = self._find_asset_row(asset_name)
            if row:
                row.set_generating()

            asset_desc = self._get_asset_desc(asset_name)
            type_label = {"character": "人物图", "scene": "场景图", "prop": "道具图"}.get(asset_type, asset_type)

            global_prefix = _get_effective_prefix(gen_settings)
            prompt = f"资产名称：{asset_name}，资产类型：{type_label}，{asset_desc}"
            if global_prefix:
                prompt = global_prefix + ", " + asset_name + "，" + prompt

            if asset_type == "character":
                prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图，需要把人物名字显示在图片上"
            elif asset_type == "scene":
                prompt += "，生成9机位的不同方向的视角图"
            elif asset_type == "prop":
                prompt += "，生成9机位的不同方向的视角图"

            resolution = gen_settings.get("resolution", "1K")
            size = _square_size_from_resolution(resolution)
            ref_image_path = None
            for data_list, _ in type_map.values():
                for item in data_list:
                    if item.get("name") == asset_name:
                        ref_image_path = item.get("ref_image_path", "")
                        break
            reference_images = None
            if ref_image_path and Path(ref_image_path).exists():
                reference_images = [ref_image_path]
            ImageGenService.instance().submit(
                image_config, prompt,
                _make_asset_on_done(
                    self._state_service, self._project_id, self._episode_num,
                    asset_name, self.asset_image_generated,
                ),
                _make_asset_on_error(
                    self._project_id, asset_name, self.asset_image_generated,
                ),
                size=size,
                reference_images=reference_images,
                resolution=resolution,
                aspect_ratio="1:1",
                text_model_config=image_text_config,
                prompt_rewrite=gen_settings.get("prompt_rewrite", True),
            )


class _AssetAddRow(QFrame):
    add_clicked = Signal()

    def __init__(self, asset_type: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("assetAddRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        icon = QLabel("+")
        icon.setObjectName("assetAddIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(28, 28)
        layout.addWidget(icon)

        btn = QPushButton(f"新增资产")
        btn.setObjectName("assetAddBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.add_clicked.emit)
        layout.addWidget(btn, stretch=1)


class _AssetItemRow(QFrame):
    generate_clicked = Signal()
    desc_edit_requested = Signal(str, str)
    image_clicked = Signal(str)
    upload_clicked = Signal()
    delete_clicked = Signal()
    ref_image_clicked = Signal(str)

    def __init__(self, asset: dict, parent: QWidget | None = None, read_only: bool = False):
        super().__init__(parent)
        self._asset = asset
        self._read_only = read_only
        self.setObjectName("assetItemRow" + ("ReadOnly" if read_only else ""))
        if read_only:
            self.setStyleSheet("background: transparent; border: none;")
        self._thumb_img: QLabel | None = None
        self._thumb_frame: QFrame | None = None
        self._image_path: str = ""
        self._ref_frame: QFrame | None = None
        self._ref_img: QLabel | None = None
        self._ref_name_label: QLabel | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # -- 主缩略图 --
        self._thumb_frame = QFrame()
        self._thumb_frame.setObjectName("assetThumbPlaceholder")
        self._thumb_frame.setFixedSize(100, 100)
        self._thumb_frame.setCursor(Qt.PointingHandCursor)
        thumb_layout = QVBoxLayout(self._thumb_frame)
        thumb_layout.setContentsMargins(4, 4, 4, 4)
        thumb_layout.setAlignment(Qt.AlignCenter)

        self._thumb_img = QLabel("\U0001f5bc")
        self._thumb_img.setAlignment(Qt.AlignCenter)
        self._thumb_img.setStyleSheet("font-size: 20px;")
        self._thumb_img.setScaledContents(True)
        thumb_layout.addWidget(self._thumb_img)

        self._image_path = self._asset.get("image_path", "")
        if self._image_path and Path(self._image_path).exists():
            self._show_thumb_pixmap(self._image_path)

        self._thumb_frame.mousePressEvent = self._on_thumb_click
        layout.addWidget(self._thumb_frame)

        # -- 垫图预览区 --
        self._ref_frame = QFrame()
        self._ref_frame.setObjectName("assetRefPreview")
        self._ref_frame.setFixedSize(60, 80)
        self._ref_frame.setToolTip("垫图图片（点击更换）")
        self._ref_frame.setCursor(Qt.PointingHandCursor)
        ref_vlayout = QVBoxLayout(self._ref_frame)
        ref_vlayout.setContentsMargins(2, 2, 2, 2)
        ref_vlayout.setSpacing(2)
        ref_vlayout.setAlignment(Qt.AlignCenter)

        self._ref_img = QLabel()
        self._ref_img.setAlignment(Qt.AlignCenter)
        self._ref_img.setScaledContents(True)
        self._ref_img.setFixedSize(56, 56)
        ref_vlayout.addWidget(self._ref_img)

        self._ref_name_label = QLabel()
        self._ref_name_label.setObjectName("assetRefName")
        self._ref_name_label.setAlignment(Qt.AlignCenter)
        ref_vlayout.addWidget(self._ref_name_label)

        self._ref_frame.mousePressEvent = self._on_ref_click
        layout.addWidget(self._ref_frame)

        # -- 名称+描述 --
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        name_label = QLabel(self._asset.get("name", ""))
        name_label.setObjectName("assetItemName")
        info_layout.addWidget(name_label)

        self._desc_label = QLabel(self._asset.get("desc", ""))
        self._desc_label.setObjectName("assetItemDesc")
        self._desc_label.setWordWrap(True)
        self._desc_label.setCursor(Qt.IBeamCursor)
        self._desc_label.mouseDoubleClickEvent = self._on_desc_double_click
        info_layout.addWidget(self._desc_label)

        layout.addLayout(info_layout, stretch=1)

        # -- 操作按钮 --
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        self._gen_btn = QPushButton("生成图片")
        self._gen_btn.setObjectName("assetGenImageBtn")
        self._gen_btn.setCursor(Qt.PointingHandCursor)
        self._gen_btn.clicked.connect(self.generate_clicked.emit)
        btn_col.addWidget(self._gen_btn)

        self._upload_btn = QPushButton("上传图片")
        self._upload_btn.setObjectName("assetUploadBtn")
        self._upload_btn.setCursor(Qt.PointingHandCursor)
        self._upload_btn.clicked.connect(self.upload_clicked.emit)
        btn_col.addWidget(self._upload_btn)

        self._ref_btn = QPushButton("\U0001f4dd 垫图")
        self._ref_btn.setObjectName("assetRefBtn")
        self._ref_btn.setCursor(Qt.PointingHandCursor)
        self._ref_btn.clicked.connect(lambda: self.ref_image_clicked.emit(self._asset.get("name", "")))
        btn_col.addWidget(self._ref_btn)

        self._delete_btn = QPushButton("\u2715")
        self._delete_btn.setObjectName("assetDeleteBtn")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setFixedWidth(30)
        self._delete_btn.setToolTip("删除资产")
        self._delete_btn.clicked.connect(self.delete_clicked.emit)

        layout.addLayout(btn_col)
        layout.addWidget(self._delete_btn)

        if self._read_only:
            self._gen_btn.setVisible(False)
            self._upload_btn.setVisible(False)
            self._ref_btn.setVisible(False)
            self._delete_btn.setVisible(False)
            self._desc_label.setCursor(Qt.ArrowCursor)

        self._sync_ref_preview()

    def _sync_ref_preview(self) -> None:
        ref_path = self._asset.get("ref_image_path", "")
        has_ref = bool(ref_path and Path(ref_path).exists())
        self._ref_frame.setVisible(has_ref)
        if has_ref:
            px = QPixmap(ref_path)
            if not px.isNull():
                self._ref_img.setPixmap(px.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self._ref_name_label.setText(Path(ref_path).stem[:10])
            self._ref_btn.setText("\U0001f4f9 垫图")
            self._ref_btn.setStyleSheet("")
        else:
            self._ref_img.clear()
            self._ref_name_label.setText("")
            self._ref_btn.setText("\U0001f4dd 垫图")

    def set_ref_image(self, image_path: str) -> None:
        self._asset["ref_image_path"] = image_path
        self._sync_ref_preview()

    def _on_thumb_click(self, event) -> None:
        if self._image_path and Path(self._image_path).exists():
            self.image_clicked.emit(self._image_path)

    def _on_ref_click(self, event) -> None:
        ref_path = self._asset.get("ref_image_path", "")
        if ref_path and Path(ref_path).exists():
            self.image_clicked.emit(ref_path)

    def _on_desc_double_click(self, event) -> None:
        self.desc_edit_requested.emit(
            self._asset.get("name", ""),
            self._asset.get("desc", ""),
        )

    def update_desc(self, new_desc: str) -> None:
        self._asset["desc"] = new_desc
        self._desc_label.setText(new_desc)

    def set_generating(self) -> None:
        self._gen_btn.setText("生成中...")
        self._gen_btn.setEnabled(False)

    def set_image(self, image_path: str) -> None:
        self._asset["image_path"] = image_path
        self._image_path = image_path
        self._show_thumb_pixmap(image_path)
        self._gen_btn.setText("重新生成")
        self._gen_btn.setEnabled(True)

    def set_gen_error(self) -> None:
        self._gen_btn.setText("生成失败")
        self._gen_btn.setEnabled(True)

    def _show_thumb_pixmap(self, image_path: str) -> None:
        if self._thumb_img and Path(image_path).exists():
            px = QPixmap(image_path)
            if not px.isNull():
                self._thumb_img.setPixmap(px.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation))
