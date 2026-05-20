import concurrent.futures
import json
import logging
import re
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
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clip_synth.services.ai_service import AIModelConfig, AIService
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


def _parse_json(text: str) -> dict | list:
    t = text.strip()
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(t)
        return obj
    except json.JSONDecodeError:
        pass
    return json.loads(t)


_running_workers: dict[tuple[str, int, str], QThread] = {}


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
2. 描述要足够详细，包括外貌特征、材质、颜色、状态等视觉信息，便于后续AI生成图片
3. 只提取文本中明确出现或强烈暗示的资产，不要凭空捏造
4. 如果某类资产不存在，返回空数组
5. 描述中严禁使用双引号、单引号、破折号、省略号、书名号等标点符号，只能使用逗号、句号、感叹号、问号、顿号
6. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。"""


class AssetExtractWorker(QThread):
    finished = Signal(list, list, list)
    error = Signal(str)

    def __init__(
        self,
        chapter_text: str,
        settings_service: SettingsService,
        parent=None,
    ):
        super().__init__(parent)
        self._chapter_text = chapter_text
        self._settings_service = settings_service

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            service = AIService(config)
            client = service._ensure_client()

            prompt = (
                "请分析以下小说文本，提取人物、场景、道具资产，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"小说文本：\n{self._chapter_text}"
            )

            response = client.chat.completions.create(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": ASSET_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                timeout=900,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
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


STORYBOARD_SPLIT_SYSTEM_PROMPT = """\
你是一个专业的漫画分镜师，擅长将小说文本拆分为漫画单页的分镜。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {"text": "第一段原文片段"},
    {"text": "第二段原文片段"}
  ]
}

核心要求：
1. 严格保留原文文字，不要做任何修改、润色、删减或添加
2. 每个分镜的文字量应适合一个漫画页面（通常是一个完整的动作、一个场景片段或一个情绪节拍）
3. 分镜之间要有清晰的叙事断点，例如场景切换、视角转换、对话回合、动作节拍
4. 不要拆得太碎（一句话一个分镜），也不要太长（一整章一个分镜）
5. 每个分镜的文本应该是原文中的一个连续段落
6. 所有分镜按原文顺序排列，覆盖全文，不要遗漏原文内容
7. 描述文本中严禁使用双引号、单引号、破折号、省略号、书名号等标点，只使用逗号、句号、感叹号、问号、顿号
8. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。"""


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
    error = Signal(str)

    def __init__(
        self,
        chapter_text: str,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
    ):
        super().__init__()
        self._chapter_text = chapter_text
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            service = AIService(config)
            client = service._ensure_client()

            prompt = (
                "请将以下小说文本拆分为适合漫画制作的分镜段落，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"小说文本：\n{self._chapter_text}"
            )

            response = client.chat.completions.create(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": STORYBOARD_SPLIT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                timeout=900,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            logger.info("分镜拆分AI返回: %s", content[:300])

            storyboards = self._parse_response(content)
            _save_project_storyboards(
                self._state_service, self._project_id, self._episode_num, storyboards,
            )
            self.finished.emit(storyboards)

        except Exception as e:
            logger.error("分镜拆分失败: %s", str(e), exc_info=True)
            logger.info("错误分镜: %s", content)
            self.error.emit(str(e))

    def _parse_response(self, content: str) -> list[dict]:
        data = _parse_json(content)
        items = data.get("storyboards", data) if isinstance(data, dict) else data
        result = []
        for i, item in enumerate(items):
            text = item.get("text", "")
            if text.strip():
                result.append({"index": i + 1, "text": text.strip(), "description": "", "assets": []})
        return result


MATCH_ASSETS_SYSTEM_PROMPT = """\
你是一个专业的漫画分镜策划师，负责为每个分镜匹配所需的资产（人物、场景、道具）。

输出格式要求（最重要）：
你必须且只能输出一个纯 JSON 对象，不要输出任何 Markdown、表格、标题、解释、代码块标记。整个回复从 { 开始，到 } 结束。

{
  "storyboards": [
    {
      "index": 1,
      "scene": "场景名称",
      "characters": ["人物A", "人物B"],
      "props": ["道具X"]
    }
  ]
}

你将收到：
1. 分镜列表，每个分镜有编号和文本
2. 资产池，分为人物(characters)、场景(scenes)、道具(props)三类，每项有名称

核心规则：
1. 每个分镜必须且只能选择一个场景（scene），没有场景的分镜是无效的
2. 每个分镜可以选择零个或多个人物（characters）
3. 每个分镜可以选择零个或多个道具（props）
4. 只从提供的资产池中选择，不要编造不存在的资产名称
5. 根据分镜文本内容判断该分镜发生在哪个场景、出现了哪些人物、使用了哪些道具
6. 输出合法 JSON：所有字符串值内的英文双引号（"）必须用反斜杠转义（\\"），不得出现未转义的换行符。确保返回的 JSON 可以被 json.loads 正确解析。"""


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
    ):
        super().__init__()
        self._storyboards = storyboards
        self._asset_names = asset_names
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            service = AIService(config)
            client = service._ensure_client()

            sb_texts = "\n".join(
                f"分镜#{sb['index']}: {sb['text'][:200]}"
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

            response = client.chat.completions.create(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": MATCH_ASSETS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                timeout=900,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            logger.info("资产匹配AI返回: %s", content[:300])

            assets_map = self._parse_response(content)
            self._apply_and_save(assets_map)
            self.finished.emit(assets_map)

        except Exception as e:
            logger.error("资产匹配失败: %s", str(e), exc_info=True)
            logger.info("资产匹配AI返回: %s", content)
            self.error.emit(str(e))

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
      "description": "整体排版：...\\n\\n格1 (...)\\n- 景别/角度：...\\n- 场景：...\\n- 人物：...\\n- 动作/表情：...\\n- 气泡：...\\n- 说明框：...\\n\\n格2 (...)\\n- 景别/角度：...\\n\\n格N (...)"
    }
  ]
}

你的任务是为每个分镜段落生成一页漫画的详细分镜描述。每个分镜可能包含多个格（panel），具体格数和版面由你根据内容节奏决定。

输出格式规范：

整体排版：
用一段话概述本页的版面布局方案。例如：采用左右对切、上下结构、三格阶梯式、出格效果等。说明格的数量、大致形状比例（横格/竖格/方格的宽窄高矮）和排列逻辑。

格N (形状描述，如：窄长横格 / 大方格 / 竖长格左半页 / 满版出血格 / 三小格并列 等)
- 景别/角度：全景/中景/近景/特写/大特写 等，仰视/俯视/平视/倾斜 等
- 场景：描述该格的环境、时间、天气、氛围
- 人物：出现在该格的角色名称
- 动作/表情：角色的肢体动作和面部表情细节
- 气泡：如有对话或内心独白，注明气泡类型（云朵状内心独白 / 带尖刺的爆炸形对话 / 颤抖气泡 / 破格气泡 等）和气泡内的文字，所有文字内容必须用中文双引号（""）包裹，例如：气泡：(云朵状内心独白) "明明当初分手的时候，沈故红着眼，咬牙切齿地对我说话。"
- 说明框：如有旁白或说明文字，放在画面底部或顶部，注明文字内容，所有文字内容必须用中文双引号（""）包裹，例如：说明框："其实我想过沈故会有新的女朋友。"

核心规则：
1. 一个分镜对应一页漫画，不要拆分到多个分镜描述
2. 格的数量和形状由内容节奏决定，高潮部分用大格或出格，过渡部分用小格
3. 场景使用规则（极其重要）：
   - 每个分镜已绑定了一个场景（从资产池中匹配），本页所有格必须统一使用该场景
   - 严禁使用"无明确背景""纯色背景""网点处理""抽象背景"等空洞描述代替实际场景
   - 如果某格只聚焦人物局部（如眼睛特写、耳廓特写、手部特写），场景描述应写该局部所处的环境（如"组会会议室，背景虚化，焦点落在耳廓"），而不是省略场景
   - 唯有在原文明确描写角色离开了当前场景、进入了另一个场所时，该格才能使用另一个场景，否则一律使用绑定场景
4. 人物和道具只能从已匹配的资产列表中选择，不要自行编造或添加未匹配的角色和物品
5. 每个格必须有明确的景别和角度
6. 对话气泡和内心独白要标注气泡类型
7. 描述语言要有画面感，让画师能直接照着画
8. 手机屏幕 / 电脑屏幕 / 平板 / 纸条 / 书本等媒介上显示的文字：这些不是气泡也不是说明框，而是画面内的视觉元素，应在动作/表情或场景描述中直接描述屏幕上的文字内容，例如：动作/表情：陆宴知手指微微收紧，手机屏幕冷光照亮指节，聊天界面上赫然显示谢依璇刚刚发送的信息：「今晚有空吗？」。严禁为此类媒介文字使用气泡或说明框
9. 气泡和说明框的文字内容必须使用中文双引号（""）包裹，除此之外的其他位置（场景描述、人物描述、动作表情等）严禁使用双引号、单引号、破折号、书名号等标点，只使用逗号、句号、感叹号、问号、顿号、冒号
10. 输出合法 JSON：只输出一个 JSON 对象，不要输出任何其他内容。描述文本中出现的所有英文双引号（"）必须用反斜杠转义（\\"），中文双引号（""）无需转义。所有换行符必须用 \\n 表示，不得出现真正的换行符。JSON 对象内的 description 字符串本身可以包含 \\n 来表示换行。"""


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
    ):
        super().__init__()
        self._storyboards = storyboards
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service

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
            results: list[dict | None] = [None] * total
            errors: list[str] = []
            lock = threading.Lock()
            done_ctr = [0]

            def process_one(sb: dict, idx: int) -> None:
                try:
                    svc = AIService(config)
                    cli = svc._ensure_client()

                    assets = sb.get('assets', [])
                    scene_name = assets[0] if assets else ""
                    chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'
                    prompt = (
                        "请为以下分镜生成详细的一页漫画分镜描述，"
                        "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                        f"--- 分镜 #{sb['index']} ---\n"
                        f"绑定场景: {scene_name}\n"
                        f"原文: {sb['text']}\n"
                        f"已匹配人物/道具: {chars_and_props}"
                    )

                    response = cli.chat.completions.create(
                        model=config.model_name,
                        messages=[
                            {"role": "system", "content": STORYBOARD_DESC_SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.7,
                        timeout=900,
                        response_format={"type": "json_object"},
                    )

                    content = response.choices[0].message.content or ""
                    logger.info("分镜 #%d 描述AI返回: %s", sb['index'], content[:200])

                    desc = self._parse_single(content)
                    with lock:
                        results[idx] = {"index": sb["index"], "description": desc}
                        sb["description"] = desc
                        done_ctr[0] += 1
                        self.progress.emit(sb["index"], done_ctr[0], total, desc)
                except Exception as e:
                    logger.error("分镜 #%d 描述生成异常: %s", sb['index'], str(e), exc_info=True)
                    with lock:
                        errors.append(str(e))
                        sb["description"] = ""
                        results[idx] = {"index": sb["index"], "description": ""}
                        done_ctr[0] += 1
                        self.progress.emit(sb["index"], done_ctr[0], total, "")

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = [
                    pool.submit(process_one, sb, i)
                    for i, sb in enumerate(self._storyboards)
                ]
                concurrent.futures.wait(futures)

            if errors:
                raise RuntimeError(f"{len(errors)}/{total} 个分镜生成失败: {errors[0]}")

            _save_project_storyboards(
                self._state_service, self._project_id, self._episode_num, self._storyboards,
            )
            self.finished.emit(results)

        except Exception as e:
            logger.error("分镜描述生成失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))

    def _parse_single(self, content: str) -> str:
        data = _parse_json(content)
        if isinstance(data, dict) and "description" in data:
            return data["description"]
        items = data.get("storyboards", data) if isinstance(data, dict) else data
        if items and len(items) > 0:
            return items[0].get("description", "")
        return ""


class SingleDescWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        storyboard: dict,
        project_id: str,
        episode_num: int,
        settings_service: SettingsService,
        state_service: NovelComicStateService,
    ):
        super().__init__()
        self._storyboard = storyboard
        self._project_id = project_id
        self._episode_num = episode_num
        self._settings_service = settings_service
        self._state_service = state_service

    def run(self) -> None:
        try:
            settings = self._settings_service.load()
            text_config = settings.text_model

            config = AIModelConfig(
                model_name=text_config.model_name,
                api_key=text_config.api_key,
                base_url=text_config.base_url,
            )

            service = AIService(config)
            client = service._ensure_client()

            sb = self._storyboard
            assets = sb.get('assets', [])
            scene_name = assets[0] if assets else ""
            chars_and_props = ', '.join(assets[1:]) if len(assets) > 1 else '(无)'
            prompt = (
                "请为以下分镜生成详细的一页漫画分镜描述，"
                "严格按照系统提示中的 JSON 格式输出，不要输出任何其他内容。\n\n"
                f"--- 分镜 #{sb['index']} ---\n"
                f"绑定场景: {scene_name}\n"
                f"原文: {sb['text']}\n"
                f"已匹配人物/道具: {chars_and_props}"
            )

            response = client.chat.completions.create(
                model=config.model_name,
                messages=[
                    {"role": "system", "content": STORYBOARD_DESC_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                timeout=900,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            logger.info("分镜 #%d 单条描述AI返回: %s", sb['index'], content[:200])

            data = _parse_json(content)
            if isinstance(data, dict) and "description" in data:
                desc = data["description"]
            else:
                items = data.get("storyboards", data) if isinstance(data, dict) else data
                desc = items[0].get("description", "") if items else ""

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

            self.finished.emit(desc)

        except Exception as e:
            logger.error("单条描述生成失败: %s", str(e), exc_info=True)
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
            batch_counter[0] += 1
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
        batch_counter[0] += 1
        update_cb()
    return on_error


def _save_asset_image_to_project(
    state_service: NovelComicStateService,
    project_id: str,
    asset_name: str,
    file_path: str,
) -> None:
    project = state_service.load_project(project_id)
    if not project:
        return
    for a in project.assets:
        if a.name == asset_name:
            a.image_path = file_path
            break
    state_service.save_project(project)


def _make_asset_on_done(
    state_service: NovelComicStateService,
    project_id: str,
    name: str,
    signal,
) -> object:
    def on_done(_task_id: int, image_data: bytes) -> None:
        try:
            images_dir = state_service.get_project_images_dir(project_id)
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
            file_path = str(images_dir / f"asset_{safe_name}.png")
            Path(file_path).write_bytes(image_data)
            _save_asset_image_to_project(state_service, project_id, name, file_path)
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
        "name": "日式漫画",
        "prompt": (
            "Japanese manga style, clean ink lines, screen tones, "
            "expressive large eyes, dynamic speed lines, black and white with gray halftones, "
            "shounen aesthetic, dramatic lighting"
        ),
    },
    {
        "name": "美式漫画",
        "prompt": (
            "American comic book style, bold outlines, vibrant flat colors, "
            "halftone dot shading, dynamic action poses, dramatic foreshortening, "
            "superhero comic aesthetic, Ben-Day dots"
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
        "name": "韩式条漫",
        "prompt": (
            "Korean webtoon style, vertical scroll format, clean digital coloring, "
            "soft gradients, elegant character proportions, polished rendering, "
            "romance manhwa aesthetic, smooth cel shading"
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


class _GenerateSettingsDialog(QDialog):
    def __init__(self, current_settings: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = dict(current_settings)
        self.setWindowTitle("生图设置")
        self.setFixedSize(560, 660)
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
            self._style_combo.setCurrentIndex(idx)
        elif saved_style == "自定义画风":
            self._style_combo.setCurrentIndex(self._style_combo.count() - 1)
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
        self.accept()

    @property
    def result(self) -> dict:
        return self._settings


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

        images_dir = self._images_dir()
        missing_indices: list[int] = []
        for sb in self._storyboards:
            img = sb.get("generated_image", "")
            if img and Path(img).exists():
                continue
            base = images_dir / f"comic_panel_{sb['index']}"
            matches = sorted(images_dir.glob(f"comic_panel_{sb['index']}_*.png"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if not matches:
                missing_indices.append(sb["index"])

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
        for sb in self._storyboards:
            img = sb.get("generated_image", "")
            if img and Path(img).exists():
                image_files.append((sb["index"], Path(img)))
            else:
                matches = sorted(images_dir.glob(f"comic_panel_{sb['index']}_*.png"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if matches:
                    image_files.append((sb["index"], matches[0]))

        image_files.sort(key=lambda x: x[0])
        try:
            with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for i, (_, fp) in enumerate(image_files, start=1):
                    zf.write(fp, f"{i}.png")
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

        self._desc_gen_btn = QPushButton("\U0001f4c4  生成分镜描述")
        self._desc_gen_btn.setObjectName("comicGenActionBtn")
        self._desc_gen_btn.setCursor(Qt.PointingHandCursor)
        self._desc_gen_btn.clicked.connect(self._on_generate_descriptions)
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

        for sb in self._storyboards:
            card = _StoryboardCard(sb)
            card.add_asset_clicked.connect(lambda idx=sb["index"]: self._on_add_asset(idx))
            card.remove_asset.connect(
                lambda asset_name, idx=sb["index"]: self._on_remove_asset(idx, asset_name),
            )
            card.generate_image_clicked.connect(lambda idx=sb["index"]: self._on_generate_image(idx))
            card.history_clicked.connect(lambda idx=sb["index"]: self._on_history_images(idx))
            card.gen_desc_clicked.connect(self._on_gen_single_desc)
            card.desc_edit_requested.connect(self._on_edit_desc)
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
            self._project_id, chapter_text,
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

        self._split_status.setText("拆分中...")
        self._split_status.setStyleSheet("color: #4fc3f7;")

        key = _worker_key(self._project_id, self._episode_num, "split")
        worker = StoryboardSplitWorker(
            chapter_text, self._project_id, self._episode_num,
            self._settings_service, self._state_service,
        )
        _running_workers[key] = worker
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
            scene = match.get("scene", "")
            if scene:
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

    def _on_generate_descriptions(self) -> None:
        if not self._storyboards:
            return

        self._desc_status.setText("生成中...")
        self._desc_status.setStyleSheet("color: #4fc3f7;")

        key = _worker_key(self._project_id, self._episode_num, "desc")
        worker = StoryboardDescriptionWorker(
            self._storyboards,
            self._project_id, self._episode_num,
            self._settings_service, self._state_service,
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

    def _on_desc_error(self, error_msg: str) -> None:
        self._desc_status.setText(f"生成失败: {error_msg}")
        self._desc_status.setStyleSheet("color: #f87171;")

    def _on_add_asset(self, storyboard_index: int) -> None:
        dialog = _AddAssetDialog(self._storyboards, storyboard_index, self.window())
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
        )
        if not image_config.is_configured:
            self._show_alert("无法生成", "请先在系统配置中设置图片生成模型")
            return

        project = self._state_service.load_project(self._project_id)
        gen_settings = project.extra_data.get("gen_settings", {}) if project else {}

        prompt, size, reference_paths = self._build_comic_prompt(sb, project, gen_settings)

        if reference_paths:
            logger.info("分镜 #%d 找到 %d 张参考图: %s", storyboard_index, len(reference_paths), reference_paths)
        else:
            logger.info("分镜 #%d 没有参考图", storyboard_index)

        card = self._storyboard_cards.get(storyboard_index)
        if card:
            card.set_generating()

        signal = self.comic_image_generated
        batch_counter = [0]
        ImageGenService.instance().submit(
            image_config, prompt,
            _make_comic_on_done(
                self._state_service, self._project_id, self._episode_num,
                storyboard_index, signal, batch_counter, lambda: None,
            ),
            _make_comic_on_error(storyboard_index, signal, batch_counter, lambda: None),
            size=size,
            reference_images=reference_paths if reference_paths else None,
        )

    def _on_history_images(self, storyboard_index: int) -> None:
        dialog = _HistoryImagesDialog(
            self._state_service, self._project_id, self._episode_num,
            storyboard_index, self.window(),
        )
        dialog.exec()

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
            card._desc_edit.setPlaceholderText("生成中...")

        key = _worker_key(self._project_id, self._episode_num, f"desc_{storyboard_index}")
        worker = SingleDescWorker(
            sb, self._project_id, self._episode_num,
            self._settings_service, self._state_service,
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

    def _on_single_desc_error(self, storyboard_index: int, error_msg: str) -> None:
        card = self._storyboard_cards.get(storyboard_index)
        if card:
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

        pos = self._batch_btn.mapToGlobal(self._batch_btn.rect().bottomLeft())
        menu.exec(pos)

    def _on_batch_generate_all(self) -> None:
        self._run_batch_comic_gen(missing_only=False)

    def _on_batch_generate_missing(self) -> None:
        self._run_batch_comic_gen(missing_only=True)

    def _run_batch_comic_gen(self, missing_only: bool) -> None:
        project = self._state_service.load_project(self._project_id)
        gen_settings = project.extra_data.get("gen_settings", {}) if project else {}

        settings = self._settings_service.load()
        image_config = AIModelConfig(
            model_name=settings.image_model.model_name,
            api_key=settings.image_model.api_key,
            base_url=settings.image_model.base_url,
            api_type=settings.image_model.api_type,
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
            targets.append(sb)

        if not targets:
            self._desc_status.setText("没有需要生成的漫画图")
            self._desc_status.setStyleSheet("color: #f87171;")
            return

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

            prompt, size, reference_paths = self._build_comic_prompt(sb, project, gen_settings)
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
                reference_images=reference_paths if reference_paths else None,
            )

    def _build_comic_prompt(
        self, sb: dict, project, gen_settings: dict,
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
        prompt = desc
        if global_prefix:
            prompt = global_prefix + "，分镜内容：" + desc
        if asset_descs:
            prompt += "。参考资产形象：" + "；".join(asset_descs)

        ratio = gen_settings.get("aspect_ratio", "3:4")
        resolution = gen_settings.get("resolution", "1K")
        size = _image_size_from_settings(ratio, resolution)
        prompt += f"，{resolution}分辨率，图片比例{ratio}，尺寸{size}"

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
    preview_clicked = Signal(int)

    def __init__(self, data: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._data = data
        self.setObjectName("storyboardCard")
        self._main_layout: QHBoxLayout | None = None
        self._right_layout: QVBoxLayout | None = None
        self._text_label: QLabel | None = None
        self._desc_container: QWidget | None = None
        self._desc_edit: QTextEdit | None = None
        self._asset_row: QHBoxLayout | None = None
        self._asset_container: QWidget | None = None
        self._image_placeholder: QFrame | None = None
        self._image_label: QLabel | None = None
        self._gen_img_btn: QPushButton | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(16, 16, 16, 16)
        self._main_layout.setSpacing(16)

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
        self._main_layout.addWidget(self._image_placeholder)

        self._right_layout = QVBoxLayout()
        self._right_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        index_label = QLabel(f"#{self._data['index']}")
        index_label.setObjectName("storyboardIndexLabel")
        header_row.addWidget(index_label)

        self._text_label = QLabel(self._data["text"])
        self._text_label.setObjectName("storyboardTextLabel")
        self._text_label.setWordWrap(True)
        self._text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header_row.addWidget(self._text_label, stretch=1)
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

        gen_desc_btn = QPushButton("\U0001f4c4  生成描述")
        gen_desc_btn.setObjectName("storyboardActionBtn")
        gen_desc_btn.setCursor(Qt.PointingHandCursor)
        gen_desc_btn.clicked.connect(
            lambda: self.gen_desc_clicked.emit(self._data["index"])
        )
        btn_row.addWidget(gen_desc_btn)

        btn_row.addStretch()
        self._right_layout.addLayout(btn_row)

        self._main_layout.addLayout(self._right_layout, stretch=1)

        self._apply_data()

    def _apply_data(self) -> None:
        if self._text_label:
            self._text_label.setText(self._data["text"])

        if self._desc_edit and self._desc_container:
            desc = self._data.get("description", "")
            if desc:
                self._desc_edit.setPlainText(desc)
                self._desc_container.setVisible(True)
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

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseButtonDblClick and obj is self._desc_edit.viewport():
            current_desc = self._data.get("description", "")
            self.desc_edit_requested.emit(self._data["index"], current_desc)
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
        self._setup_ui(storyboards, storyboard_index)

    def _setup_ui(self, storyboards: list[dict], storyboard_index: int) -> None:
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

        character_data = [
            {"icon": "\U0001f464", "name": "林逸"},
            {"icon": "\U0001f464", "name": "刀疤脸"},
            {"icon": "\U0001f464", "name": "打手A"},
            {"icon": "\U0001f464", "name": "打手B"},
        ]
        scene_data = [
            {"icon": "\U0001f3ed", "name": "废弃工厂"},
            {"icon": "\U0001f3ed", "name": "仓库"},
            {"icon": "\U0001f3ed", "name": "工业区"},
        ]
        prop_data = [
            {"icon": "\U0001f4f7", "name": "照片"},
            {"icon": "\U0001f52b", "name": "匕首"},
            {"icon": "\U0001f52b", "name": "绳索"},
            {"icon": "\U0001f4e6", "name": "箱子"},
        ]

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
            cb = QCheckBox(f"{asset['icon']}  {asset['name']}")
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

                view_btn = QPushButton("查看")
                view_btn.setObjectName("assetGenImageBtn")
                view_btn.setCursor(Qt.PointingHandCursor)
                view_btn.clicked.connect(lambda checked, p=str(fp): self._on_view(p))
                row_layout.addWidget(view_btn)

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

    def _on_view(self, image_path: str) -> None:
        try:
            from clip_synth.ui.widgets.image_viewer import show_image_viewer
            show_image_viewer(image_path, "图片预览", self)
        except Exception:
            px = QPixmap(image_path)
            if px.isNull():
                return
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
            d = QDialog(self)
            d.setWindowTitle("图片预览")
            d.resize(600, 600)
            layout = QVBoxLayout(d)
            lbl = QLabel()
            lbl.setPixmap(px.scaled(560, 560, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)
            d.exec()


class _AssetManagementDialog(QDialog):
    asset_image_generated = Signal(str, str)

    def __init__(
        self,
        project_id: str,
        chapter_text: str,
        state_service: NovelComicStateService,
        settings_service: SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._chapter_text = chapter_text
        self._state_service = state_service
        self._settings_service = settings_service
        self._worker: AssetExtractWorker | None = None
        self.setWindowTitle("资产管理")
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

    def _load_from_project(self) -> None:
        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        for asset in project.assets:
            item = {"name": asset.name, "desc": asset.desc, "image_path": asset.image_path or ""}
            if asset.asset_type == "character":
                self._character_data.append(item)
            elif asset.asset_type == "scene":
                self._scene_data.append(item)
            elif asset.asset_type == "prop":
                self._prop_data.append(item)
        self._refresh_asset_list("character", self._character_data)
        self._refresh_asset_list("scene", self._scene_data)
        self._refresh_asset_list("prop", self._prop_data)
        if self._character_data or self._scene_data or self._prop_data:
            total = len(self._character_data) + len(self._scene_data) + len(self._prop_data)
            self._extract_status.setText(f"已加载 {total} 个资产")
            self._extract_status.setStyleSheet("color: #4ade80;")

    def _save_to_project(self) -> None:
        from clip_synth.models.novel_comic_project_state import NovelComicAsset

        project = self._state_service.load_project(self._project_id)
        if not project:
            return
        project.assets = []
        for item in self._character_data:
            project.assets.append(NovelComicAsset(
                name=item["name"], desc=item["desc"], asset_type="character",
                image_path=item.get("image_path", ""),
            ))
        for item in self._scene_data:
            project.assets.append(NovelComicAsset(
                name=item["name"], desc=item["desc"], asset_type="scene",
                image_path=item.get("image_path", ""),
            ))
        for item in self._prop_data:
            project.assets.append(NovelComicAsset(
                name=item["name"], desc=item["desc"], asset_type="prop",
                image_path=item.get("image_path", ""),
            ))
        self._state_service.save_project(project)

    def _on_extract_assets(self) -> None:
        if not self._chapter_text.strip():
            self._extract_status.setText("没有可提取的文本内容")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        self._extract_status.setText("提取中...")
        self._extract_status.setStyleSheet("color: #4fc3f7;")

        self._worker = AssetExtractWorker(self._chapter_text, self._settings_service)
        self._worker.finished.connect(self._on_extract_finished)
        self._worker.error.connect(self._on_extract_error)
        self._worker.start()

    def _on_extract_finished(self, characters: list, scenes: list, props: list) -> None:
        self._merge_image_paths(self._character_data, characters)
        self._merge_image_paths(self._scene_data, scenes)
        self._merge_image_paths(self._prop_data, props)
        self._character_data = characters
        self._scene_data = scenes
        self._prop_data = props
        self._refresh_asset_list("character", self._character_data)
        self._refresh_asset_list("scene", self._scene_data)
        self._refresh_asset_list("prop", self._prop_data)
        self._save_to_project()
        total = len(characters) + len(scenes) + len(props)
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

    @staticmethod
    def _merge_image_paths(old_data: list[dict], new_data: list[dict]) -> None:
        old_map = {item.get("name", ""): item.get("image_path", "") for item in old_data}
        for item in new_data:
            name = item.get("name", "")
            if name in old_map and old_map[name]:
                item["image_path"] = old_map[name]

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

        if not data:
            empty = QLabel("暂无资产数据")
            empty.setObjectName("assetEmptyLabel")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty)
        else:
            self._asset_rows = getattr(self, '_asset_rows', {})
            self._asset_rows[asset_type] = []
            for asset in data:
                row = _AssetItemRow(asset)
                row.generate_clicked.connect(lambda n=asset["name"]: self._on_gen_asset_image(n))
                row.desc_edit_requested.connect(self._on_edit_asset_desc)
                row.image_clicked.connect(self._on_preview_asset_image)
                row.upload_clicked.connect(lambda r=row: self._on_upload_asset_image(r))
                row.delete_clicked.connect(lambda a=asset, at=asset_type: self._on_delete_asset(a, at))
                layout.addWidget(row)
                self._asset_rows[asset_type].append(row)

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
        )
        if not image_config.is_configured:
            self._extract_status.setText("请先在系统配置中设置图片生成模型")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        asset_type = self._get_asset_type(asset_name)
        asset_desc = self._get_asset_desc(asset_name)

        global_prefix = gen_settings.get("prefix", "").strip()
        prompt = asset_desc
        if global_prefix:
            prompt = global_prefix + ", " + asset_desc

        if asset_type == "character":
            prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图"
        elif asset_type == "scene":
            prompt += "，生成9机位的不同方向的视角图"
        elif asset_type == "prop":
            prompt += "，生成9机位的不同方向的视角图"

        row.set_generating()

        resolution = gen_settings.get("resolution", "1K")
        size = _square_size_from_resolution(resolution)
        ImageGenService.instance().submit(
            image_config, prompt,
            _make_asset_on_done(
                self._state_service, self._project_id,
                asset_name, self.asset_image_generated,
            ),
            _make_asset_on_error(
                self._project_id, asset_name, self.asset_image_generated,
            ),
            size=size,
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
        self._update_batch_status()

    def _on_batch_gen_assets_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("batchGenMenu")

        all_action = menu.addAction("\U0001f4e6  全部资产")
        all_action.triggered.connect(lambda: self._run_batch_asset_gen("all"))

        char_action = menu.addAction("\U0001f9d1  仅人物")
        char_action.triggered.connect(lambda: self._run_batch_asset_gen("character"))

        scene_action = menu.addAction("\U0001f3de  仅场景")
        scene_action.triggered.connect(lambda: self._run_batch_asset_gen("scene"))

        prop_action = menu.addAction("\U0001f52e  仅道具")
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
        )
        if not image_config.is_configured:
            self._extract_status.setText("请先在系统配置中设置图片生成模型")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

        concurrency = gen_settings.get("concurrency", 3)
        ImageGenService.instance().set_concurrency(concurrency)

        all_assets: list[tuple[str, str]] = []
        type_map: dict[str, tuple[list, str]] = {
            "character": (self._character_data, "character"),
            "scene": (self._scene_data, "scene"),
            "prop": (self._prop_data, "prop"),
        }
        if filter_type == "all":
            for data_list, atype in type_map.values():
                for item in data_list:
                    all_assets.append((item["name"], atype))
        else:
            data_list, atype = type_map[filter_type]
            for item in data_list:
                all_assets.append((item["name"], atype))

        if not all_assets:
            self._extract_status.setText("没有需要生成的资产")
            self._extract_status.setStyleSheet("color: #f87171;")
            return

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

            global_prefix = gen_settings.get("prefix", "").strip()
            prompt = asset_desc
            if global_prefix:
                prompt = global_prefix + ", " + asset_desc

            if asset_type == "character":
                prompt += "，生成人物4视角（正面全身视图，左侧身视图，右侧视图，背面视图），白底图"
            elif asset_type == "scene":
                prompt += "，生成9机位的不同方向的视角图"
            elif asset_type == "prop":
                prompt += "，生成9机位的不同方向的视角图"

            resolution = gen_settings.get("resolution", "1K")
            size = _square_size_from_resolution(resolution)
            ImageGenService.instance().submit(
                image_config, prompt,
                _make_asset_on_done(
                    self._state_service, self._project_id,
                    asset_name, self.asset_image_generated,
                ),
                _make_asset_on_error(
                    self._project_id, asset_name, self.asset_image_generated,
                ),
                size=size,
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

    def __init__(self, asset: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._asset = asset
        self.setObjectName("assetItemRow")
        self._thumb_img: QLabel | None = None
        self._thumb_frame: QFrame | None = None
        self._image_path: str = ""
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

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

        self._delete_btn = QPushButton("\u2715")
        self._delete_btn.setObjectName("assetDeleteBtn")
        self._delete_btn.setCursor(Qt.PointingHandCursor)
        self._delete_btn.setFixedWidth(30)
        self._delete_btn.setToolTip("删除资产")
        self._delete_btn.clicked.connect(self.delete_clicked.emit)

        layout.addLayout(btn_col)
        layout.addWidget(self._delete_btn)

    def _on_thumb_click(self, event) -> None:
        if self._image_path and Path(self._image_path).exists():
            self.image_clicked.emit(self._image_path)

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
