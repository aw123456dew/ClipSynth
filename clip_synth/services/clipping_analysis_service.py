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
6. 如果原声比例大于0，需要将片段拆分为多个子片段，每个子片段作为独立项返回：
   - content_type: "narration"表示解说，"original_sound"表示原声
   - start_time: 片段开始时间
   - end_time: 片段结束时间
   - narration_script: 解说文案（如果是原声片段则为空字符串）

返回格式示例（无原声）：
```json
[
    {
        "segment_id": "片段ID",
        "start_time": "00:00:00",
        "end_time": "00:00:15",
        "content_type": "narration",
        "story_summary": "这个片段的故事梗概",
        "narration_script": "第三人称解说文案..."
    }
]
```

返回格式示例（有原声比例30%）：
```json
[
    {
        "segment_id": "片段ID",
        "start_time": "00:00:00",
        "end_time": "00:00:07",
        "content_type": "narration",
        "story_summary": "这个片段的故事梗概",
        "narration_script": "解说文案..."
    },
    {
        "segment_id": "片段ID",
        "start_time": "00:00:07",
        "end_time": "00:00:10",
        "content_type": "original_sound",
        "story_summary": "",
        "narration_script": ""
    },
    {
        "segment_id": "片段ID",
        "start_time": "00:00:10",
        "end_time": "00:00:15",
        "content_type": "narration",
        "story_summary": "",
        "narration_script": "解说文案..."
    }
]
```

注意：只返回JSON数组，不要包含其他文字。"""

CLIPPING_SYSTEM_PROMPT = """\
你是一个专业的短视频剪辑师，擅长从视频片段中选择最佳组合来制作高质量的解说视频。

你的任务是根据用户选择的解说风格，从提供的片段列表中选择最合适的片段组合。

要求：
1. 选择的片段在时间上**绝对不能重合**（即不能有时间重叠的片段）
2. 每个片段只能选择一次
3. 根据解说风格的特点选择最能展现该风格特色的片段
4. 优先选择有清晰剧情、有看点、有冲突的片段
5. 返回格式必须是合法的JSON数组

返回格式示例：
```json
[
    {
        "segment_id": "片段ID",
        "reason": "选择这个片段的原因"
    }
]
```

注意：只返回JSON数组，不要包含其他文字。"""


def _time_to_seconds(t: str) -> int:
    """将 HH:MM:SS 或 MM:SS 格式转为秒数"""
    parts = list(map(int, t.split(":")))
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


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

    for video_path, type_dict in segments_by_video.items():
        video_name = video_path.split("/")[-1].split("\\")[-1]
        lines.append(f"\n## 视频：{video_name}")

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
                    f"- ID: {seg.id} | {seg.start_time}-{seg.end_time} | {seg.description}"
                )

    lines.append(
        "\n请根据解说风格选择最合适的片段组合，确保片段之间时间不重叠。"
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
    lang_names = {"zh": "中文", "en": "英文", "th": "泰文"}
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
        "\n1. 先分析整体故事梗概"
        "\n2. 为每个选中的片段生成第三人称解说"
        "\n3. 确保故事线清晰，主线明确"
        "\n4. 让观众能快速看懂故事"
    )
    
    if original_sound_ratio > 0:
        lines.append(
            f"\n5. 原声片段比例：{original_sound_ratio}%，即每个片段中{original_sound_ratio}%使用原始声音，{100-original_sound_ratio}%使用解说"
            f"\n6. 如果原声比例大于0，需要将片段拆分为多个子片段："
            f"\n   - 每个子片段需要标注是'narration'(解说)还是'original_sound'(原声)"
            f"\n   - 原声片段应选择该片段中最精彩、最有代表性的部分"
            f"\n   - 解说片段与原声片段的总时长应保持原始片段的时长"
        )
    
    return "\n".join(lines)


def _parse_ai_response(response: str) -> List[dict]:
    """解析AI返回的JSON"""
    json_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", response)
    if json_match:
        response = json_match.group(1)

    response = response.strip()
    if response.startswith("```"):
        response = response.strip("`").strip()
    if response.startswith("json"):
        response = response[4:].strip()

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        logger.warning("JSON解析失败，尝试提取数组: %s", response[:200])
        array_match = re.search(r"\[[\s\S]*?\]", response)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        raise


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
                    max_tokens=4096,
                    timeout=120,
                )
                logger.info("AI片段选择返回: %s", response[:300])
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
                key=lambda x: _time_to_seconds(all_segments[x[0]].start_time)
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
        max_retries: int = 3,
    ) -> List[dict]:
        """
        润色解说文案，确保时长不超过片段时长

        Args:
            narration_results: 初代解说文案列表
            language: "zh" | "en" | "th"
            max_retries: AI返回格式异常时的最大重试次数

        Returns:
            润色后的解说文案列表
        """
        polish_prompt = _build_polish_prompt(narration_results, language)

        last_error = None
        for attempt in range(max_retries):
            try:
                response = await self._ai_service.generate_text(
                    prompt=polish_prompt,
                    system_prompt=POLISH_NARRATION_SYSTEM_PROMPT,
                    temperature=0.7,
                    max_tokens=8192,
                    timeout=180,
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
                    max_tokens=8192,
                    timeout=180,
                )
                logger.info("AI解说文案生成返回: %s", response[:300])
            except Exception as e:
                logger.error("AI解说文案生成请求失败(第%d次): %s", attempt + 1, str(e))
                last_error = e
                continue

            try:
                narration_scripts = _parse_ai_response(response)
                logger.info("成功解析 %d 条解说文案", len(narration_scripts))

                polished_results = await self.polish_narration(narration_scripts, language)
                if polished_results and polished_results != narration_scripts:
                    for item in narration_scripts:
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


def _build_polish_prompt(narration_results: List[dict], language: str) -> str:
    """构建润色提示词"""
    lang_names = {"zh": "中文", "en": "英文", "th": "泰文"}
    lang_name = lang_names.get(language, "中文")

    lang_config = {
        "zh": {
            "name": "中文",
            "speed": "每100字约需23秒",
            "chars_per_sec": 4.3,
        },
        "en": {
            "name": "英文",
            "speed": "每100词约需12秒",
            "chars_per_sec": 8,
        },
        "th": {
            "name": "泰文",
            "speed": "每100字约需18秒",
            "chars_per_sec": 5.5,
        },
    }
    config = lang_config.get(language, lang_config["zh"])

    lines = [
        f"解说语言：{lang_name}",
        f"语速参考：{config['speed']}",
        "",
        "请对以下解说文案进行润色，确保文案朗读时长不超过对应片段时长：",
        "",
    ]

    for i, item in enumerate(narration_results):
        seg_id = item.get("segment_id", "")
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

        max_chars = int(duration * config["chars_per_sec"])

        lines.append(f"{i+1}. [解说片段] {start_time}-{end_time} (时长{duration}秒)")
        lines.append(f"   故事梗概：{story_summary}")
        lines.append(f"   原始文案：{narration_script}")
        lines.append(f"   建议字数：不超过{max_chars}字")
        lines.append("")

    lines.append("")
    lines.append("请返回润色后的JSON数组，格式：")
    lines.append('[{"segment_id": "1", "polished_script": "润色后的文案", ...}, ...]')
    lines.append("只返回JSON数组，不要包含其他文字。")

    return "\n".join(lines)


POLISH_NARRATION_SYSTEM_PROMPT = """\
你是一个专业的短剧解说配音编辑，擅长将解说文案调整到适合配音的时长。

你的任务：
1. 分析原始解说文案的内容和时长
2. 根据目标时长（片段时长）调整文案长度
3. 保持文案的核心信息和风格
4. 确保调整后的文案流畅易读

要求：
1. 文案必须保持第三人称叙述
2. 故事线要清晰，主线明确
3. 朗读时长不能超过对应片段时长
4. 返回格式必须是合法的JSON数组
5. 只处理 content_type="narration" 的片段，original_sound 片段保持不变

返回格式示例：
```json
[
    {
        "segment_id": "1",
        "polished_script": "润色后的解说文案..."
    },
    {
        "segment_id": "2",
        "polished_script": "润色后的解说文案..."
    }
]
```

注意：只返回JSON数组，不要包含其他文字。"""


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

    