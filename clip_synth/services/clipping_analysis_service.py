import json
import logging
import re
from typing import Dict, List, Tuple

from clip_synth.models.project_state import VideoSegment
from clip_synth.services.ai_service import AIService

logger = logging.getLogger("clip_synth.clipping_analysis")

CLIPPING_SYSTEM_PROMPT = """\
你是一个专业的短视频剪辑师，擅长从视频片段中选择最佳组合来制作高质量的混剪视频。

你的任务是根据用户选择的剪辑手法，从提供的片段列表中选择最合适的片段组合。

要求：
1. 选择的片段在时间上**绝对不能重合**（即不能有时间重叠的片段）
2. 每个片段只能选择一次
3. 根据剪辑手法的特点选择最合适的片段
4. 返回格式必须是合法的JSON数组

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
    lines = [f"剪辑手法：{style_name}", f"手法说明：{style_description}", ""]
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
        "\n请根据剪辑手法选择最合适的片段组合，确保片段之间时间不重叠。"
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
    """剪辑手法AI分析服务"""

    def __init__(self, ai_service: AIService):
        self._ai_service = ai_service

    async def analyze(
        self,
        segments_by_video: Dict[str, Dict[str, List[VideoSegment]]],
        style_key: str,
    ) -> List[Tuple[str, str]]:
        """
        分析并选择最佳片段组合

        Args:
            segments_by_video: {video_path: {type: [VideoSegment, ...]}}
            style_key: "high_energy" | "hot_prelude" | "golden_three"

        Returns:
            [(segment_id, reason), ...] 按时间排序的选中片段列表
        """
        style_names = {
            "high_energy": "高燃混剪",
            "hot_prelude": "热点前置",
            "golden_three": "黄金三段",
        }
        style_descriptions = {
            "high_energy": (
                "AI智能识别视频高能瞬间，自动提取精彩片段，"
                "一键生成节奏紧凑、情绪炸裂的高燃混剪，瞬间点燃观众热情"
            ),
            "hot_prelude": (
                "用悬念、冲突或反转打造「黄金前3秒」，"
                "强势抓住注意力，迅速激发观看兴趣，再自然衔接原片完整剧情"
            ),
            "golden_three": (
                "高能开场 → 完整叙事 → 引流转化收尾，层层递进，"
                "兼顾吸引力与传播目标，提升完播与转化效率"
            ),
        }

        style_name = style_names.get(style_key, style_key)
        style_desc = style_descriptions.get(style_key, "")

        prompt = _build_segments_prompt(segments_by_video, style_name, style_desc)

        try:
            response = await self._ai_service.generate_text(
                prompt=prompt,
                system_prompt=CLIPPING_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=4096,
                timeout=120,
            )
            logger.info("AI剪辑分析返回: %s", response[:300])
        except Exception as e:
            logger.error("AI剪辑分析请求失败: %s", str(e))
            raise

        try:
            selections = _parse_ai_response(response)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("解析AI响应失败: %s, 原始响应: %s", str(e), response)
            raise ValueError(f"AI返回格式异常: {e}") from e

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
