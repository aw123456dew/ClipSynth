import concurrent.futures
import json
import logging
import re
import struct
import threading
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QImage, QImageReader, QPainter, QPen, QPixmap
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
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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
    {"name": "角色名-版本", "desc": "详细描述"}
  ],
  "scenes": [
    {"name": "场景名-版本", "desc": "详细描述"}
  ],
  "props": [
    {"name": "道具名", "desc": "详细描述"}
  ]
}

资产分类说明：
1. 人物（characters）：文本中出现的所有具名角色、有明确身份的角色，以及**无名的路人、群演、服务人员等**（如"路人""同学A""服务员""司机"等）。即使只在某个场景中出现一次，也**必须提取**。
2. 场景（scenes）：文本中描述的所有地点、环境、空间，包括**过渡性/一次性场景**如走廊、门口、楼梯间、电梯内、街角、阳台等。哪怕场景只出现一次，也**必须提取**。
3. 道具（props）：文本中出现的所有物品、装备、工具、武器等

要求：
1. 每个资产必须包含名称(name)和详细描述(desc)
2. 只提取文本中明确出现或强烈暗示的资产，不要凭空捏造
3. 如果某类资产不存在，返回空数组
4. 描述必须具体到可以用于 AI 生图的程度，不同类型有不同的描述重点：

   - 人物（characters）：仅客观描述外貌特征——性别、年龄、发型、脸型、五官特点、身材、着装（衣服款式、颜色、材质）。**严禁描述角色的表情、神态、情绪、内心想法或性格特点（如"面带微笑""愤怒地""开心地""沮丧地"等），只写客观可见的静态外观**。
     **外观补全规则**：如果原文没有描述某个角色的外观，你必须根据该角色的身份、年龄、社会地位和故事基调，**发挥想象补全一套完整的外观描述**。补全的外观必须具有**唯一性**——每个角色的长相、发型、着装风格必须明显不同，让读者一眼就能区分。例如：一个高冷学霸 vs 一个阳光运动少年，即便原文没写外貌，你也应该给前者配眼镜/整齐发型/深色着装，给后者配运动服/凌乱发型/亮色着装。同一个角色的不同版本（如常服版 vs 校服版）除外，它们共享同一基础外观，仅在着装上有差异。

   - 场景（scenes）：只客观描述环境本身——空间大小、建筑风格、光线氛围、色调、时间、天气、装饰、设施。**必须详细描述场景的空间布局**，例如左侧/右侧/前方/后方各有什么、家具和物品的摆放位置、空间的开阔程度等。**严禁描述场景中的人物数量、人物活动或"很多人""空无一人"等涉及人物的描述**。对于走廊、门口等过渡性场景，同样要描述其空间特征（宽度、长度、左右两侧墙壁材质、地面材质、照明方式等）。
   
   - 道具（props）：描述物品的外观、材质、颜色、尺寸、状态（新旧/破损/脏污等）。和场景一样，只描述物品本身，不能说角色在使用它。**重要限制：只描述物品的物理外观，严禁描述物品内部的文字内容或显示内容。** 例如手机只写"黑色智能手机，玻璃背板，银色金属边框"，不写屏幕上显示了什么；笔记本只写"棕色皮革笔记本，A5尺寸"，不写里面写了什么字；书只写"红色封面旧书，书脊有磨损"，不写书的内容。

5. **版本识别规则（重要）**：同一个人物或场景如果以不同的形态/状态出现，必须拆分为多个独立的资产条目，名称中用 `-` 分隔版本标识：
   - 人物示例：`陆宴知-常服版`、`陆宴知-校服版`、`陆宴知-运动装版`、`林清许-古装版`、`林清许-现代装版`
   - 场景示例：`教室-白天版`、`教室-黄昏版`、`校园-白天版`、`校园-夜晚版`、`公园-晴天版`、`公园-雨天版`、`走廊-白天版`、`走廊-夜晚版`
   - 什么时候拆分：当文本明确提到换装、换场景、时间变化（白天/黑夜/季节）、天气变化、地点氛围变化时，考虑创建新版本
   - 如果全文只出现一种状态，则不需要加版本后缀，直接用名称即可，如 `教室`、`走廊`、`门口`
   - 版本后缀要简洁，用 2-4 个字概括核心差异，如 `常服版`、`校服版`、`白天版`、`夜晚版`、`雨天版`
   - 同一个基础名称的不同版本，在 desc 中要重点描述它们之间的差异化特征（着装区别、光线氛围区别等）

6. 描述中严禁使用双引号、单引号、破折号、省略号、书名号等标点符号，只能使用逗号、句号、感叹号、问号、顿号
7. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。"""


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
1. text 为该分镜对应的原文片段，保留原文文字，不要做任何修改、润色、删减或添加。每个分镜对应的text不得低于30个字，不得超过60个字。并且一个分镜中不超出3个句号
2. 每个分镜对应一页漫画，一页漫画 1-3 格。一个格能表达清楚就只用 1 格（大画面），内容较多时用 2 格，复杂时才用 3-4 格。
3. panel_count_suggestion 是推荐格数，根据本页内容的节奏和复杂度给出合理建议（整数 1-4）
4. present_characters 列出本页出场的人物名称，不要遗漏
5. bubbles 列出本页中所有角色的对话，speaker 是说话人，text 是对话内容。text 字段中凡是对话部分都要提取到 bubbles 中
6. narrative 仅保留角色的内心独白、内心想法或主观看法（心理活动、内心感受、主观评价等），按顺序放入数组。其他所有的叙事描述（场景描写、环境交代、角色动作、面部表情等）**一律省略不放入 narrative**，以精简内容，并且移除所有无意义的标点符号（如破折号、省略号、书名号、单双引号等）。
7. 所有分镜按原文顺序排列，覆盖全文，不要遗漏原文内容
8. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符
9. 所有字段必须使用与原文一致的语言（原文是中文则用中文，英文则用英文），**严禁出现英文的人称代词(he/she/him/her等)或英文单词，中文原文必须全部用中文表达**
10. 旁白和对话的文字中必须移除无意义的标点符号（如破折号、省略号、书名号、单双引号等），只保留逗号、句号、感叹号、问号、顿号"""


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


MATCH_ASSETS_SYSTEM_PROMPT = """
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
1. 分镜列表，每个分镜包含编号、原文文本、出场人物列表、对话台词、旁白叙述等详细信息
2. 资产池，分为人物(characters)、场景(scenes)、道具(props)三类，每项有名称

核心规则：
1. 每个分镜可以选择零个、一个或多个场景（scenes），根据分镜内容判断发生在哪些场景中。场景字段为数组，哪怕只有一个场景也要用数组格式
2. 每个分镜可以选择零个或多个人物（characters），优先参考分镜已有的 present_characters 字段
3. 每个分镜可以选择零个或多个道具（props），根据分镜的原文文本、旁白叙述判断使用了哪些道具
4. 只从提供的资产池中选择，不要编造不存在的资产名称。如果资产池中没有匹配的人物，则从 present_characters 中选择已有角色名
5. 根据分镜的原文文本、出场人物、对话台词和旁白叙述综合判断该分镜发生在哪些场景、出现了哪些人物、使用了哪些道具
6. **版本资产匹配规则**：资产池中可能存在同一基础人物的不同版本（如 `陆宴知-常服版`、`陆宴知-校服版`），同一基础场景的不同版本（如 `教室-白天版`、`教室-夜晚版`）。请根据分镜的时间、天气、角色着装描述，选择最合适的版本。如果分镜描述中是白天教室场景，应匹配 `教室-白天版`；如果是夜晚校园场景，应匹配 `校园-夜晚版`。角色同理，根据原文中该分镜的着装描述选择对应的服装版本。
7. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。
"""


def _get_effective_prefix(gen_settings: dict) -> str:
    """获取选中画风的提示词，与全局前缀无关"""
    style = gen_settings.get("style", "")
    if style == "自定义画风":
        return ""
    if style:
        for preset in COMIC_STYLE_PRESETS:
            if preset["name"] == style:
                return preset["prompt"]
    # 没有设置过画风时，默认使用商业韩漫
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
                scenes = entry.get("scenes", [])
                scene = entry.get("scene", "")
                if isinstance(scenes, list) and scenes:
                    for s in scenes:
                        if s:
                            parts.append(s)
                elif scene:
                    parts.append(scene)
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
            scenes = item.get("scenes", []) or []
            scene = item.get("scene", "")
            result[idx] = {
                "scenes": scenes if isinstance(scenes, list) else ([scenes] if scenes else []),
                "scene": scene,
                "characters": item.get("characters", []) or [],
                "props": item.get("props", []) or [],
            }
        return result


STORYBOARD_DESC_AI_PROMPT = """\
你是一个顶级的图文小说编辑，擅长将小说片段转化为结构化的场景叙事脚本，供下游 AI 自行分格并生成图文。

输出格式要求（最重要）：
只能输出一个纯 JSON 对象，不要输出任何 Markdown、代码块标记、解释。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "summary": "本段核心剧情与情绪走向的一句话概述。",
      "scene": "场景资产名称-版本",
      "characters": ["角色A-版本", "角色B-版本"],
      "props": ["道具名称"],
      "description": "按时间顺序叙述：动作用自然句，对话用台词标签，所有非对话句子原句保留为旁白标签。"
    }
  ]
}

=== 任务 ===
一个分镜对应一段连贯的场景叙事。**不要分格、不要指定格数**。你只需按时间顺序，把这段戏讲清楚：人物在哪里、做了什么动作、说了什么、心里想什么。分格交由下游 AI 自行识别。
场景、人物、道具**只能从该分镜的「已绑定资产」列表中选择，禁止编造**；列表中无合适项时取最接近的。

=== 标签规则（重要） ===
- **动作描述用自然句**：人物的动作、姿态、表情直接写成完整句子，**严禁用"角色名："这种冒号标签**。
  ❌错误：苏渺：眼睛瞪大，整个人僵在椅子上。
  ✅正确：苏渺眼睛瞪大，整个人僵在椅子上。
- **对话用台词标签**：用"角色名-版本："单独成行标出，每条只写一句对话，内容用中文双引号包裹。
  ✅正确：陆衍-西装版："是你吧，小渺？"
- **非对话句子用旁白标签**：按下方"旁白与动作的关系"筛选后，用"旁白："单独成行标出，每条只写一句。**旁白必须逐字照搬原文原句，严禁改写、润色、增删，严禁替换人称**——原文是"我"就写"我"，是"他"就写"他"，绝不改成角色名。

=== 旁白与动作的关系（重要） ===
原文中的非对话句子**不是全部都要进旁白**。按以下逻辑处理：
- **进旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。
- **不进旁白（仅在动作自然句中体现即可）**：环境氛围描写、纯外观罗列。
原文示例："随即骤然转向。径直朝我们这边走来。我心脏砰砰跳。"
✅正确：
  陆衍在过道上骤然转身，身体朝向苏渺工位方向，迈步走来。苏渺坐在座位上，双手紧紧攥在一起，神色紧张。
  旁白："随即骤然转向。"
  旁白："径直朝我们这边走来。"
  旁白："我心脏砰砰跳。"
❌错误（把叙事句吃进动作、漏了旁白）：
  陆衍骤然转身朝苏渺走来。
  旁白："我心脏砰砰跳。"

**环境描写不进旁白的示例：**
❌ 错误：原文"包厢门虚掩着"→ 旁白："包厢门虚掩着"（环境描写，已在动作句中体现）
✅ 正确：原文"包厢门虚掩着"→ 不进旁白，仅在动作句中写"包厢门留有一道缝隙"即可

=== 输入字段 ===
- `原文:` 该分镜原文（最终以此为准）。
- `对话:` 已提取对话（说话人: 内容），用于补全台词，仍需对照原文查漏。
- `旁白:` 已提取独白/看法，用于补全旁白，仍需对照原文查漏。
- `出场人物:` 人物匹配参考。
- `所属段落原文:` 用于指代分析。

=== scene / characters / props 字段 ===
- scene：从已绑定场景资产中选一个最贴合本段的。
- characters：列出本段出场人物，使用完整的"角色名-版本"格式。
- props：列出本段涉及的已绑定道具，无则空数组。
- 以上均**只能取自已绑定资产，禁止编造**。

=== description 的写法（核心） ===
按时间顺序还原本段：
- **动作（自然句，给生图）**：写成可视的静态画面，描述"画面里能看到什么"（姿态、表情、面部朝向、站位、空间关系），不要用动词描述正在发生或将发生的过程。✅"苏渺猛地抬头，眼睛瞪大、嘴唇微张"  ❌"苏渺举起手转过身"。
- **场景/氛围前置**：**动作描述的第一句必须指明当前场景**，直接写该分镜已绑定的场景资产名称即可，**不需要对环境做额外描写**。例如："聚会餐厅内，顾晚专注地盯着智能手机屏幕。" 场景资产名起到提示参考图的作用。❌错误：跳过场景直接写人物动作。**场景必须从该分镜已绑定的 scene 资产中选择**。
- **对话（台词标签）**：用"角色名-版本："标出，一条一句，内容用原文。
- **旁白（旁白标签，给读者）**：原文中的非对话句子**只选择以下类别放入旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。**环境氛围描写、纯外观罗列等已在动作自然句中体现的内容，不再重复放入旁白**。每条旁白单独占一行。

**格式要求（重要）**：description 中每一句旁白和每一句台词都**必须单独占一行**，不要将多句旁白合并到同一行。正确示例：
  包厢内灯光昏暗。顾念站在包厢门口，身体僵住，脸色苍白。
  旁白："程屿川篮球赛获胜庆功宴，我姗姗来迟。"
  旁白："却见到他和别的女孩忘情拥吻。"
❌ 错误（多句旁白挤在同一行）：
  包厢内灯光昏暗。...旁白："..."旁白："..."

=== 内容规则 ===
1. **旁白筛选规则**：原文中的非对话句子**只选以下类别放入旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。**环境氛围描写、纯外观罗列等已在动作自然句中体现的，不再放入旁白**。
2. **旁白忠于原文**：旁白内容必须是原文原句，逐字保留，不得改写、润色、增删或替换人称代词（我/你/他原样保留），更不得自行编造原文没有的句子。
3. **人物身份与关系必须保留**：如"网恋三年的男人""她的顶头上司"等画面画不出的关系，用旁白交代。
4. **动作只写静态可视内容**：严禁用动词描述动态过程；连续动作只取某一瞬间的静态画面。
5. **顺序清晰**：严格按原文时间顺序叙述，便于下游 AI 自然分格。
6. 人称代词依据 `所属段落原文:` 或邻近分镜原文解析指代（仅用于动作叙述确认指代对象，旁白本身人称不变）。
7. **原文不重复原则**：同一句原文**只能出现在旁白或台词中的一种**，不得同时出现在两者中。原文中的对话句只进台词、不进旁白；原文中的叙事/心理/动作叙述句只进旁白、不进台词。旁白和台词互斥，一句原文不能分身两处。
8. 看手机/屏幕时角色目光落在屏幕上。
9. 台词和旁白用中文双引号（""）包裹，句末句号、问号、感叹号保留，移除破折号、省略号、书名号等无实义标点，保留逗号。
10. 自动替换敏感内容。
11. 输出合法 JSON：英文双引号转义为 \\"，中文双引号不转义。

"""


STORYBOARD_DESC_MANUAL_PROMPT = """\
你是一个顶级的图文小说编辑，擅长将小说片段转化为结构化的场景叙事脚本，供下游 AI 自行分格并生成图文。

输出格式要求（最重要）：
只能输出一个纯 JSON 对象，不要输出任何 Markdown、代码块标记、解释。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "summary": "本段核心剧情与情绪走向的一句话概述。",
      "scene": "场景资产名称-版本",
      "characters": ["角色A-版本", "角色B-版本"],
      "props": ["道具名称"],
      "description": "按时间顺序叙述：动作用自然句，对话用台词标签，所有非对话句子原句保留为旁白标签。"
    }
  ]
}

=== 任务 ===
一个分镜对应一段连贯的场景叙事。**不要分格、不要指定格数**。你只需按时间顺序，把这段戏讲清楚：人物在哪里、做了什么动作、说了什么、心里想什么。分格交由下游 AI 自行识别。
场景、人物、道具**只能从该分镜的「已绑定资产」列表中选择，禁止编造**；列表中无合适项时取最接近的。

=== 标签规则（重要） ===
- **动作描述用自然句**：人物的动作、姿态、表情直接写成完整句子，**严禁用"角色名："这种冒号标签**。
  ❌错误：苏渺：眼睛瞪大，整个人僵在椅子上。
  ✅正确：苏渺眼睛瞪大，整个人僵在椅子上。
- **对话用台词标签**：用"角色名-版本："单独成行标出，每条只写一句对话，内容用中文双引号包裹。
  ✅正确：陆衍-西装版："是你吧，小渺？"
- **非对话句子用旁白标签**：按下方"旁白与动作的关系"筛选后，用"旁白："单独成行标出，每条只写一句。**旁白必须逐字照搬原文原句，严禁改写、润色、增删，严禁替换人称**——原文是"我"就写"我"，是"他"就写"他"，绝不改成角色名。

=== 旁白与动作的关系（重要） ===
原文中的非对话句子**不是全部都要进旁白**。按以下逻辑处理：
- **进旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。
- **不进旁白（仅在动作自然句中体现即可）**：环境氛围描写、纯外观罗列。
原文示例："随即骤然转向。径直朝我们这边走来。我心脏砰砰跳。"
✅正确：
  陆衍在过道上骤然转身，身体朝向苏渺工位方向，迈步走来。苏渺坐在座位上，双手紧紧攥在一起，神色紧张。
  旁白："随即骤然转向。"
  旁白："径直朝我们这边走来。"
  旁白："我心脏砰砰跳。"
❌错误（把叙事句吃进动作、漏了旁白）：
  陆衍骤然转身朝苏渺走来。
  旁白："我心脏砰砰跳。"

**环境描写不进旁白的示例：**
❌ 错误：原文"包厢门虚掩着"→ 旁白："包厢门虚掩着"（环境描写，已在动作句中体现）
✅ 正确：原文"包厢门虚掩着"→ 不进旁白，仅在动作句中写"包厢门留有一道缝隙"即可

=== 输入字段 ===
- `原文:` 该分镜原文（最终以此为准）。
- `所属段落原文:` 用于指代分析。

=== scene / characters / props 字段 ===
- scene：从已绑定场景资产中选一个最贴合本段的。
- characters：列出本段出场人物，使用完整的"角色名-版本"格式。
- props：列出本段涉及的已绑定道具，无则空数组。
- 以上均**只能取自已绑定资产，禁止编造**。

=== description 的写法（核心） ===
按时间顺序还原本段：
- **动作（自然句，给生图）**：写成可视的静态画面，描述"画面里能看到什么"（姿态、表情、面部朝向、站位、空间关系），不要用动词描述正在发生或将发生的过程。✅"苏渺猛地抬头，眼睛瞪大、嘴唇微张"  ❌"苏渺举起手转过身"。
- **场景/氛围前置**：**动作描述的第一句必须指明当前场景**，直接写该分镜已绑定的场景资产名称即可，**不需要对环境做额外描写**。例如："聚会餐厅内，顾晚专注地盯着智能手机屏幕。" 场景资产名起到提示参考图的作用。❌错误：跳过场景直接写人物动作。**场景必须从该分镜已绑定的 scene 资产中选择**。
- **对话（台词标签）**：用"角色名-版本："标出，一条一句，内容用原文。
- **旁白（旁白标签，给读者）**：原文中的非对话句子**只选择以下类别放入旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。**环境氛围描写、纯外观罗列等已在动作自然句中体现的内容，不再重复放入旁白**。每条旁白单独占一行。

**格式要求（重要）**：description 中每一句旁白和每一句台词都**必须单独占一行**，不要将多句旁白合并到同一行。正确示例：
  包厢内灯光昏暗。顾念站在包厢门口，身体僵住，脸色苍白。
  旁白："程屿川篮球赛获胜庆功宴，我姗姗来迟。"
  旁白："却见到他和别的女孩忘情拥吻。"
❌ 错误（多句旁白挤在同一行）：
  包厢内灯光昏暗。...旁白："..."旁白："..."

=== 内容规则 ===
1. **旁白筛选规则**：原文中的非对话句子**只选以下类别放入旁白**：内心想法、心理活动、动作过程叙述、人物身份/关系说明（画面画不出的信息）、因果逻辑说明、转场/时间过渡。**环境氛围描写、纯外观罗列等已在动作自然句中体现的，不再放入旁白**。
2. **旁白忠于原文**：旁白内容必须是原文原句，逐字保留，不得改写、润色、增删或替换人称代词（我/你/他原样保留），更不得自行编造原文没有的句子。
3. **人物身份与关系必须保留**：如"网恋三年的男人""她的顶头上司"等画面画不出的关系，用旁白交代。
4. **动作只写静态可视内容**：严禁用动词描述动态过程；连续动作只取某一瞬间的静态画面。
5. **顺序清晰**：严格按原文时间顺序叙述，便于下游 AI 自然分格。
6. 人称代词依据 `所属段落原文:` 或邻近分镜原文解析指代（仅用于动作叙述确认指代对象，旁白本身人称不变）。
7. **原文不重复原则**：同一句原文**只能出现在旁白或台词中的一种**，不得同时出现在两者中。原文中的对话句只进台词、不进旁白；原文中的叙事/心理/动作叙述句只进旁白、不进台词。旁白和台词互斥，一句原文不能分身两处。
8. 看手机/屏幕时角色目光落在屏幕上。
9. 台词和旁白用中文双引号（""）包裹，句末句号、问号、感叹号保留，移除破折号、省略号、书名号等无实义标点，保留逗号。
10. 自动替换敏感内容。
11. 输出合法 JSON：英文双引号转义为 \\"，中文双引号不转义。

"""


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
        all_assets: dict | None = None,
        desc_mode: str = "ai",
    ):
        super().__init__()
        self._storyboards = storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager
        self._single_batch = single_batch
        self._all_assets = all_assets or {"characters": [], "scenes": [], "props": []}
        self._desc_mode = desc_mode
        if desc_mode == "manual":
            self._system_prompt = STORYBOARD_DESC_MANUAL_PROMPT
        else:
            self._system_prompt = STORYBOARD_DESC_AI_PROMPT

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

                        # 该分镜已绑定的资产（只能使用这些，不能编造）
                        bound_assets = sb.get("assets", [])
                        if bound_assets:
                            lines.append(f"已绑定资产: {', '.join(bound_assets)}")

                        seg_text = sb.get("segment_text", "")
                        if seg_text:
                            lines.append(f"所属段落原文: {seg_text}")
                        batch_parts.append("\n".join(lines))
                    full_input = "\n\n".join(batch_parts)

                    prompt = (
                        "请为以下 %d 个分镜分别生成详细的一页漫画分镜描述。"
                        "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。"
                        "必须为每个分镜生成独立的 description。\n\n"
                        "=== 分镜数据 ===\n%s"
                    ) % (len(batch), full_input)

                    content = chat_caller(
                        self._system_prompt, prompt,
                        temperature=0.7, response_format={"type": "json_object"},
                    )
                    indices = [sb["index"] for sb in batch]
                    logger.info("批次分镜 %s 描述AI返回: %s", indices, content[:300])

                    desc_map = self._parse_batch(content, batch)
                    with lock:
                        for sb in batch:
                            idx = sb["index"]
                            entry = desc_map.get(idx, {})
                            desc = entry.get("description", "") if isinstance(entry, dict) else entry
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

    def _parse_batch(self, content: str, batch: list[dict]) -> dict[int, dict]:
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
        
        result: dict[int, dict] = {}
        used: set[int] = set()
        for item in items:
            if not isinstance(item, dict):
                logger.warning("分镜项不是字典类型，跳过")
                continue
            idx = item.get("index", 0)
            desc = item.get("description", "")
            if idx and desc and any(sb["index"] == idx for sb in batch):
                entry = {"description": desc}
                if "matched_assets" in item:
                    entry["matched_assets"] = item["matched_assets"]
                result[idx] = entry
                used.add(idx)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if i < len(batch):
                idx = batch[i]["index"]
                desc = item.get("description", "")
                if desc and idx not in used:
                    entry = {"description": desc}
                    if "matched_assets" in item:
                        entry["matched_assets"] = item["matched_assets"]
                    result[idx] = entry
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
        desc_mode: str = "ai",
    ):
        super().__init__()
        self._storyboard = storyboard
        self._all_storyboards = all_storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service
        self._chat_manager = chat_manager
        self._desc_mode = desc_mode
        if desc_mode == "manual":
            self._system_prompt = STORYBOARD_DESC_MANUAL_PROMPT
        else:
            self._system_prompt = STORYBOARD_DESC_AI_PROMPT

    def _format_asset_pool(self) -> str:
        parts = []
        chars = self._all_assets.get("characters", [])
        scenes = self._all_assets.get("scenes", [])
        props = self._all_assets.get("props", [])
        if chars:
            parts.append(f"人物: {', '.join(chars)}")
        if scenes:
            parts.append(f"场景: {', '.join(scenes)}")
        if props:
            parts.append(f"道具: {', '.join(props)}")
        return "\n".join(parts) if parts else "(无)"

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

                    # 该分镜已绑定的资产（只能使用这些，不能编造）
                    if s["index"] == self._storyboard["index"]:
                        bound_assets = s.get("assets", [])
                        if bound_assets:
                            s_lines.append(f"已绑定资产: {', '.join(bound_assets)}")

                    seg_text = s.get("segment_text", "")
                    if seg_text:
                        s_lines.append(f"所属段落原文: {seg_text}")
                else:
                    s_lines.append(f"摘要: {s.get('summary', '') or s.get('text', '')[:100]}")
                context_parts.append("\n".join(s_lines))
            full_context = "\n\n".join(context_parts)

            prompt = (
                "以下是一页漫画的全部分镜摘要，请为标记为「当前要生成的分镜」的那个分镜生成详细的一页漫画分镜描述。"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。"
                "你可以参考前后分镜的上下文来理解叙事节奏和人物状态。\n\n"
                f"{full_context}"
            )

            content = chat_caller(
                self._system_prompt, prompt,
                temperature=0.7, response_format={"type": "json_object"},
            )
            sb = self._storyboard
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
                    item = items[0]
                    desc = item.get("description", "")
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
    _SIZE_TABLE = {
        "1:1":  {"1K": "1024x1024", "2K": "2048x2048", "4K": "2880x2880"},
        "3:2":  {"1K": "1536x1024", "2K": "2048x1360", "4K": "3520x2336"},
        "2:3":  {"1K": "1024x1536", "2K": "1360x2048", "4K": "2336x3520"},
        "16:9": {"1K": "1824x1024", "2K": "2048x1152", "4K": "3840x2160"},
        "9:16": {"1K": "1024x1824", "2K": "1152x2048", "4K": "2160x3840"},
        "4:3":  {"1K": "1360x1024", "2K": "2048x1536", "4K": "3312x2480"},
        "3:4":  {"1K": "1024x1360", "2K": "1536x2048", "4K": "2480x3312"},
        "21:9": {"1K": "2384x1024", "2K": "2048x880",  "4K": "3840x1648"},
    }
    resolved = resolution or "1K"
    sizes = _SIZE_TABLE.get(ratio)
    if sizes:
        return sizes.get(resolved, sizes["1K"])
    # fallback: 默认按 3:4 处理
    return _SIZE_TABLE["3:4"].get(resolved, "1024x1360")


def _merge_ref_paths(gen_settings: dict, asset_refs: list[str]) -> list[str]:
    """合并画风垫图和资产参考图，垫图放在最前面"""
    sr_settings = gen_settings.get("style_ref", {})
    if sr_settings.get("enabled", False):
        sr_paths = sr_settings.get("paths", [])
        valid_sr = [p for p in sr_paths if p and Path(p).exists()]
        return valid_sr + asset_refs
    return asset_refs


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
        "name": "商业韩漫",
        "prompt": (
            """
            【画风定位】
            韩国主流商业网漫画风（Naver Webtoon / Kakao Page），竖版长条构图，适合手机滑屏阅读。人物精致漂亮，具有偶像气质，画面饱和明亮，视觉冲击力强。
            【色调与光影】
            高饱和度、高明度的鲜艳配色（糖果色、荧光粉、宝石蓝、薄荷绿），整体通透亮眼。光影对比明显，多层光源，高光突出（瞳孔、发丝、金属），阴影偏冷灰或淡紫，柔和但有层次。可带轻微泛光或光晕。
            【线条与上色】
            线条纤细、干净、流畅，无毛躁。上色为平涂+多层渐变（皮肤、头发、衣褶均有细腻过渡），无可见笔触，保留高光层和阴影层。背景可做模糊或光效处理。
            【负面约束】
            不要横屏或方形的页漫构图。不要厚涂油画风格。不要低饱和粉彩或大面积留白（商业韩漫背景较饱满）。
            """
        ),
    },
    {
        "name": "女频-虐文",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。韩系薄涂手绘风，略带水彩晕染，整体色调偏冷，大面积留白营造孤寂感。
            【色调与光影】
            低饱和度、偏冷的灰蓝、浅紫、米白色系，整体压抑忧伤。光线为阴天的散射光或黄昏最后一抹微光，无强烈高光，阴影朦胧柔和。
            【线条与上色】
            线条纤细、略带顿挫感，保留轻微手工痕迹。上色采用半透明薄涂，允许极轻微的晕染或水彩扩散效果，但不过度。避免厚涂。
            【负面约束】
            不要横屏或方形的页漫构图。不要明亮鲜艳色彩，不要阳光明媚的场景，不要微笑表情。不要厚涂油画风格，不要日式漫画的夸张表情（如大哭、Q版）。不要速度线或网点纸。
            """
        ),
    },
    {
        "name": "女频-甜文",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。清新的粉彩插画风格，线条干净，色彩甜蜜。
            【色调与光影】
            高明度、低饱和度的粉彩系配色（淡粉、奶油黄、薄荷绿、浅薰衣草紫），整体明亮通透，适当留白。光线为春日午后的柔光，带淡淡光晕，影子浅而柔和。
            【线条与上色】
            简洁流畅的圆润线条，无锋利棱角。上色为纯粹的平涂，无笔触感，无混色。脸颊、指尖可加淡粉色晕染。
            【负面约束】
            不要横屏或方形的页漫构图。不要任何悲伤压抑的元素。不要厚涂，不要强烈对比的光影。
            """
        ),
    },
    {
        "name": "女频-爽文",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。都市扁平风，线条干脆，配色高级，突出女主强大气场。
            【色调与光影】
            高饱和度的清冷色系（宝石蓝、酒红、墨绿、金属银），搭配大面积中性灰或白色背景。光影对比鲜明，使用硬朗的光束或聚光灯效果，阴影边缘清晰，无柔光。
            【线条与上色】
            线条锋利、流畅，粗细变化明显（关键轮廓稍粗）。上色为平涂+局部渐变（如玻璃、金属反射），保留干净利落的色块感，无笔触纹理。可带轻微磨砂质感。
            【负面约束】
            不要横屏或方形的页漫构图。不要柔美可爱少女心元素。不要日式漫画夸张表情或身材比例。不要厚涂。
            """
        ),
    },
    {
        "name": "女频-古风",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。中国传统工笔淡彩风格，仿古绢本或宣纸背景，线条精细，设色雅致。
            【色调与光影】
            低饱和度的传统国画色（黛蓝、胭脂、鹅黄、石绿、赭石），整体偏暖灰调，无强烈光源，阴影极淡或不表现。
            【线条与上色】
            线条极细且均匀，类似“高古游丝描”的流畅细线。上色为多层薄染的淡彩，保留纸纹或绢纹质感，无笔触堆积。局部可用金粉勾边。
            【负面约束】
            不要横屏或方形的页漫构图。不要日式和风元素（如浮世绘、樱花纹样、和服）。不要厚涂或油画笔触。不要过于鲜艳的荧光色。不要任何欧式元素。
            """
        ),
    },
    {
        "name": " 男频-古风",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。水墨写意风格，强调笔触的飞白和墨色变化，构图留白大胆，突出意境与力量感。
            【色调与光影】
            以黑白灰为主，辅以极少量低饱和的赭石、花青做点缀。光影表现为墨色的浓淡干湿，不使用外光源。
            【线条与上色】
            线条粗犷、有顿挫、带飞白，类似书法用笔。上色为水墨晕染，允许自然的扩散和笔触堆积，保持整体简洁。保留宣纸纹理。
            【负面约束】
            不要横屏或方形的页漫构图。不要日式漫画夸张表情或动作。不要精细机械或铠甲细节（保持写意）。不要甜美或阴柔元素。
            """
        ),
    },
    {
        "name": " 男频-热血战斗",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。韩式热血条漫风格，强调动态拉伸和镜头冲击力，使用简洁的动态线表示速度（避免传统日式速度线）。
            【色调与光影】
            高饱和度的对比色系（烈焰红、电光黄、深蓝紫、金属灰），光影对比极度强烈，使用逆光、爆闪效果，阴影为纯黑或深色块状。
            【线条与上色】
            线条粗犷有力，动作轨迹用干净的流线型动线（非网点速度线）。上色采用硬边色块+少量渐变，保留部分笔触感增强动感。
            【负面约束】
            不要横屏或方形的页漫构图。不要传统日式速度线、网点纸、Q版表情。不要柔和平涂或粉彩色系。
            """
        ),
    },
    {
        "name": "男频-西幻",
        "prompt": (
            """
            【画风定位】
            竖屏长条构图，适合手机滑屏阅读。欧美写实奇幻插画风格，使用厚涂技法，强调体积、光影和材质纹理（金属、布料、皮肤、魔法光效）。
            【色调与光影】
            深邃的暗色调（深蓝、暗紫、墨绿）搭配高亮度的魔法光（橙黄、冰蓝、金色）。光影为戏剧性的点光源（如发光的法杖、篝火、月光），阴影浓郁且有色彩倾向。
            【线条与上色】
            几乎没有外轮廓线，依靠色块和光影塑造形体。上色为厚涂笔刷，有明显笔触堆叠，颜料感强，混合过渡自然。画布可带粗糙纹理。
            【负面约束】
            不要横屏或方形的页漫构图。不要任何漫画线条或速度线。不要平涂风格。不要日式Q版或夸张表情。避免过于甜美的色彩。
            """
        ),
    },
    {
        "name": "男频-都市异能",
        "prompt": (
           """
           【画风定位】
            赛博朋克扁平插画风格，结合霓虹光效，线条硬朗，色彩冷峻但带有高光带。
            【色调与光影】
            以深灰、藏青为底色，搭配高亮度的霓虹色（洋红、电青、荧光黄）。光影为冷色环境光+暖色点光源，阴影多为纯黑，边缘锐利。
            【线条与上色】
            线条极细且锋利，无粗细变化。上色为硬边平涂+光效叠加，保留大量纯黑区域。金属表面、玻璃反射使用高光带（不渐变）。无笔触感。
            【负面约束】
            不要水墨或油画风格。不要日式热血的表情或动作。不要过于繁杂的机械细节（保持扁平简洁）。避免任何复古元素。
           """
        ),
    },
    {
        "name": "女频-悬疑",
        "prompt": (
           """
           【画风定位】
            黑白或极低饱和的素描风格，线条带有不安的扭曲或多重影子，氛围压抑紧张。
            【色调与光影】
            近乎黑白的灰阶，只有关键物体（如血红色信封、绿色眼睛）保留极低饱和度彩点。光影使用强烈的侧光或底光，拉长影子，产生不安感。
            【线条与上色】
            线条粗细不均，有时断续或颤抖，有重复描边（表现焦虑）。上色为铅笔排线或炭笔涂抹，保留粗糙颗粒感，无平滑渐变。
            【负面约束】
            不要明亮色彩，不要平涂或光滑表面。不要浪漫或温馨元素。避免日式可爱或夸张表情。
           """
        ),
    }
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
    def __init__(self, current_settings: dict, project_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self._project_name = project_name
        self.setWindowTitle("生图设置")
        self.setFixedSize(560, 850)
        self.setObjectName("genSettingsDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(12)

        title = QLabel("生图设置")
        title.setObjectName("dialogTitle")
        main_layout.addWidget(title)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("genSettingsScroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        scroll_content.setObjectName("genSettingsScrollContent")
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # === 基本设置 ===
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
        self._prefix_edit.setFixedHeight(80)
        self._prefix_edit.setPlaceholderText("输入全局前缀提示词，会附加到每张生图请求的前面...")
        self._prefix_edit.setPlainText(self._settings.get("prefix", ""))
        layout.addWidget(self._prefix_edit)

        # === 画风垫图（可收缩） ===
        self._sr_expanded = False

        self._sr_toggle_btn = QPushButton("▶  画风垫图")
        self._sr_toggle_btn.setObjectName("genSettingsCollapseBtn")
        self._sr_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._sr_toggle_btn.setFlat(True)
        self._sr_toggle_btn.clicked.connect(self._toggle_sr_section)
        layout.addWidget(self._sr_toggle_btn)

        self._sr_container = QFrame()
        self._sr_container.setObjectName("pnContainer")
        sr_layout = QVBoxLayout(self._sr_container)
        sr_layout.setContentsMargins(0, 0, 0, 0)
        sr_layout.setSpacing(8)

        sr_settings = self._settings.get("style_ref", {})

        self._sr_enabled_cb = QCheckBox("启用画风垫图（开启后画风预设prompt不传入生图）")
        self._sr_enabled_cb.setObjectName("genSettingsCheckbox")
        self._sr_enabled_cb.setChecked(sr_settings.get("enabled", False))
        self._sr_enabled_cb.toggled.connect(self._on_sr_enabled_toggled)
        sr_layout.addWidget(self._sr_enabled_cb)

        self._sr_paths: list[str] = sr_settings.get("paths", [])
        self._sr_path_labels: list[QLabel] = []
        self._sr_remove_btns: list[QPushButton] = []
        for i in range(3):
            row = QHBoxLayout()
            row.setSpacing(8)

            path_label = QLabel("未选择" if i >= len(self._sr_paths) else self._sr_paths[i])
            path_label.setObjectName("dialogFieldLabel")
            path_label.setWordWrap(True)
            row.addWidget(path_label, stretch=1)
            self._sr_path_labels.append(path_label)

            sel_btn = QPushButton(f"选择图片{i+1}")
            sel_btn.setObjectName("dialogConfirmBtn")
            sel_btn.setFixedHeight(28)
            sel_btn.clicked.connect(lambda checked, idx=i: self._on_select_sr_image(idx))
            row.addWidget(sel_btn)

            remove_btn = QPushButton("✕")
            remove_btn.setObjectName("dialogCancelBtn")
            remove_btn.setFixedSize(28, 28)
            remove_btn.clicked.connect(lambda checked, idx=i: self._on_remove_sr_image(idx))
            row.addWidget(remove_btn)
            self._sr_remove_btns.append(remove_btn)

            sr_layout.addLayout(row)

        self._sync_sr_ui()
        self._sr_container.setVisible(False)
        layout.addWidget(self._sr_container)

        # === 页码设置（可收缩） ===
        self._pn_expanded = False

        self._pn_toggle_btn = QPushButton("▶  页码设置")
        self._pn_toggle_btn.setObjectName("genSettingsCollapseBtn")
        self._pn_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._pn_toggle_btn.setFlat(True)
        self._pn_toggle_btn.clicked.connect(self._toggle_pn_section)
        layout.addWidget(self._pn_toggle_btn)

        self._pn_container = QFrame()
        self._pn_container.setObjectName("pnContainer")
        pn_layout = QVBoxLayout(self._pn_container)
        pn_layout.setContentsMargins(0, 0, 0, 0)
        pn_layout.setSpacing(8)

        pn_settings = self._settings.get("page_number", {})

        self._pn_enabled_cb = QCheckBox("启用页码")
        self._pn_enabled_cb.setObjectName("genSettingsCheckbox")
        self._pn_enabled_cb.setChecked(pn_settings.get("enabled", True))
        self._pn_enabled_cb.toggled.connect(self._on_pn_enabled_toggled)
        pn_layout.addWidget(self._pn_enabled_cb)

        pn_prefix_label = QLabel("页码前缀名称")
        pn_prefix_label.setObjectName("dialogFieldLabel")
        pn_layout.addWidget(pn_prefix_label)

        self._pn_prefix_edit = QLineEdit()
        self._pn_prefix_edit.setObjectName("genSettingsInput")
        self._pn_prefix_edit.setPlaceholderText(f"默认使用项目名称，如《项目名称01》")
        self._pn_prefix_edit.setText(pn_settings.get("prefix", ""))
        self._pn_prefix_edit.setFixedHeight(36)
        pn_layout.addWidget(self._pn_prefix_edit)

        color_row = QHBoxLayout()
        color_row.setSpacing(12)

        font_color_layout = QVBoxLayout()
        font_color_layout.setSpacing(4)
        font_color_label = QLabel("字体颜色")
        font_color_label.setObjectName("dialogFieldLabel")
        font_color_layout.addWidget(font_color_label)
        self._font_color_edit = QLineEdit()
        self._font_color_edit.setObjectName("genSettingsInput")
        self._font_color_edit.setPlaceholderText("#000000")
        self._font_color_edit.setText(pn_settings.get("font_color", "#000000"))
        self._font_color_edit.setFixedHeight(36)
        font_color_layout.addWidget(self._font_color_edit)
        color_row.addLayout(font_color_layout)

        bg_color_layout = QVBoxLayout()
        bg_color_layout.setSpacing(4)
        bg_color_label = QLabel("背景色")
        bg_color_label.setObjectName("dialogFieldLabel")
        bg_color_layout.addWidget(bg_color_label)
        self._bg_color_edit = QLineEdit()
        self._bg_color_edit.setObjectName("genSettingsInput")
        self._bg_color_edit.setPlaceholderText("#FFFFFF")
        self._bg_color_edit.setText(pn_settings.get("bg_color", "#FFFFFF"))
        self._bg_color_edit.setFixedHeight(36)
        bg_color_layout.addWidget(self._bg_color_edit)
        color_row.addLayout(bg_color_layout)

        pn_layout.addLayout(color_row)

        opacity_label = QLabel(f"背景透明度: {pn_settings.get('bg_opacity', 200)}")
        opacity_label.setObjectName("dialogFieldLabel")
        pn_layout.addWidget(opacity_label)

        self._pn_opacity_slider = QSlider(Qt.Horizontal)
        self._pn_opacity_slider.setObjectName("genSettingsSlider")
        self._pn_opacity_slider.setMinimum(0)
        self._pn_opacity_slider.setMaximum(255)
        self._pn_opacity_slider.setValue(pn_settings.get("bg_opacity", 200))
        self._pn_opacity_slider.setFixedHeight(20)
        self._pn_opacity_slider.valueChanged.connect(
            lambda v: opacity_label.setText(f"背景透明度: {v}")
        )
        pn_layout.addWidget(self._pn_opacity_slider)

        size_layout = QVBoxLayout()
        size_layout.setSpacing(4)
        self._pn_size_label = QLabel(f"页码大小: {pn_settings.get('size', 0.7):.1f}x")
        self._pn_size_label.setObjectName("dialogFieldLabel")
        size_layout.addWidget(self._pn_size_label)
        self._pn_size_slider = QSlider(Qt.Horizontal)
        self._pn_size_slider.setObjectName("genSettingsSlider")
        self._pn_size_slider.setMinimum(3)
        self._pn_size_slider.setMaximum(20)
        self._pn_size_slider.setValue(int(pn_settings.get("size", 0.7) * 10))
        self._pn_size_slider.setFixedHeight(20)
        self._pn_size_slider.valueChanged.connect(
            lambda v: self._pn_size_label.setText(f"页码大小: {v / 10:.1f}x")
        )
        size_layout.addWidget(self._pn_size_slider)
        pn_layout.addLayout(size_layout)

        align_label = QLabel("位置")
        align_label.setObjectName("dialogFieldLabel")
        pn_layout.addWidget(align_label)

        self._pn_align_combo = QComboBox()
        self._pn_align_combo.setObjectName("genSettingsCombo")
        self._pn_align_combo.addItem("靠左", "left")
        self._pn_align_combo.addItem("居中", "center")
        self._pn_align_combo.addItem("靠右", "right")
        saved_align = pn_settings.get("alignment", "center")
        align_idx = self._pn_align_combo.findData(saved_align)
        self._pn_align_combo.setCurrentIndex(align_idx if align_idx >= 0 else 1)
        self._pn_align_combo.setFixedHeight(36)
        pn_layout.addWidget(self._pn_align_combo)

        self._pn_container.setVisible(False)
        layout.addWidget(self._pn_container)

        layout.addStretch()

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area, stretch=1)

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

        main_layout.addLayout(btn_row)

    def _on_pn_enabled_toggled(self, enabled: bool) -> None:
        self._pn_prefix_edit.setEnabled(enabled)
        self._font_color_edit.setEnabled(enabled)
        self._bg_color_edit.setEnabled(enabled)
        self._pn_opacity_slider.setEnabled(enabled)
        self._pn_size_slider.setEnabled(enabled)
        self._pn_align_combo.setEnabled(enabled)

    def _toggle_pn_section(self) -> None:
        self._pn_expanded = not self._pn_expanded
        self._pn_container.setVisible(self._pn_expanded)
        self._pn_toggle_btn.setText("▼  页码设置" if self._pn_expanded else "▶  页码设置")

    def _toggle_sr_section(self) -> None:
        self._sr_expanded = not self._sr_expanded
        self._sr_container.setVisible(self._sr_expanded)
        self._sr_toggle_btn.setText("▼  画风垫图" if self._sr_expanded else "▶  画风垫图")

    def _on_select_sr_image(self, idx: int) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择垫图图片{idx+1}", "",
            "图片文件 (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        while len(self._sr_paths) <= idx:
            self._sr_paths.append("")
        self._sr_paths[idx] = path
        self._sync_sr_ui()

    def _on_remove_sr_image(self, idx: int) -> None:
        if idx < len(self._sr_paths):
            self._sr_paths[idx] = ""
            self._sync_sr_ui()

    def _on_sr_enabled_toggled(self, enabled: bool) -> None:
        for i in range(3):
            self._sr_path_labels[i].setEnabled(enabled)

    def _sync_sr_ui(self) -> None:
        for i in range(3):
            has_path = i < len(self._sr_paths) and bool(self._sr_paths[i])
            self._sr_path_labels[i].setText(
                self._sr_paths[i] if has_path else "未选择"
            )
            self._sr_remove_btns[i].setEnabled(has_path)

    def _current_style_prompt(self) -> str:
        idx = self._style_combo.currentIndex()
        if 0 <= idx < len(COMIC_STYLE_PRESETS):
            return COMIC_STYLE_PRESETS[idx]["prompt"]
        return ""

    def _on_style_changed(self, _index: int) -> None:
        pass

    def _on_confirm(self) -> None:
        self._settings["concurrency"] = self._concurrency_spin.value()
        self._settings["style"] = self._style_combo.currentText()
        self._settings["prefix"] = self._prefix_edit.toPlainText().strip()
        self._settings["aspect_ratio"] = self._ratio_combo.currentText()
        self._settings["resolution"] = self._resolution_combo.currentText()
        self._settings["prompt_rewrite"] = self._rewrite_cb.isChecked()
        self._settings["page_number"] = {
            "enabled": self._pn_enabled_cb.isChecked(),
            "prefix": self._pn_prefix_edit.text().strip(),
            "font_color": self._font_color_edit.text().strip() or "#000000",
            "bg_color": self._bg_color_edit.text().strip() or "#FFFFFF",
            "bg_opacity": self._pn_opacity_slider.value(),
            "size": self._pn_size_slider.value() / 10,
            "alignment": self._pn_align_combo.currentData(),
        }
        self._settings["style_ref"] = {
            "enabled": self._sr_enabled_cb.isChecked(),
            "paths": [p for p in self._sr_paths if p.strip()],
        }
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


class _ExportWorker(QThread):
    """后台导出ZIP的Worker，带页码渲染"""
    progress = Signal(int, int)  # current, total
    message = Signal(str)
    finished = Signal(str)  # save_path
    error = Signal(str)

    def __init__(self, image_files: list, save_path: str, project_name: str, gen_settings: dict, parent=None):
        super().__init__(parent)
        self._image_files = image_files
        self._save_path = save_path
        self._project_name = project_name
        self._gen_settings = gen_settings

    def run(self) -> None:
        try:
            import os as _os
            import tempfile as _tempfile
            import zipfile as _zipfile
            import time as _time

            pn_settings = self._gen_settings.get("page_number", {})
            pn_prefix = pn_settings.get("prefix", "").strip() or self._project_name
            pn_enabled = pn_settings.get("enabled", True)
            total = len(self._image_files)
            base_ts = _time.time() - total

            # 缓存字体对象，避免重复加载
            _font_cache: dict = {}

            def _render_with_cache(fp, idx, page_num):
                nonlocal _font_cache
                from PIL import Image, ImageDraw, ImageFont
                pn = self._gen_settings.get("page_number", {})
                if not pn.get("enabled", True):
                    return fp
                scale = pn.get("size", 0.7)
                prefix = pn.get("prefix", "").strip() or self._project_name
                font_color = pn.get("font_color", "#000000")
                bg_color = pn.get("bg_color", "#FFFFFF")
                bg_opacity = pn.get("bg_opacity", 200)
                text = f"《{prefix}{page_num:02d}》"

                img = Image.open(fp).convert("RGBA")
                w, h = img.size
                font_size = max(int(w // 40 * scale), int(16 * scale))

                # 缓存字体
                cache_key = font_size
                font = _font_cache.get(cache_key)
                if font is None:
                    try:
                        font = ImageFont.truetype("msyh.ttc", font_size)
                    except Exception:
                        try:
                            font = ImageFont.truetype("simhei.ttf", font_size)
                        except Exception:
                            font = ImageFont.load_default()
                    _font_cache[cache_key] = font

                txt_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(txt_layer)

                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]

                padding_x = max(int(4 * scale), 2)
                padding_y = max(int(4 * scale), 2)
                margin_bottom = max(int(2 * scale), 1)
                margin_side = max(int(8 * scale), 4)
                bg_w = tw + padding_x * 2
                bg_h = th + padding_y * 2
                align = pn.get("alignment", "center")
                if align == "left":
                    bg_x = margin_side
                elif align == "right":
                    bg_x = w - bg_w - margin_side
                else:
                    bg_x = (w - bg_w) // 2
                bg_y = h - bg_h - margin_bottom

                def _parse_color(hex_color):
                    hex_color = hex_color.lstrip("#")
                    if len(hex_color) == 6:
                        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
                    return (0, 0, 0)

                bg_rgba = _parse_color(bg_color) + (min(bg_opacity, 255),)
                draw.rectangle([bg_x, bg_y, bg_x + bg_w, bg_y + bg_h], fill=bg_rgba)
                tx = bg_x + (bg_w - tw) // 2 - bbox[0]
                ty = bg_y + (bg_h - th) // 2 - bbox[1]
                draw.text((tx, ty), text, fill=_parse_color(font_color) + (255,), font=font)

                img = Image.alpha_composite(img, txt_layer).convert("RGB")
                with _tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    temp_path = tmp.name
                img.save(temp_path, quality=95, optimize=True)
                return temp_path

            with _zipfile.ZipFile(self._save_path, 'w', _zipfile.ZIP_DEFLATED) as zf:
                for i, (idx, fp) in enumerate(self._image_files, start=1):
                    zi = _zipfile.ZipInfo.from_file(fp, f"{i}.png")
                    ts = base_ts + idx
                    zi.date_time = _time.localtime(ts)[:6]
                    zi.extra = _make_zip_ntfs_extra(ts)
                    if pn_enabled:
                        temp_path = _render_with_cache(str(fp), idx, idx)
                        with open(temp_path, "rb") as f:
                            zf.writestr(zi, f.read())
                        try:
                            _os.unlink(temp_path)
                        except Exception:
                            pass
                    else:
                        with open(fp, "rb") as f:
                            zf.writestr(zi, f.read())
                    self.progress.emit(i, total)
                    self.message.emit(f"正在导出 {i}/{total}...")

            self.message.emit("导出完成")
            self.finished.emit(self._save_path)
        except Exception as e:
            logger.error("导出失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))


class NovelComicGeneratePage(QFrame):
    back_to_chapters = Signal()
    switch_to_rewrite = Signal()
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
        self._export_worker: _ExportWorker | None = None
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
        project = self._state_service.load_project(self._project_id)
        project_name = project.name if project else ""
        dialog = _GenerateSettingsDialog(self._gen_settings, project_name, self.window())
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

        self._export_btn.setEnabled(False)
        self._export_btn.setText("导出中...")
        self._desc_status.setText(f"准备导出 {len(image_files)} 张图片...")
        self._desc_status.setStyleSheet("color: #4fc3f7;")

        self._export_worker = _ExportWorker(
            image_files, save_path, project.name, self._gen_settings, self,
        )
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.message.connect(self._on_export_message)
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_progress(self, current: int, total: int) -> None:
        self._desc_status.setText(f"正在导出 {current}/{total}...")

    def _on_export_message(self, msg: str) -> None:
        self._desc_status.setText(msg)

    def _on_export_finished(self, save_path: str) -> None:
        self._export_btn.setEnabled(True)
        self._export_btn.setText("📦  导出")
        self._desc_status.setText(f"导出完成 → {save_path}")
        self._desc_status.setStyleSheet("color: #4ade80;")

    def _on_export_error(self, error_msg: str) -> None:
        self._export_btn.setEnabled(True)
        self._export_btn.setText("📦  导出")
        self._desc_status.setText(f"导出失败: {error_msg}")
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

        rewrite_btn = QPushButton("\U0001f4d6  小说改写")
        rewrite_btn.setObjectName("comicGenActionBtn")
        rewrite_btn.setCursor(Qt.PointingHandCursor)
        rewrite_btn.clicked.connect(self.switch_to_rewrite.emit)
        toolbar_layout.addWidget(rewrite_btn)

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
            card.edit_clicked.connect(lambda idx=sb["index"]: self._on_edit_image(idx))
            card.delete_clicked.connect(self._on_delete_storyboard)
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

    def _on_delete_storyboard(self, storyboard_index: int) -> None:
        dlg = _ConfirmDialog(f"确定删除分镜 #{storyboard_index} 吗？\n删除后分镜序号将重新排列。", self)
        dlg.setWindowTitle("删除分镜")
        if dlg.exec() != QDialog.Accepted:
            return

        self._storyboards = [sb for sb in self._storyboards if sb["index"] != storyboard_index]
        self._renumber_storyboards()
        self._save_storyboards()
        self._refresh_storyboard_list()

    def _renumber_storyboards(self) -> None:
        for i, sb in enumerate(self._storyboards, start=1):
            old_index = sb["index"]
            sb["index"] = i
            if sb.get("generated_image"):
                old_img = sb["generated_image"]
                img_path = Path(old_img)
                if img_path.exists():
                    try:
                        new_name = f"comic_panel_{i}{img_path.suffix}"
                        new_path = img_path.with_name(new_name)
                        import shutil
                        shutil.move(str(img_path), str(new_path))
                        sb["generated_image"] = str(new_path)
                    except Exception:
                        pass

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
                desc_text = f"（{a.desc}）" if a.desc else ""
                result[key].append(f"{a.name}{desc_text}")
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

        asset_names = self._get_flattened_asset_names()

        key = _worker_key(self._project_id, self._episode_num, "desc")
        worker = StoryboardDescriptionWorker(
            target,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
            chat_manager=self._get_chat_manager(),
            single_batch=False,
            all_assets=asset_names,
            desc_mode=self._split_mode,
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

        # 重新生成时重置 viewed 状态
        sb["viewed"] = False
        self._save_storyboards()
        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_viewed(False)

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
        reference_paths = _merge_ref_paths(gen_settings, reference_paths)

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

        # 使用生图设置的并发数
        concurrency = gen_settings.get("concurrency", 3)
        ImageGenService.instance().set_concurrency(concurrency)

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
        project = self._state_service.load_project(self._project_id)
        project_name = project.name if project else ""
        dialog = _StoryboardPreviewDialog(
            self._storyboards, storyboard_index,
            gen_settings=self._gen_settings,
            project_name=project_name,
            parent=self.window(),
        )
        dialog.show_preview()

        # 弹窗关闭后，将所有查看过的分镜标记为已查看
        viewed = getattr(dialog, "_viewed_indices", {storyboard_index})
        changed = False
        for sb in self._storyboards:
            if sb["index"] in viewed and not sb.get("viewed"):
                sb["viewed"] = True
                changed = True
            card = self._storyboard_cards.get(sb["index"])
            if card:
                card.set_viewed(sb.get("viewed", False))
        if changed:
            self._save_storyboards()

    def _on_comic_image_generated(self, storyboard_index: int, image_path: str) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card is None:
            return
        if image_path:
            for sb in self._storyboards:
                if sb["index"] == storyboard_index:
                    sb["generated_image"] = image_path
                    sb["viewed"] = False  # 新图片返回，重置为未查看
                    break
            self._refresh_single_card(storyboard_index)
            self._save_storyboards()
        else:
            card.set_gen_error()

    def _on_edit_image(self, storyboard_index: int) -> None:
        """图片编辑：弹窗 → 提交修改到生图队列"""
        sb = None
        for s in self._storyboards:
            if s["index"] == storyboard_index:
                sb = s
                break
        if sb is None:
            return

        image_path = sb.get("generated_image", "")
        if not image_path or not Path(image_path).exists():
            self._show_alert("无法编辑", "该分镜还没有图片，请先生成分镜图")
            return

        dialog = _ImageEditDialog(image_path, storyboard_index, self.window())
        if dialog.exec() != QDialog.Accepted:
            return

        edit_text = dialog.edit_text
        if not edit_text:
            self._show_alert("提示", "请输入修改要求")
            return

        mask_path = dialog.mask_path

        # 提交到生图队列
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
            self._show_alert("无法编辑", "请先在系统配置中设置图片生成模型")
            return

        gen_settings = self._gen_settings
        concurrency = gen_settings.get("concurrency", 3)
        ImageGenService.instance().set_concurrency(concurrency)

        # 构造编辑提示词：原始描述 + 修改要求
        original_desc = sb.get("description", "")
        edit_prompt = f"{original_desc}\n\n请根据以下要求修改图片：{edit_text}"
        size = _image_size_from_settings(
            gen_settings.get("aspect_ratio", "3:4"),
            gen_settings.get("resolution", "1K"),
        )

        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_generating()

        signal = self.comic_image_generated
        batch_counter = [0, 0]

        # 如果使用了遮罩，在完成时清理临时文件
        _mask_cleanup = [mask_path]

        def _edit_on_done(*args, **kwargs):
            try:
                if _mask_cleanup[0]:
                    p = Path(_mask_cleanup[0])
                    if p.exists():
                        p.unlink()
            except Exception:
                pass
            _make_comic_on_done(
                self._state_service, self._project_id, self._episode_num,
                storyboard_index, signal, batch_counter, lambda: None,
            )(*args, **kwargs)

        ImageGenService.instance().submit(
            image_config, edit_prompt,
            _edit_on_done,
            _make_comic_on_error(storyboard_index, signal, batch_counter, lambda: None),
            size=size,
            reference_images=[image_path],
            mask_image=mask_path or None,
            resolution=gen_settings.get("resolution"),
            aspect_ratio=gen_settings.get("aspect_ratio", "3:4"),
            text_model_config=image_text_config,
        )

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
            desc_mode=self._split_mode,
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
            reference_paths = _merge_ref_paths(gen_settings, reference_paths)

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

        global_prefix = gen_settings.get("prefix", "").strip()
        sr_settings = gen_settings.get("style_ref", {})
        sr_enabled = sr_settings.get("enabled", False)
        sr_paths: list[str] = sr_settings.get("paths", [])
        logger.debug("_build_comic_prompt: sr_enabled=%s, sr_paths=%s", sr_enabled, sr_paths)

        prompt = desc
        if sr_enabled:
            sr_refs = [p for p in sr_paths if p and Path(p).exists()]
            if sr_refs:
                prompt = "参考图片{}的画风，生成对应画风的漫画内容".format(
                    "，图片".join(str(i+1) for i in range(len(sr_refs)))
                ) + prompt
        else:
            style_prefix = _get_effective_prefix(gen_settings)
            if style_prefix:
                prompt = style_prefix + "，分镜内容：" + prompt

        if global_prefix:
            prompt = global_prefix + "，" + prompt
        if asset_descs:
            prompt += "。参考资产形象：" + "；".join(asset_descs)

        ratio = gen_settings.get("aspect_ratio", "3:4")
        resolution = gen_settings.get("resolution", "1K")
        size = _image_size_from_settings(ratio, resolution)
        prompt += f"。去除图像中的噪点和高频细节。保持所有的线条、颜色和亮度不变"
        # prompt += f"，{resolution}分辨率，图片比例{ratio}，尺寸{size}"
        prompt = "将以下内容画成网格漫画。蒙太奇排版。" + prompt

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
    edit_clicked = Signal(int)
    delete_clicked = Signal(int)

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

        edit_btn = QPushButton("\u270f\ufe0f  图片编辑")
        edit_btn.setObjectName("storyboardActionBtn")
        edit_btn.setCursor(Qt.PointingHandCursor)
        edit_btn.clicked.connect(
            lambda: self.edit_clicked.emit(self._data["index"])
        )
        self._edit_btn = edit_btn
        btn_row.addWidget(edit_btn)

        self._gen_desc_btn = QPushButton("\U0001f4c4  生成描述")
        self._gen_desc_btn.setObjectName("storyboardActionBtn")
        self._gen_desc_btn.setCursor(Qt.PointingHandCursor)
        self._gen_desc_btn.clicked.connect(
            lambda: self.gen_desc_clicked.emit(self._data["index"])
        )
        btn_row.addWidget(self._gen_desc_btn)

        delete_btn = QPushButton("🗑  删除")
        delete_btn.setObjectName("storyboardDeleteBtn")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.clicked.connect(
            lambda: self.delete_clicked.emit(self._data["index"])
        )
        btn_row.addWidget(delete_btn)

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

    def set_viewed(self, viewed: bool) -> None:
        """从外部设置 viewed 状态并刷新边框"""
        self._data["viewed"] = viewed
        self._update_viewed_border()

    def _show_generated_pixmap(self, image_path: str) -> None:
        if self._image_label and Path(image_path).exists():
            px = QPixmap(image_path)
            if not px.isNull():
                self._image_label.setPixmap(px.scaled(110, 148, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._image_label.setText("")
                if self._gen_img_btn:
                    self._gen_img_btn.setText("\U0001f5bc  重新生成")
                    self._gen_img_btn.setEnabled(True)
                if self._edit_btn:
                    self._edit_btn.setEnabled(True)
        else:
            if self._edit_btn:
                self._edit_btn.setEnabled(False)
        self._update_viewed_border()

    def _update_viewed_border(self) -> None:
        """根据 viewed 状态设置图片占位边框颜色：红色=未查看，绿色=已查看"""
        viewed = self._data.get("viewed", False)
        generated = self._data.get("generated_image", "")
        has_image = bool(generated and Path(generated).exists())
        if not has_image:
            self._image_placeholder.setStyleSheet("")
            return
        color = "#22c55e" if viewed else "#ef4444"
        self._image_placeholder.setStyleSheet(
            f"#storyboardImagePlaceholder {{ border: 3px solid {color}; border-radius: 6px; }}"
        )

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
    """全屏分镜预览，支持缓存+异步预加载，秒开4K图片"""

    _MAX_CACHE = 10

    def __init__(
        self,
        storyboards: list[dict],
        current_index: int,
        gen_settings: dict | None = None,
        project_name: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent, Qt.FramelessWindowHint)
        self._storyboards = storyboards
        self._current_idx = current_index
        self._gen_settings = gen_settings or {}
        self._project_name = project_name
        self._viewed_indices: set[int] = {current_index}  # 已查看的分镜索引
        self._sorted = sorted(
            [s for s in storyboards if s.get("generated_image") and Path(s["generated_image"]).exists()],
            key=lambda s: s["index"],
        )
        self._preview_cache: dict[int, QPixmap] = {}
        self._pending_preloads: set[int] = set()
        self._preload_timer = QTimer(self)
        self._preload_timer.setSingleShot(True)
        self._preload_timer.timeout.connect(self._do_preload)

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
        self._image_label.setText("加载中...")
        self._image_label.setAlignment(Qt.AlignCenter)
        self.showFullScreen()
        # 窗口显示后再加载图片，避免白屏卡顿
        QTimer.singleShot(0, self._show_current)
        self.exec()

    def _current_pos(self) -> int:
        for i, s in enumerate(self._sorted):
            if s["index"] == self._current_idx:
                return i
        return 0

    def _show_current(self) -> None:
        if not self._sorted:
            self.reject()
            return
        pos = self._current_pos()
        sb = self._sorted[pos]
        idx = sb["index"]

        # 缓存命中
        if idx in self._preview_cache:
            self._image_label.setPixmap(self._preview_cache[idx])
        else:
            px = self._load_scaled_pixmap(sb["generated_image"])
            if px.isNull():
                return
            px = self._render_page_number(px, sb["index"])
            self._cache_and_set(idx, px)

        # 触发预加载
        self._preload_timer.start(50)

    def _load_scaled_pixmap(self, path: str) -> QPixmap:
        """加载图片并缩放到屏幕尺寸，保持宽高比"""
        screen = self.screen().size() if self.screen() else QSize(1920, 1080)
        max_w = int(screen.width() * 0.9)
        max_h = int(screen.height() * 0.9)

        reader = QImageReader(path)
        reader.setAutoTransform(True)
        # 设一个上限缩小加载量，防止4K图解码过慢
        orig_size = reader.size()
        if orig_size.isValid():
            ratio = min(max_w / orig_size.width(), max_h / orig_size.height())
            if ratio < 1.0:
                reader.setScaledSize(QSize(int(orig_size.width() * ratio), int(orig_size.height() * ratio)))
        img = reader.read()
        if img.isNull():
            return QPixmap()
        px = QPixmap.fromImage(img)
        # 保险：确保最终像素尺寸不超过屏幕
        if px.width() > max_w or px.height() > max_h:
            px = px.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return px

    def _render_page_number(self, px: QPixmap, page_num: int) -> QPixmap:
        """直接用 QPainter 在图片上绘制页码，零 IO"""
        pn_settings = self._gen_settings.get("page_number", {})
        if not pn_settings.get("enabled", True):
            return px

        scale = pn_settings.get("size", 0.7)
        prefix = pn_settings.get("prefix", "").strip() or self._project_name
        text = f"《{prefix}{page_num:02d}》"

        result = QPixmap(px)  # 浅拷贝
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        font_size = max(int(result.width() // 40 * scale), int(16 * scale))
        font = QFont()
        font.setPointSize(font_size)
        painter.setFont(font)

        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        padding = max(int(4 * scale), 2)
        margin_bottom = max(int(4 * scale), 2)
        margin_side = max(int(8 * scale), 4)
        bg_w = tw + padding * 2
        bg_h = th + padding * 2

        align = pn_settings.get("alignment", "center")
        if align == "left":
            bg_x = margin_side
        elif align == "right":
            bg_x = result.width() - bg_w - margin_side
        else:
            bg_x = (result.width() - bg_w) // 2
        bg_y = result.height() - bg_h - margin_bottom

        # 背景
        bg_color_hex = pn_settings.get("bg_color", "#FFFFFF")
        bg_opacity = pn_settings.get("bg_opacity", 200)
        r = int(bg_color_hex[1:3], 16)
        g = int(bg_color_hex[3:5], 16)
        b_ = int(bg_color_hex[5:7], 16)
        painter.fillRect(bg_x, bg_y, bg_w, bg_h, QColor(r, g, b_, min(bg_opacity, 255)))

        # 文字
        font_color_hex = pn_settings.get("font_color", "#000000")
        fr = int(font_color_hex[1:3], 16)
        fg = int(font_color_hex[3:5], 16)
        fb = int(font_color_hex[5:7], 16)
        painter.setPen(QColor(fr, fg, fb))
        painter.drawText(bg_x + padding, bg_y + padding, tw, th, Qt.AlignLeft, text)
        painter.end()

        return result

    def _cache_and_set(self, idx: int, px: QPixmap) -> None:
        """放入缓存并设置显示"""
        self._preview_cache[idx] = px
        # LRU 修剪
        if len(self._preview_cache) > self._MAX_CACHE:
            oldest = min(self._preview_cache.keys())
            del self._preview_cache[oldest]
        self._image_label.setPixmap(px)

    def _do_preload(self) -> None:
        """预加载前后页到缓存"""
        pos = self._current_pos()
        for offset in (1, -1, 2, -2):
            np = pos + offset
            if 0 <= np < len(self._sorted):
                idx = self._sorted[np]["index"]
                if idx not in self._preview_cache and idx not in self._pending_preloads:
                    self._pending_preloads.add(idx)
                    QTimer.singleShot(0, lambda i=idx: self._preload_one(i))

    def _preload_one(self, idx: int) -> None:
        """加载单页并缓存"""
        self._pending_preloads.discard(idx)
        for sb in self._sorted:
            if sb["index"] == idx:
                px = self._load_scaled_pixmap(sb["generated_image"])
                if not px.isNull():
                    px = self._render_page_number(px, idx)
                    self._preview_cache[idx] = px
                    if len(self._preview_cache) > self._MAX_CACHE:
                        oldest = min(self._preview_cache.keys())
                        del self._preview_cache[oldest]
                return

    def _navigate(self, direction: int) -> None:
        pos = self._current_pos()
        new_pos = pos + direction
        if 0 <= new_pos < len(self._sorted):
            self._current_idx = self._sorted[new_pos]["index"]
            self._viewed_indices.add(self._current_idx)
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


class _MaskEditorWidget(QWidget):
    """遮罩绘制控件 - 在图片上绘制白色遮罩区域（white = 需要修改的区域）"""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._pixmap = QPixmap(image_path)
        self._pen_radius = 15
        self._is_erasing = False

        # 遮罩层：全黑（全黑 = 不修改）
        self._mask_size = self._pixmap.size()
        self._mask = QImage(self._mask_size, QImage.Format_ARGB32)
        self._mask.fill(QColor(0, 0, 0))

        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self._last_pos: tuple[int, int] | None = None

    def set_pen_radius(self, r: int) -> None:
        self._pen_radius = max(3, min(r, 80))

    def set_erasing(self, erasing: bool) -> None:
        self._is_erasing = erasing

    def clear_mask(self) -> None:
        self._mask.fill(QColor(0, 0, 0))
        self._last_pos = None
        self.update()

    def has_mask(self) -> bool:
        """检查是否有遮罩区域（是否有白色像素）"""
        for y in range(self._mask.height()):
            for x in range(0, self._mask.width(), 8):
                if self._mask.pixelColor(x, y).red() > 0:
                    return True
        return False

    def save_mask(self, save_path: str) -> None:
        """保存遮罩为 PNG 文件"""
        self._mask.save(save_path)

    def _draw_at(self, x: int, y: int) -> None:
        painter = QPainter(self._mask)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(0, 0, 0) if self._is_erasing else QColor(255, 255, 255)
        painter.setPen(QPen(color, self._pen_radius * 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawPoint(x, y)
        painter.end()
        self.update()

    def _draw_line(self, x1: int, y1: int, x2: int, y2: int) -> None:
        painter = QPainter(self._mask)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(0, 0, 0) if self._is_erasing else QColor(255, 255, 255)
        painter.setPen(QPen(color, self._pen_radius * 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(x1, y1, x2, y2)
        painter.end()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # 缩放图片适配控件
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        ox = (self.width() - scaled.width()) // 2
        oy = (self.height() - scaled.height()) // 2
        painter.drawPixmap(ox, oy, scaled)

        # 绘制遮罩覆盖层（半透明红色 = 被遮罩区域）
        mask_scaled = self._mask.scaled(
            scaled.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        for y in range(mask_scaled.height()):
            for x in range(mask_scaled.width()):
                c = mask_scaled.pixelColor(x, y)
                if c.red() > 0:
                    painter.setPen(QPen(QColor(255, 0, 0, 100), 1))
                    painter.drawPoint(ox + x, oy + y)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._last_pos = (event.position().x(), event.position().y())
            self._draw_at(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and self._last_pos:
            x, y = event.position().x(), event.position().y()
            self._draw_line(self._last_pos[0], self._last_pos[1], x, y)
            self._last_pos = (x, y)

    def mouseReleaseEvent(self, event) -> None:
        self._last_pos = None


class _ImageEditDialog(QDialog):
    """图片编辑弹窗：显示当前图片 + 遮罩绘制 + 文本输入框"""

    def __init__(self, image_path: str, storyboard_index: int, parent=None):
        super().__init__(parent)
        self._image_path = image_path
        self._storyboard_index = storyboard_index
        self._mask_path: str = ""
        self.setWindowTitle(f"分镜 #{storyboard_index} 图片编辑")
        self.setObjectName("imageEditDialog")
        self.setMinimumSize(700, 680)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel(f"分镜 #{self._storyboard_index} 图片编辑")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        # 中间区域：遮罩编辑器 + 预览
        self._mask_editor = _MaskEditorWidget(self._image_path)
        self._mask_editor.setObjectName("imageEditPreview")
        self._mask_editor.setMinimumHeight(300)
        layout.addWidget(self._mask_editor, stretch=1)

        # 遮罩工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        brush_size_label = QLabel("画笔大小:")
        brush_size_label.setStyleSheet("color: #94a3b8; font-size: 13px;")
        toolbar.addWidget(brush_size_label)

        self._brush_slider = QSlider(Qt.Horizontal)
        self._brush_slider.setMinimum(3)
        self._brush_slider.setMaximum(60)
        self._brush_slider.setValue(15)
        self._brush_slider.setFixedWidth(120)
        self._brush_slider.valueChanged.connect(
            lambda v: self._mask_editor.set_pen_radius(v)
        )
        toolbar.addWidget(self._brush_slider)

        self._draw_btn = QPushButton("✏️ 绘制")
        self._draw_btn.setObjectName("dialogConfirmBtn")
        self._draw_btn.setFixedHeight(28)
        self._draw_btn.setStyleSheet("font-size: 12px; padding: 0 10px;")
        self._draw_btn.clicked.connect(self._on_draw_mode)
        toolbar.addWidget(self._draw_btn)

        self._erase_btn = QPushButton("🧹 擦除")
        self._erase_btn.setObjectName("dialogCancelBtn")
        self._erase_btn.setFixedHeight(28)
        self._erase_btn.setStyleSheet("font-size: 12px; padding: 0 10px;")
        self._erase_btn.clicked.connect(self._on_erase_mode)
        toolbar.addWidget(self._erase_btn)

        clear_btn = QPushButton("🗑 清除遮罩")
        clear_btn.setObjectName("dialogCancelBtn")
        clear_btn.setFixedHeight(28)
        clear_btn.setStyleSheet("font-size: 12px; padding: 0 10px;")
        clear_btn.clicked.connect(self._mask_editor.clear_mask)
        toolbar.addWidget(clear_btn)

        toolbar.addStretch()

        hint = QLabel("在图片上涂抹白色区域标记要修改的部分，红色半透明层为遮罩区域")
        hint.setStyleSheet("color: #64748b; font-size: 12px;")

        layout.addWidget(toolbar)
        layout.addWidget(hint)

        # 修改描述输入
        desc_label = QLabel("修改要求（描述图片需要怎么修改）")
        desc_label.setObjectName("dialogFieldLabel")
        layout.addWidget(desc_label)

        self._edit_input = QPlainTextEdit()
        self._edit_input.setObjectName("imageEditInput")
        self._edit_input.setPlaceholderText("例如：把人物表情改成微笑、背景颜色换成蓝色、把红裙改成白裙...")
        self._edit_input.setMinimumHeight(80)
        self._edit_input.setMaximumHeight(120)
        layout.addWidget(self._edit_input)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确认修改")
        confirm_btn.setObjectName("dialogConfirmBtn")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_draw_mode(self) -> None:
        self._mask_editor.set_erasing(False)

    def _on_erase_mode(self) -> None:
        self._mask_editor.set_erasing(True)

    def _on_confirm(self) -> None:
        """保存遮罩并确认"""
        if self._mask_editor.has_mask():
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            self._mask_path = tmp.name
            tmp.close()
            self._mask_editor.save_mask(self._mask_path)
        else:
            self._mask_path = ""
        self.accept()

    @property
    def edit_text(self) -> str:
        return self._edit_input.toPlainText().strip()

    @property
    def mask_path(self) -> str:
        return self._mask_path


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

        style_prefix = _get_effective_prefix(gen_settings)
        prompt = f"资产名称：{asset_name}，资产类型：{type_label}，{asset_desc}"

        sr_settings = gen_settings.get("style_ref", {})
        sr_enabled = sr_settings.get("enabled", False)
        sr_paths: list[str] = sr_settings.get("paths", [])
        if sr_enabled:
            valid_sr = [p for p in sr_paths if p and Path(p).exists()]
            if valid_sr:
                ref_desc = "，图片".join(f"图片{i+1}" for i in range(len(valid_sr)))
                prompt = f"参考{ref_desc}的画风，生成对应画风的图片" + prompt
        elif style_prefix:
            prompt = f"{style_prefix}，{asset_name}，{prompt}"

        user_prefix = gen_settings.get("prefix", "").strip()
        if user_prefix:
            prompt = f"{user_prefix}，{asset_name}，{prompt}"

        if asset_type == "character":
            prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图，需要把人物名字显示在图片上"
        elif asset_type == "scene":
            prompt += ""
        elif asset_type == "prop":
            prompt += ""

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
        reference_images = _merge_ref_paths(gen_settings, reference_images or [])

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

            style_prefix = _get_effective_prefix(gen_settings)
            prompt = f"资产名称：{asset_name}，资产类型：{type_label}，{asset_desc}"

            sr_settings = gen_settings.get("style_ref", {})
            sr_enabled = sr_settings.get("enabled", False)
            sr_paths: list[str] = sr_settings.get("paths", [])
            if sr_enabled:
                valid_sr = [p for p in sr_paths if p and Path(p).exists()]
                if valid_sr:
                    ref_desc = "，图片".join(f"图片{i+1}" for i in range(len(valid_sr)))
                    prompt = f"参考{ref_desc}的画风，生成对应画风的素材。" + prompt
            elif style_prefix:
                prompt = f"{style_prefix}，{asset_name}，{prompt}"

            user_prefix = gen_settings.get("prefix", "").strip()
            if user_prefix:
                prompt = f"{user_prefix}，{asset_name}，{prompt}"

            if asset_type == "character":
                prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图，需要把人物名字显示在图片上"
            elif asset_type == "scene":
                prompt += ""
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
            reference_images = _merge_ref_paths(gen_settings, reference_images or [])
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
