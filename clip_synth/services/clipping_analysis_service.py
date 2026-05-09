import json
import logging
import re
from typing import Dict, List, Tuple

from clip_synth.models.project_state import VideoSegment
from clip_synth.services.ai_service import AIService

logger = logging.getLogger("clip_synth.clipping_analysis")

NARRATION_SYSTEM_PROMPT = """\
你是一个专业的短剧解说编剧，擅长分析视频画面和字幕内容，提取故事主线，生成吸引观众的解说文案。

你的任务：
1. 分析提供的视频画面描述和字幕内容
2. 梳理出清晰的故事主线和剧情脉络
3. 识别关键人物关系和剧情转折点
4. 挑选适合解说的精彩片段
5. 生成符合指定风格的第三人称解说文案

解说风格要求：
- 情感共鸣：深入挖掘角色内心情感，语言细腻动人，让观众与角色同悲同喜
- 搞笑幽默：轻松诙谐，挖掘笑点和梗，用幽默语言风格让观众在欢笑中看完故事
- 逻辑严谨：清晰呈现事件因果，帮助观众理清复杂的人物关系和故事脉络
- 超快节奏：简洁有力，信息密度大，保持观众持续观看的好奇心和紧张感

要求：
1. 解说文案必须是第三人称叙述
2. 故事线要清晰，主线明确
3. 每段解说要标注对应的时间范围
4. 解说内容要能让观众快速看懂故事
5. 返回格式必须是合法的JSON数组

**时间格式必须严格遵守：HH:MM:SS,mmm（例如：00:01:25,500），秒和毫秒之间用逗号隔开，这是SRT标准格式**

返回格式示例（无原声）：
```json
[
    {
        "segment_id": "片段ID",
        "start_time": "00:00:00,000",
        "end_time": "00:00:15,000",
        "content_type": "narration",
        "story_summary": "这个片段的故事梗概",
        "narration_script": "第三人称解说文案..."
    }
]
```

返回格式示例（原声比例>0%时，每个片段拆分为解说+原声两部分）：
```json
[
    {
        "segment_id": "片段ID-1",
        "start_time": "00:00:00,000",
        "end_time": "00:00:07,000",
        "content_type": "narration",
        "story_summary": "这个片段的故事梗概",
        "narration_script": "解说文案..."
    },
    {
        "segment_id": "片段ID-1",
        "start_time": "00:00:07,000",
        "end_time": "00:00:10,000",
        "content_type": "original_sound",
        "story_summary": "",
        "narration_script": ""
    }
]
```

注意：
- 原声比例=0%时，所有项都是content_type="narration"，不需要拆分
- 原声比例>0%时，才需要按比例拆分为narration+original_sound
- 只返回JSON数组，不要包含其他文字。"""

CLIPPING_SYSTEM_PROMPT = """\
你是一个专业的短视频剪辑师。从提供的片段列表中按时间顺序选择片段，串联成一个完整的故事解说。

选择要求：
1. **按时间顺序**选择片段，串联起来能还原完整故事脉络
2. 片段时间**绝对不能重叠**
3. 优先选择 gold_3s（黄金3秒）、highlight（亮点解析）、plot（剧情解析）、ending（结尾悬念）四种类型
4. 返回合法的JSON数组

返回格式：
```json
[
    {"segment_id": "片段ID", "reason": "选择原因"}
]
```
只返回JSON数组，不要包含其他文字。"""


def _time_to_seconds(t: str) -> int:
    """将 HH:MM:SS,mmm 格式转为秒数"""
    parts = t.split(":")
    if len(parts) == 3:
        sec_part = parts[2].split(",")[0]
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(sec_part)
    return 0


def _seconds_to_time(seconds: int) -> str:
    """将秒数转为 HH:MM:SS 格式"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _segments_overlap(a: VideoSegment, b: VideoSegment) -> bool:
    """检查两个片段是否有时间重叠"""
    a_start = _time_to_seconds(a.start_time)
    a_end = _time_to_seconds(a.end_time)
    b_start = _time_to_seconds(b.start_time)
    b_end = _time_to_seconds(b.end_time)
    return a_start < b_end and b_start < a_end


def _build_segments_prompt(
    segments_by_video: Dict[str, Dict[str, List[VideoSegment]]],
    style_name: str,
    style_description: str,
) -> str:
    """构建包含所有片段信息的提示词"""
    lines = [f"解说风格：{style_name}", f"风格说明：{style_description}", ""]
    lines.append("可用的片段列表：")

    max_duration = 0
    for video_path, type_dict in segments_by_video.items():
        video_name = video_path.split("/")[-1].split("\\")[-1]
        lines.append(f"\n## 视频：{video_name}")

        for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
            segments = type_dict.get(seg_type, [])
            if not segments:
                continue

            for seg in segments:
                seg_end = _time_to_seconds(seg.end_time)
                if seg_end > max_duration:
                    max_duration = seg_end

            type_names = {
                "gold_3s": "黄金3秒",
                "highlight": "亮点解析",
                "plot": "剧情解析",
                "ending": "结尾悬念",
            }
            lines.append(f"\n### {type_names.get(seg_type, seg_type)}")
            for seg in segments:
                lines.append(
                    f"- ID: {seg.id} | {seg.start_time}-{seg.end_time} | {seg.description}"
                )

    total_minutes = max_duration // 60
    lines.append(f"\n原视频总时长：约{total_minutes}分钟")

    lines.append(
        "\n请根据解说风格选择最合适的片段组合，确保片段之间时间不重叠，按时间顺序排列。"
    )
    return "\n".join(lines)


def _build_narration_prompt(
    segments_by_video: Dict[str, Dict[str, List[VideoSegment]]],
    subtitles_by_video: Dict[str, str],
    style_key: str,
    style_name: str,
    style_description: str,
    language: str = "zh",
    original_sound_ratio: int = 0,
) -> str:
    """构建用于生成解说文案的提示词"""
    lang_names = {"zh": "中文", "en": "英文", "th": "泰文", "id": "印尼文"}
    lang_name = lang_names.get(language, "中文")
    
    lines = [
        f"解说风格：{style_name}",
        f"风格要求：{style_description}",
        f"解说语言：{lang_name}",
        "",
        "请分析以下视频片段的画面内容和字幕，生成解说文案：",
    ]

    for video_path, type_dict in segments_by_video.items():
        video_name = video_path.split("/")[-1].split("\\")[-1]
        subtitle_content = subtitles_by_video.get(video_path, "无字幕")
        lines.append(f"\n## 视频：{video_name}")

        if subtitle_content and subtitle_content != "无字幕":
            lines.append(f"\n### 字幕内容：")
            lines.append(subtitle_content[:2000])

        for seg_type in ["gold_3s", "highlight", "plot", "ending"]:
            segments = type_dict.get(seg_type, [])
            if not segments:
                continue

            type_names = {
                "gold_3s": "黄金3秒",
                "highlight": "亮点解析",
                "plot": "剧情解析",
                "ending": "结尾悬念",
            }
            lines.append(f"\n### {type_names.get(seg_type, seg_type)}")
            for seg in segments:
                lines.append(
                f"- ID: {seg.id} | {seg.start_time}-{seg.end_time}\n  画面描述: {seg.description}"
            )

    lines.append(
        "\n\n请按照指定风格生成解说文案，要求："
        "\n1. 先分析整体故事梗概，梳理清晰的故事主线"
        "\n2. 选择片段时，优先选择能推动主线剧情发展的关键片段"
        "\n3. 片段数量不限制，但片段时间戳绝对不能重叠"
        "\n4. 所有选中片段的总时长应控制在原视频时长的50%-70%之间，不宜过短或过长"
        "\n5. 确保故事线清晰完整，主线叙事连贯，让观众能快速看懂故事"
        "\n6. 为每个选中的片段生成第三人称解说文案"
    )
    
    if original_sound_ratio > 0:
        lines.append(
            f"\n7. 原声片段比例：每个选中片段中，前{100-original_sound_ratio}%时长是解说，后{original_sound_ratio}%时长是原声"
            f"\n   例如：一个10秒的片段，原声比例30%，则前7秒是解说，后3秒是原声"
            f"\n8. 每个选中片段最多拆分为2个子片段（解说+原声），不许拆分为3个"
            f"\n9. 不许出现连续的原声片段"
        )
    else:
        lines.append(
            "\n7. 原声比例=0%，所有片段都是解说片段，不需要拆分原声"
        )
    
    return "\n".join(lines)


TIME_FORMAT_PATTERN = r"^\d{2}:\d{2}:\d{2},\d{3}$"


def _validate_time_format(t: str) -> bool:
    if not re.match(TIME_FORMAT_PATTERN, t):
        return False
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s, ms = parts[2].split(",")
    s, ms = int(s), int(ms)
    return 0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59 and 0 <= ms <= 999


def _validate_narration_time_format(scripts: List[dict]) -> list[str]:
    errors = []
    for i, item in enumerate(scripts):
        for key in ("start_time", "end_time"):
            val = item.get(key, "")
            if val and not _validate_time_format(val):
                errors.append(f"第{i+1}条文案 {key}格式错误: {repr(val)}，应为HH:MM:SS,mmm")
    return errors


def _time_str_to_sec(t: str) -> float:
    parts = t.split(":")
    sec_part = parts[2].replace(",", ".")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(sec_part)


def _post_process_scripts(scripts: List[dict]) -> List[dict]:
    if not scripts:
        return scripts

    seen_seg_ids = set()
    deduped = []
    for item in scripts:
        seg_id = item.get("segment_id", "")
        combo = (seg_id, item.get("content_type", ""), item.get("start_time", ""), item.get("end_time", ""))
        if combo not in seen_seg_ids:
            seen_seg_ids.add(combo)
            deduped.append(item)

    if len(deduped) != len(scripts):
        logger.warning(f"去除了 {len(scripts) - len(deduped)} 个重复的片段")

    cleaned = []
    for item in deduped:
        if item.get("content_type") == "original_sound" and cleaned and cleaned[-1].get("content_type") == "original_sound":
            prev = cleaned[-1]
            if _time_str_to_sec(item["end_time"]) > _time_str_to_sec(prev["end_time"]):
                prev["end_time"] = item["end_time"]
            continue
        cleaned.append(item)

    if len(cleaned) != len(deduped):
        logger.warning(f"合并了 {len(deduped) - len(cleaned)} 个连续的原声片段")

    return cleaned


def _extract_json_array(text: str) -> str | None:
    text = text.strip()

    md_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if md_match:
        candidate = md_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    start = text.find("[")
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    return None
    return None


def _parse_ai_response(response: str) -> List[dict]:
    """解析AI返回的JSON"""
    extracted = _extract_json_array(response)
    if extracted is not None:
        parsed = json.loads(extracted)
        time_errors = _validate_narration_time_format(parsed)
        if time_errors:
            error_detail = "；".join(time_errors)
            logger.error(f"AI返回的时间格式错误: {error_detail}")
            raise ValueError(
                f"AI返回的时间格式不符合要求（应为HH:MM:SS,mmm格式，如00:01:25,500），"
                f"请点击「重新生成」按钮重新生成。\n详细错误：{error_detail}"
            )
        return parsed

    logger.error("无法从AI响应中提取JSON数组: %s", response[:500])
    raise ValueError("AI返回格式错误，无法解析为JSON数组")


class ClippingAnalysisService:
    """AI解说分析服务"""

    def __init__(self, ai_service: AIService):
        self._ai_service = ai_service

    async def close(self):
        await self._ai_service.close()

    async def analyze(
        self,
        segments_by_video: Dict[str, Dict[str, List[VideoSegment]]],
        style_key: str,
        max_retries: int = 3,
    ) -> List[Tuple[str, str]]:
        """
        分析并选择最佳片段组合

        Args:
            segments_by_video: {video_path: {type: [VideoSegment, ...]}}
            style_key: "emotional" | "humorous" | "logical" | "fast_paced"
            max_retries: AI返回格式异常时的最大重试次数

        Returns:
            [(segment_id, reason), ...] 按时间排序的选中片段列表
        """
        style_names = {
            "emotional": "情感共鸣",
            "humorous": "搞笑幽默",
            "logical": "逻辑严谨",
            "fast_paced": "超快节奏",
        }
        style_descriptions = {
            "emotional": (
                "深入挖掘角色情感，通过细腻的情感表达引发观众共鸣，"
                "让观众与角色同悲同喜，获得情感上的触动与释放"
            ),
            "humorous": (
                "以轻松诙谐的方式解读剧情，挖掘笑点和梗，"
                "用幽默的语言风格让观众在欢笑中看完故事"
            ),
            "logical": (
                "严谨梳理剧情逻辑，清晰呈现事件因果，"
                "帮助观众理清复杂的人物关系和故事脉络"
            ),
            "fast_paced": (
                "快节奏、高能输出，简洁有力的语言风格，"
                "信息密度大，保持观众持续观看的好奇心和紧张感"
            ),
        }

        style_name = style_names.get(style_key, style_key)
        style_desc = style_descriptions.get(style_key, "")

        prompt = _build_segments_prompt(segments_by_video, style_name, style_desc)

        last_error = None
        for attempt in range(max_retries):
            try:
                response = await self._ai_service.generate_text(
                    prompt=prompt,
                    system_prompt=CLIPPING_SYSTEM_PROMPT,
                    temperature=0.3,
                    timeout=600,
                )
                logger.info("AI片段选择返回: %s", response[:500])
            except Exception as e:
                logger.error("AI片段选择请求失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                continue

            try:
                selections = _parse_ai_response(response)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("解析AI响应失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                if attempt < max_retries - 1:
                    logger.info("正在重试...")
                continue

            all_segments: Dict[str, VideoSegment] = {}
            for type_dict in segments_by_video.values():
                for seg_list in type_dict.values():
                    for seg in seg_list:
                        all_segments[seg.id] = seg

            selected_segments: List[VideoSegment] = []
            result: List[Tuple[str, str]] = []

            for item in selections:
                seg_id = item.get("segment_id", "")
                reason = item.get("reason", "")
                if seg_id in all_segments:
                    seg = all_segments[seg_id]
                    if not any(_segments_overlap(seg, s) for s in selected_segments):
                        selected_segments.append(seg)
                        result.append((seg_id, reason))
                    else:
                        logger.info("跳过重叠片段: %s (%s-%s)", seg_id, seg.start_time, seg.end_time)

            result.sort(
                key=lambda x: _time_to_seconds(all_segments[x[0]].start_time),
            )

            logger.info(
                "AI选择了 %d 个片段（原始返回 %d 个）",
                len(result), len(selections),
            )
            return result

        error_msg = f"AI返回格式异常（已重试{max_retries}次）: {last_error}"
        logger.error(error_msg)
        raise ValueError(error_msg) from last_error

    async def polish_narration(
        self,
        narration_results: List[dict],
        language: str = "zh",
        narration_speed: int = 3,
        max_retries: int = 3,
    ) -> List[dict]:
        """
        润色解说文案，确保时长不超过片段时长

        Args:
            narration_results: 初代解说文案列表
            language: "zh" | "en" | "th"
            narration_speed: 目标语速（字/秒）
            max_retries: AI返回格式异常时的最大重试次数

        Returns:
            润色后的解说文案列表
        """
        polish_prompt = _build_polish_prompt(narration_results, language, narration_speed)

        last_error = None
        for attempt in range(max_retries):
            try:
                response = await self._ai_service.generate_text(
                    prompt=polish_prompt,
                    system_prompt=POLISH_NARRATION_SYSTEM_PROMPT,
                    temperature=0.7,
                    timeout=600,
                )
                logger.info("AI文案润色返回: %s", response[:300])
            except Exception as e:
                logger.error("AI文案润色请求失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                continue

            try:
                polished_scripts = _parse_ai_polish_response(response)
                logger.info("成功润色 %d 条解说文案", len(polished_scripts))
                return polished_scripts
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("解析润色响应失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                if attempt < max_retries - 1:
                    logger.info("正在重试...")
                continue

        logger.warning("文案润色失败，使用原始文案")
        return narration_results

    async def generate_narration(
        self,
        segments_by_video: Dict[str, Dict[str, List[VideoSegment]]],
        subtitles_by_video: Dict[str, str],
        style_key: str,
        language: str = "zh",
        original_sound_ratio: int = 0,
        narration_speed: int = 3,
        max_retries: int = 3,
    ) -> List[dict]:
        """
        生成解说文案

        Args:
            segments_by_video: {video_path: {type: [VideoSegment, ...]}}
            subtitles_by_video: {video_path: subtitle_content}
            style_key: "emotional" | "humorous" | "logical" | "fast_paced"
            language: "zh" | "en" | "th"
            original_sound_ratio: 0-70, 原声片段比例
            narration_speed: 目标语速（字/秒）
            max_retries: AI返回格式异常时的最大重试次数

        Returns:
            [
                {
                    "segment_id": "片段ID",
                    "start_time": "00:00:00",
                    "end_time": "00:00:15",
                    "story_summary": "故事梗概",
                    "narration_script": "解说文案",
                    "sub_segments": [...]  // 如果有原声比例
                }, ...
            ]
        """
        style_names = {
            "emotional": "情感共鸣",
            "humorous": "搞笑幽默",
            "logical": "逻辑严谨",
            "fast_paced": "超快节奏",
        }
        style_descriptions = {
            "emotional": (
                "深入挖掘角色情感，通过细腻的情感表达引发观众共鸣，"
                "让观众与角色同悲同喜，获得情感上的触动与释放"
            ),
            "humorous": (
                "以轻松诙谐的方式解读剧情，挖掘笑点和梗，"
                "用幽默的语言风格让观众在欢笑中看完故事"
            ),
            "logical": (
                "严谨梳理剧情逻辑，清晰呈现事件因果，"
                "帮助观众理清复杂的人物关系和故事脉络"
            ),
            "fast_paced": (
                "快节奏、高能输出，简洁有力的语言风格，"
                "信息密度大，保持观众持续观看的好奇心和紧张感"
            ),
        }

        style_name = style_names.get(style_key, style_key)
        style_desc = style_descriptions.get(style_key, "")

        prompt = _build_narration_prompt(
            segments_by_video, subtitles_by_video, style_key, style_name, style_desc,
            language, original_sound_ratio
        )

        last_error = None
        for attempt in range(max_retries):
            try:
                response = await self._ai_service.generate_text(
                    prompt=prompt,
                    system_prompt=NARRATION_SYSTEM_PROMPT,
                    temperature=0.7,
                    timeout=600,
                )
                logger.info("AI解说文案生成返回: %s", response[:300])
            except Exception as e:
                logger.error("AI解说文案生成请求失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                continue

            try:
                narration_scripts = _parse_ai_response(response)
                narration_scripts = _post_process_scripts(narration_scripts)
                logger.info("成功解析 %d 条解说文案", len(narration_scripts))

                polished_results = await self.polish_narration(narration_scripts, language, narration_speed)
                if polished_results and polished_results != narration_scripts:
                    for item in narration_scripts:
                        if item.get("content_type") != "narration":
                            continue
                        seg_id = item.get("segment_id", "")
                        if seg_id in polished_results:
                            item["narration_script"] = polished_results[seg_id]
                    logger.info("文案润色完成")

                return narration_scripts
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("解析AI响应失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                if attempt < max_retries - 1:
                    logger.info("正在重试...")
                continue

        error_msg = f"AI返回格式异常（已重试{max_retries}次）: {last_error}"
        logger.error(error_msg)
        raise ValueError(error_msg) from last_error


def _build_polish_prompt(narration_results: List[dict], language: str, narration_speed: int = 3) -> str:
    """构建润色提示词"""
    lang_names = {"zh": "中文", "en": "英文", "th": "泰文", "id": "印尼文"}
    lang_name = lang_names.get(language, "中文")

    lines = [
        f"解说语言：{lang_name}",
        f"目标语速：{narration_speed}字/秒",
        "",
        "请对以下解说文案进行润色，使其朗读时长精确匹配对应片段的时长。",
        "润色时需计算当前文案的字数和语速，再根据片段时长调整到合适长度。",
        "",
    ]

    for i, item in enumerate(narration_results):
        content_type = item.get("content_type", "narration")
        start_time = item.get("start_time", "")
        end_time = item.get("end_time", "")
        narration_script = item.get("narration_script", "")
        story_summary = item.get("story_summary", "")

        if content_type == "original_sound":
            lines.append(f"{i+1}. [原声片段] {start_time}-{end_time} - 不需要处理")
            continue

        start_sec = _time_to_seconds(start_time)
        end_sec = _time_to_seconds(end_time)
        duration = end_sec - start_sec
        char_count = len(narration_script)
        current_speed = char_count / duration if duration > 0 else 0
        target_chars = int(duration * narration_speed)

        lines.append(f"{i+1}. [解说片段] {start_time}-{end_time} (时长{duration}秒)")
        lines.append(f"   故事梗概：{story_summary}")
        lines.append(f"   原始文案（{char_count}字）：{narration_script}")
        lines.append(f"   当前语速：{current_speed:.1f}字/秒 | 目标语速：{narration_speed}字/秒")
        lines.append(f"   目标字数：约{target_chars}字")
        lines.append("")

    lines.append("")
    lines.append("请返回润色后的JSON数组，格式：")
    lines.append('[{"segment_id": "1", "polished_script": "润色后的文案"}, ...]')
    lines.append("只返回JSON数组，不要包含其他文字。")

    return "\n".join(lines)


POLISH_NARRATION_SYSTEM_PROMPT = """\
你是一个专业的短剧解说配音编辑，擅长将解说文案调整到精确匹配片段时长。

你的工作流程：
1. 分析原始文案的字数，计算当前语速（字数/片段时长）
2. 根据提示词中指定的目标语速计算应保留的字数
3. 精简或扩写文案，使润色后的文案能在片段时长内自然读完

要求：
1. 保持文案的核心信息、故事脉络和第三人称叙述风格
2. 朗读时长必须精确匹配对应片段时长（不能超时，也不要太短）
3. 只处理 content_type="narration" 的片段，original_sound 保持不变
4. 返回合法的JSON数组

返回格式：
```json
[
    {"segment_id": "1", "polished_script": "润色后的解说文案"},
    {"segment_id": "2", "polished_script": "润色后的解说文案"}
]
```
只返回JSON数组，不要包含其他文字。"""


def _parse_ai_polish_response(response: str) -> List[dict]:
    """解析润色AI的响应"""
    response = response.strip()

    if response.startswith("```json"):
        response = response[7:]
    if response.startswith("```"):
        response = response[3:]
    if response.endswith("```"):
        response = response[:-3]

    response = response.strip()

    polished_map = {}
    try:
        polished_list = json.loads(response)
        for item in polished_list:
            seg_id = item.get("segment_id", "")
            polished_script = item.get("polished_script", "")
            if seg_id and polished_script:
                polished_map[seg_id] = polished_script
    except json.JSONDecodeError as e:
        logger.warning("JSON解析失败: %s", e)
        raise ValueError(f"JSON解析失败: {e}")

    if not polished_map:
        raise ValueError("没有找到有效的润色结果")

    return polished_map

    