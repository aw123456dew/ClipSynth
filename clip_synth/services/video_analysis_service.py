import asyncio
import json
import logging
import re
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from clip_synth.models.project_state import AnalysisMode, VideoSegment
from clip_synth.services.ai_service import AIService
from clip_synth.services.video_preprocessor import VideoPreprocessor

logger = logging.getLogger("clip_synth.video_analysis")

SLICE_DURATION = 10
MAX_RETRIES = 3
RETRY_DELAY = 2.0

FRAME_DESCRIPTION_PROMPT = (
    "你是一个视频画面分析专家。请仔细观察这组连续的视频帧，"
    "用一段简洁的中文描述这段时间内发生的画面内容。\n"
    "\n"
    "## 输出格式要求\n"
    "请严格按照以下JSON格式输出，不要包含任何其他内容：\n"
    "\n"
    "```json\n"
    '{"description": "这里填写画面描述，50字以内简洁描述画面中的人物、动作、场景变化"}\n'
    "```\n"
    "\n"
    "## 重要规则\n"
    "1. 描述要简洁，控制在50字以内\n"
    "2. 重点描述：画面中的人物、动作、场景变化\n"
    "3. 如果画面是黑屏或纯色，描述为'黑屏'或'纯色画面'\n"
    "4. 严格按照实际画面内容描述，不要编造\n"
    "5. 确保输出的JSON是合法有效的"
)

FINAL_ANALYSIS_PROMPT = (
    "你是一个专业的短视频/短剧分析专家。请根据提供的视频画面描述和字幕内容，对视频进行深度分析。\n"
    "\n"
    "## 分析目标\n"
    "从视频中提取出以下4类关键片段，**每类至少输出3-5个片段**，如果视频内容丰富可以更多：\n"
    "\n"
    "### 1. AI黄金3秒 (gold_3s) —— 至少3个\n"
    "- 能迅速抓住观众注意力，具有视觉冲击力和情感共鸣的黄金三秒剧情片段\n"
    "- 例如：主角被侮辱/被揍、获得金手指/系统、身份反转、激烈冲突等\n"
    "- 不限于视频开头，可以是任意时间点的高潮片段\n"
    "- **长度控制在3-8秒**，短小精悍，一上来就抓住观众\n"
    "\n"
    "### 2. AI亮点解析 (highlight) —— 至少3个\n"
    "- 按照时间轴以故事化的方式叙述每一段重要剧情\n"
    "- **每个片段必须是完整的剧情段落**，包含起因、经过、结果\n"
    "- **长度建议在15-40秒**，确保情节完整不截断\n"
    "- 片段之间按时间顺序排列，串联起来能还原整个故事脉络\n"
    "\n"
    "### 3. AI剧情解析 (plot) —— 至少3个\n"
    "- 具有视觉冲击力，留有悬念，引发观众好奇心及情感共鸣的有亮点的剧情片段\n"
    "- 包括反转、冲突升级、关键信息揭露、情绪爆发点等\n"
    "- **每个片段必须是完整的场景**，长度建议在10-30秒\n"
    "- 每个片段应独立成篇，本身就有足够的看点和吸引力\n"
    "\n"
    "### 4. AI结尾悬念 (ending) —— 至少3个\n"
    "- 关键时刻将情节拉至极致，既制造出突如其来的转折，又巧妙地留下一系列疑问的片段\n"
    "- 包括未完待续、悬念设置、下集预告、反转留白等\n"
    "- **每个片段必须是完整的悬念段落**，长度建议在10-25秒\n"
    "- 用于吸引观众继续观看下一集\n"
    "\n"
    "## 输出格式要求\n"
    "请严格按照以下JSON格式输出，不要包含任何其他内容：\n"
    "\n"
    "```json\n"
    "{\n"
    '  "segments": [\n'
    "    {\n"
    '      "type": "gold_3s",\n'
    '      "start_time": "00:00:00,000",\n'
    '      "end_time": "00:00:00,000",\n'
    '      "description": "片段描述（20字以内）"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "```\n"
    "\n"
    "## 重要规则\n"
    "1. **时间格式必须是 HH:MM:SS,mmm（例如：00:01:25,500），秒和毫秒之间用逗号隔开，这是SRT标准格式**\n"
    "2. **黄金3秒(gold_3s)长度控制在3-8秒**，其他类型片段要完整，长度在10-40秒\n"
    "3. 描述要简洁有力，控制在20字以内\n"
    "4. **每类至少输出3-5个片段**，内容丰富时可以更多，不设上限\n"
    "5. 片段之间时间不要重叠\n"
    "6. 严格按照视频画面描述和字幕的实际内容分析，不要编造\n"
    "7. 如果某类片段确实不存在，可以不输出该类型\n"
    "8. 确保输出的JSON是合法有效的"
)

SUBTITLE_ONLY_PROMPT = (
    "你是一个专业的短视频/短剧分析专家。请根据提供的字幕内容，对视频进行深度分析。\n"
    "\n"
    "## 分析目标\n"
    "从视频中提取出以下4类关键片段，**每类至少输出3-5个片段**，如果视频内容丰富可以更多：\n"
    "\n"
    "### 1. AI黄金3秒 (gold_3s) —— 至少3个\n"
    "- 能迅速抓住观众注意力，具有视觉冲击力和情感共鸣的黄金三秒剧情片段\n"
    "- 例如：主角被侮辱/被揍、获得金手指/系统、身份反转、激烈冲突等\n"
    "- 不限于视频开头，可以是任意时间点的高潮片段\n"
    "- **长度控制在3-8秒**，短小精悍，一上来就抓住观众\n"
    "\n"
    "### 2. AI亮点解析 (highlight) —— 至少3个\n"
    "- 按照时间轴以故事化的方式叙述每一段重要剧情\n"
    "- **每个片段必须是完整的剧情段落**，包含起因、经过、结果\n"
    "- **长度建议在15-40秒**，确保情节完整不截断\n"
    "- 片段之间按时间顺序排列，串联起来能还原整个故事脉络\n"
    "\n"
    "### 3. AI剧情解析 (plot) —— 至少3个\n"
    "- 具有视觉冲击力，留有悬念，引发观众好奇心及情感共鸣的有亮点的剧情片段\n"
    "- 包括反转、冲突升级、关键信息揭露、情绪爆发点等\n"
    "- **每个片段必须是完整的场景**，长度建议在10-30秒\n"
    "- 每个片段应独立成篇，本身就有足够的看点和吸引力\n"
    "\n"
    "### 4. AI结尾悬念 (ending) —— 至少3个\n"
    "- 关键时刻将情节拉至极致，既制造出突如其来的转折，又巧妙地留下一系列疑问的片段\n"
    "- 包括未完待续、悬念设置、下集预告、反转留白等\n"
    "- **每个片段必须是完整的悬念段落**，长度建议在10-25秒\n"
    "- 用于吸引观众继续观看下一集\n"
    "\n"
    "## 输出格式要求\n"
    "请严格按照以下JSON格式输出，不要包含任何其他内容：\n"
    "\n"
    "```json\n"
    "{\n"
    '  "segments": [\n'
    "    {\n"
    '      "type": "gold_3s",\n'
    '      "start_time": "00:00:00,000",\n'
    '      "end_time": "00:00:00,000",\n'
    '      "description": "片段描述（20字以内）"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "```\n"
    "\n"
    "## 重要规则\n"
    "1. **时间格式必须是 HH:MM:SS,mmm（例如：00:01:25,500），秒和毫秒之间用逗号隔开，这是SRT标准格式**\n"
    "2. **黄金3秒(gold_3s)长度控制在3-8秒**，其他类型片段要完整，长度在10-40秒\n"
    "3. 描述要简洁有力，控制在20字以内\n"
    "4. **每类至少输出3-5个片段**，内容丰富时可以更多，不设上限\n"
    "5. 片段之间时间不要重叠\n"
    "6. 严格按照字幕的实际内容分析，不要编造\n"
    "7. 如果某类片段确实不存在，可以不输出该类型\n"
    "8. 确保输出的JSON是合法有效的"
)


def _validate_time_format(t: str) -> bool:
    if not re.match(r"^\d{2}:\d{2}:\d{2},\d{3}$", t):
        return False
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    s, ms = parts[2].split(",")
    s, ms = int(s), int(ms)
    return 0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59 and 0 <= ms <= 999


def _validate_segment_time_format(segments: list) -> list[str]:
    errors = []
    for i, seg in enumerate(segments):
        st = seg.get("start_time", "")
        et = seg.get("end_time", "")
        if not _validate_time_format(st):
            errors.append(f"片段{i+1} start_time格式错误: {repr(st)}，应为HH:MM:SS,mmm")
        if not _validate_time_format(et):
            errors.append(f"片段{i+1} end_time格式错误: {repr(et)}，应为HH:MM:SS,mmm")
    return errors


class VideoAnalysisService:
    def __init__(self, ai_service: AIService):
        self._ai_service = ai_service
        self._preprocessor = VideoPreprocessor()

    async def close(self):
        await self._ai_service.close()

    async def analyze_video(
        self,
        video_path: str,
        subtitle_path: str | None,
        mode: AnalysisMode = AnalysisMode.PRECISE,
        progress_callback: Callable | None = None,
    ) -> list[VideoSegment]:
        video_name = Path(video_path).name
        duration = self._get_video_duration(video_path)
        logger.info(
            "视频时长: %.1f 秒, 名称: %s, 分析模式: %s",
            duration, video_name, mode.display_name,
        )

        if mode == AnalysisMode.FASTEST:
            if progress_callback:
                progress_callback(10, "极速模式：仅使用字幕分析...")
            segments = await self._analyze_with_subtitle_only(
                video_name, subtitle_path, duration,
            )
            if progress_callback:
                progress_callback(100, "分析完成")
            logger.info(
                "极速分析完成: %s, 共提取 %d 个片段",
                video_name, len(segments),
            )
            return segments

        preprocessed_path = self._preprocessor.preprocess(video_path)

        if progress_callback:
            progress_callback(5, "预处理完成，开始画面分析...")

        frame_descriptions = await self._analyze_frames(
            preprocessed_path, video_name, duration, mode, progress_callback,
        )

        if progress_callback:
            progress_callback(85, "画面分析完成，开始综合分析...")

        segments = await self._analyze_with_descriptions(
            video_name, frame_descriptions, subtitle_path, duration,
        )

        if progress_callback:
            progress_callback(100, "分析完成")

        logger.info(
            "视频分析完成: %s, 共提取 %d 个片段",
            video_name, len(segments),
        )
        return segments

    async def _analyze_with_subtitle_only(
        self,
        video_name: str,
        subtitle_path: str | None,
        duration: float,
    ) -> list[VideoSegment]:
        subtitle_text = ""
        if subtitle_path:
            subtitle_text = self._read_subtitle(subtitle_path)

        prompt_parts = [f"## 视频信息\n视频名称：{video_name}\n"]
        prompt_parts.append(f"## 视频总时长\n{self._format_time(duration)}\n")

        if subtitle_text:
            prompt_parts.append("## 字幕内容\n")
            prompt_parts.append(subtitle_text)
            prompt_parts.append("")

        prompt_parts.append("## 分析指令")
        prompt_parts.append(
            "请根据以上字幕内容，按照要求输出分析结果。"
            "注意：输出的时间戳必须基于视频的绝对时间。"
        )
        prompt_parts.append("")
        prompt_parts.append(SUBTITLE_ONLY_PROMPT)

        prompt = "\n".join(prompt_parts)

        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._ai_service.generate_text(
                    prompt=prompt,
                    system_prompt="你是一个专业的视频分析专家，严格按照JSON格式输出分析结果。",
                    temperature=0.3,
                )
                segments = self._parse_response(response)
                if not segments:
                    last_error = "解析结果为空"
                    logger.warning(
                        "极速分析重试 %d/%d: %s, 结果为空",
                        attempt, MAX_RETRIES, video_name,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)
                    continue

                merged = self._merge_segments(segments)
                logger.info(
                    "极速分析完成: %s, 提取 %d 个片段",
                    video_name, len(merged),
                )
                return merged
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "极速分析重试 %d/%d: %s, 错误: %s",
                    attempt, MAX_RETRIES, video_name, last_error,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error("极速分析失败，已达最大重试次数: %s, 错误: %s", video_name, last_error)
        raise RuntimeError(f"极速分析失败: {last_error}")

    async def _analyze_frames(
        self,
        video_path: str,
        video_name: str,
        duration: float,
        mode: AnalysisMode,
        progress_callback: Callable | None = None,
    ) -> list[dict]:
        frames_per_slice = mode.frames_per_slice
        if frames_per_slice <= 0:
            return []

        cache_dir = self._get_frame_cache_dir(video_name, mode)
        cache_file = cache_dir / "frame_descriptions.json"

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                logger.info(
                    "使用缓存的画面描述: %s (%s, %d 条)",
                    video_name, mode.display_name, len(cached),
                )
                return cached
            except Exception as e:
                logger.warning("读取缓存失败，重新分析: %s", str(e))

        num_slices = max(
            1,
            int(duration / SLICE_DURATION)
            + (1 if duration % SLICE_DURATION > 0 else 0),
        )
        logger.info(
            "开始画面分析: %s, 模式: %s, 共 %d 个切片, 每切片 %d 帧",
            video_name, mode.display_name, num_slices, frames_per_slice,
        )

        all_descriptions: list[dict] = []
        for slice_idx in range(num_slices):
            slice_start = slice_idx * SLICE_DURATION
            slice_end = min(slice_start + SLICE_DURATION, duration)

            if progress_callback:
                pct = 5 + int((slice_idx / num_slices) * 75)
                msg = (
                    f"画面分析中: 第 {slice_idx + 1}/{num_slices} 段 "
                    f"({self._format_time(slice_start)}-{self._format_time(slice_end)})"
                )
                progress_callback(pct, msg)

            frame_paths = self._extract_slice_frames(
                video_path, slice_start, slice_end, frames_per_slice,
            )
            if not frame_paths:
                all_descriptions.append({
                    "start_time": self._format_time(slice_start),
                    "end_time": self._format_time(slice_end),
                    "description": "无法提取画面帧",
                })
                continue

            description = await self._describe_frames_with_retry(
                frame_paths, slice_idx, num_slices, mode,
            )
            self._cleanup_frames(frame_paths)

            all_descriptions.append({
                "start_time": self._format_time(slice_start),
                "end_time": self._format_time(slice_end),
                "description": description,
            })
            logger.info(
                "切片 %d/%d 分析完成: [%s-%s] %s",
                slice_idx + 1, num_slices,
                all_descriptions[-1]["start_time"],
                all_descriptions[-1]["end_time"],
                description[:40],
            )

        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(all_descriptions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("画面描述已缓存: %s", cache_file)

        return all_descriptions

    def _get_frame_cache_dir(self, video_name: str, mode: AnalysisMode) -> Path:
        base_cache = (
            Path(__file__).resolve().parent.parent.parent
            / "cache" / "frame_descriptions"
        )
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", video_name)
        if safe_name.endswith(".mp4"):
            safe_name = safe_name[:-4]
        return base_cache / f"{safe_name}_{mode.value}"

    def _extract_slice_frames(
        self,
        video_path: str,
        slice_start: float,
        slice_end: float,
        frames_per_slice: int,
    ) -> list[str]:
        frame_dir = Path(tempfile.mkdtemp(prefix="clip_synth_slice_"))
        duration = slice_end - slice_start
        if duration <= 0:
            duration = 1

        frame_paths = []
        for i in range(frames_per_slice):
            if frames_per_slice == 3:
                if i == 0:
                    offset = 0
                elif i == 1:
                    offset = duration / 2
                else:
                    offset = duration - 0.1
            else:
                offset = (duration / frames_per_slice) * i

            target_time = slice_start + offset
            output_path = str(frame_dir / f"frame_{i+1:04d}.jpg")

            cmd = [
                "ffmpeg",
                "-ss", str(target_time),
                "-i", video_path,
                "-vframes", "1",
                "-vf", "scale=640:-1",
                "-q:v", "5",
                "-y",
                output_path,
            ]

            try:
                kwargs = {}
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(
                    cmd, capture_output=True, text=False, timeout=30, **kwargs
                )
                if result.returncode == 0:
                    frame_paths.append(output_path)
            except Exception as e:
                logger.warning("提取帧 %d 失败: %s", i, str(e))

        return frame_paths

    async def _describe_frames_with_retry(
        self,
        frame_paths: list[str],
        slice_idx: int,
        total_slices: int,
        mode: AnalysisMode,
    ) -> str:
        frame_desc = f"（共{len(frame_paths)}帧"
        if mode == AnalysisMode.QUICK:
            frame_desc += "，开头/中间/结尾各1帧"
        elif mode == AnalysisMode.PRECISE:
            frame_desc += "，约1.7秒/帧"
        elif mode == AnalysisMode.DEEP:
            frame_desc += "，每秒1帧"
        frame_desc += "）"

        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._ai_service.generate_text_with_images(
                    prompt=(
                        f"这是视频第 {slice_idx + 1}/{total_slices} 段的画面帧"
                        f"{frame_desc}\n\n"
                        f"{FRAME_DESCRIPTION_PROMPT}"
                    ),
                    image_paths=frame_paths,
                    system_prompt=None,
                    temperature=0.3,
                    max_tokens=512,
                )
                description = self._parse_frame_description(response)
                if description:
                    return description

                last_error = "解析返回为空"
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "画面分析重试 %d/%d: 切片 %d, 错误: %s",
                    attempt, MAX_RETRIES, slice_idx + 1, last_error,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error("画面分析失败，已达最大重试次数: %s", last_error)
        return f"画面分析失败: {last_error}"

    def _parse_frame_description(self, response: str) -> str:
        json_str = self._extract_json(response)
        if not json_str:
            cleaned = response.strip().strip('"').strip("'")
            if cleaned:
                return cleaned[:50]
            return ""

        try:
            data = json.loads(json_str)
            desc = data.get("description", "")
            return desc[:50] if desc else ""
        except json.JSONDecodeError:
            cleaned = response.strip().strip('"').strip("'")
            return cleaned[:50] if cleaned else ""

    async def _analyze_with_descriptions(
        self,
        video_name: str,
        frame_descriptions: list[dict],
        subtitle_path: str | None,
        duration: float,
    ) -> list[VideoSegment]:
        timeline_parts = []
        for fd in frame_descriptions:
            timeline_parts.append(
                f"[{fd['start_time']}-{fd['end_time']}] {fd['description']}"
            )
        timeline_text = "\n".join(timeline_parts)

        subtitle_text = ""
        if subtitle_path:
            subtitle_text = self._read_subtitle(subtitle_path)

        prompt_parts = [f"## 视频信息\n视频名称：{video_name}\n"]
        prompt_parts.append(f"## 视频总时长\n{self._format_time(duration)}\n")
        prompt_parts.append("## 画面时间线（每10秒一段）\n")
        prompt_parts.append(timeline_text)
        prompt_parts.append("")

        if subtitle_text:
            prompt_parts.append("## 字幕内容\n")
            prompt_parts.append(subtitle_text)
            prompt_parts.append("")

        prompt_parts.append("## 分析指令")
        prompt_parts.append(
            "请结合以上画面描述和字幕内容，按照要求输出分析结果。"
            "注意：输出的时间戳必须基于视频的绝对时间。"
        )
        prompt_parts.append("")
        prompt_parts.append(FINAL_ANALYSIS_PROMPT)

        prompt = "\n".join(prompt_parts)

        last_error = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._ai_service.generate_text(
                    prompt=prompt,
                    system_prompt="你是一个专业的视频分析专家，严格按照JSON格式输出分析结果。",
                    temperature=0.3,
                )
                segments = self._parse_response(response)
                if not segments:
                    last_error = "解析结果为空"
                    logger.warning(
                        "综合分析重试 %d/%d: %s, 结果为空",
                        attempt, MAX_RETRIES, video_name,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_DELAY)
                    continue

                merged = self._merge_segments(segments)
                logger.info(
                    "综合分析完成: %s, 提取 %d 个片段",
                    video_name, len(merged),
                )
                return merged
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "综合分析重试 %d/%d: %s, 错误: %s",
                    attempt, MAX_RETRIES, video_name, last_error,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

        logger.error("综合分析失败，已达最大重试次数: %s, 错误: %s", video_name, last_error)
        raise RuntimeError(f"综合分析失败: {last_error}")

    def _cleanup_frames(self, frame_paths: list[str]) -> None:
        parent_dirs = set()
        for fp in frame_paths:
            try:
                Path(fp).unlink(missing_ok=True)
                parent_dirs.add(Path(fp).parent)
            except Exception:
                pass
        for d in parent_dirs:
            try:
                remaining = list(d.iterdir())
                if not remaining:
                    d.rmdir()
            except Exception:
                pass

    def _get_video_duration(self, video_path: str) -> float:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path,
        ]
        try:
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                cmd, capture_output=True, text=False, timeout=30, **kwargs
            )
            if result.returncode == 0:
                stdout = result.stdout.decode("utf-8", errors="replace").strip()
                if stdout:
                    return float(stdout)
        except Exception as e:
            logger.warning("获取视频时长失败: %s, 使用默认值 600 秒", str(e))
        return 600.0

    def _read_subtitle(self, subtitle_path: str) -> str:
        entries = self._parse_srt_entries(subtitle_path)
        lines = []
        for start, end, text in entries:
            lines.append(
                f"[{self._format_time(start)}-{self._format_time(end)}] {text}"
            )
        return "\n".join(lines)

    def _parse_srt_entries(self, subtitle_path: str) -> list[tuple[float, float, str]]:
        try:
            content = Path(subtitle_path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("读取字幕文件失败: %s, 错误: %s", subtitle_path, str(e))
            return []

        entries: list[tuple[float, float, str]] = []
        blocks = re.split(r"\n\s*\n", content.strip())

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue

            time_line = None
            text_lines = []
            for line in lines:
                if "-->" in line:
                    time_line = line
                elif not line.strip().isdigit():
                    text_lines.append(line.strip())

            if not time_line or not text_lines:
                continue

            time_match = re.match(
                r"(\d{2}):(\d{2}):(\d{2})[.,]\d{3}\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,]\d{3}",
                time_line,
            )
            if not time_match:
                continue

            start_sec = (
                int(time_match.group(1)) * 3600
                + int(time_match.group(2)) * 60
                + int(time_match.group(3))
            )
            end_sec = (
                int(time_match.group(4)) * 3600
                + int(time_match.group(5)) * 60
                + int(time_match.group(6))
            )

            text = " ".join(text_lines)
            entries.append((float(start_sec), float(end_sec), text))

        return entries

    def _parse_response(self, response: str) -> list[VideoSegment]:
        json_str = self._extract_json(response)
        if not json_str:
            logger.warning("无法从响应中提取JSON: %s", response[:200])
            return []

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("JSON解析失败: %s, 内容: %s", str(e), json_str[:200])
            return []

        raw_segments = data.get("segments", [])
        if not raw_segments:
            logger.warning("JSON中未找到segments字段")
            return []

        time_errors = _validate_segment_time_format(raw_segments)
        if time_errors:
            error_detail = "；".join(time_errors)
            logger.error(f"AI返回的时间格式错误: {error_detail}")
            raise ValueError(
                f"AI返回的时间格式不符合要求（应为HH:MM:SS,mmm格式，如00:01:25,500），"
                f"请点击「重新分析」按钮重新生成。\n详细错误：{error_detail}"
            )

        segments = []
        for raw in raw_segments:
            seg_type = raw.get("type", "")
            start_time = raw.get("start_time", "00:00:00")
            end_time = raw.get("end_time", "00:00:00")
            description = raw.get("description", "")

            if seg_type not in ("gold_3s", "highlight", "plot", "ending"):
                continue

            segments.append(VideoSegment(
                id=self._generate_id(),
                type=seg_type,
                start_time=start_time,
                end_time=end_time,
                description=description,
                selected=False,
            ))

        return segments

    def _extract_json(self, text: str) -> str | None:
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            return json_match.group(1).strip()

        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            return brace_match.group(0)

        return None

    def _generate_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _format_time(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        msecs = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{msecs:03d}"

    def _merge_segments(self, segments: list[VideoSegment]) -> list[VideoSegment]:
        grouped: dict[str, list[VideoSegment]] = {
            "gold_3s": [],
            "highlight": [],
            "plot": [],
            "ending": [],
        }
        for seg in segments:
            if seg.type in grouped:
                grouped[seg.type].append(seg)

        merged: list[VideoSegment] = []
        for seg_type in ("gold_3s", "highlight", "plot", "ending"):
            segs = grouped[seg_type]
            if not segs:
                continue

            segs.sort(key=lambda s: self._time_to_seconds(s.start_time))
            deduped = [segs[0]]
            for seg in segs[1:]:
                prev = deduped[-1]
                prev_end = self._time_to_seconds(prev.end_time)
                if self._time_to_seconds(seg.start_time) <= prev_end + 2:
                    if self._time_to_seconds(seg.end_time) > self._time_to_seconds(prev.end_time):
                        prev.end_time = seg.end_time
                    if seg.description not in prev.description:
                        prev.description += f" / {seg.description}"
                else:
                    deduped.append(seg)
            merged.extend(deduped)

        return merged

    def _time_to_seconds(self, time_str: str) -> float:
        parts = time_str.split(":")
        if len(parts) == 3:
            sec_part = parts[2].replace(",", ".")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(sec_part)
        return 0.0
