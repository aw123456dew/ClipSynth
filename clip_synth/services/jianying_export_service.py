import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger(__name__)


def _normalize_time(t: str) -> str:
    return t.replace(",", ".")


def _build_blur_filter(
    mask_x: float,
    mask_y: float,
    mask_width: float,
    mask_height: float,
    blur_radius: int,
    video_width: int,
    video_height: int,
    feather: int = 0,
) -> str:
    """构建模糊遮罩滤镜
    使用遮罩混合技术实现带羽化的模糊效果
    """
    x = int(video_width * mask_x)
    y = int(video_height * mask_y)
    w = int(video_width * mask_width)
    h = int(video_height * mask_height)
    
    # 使用复合滤镜实现带羽化的模糊遮罩
    # 使用alphamerge将遮罩作为alpha通道应用
    filter_str = (
        f"split=3[base][blur_stream][mask_stream];"
        f"[blur_stream]boxblur=luma_radius={blur_radius}:luma_power=1[blurred];"
        f"[mask_stream]drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=fill[mask];"
    )
    
    # 添加羽化效果并应用遮罩
    if feather > 0:
        # 使用gblur模糊遮罩边缘实现羽化，然后作为alpha通道
        filter_str += (
            f"[mask]gblur=sigma={feather}[mask_blur];"
            f"[blurred][mask_blur]alphamerge[masked_blur];"
            f"[base][masked_blur]overlay=x=0:y=0"
        )
    else:
        filter_str += (
            f"[blurred][mask]alphamerge[masked_blur];"
            f"[base][masked_blur]overlay=x=0:y=0"
        )
    
    return filter_str


def _get_video_duration(path: str) -> float:
    """获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            **kwargs,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"获取视频时长失败: {path}, 错误: {e}")
    return 0.0


def _get_video_resolution(path: str) -> tuple:
    """获取视频分辨率"""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        path,
    ]
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=flags)
    if result.returncode == 0:
        parts = result.stdout.strip().split(",")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    return 1920, 1080


def _time_to_seconds(t: str) -> float:
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    try:
        return float(t)
    except ValueError:
        return 0.0


def _get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                return float(stdout)
    except Exception as e:
        logger.warning("获取时长失败: %s", str(e))
    return 0.0


def _run_cmd(cmd: List[str], action: str = "处理") -> None:
    logger.info(f"[ffmpeg] {action}: {' '.join(cmd)}")
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=flags,
    )
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
        raise RuntimeError(f"ffmpeg {action}失败: {error_msg}")


def _generate_srt(scripts_data: List[dict], audio_files: List[dict], output_dir: str) -> Optional[str]:
    """从音频文件的时间戳生成合并的SRT字幕文件"""
    from clip_synth.services.subtitle_service import SubtitleService

    srt_sections = []
    time_offset = 0.0
    audio_idx = 0

    for script in scripts_data:
        content_type = script.get("content_type", "narration")

        # 计算片段原始时长（用 end_time - start_time）
        raw_start = _normalize_time(script.get("start_time", "00:00:00"))
        raw_end = _normalize_time(script.get("end_time", "00:00:00"))
        seg_duration = max(0.0, _time_to_seconds(raw_end) - _time_to_seconds(raw_start))

        if content_type == "original_sound":
            time_offset += seg_duration
            continue

        if audio_idx >= len(audio_files):
            time_offset += seg_duration
            continue

        audio_info = audio_files[audio_idx]
        audio_idx += 1

        audio_duration = audio_info.get("duration", 0) or _get_media_duration(audio_info.get("path", "") or audio_info.get("audio_path", ""))
        timestamps = audio_info.get("timestamps", [])

        if not timestamps:
            time_offset += audio_duration
            continue

        reference = script.get("narration_script", "") or script.get("text", "") or ""
        sentences = SubtitleService.merge_words_to_sentences(timestamps, reference_text=reference)
        if sentences:
            srt_content = SubtitleService.generate_srt(sentences, clip_start_time=time_offset)
            srt_sections.append(srt_content.strip())

        time_offset += audio_duration

    if not srt_sections:
        return None

    merged_content = "\n\n".join(srt_sections)

    # 重新编排序号
    lines = merged_content.split("\n")
    renumbered = []
    idx = 1
    for line in lines:
        if line.strip().isdigit():
            renumbered.append(str(idx))
            idx += 1
        else:
            renumbered.append(line)

    srt_path = os.path.join(output_dir, "subtitles.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(renumbered))

    logger.info(f"字幕文件已生成: {srt_path}")
    return srt_path


class JianYingExportService:
    """剪映草稿导出服务（参考NarratoAI实现）"""

    def __init__(self, settings_service: SettingsService):
        self._settings_service = settings_service
        self._process = None
        self._canceled = False

    def export_to_jianying(
        self,
        project: NarrateProjectState,
        output_dir: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Dict[str, str]:
        """
        导出项目到剪映草稿

        流程：
        1. 用ffmpeg按时间戳裁剪视频片段
           - 解说片段(narration)：移除原声(-an)，时长按配音时长
           - 原声片段(original_sound)：保留原声，时长按片段时长
        2. 将裁剪好的片段、配音、字幕添加到剪映草稿轨道
        """
        try:
            import pyJianYingDraft
            from pyJianYingDraft import DraftFolder, VideoSegment, AudioSegment, trange, TrackType
        except ImportError as e:
            raise ImportError(f"pyJianYingDraft库导入失败: {e}\n请确保已正确安装该库")

        settings = self._settings_service.load()
        jianying_draft_path = settings.draft_output_dir
        if not jianying_draft_path:
            raise ValueError("剪映草稿路径未配置，请在系统配置中设置")

        if progress_callback:
            progress_callback("正在准备导出到剪映草稿...", 10)

        scripts = project.narration_scripts
        audio_files = project.audio_files
        if not scripts:
            raise RuntimeError("没有解说文案，无法导出")

        # 构建片段到视频的映射
        seg_video_map = {}
        for video_state in project.videos:
            for seg_list in video_state.segments.values():
                for seg in seg_list:
                    seg_video_map[seg.id] = video_state.video_path

        # 准备裁剪目录
        clip_dir = Path(output_dir) / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)

        # 获取移除字幕（模糊遮罩）设置
        enable_remove_subtitle = getattr(project, "enable_remove_subtitle", False)
        mask_settings = {
            "mask_x": getattr(project, "mask_x", 0.2),
            "mask_y": getattr(project, "mask_y", 0.85),
            "mask_width": getattr(project, "mask_width", 0.6),
            "mask_height": getattr(project, "mask_height", 0.1),
            "blur_radius": getattr(project, "mask_blur_radius", 20),
        }
        logger.info(f"移除字幕设置: enable={enable_remove_subtitle}, mask=({mask_settings['mask_x']}, {mask_settings['mask_y']}, {mask_settings['mask_width']}, {mask_settings['mask_height']}), blur={mask_settings['blur_radius']}")

        # 获取视频分辨率（用于计算模糊区域）
        video_width, video_height = 1920, 1080
        if project.videos:
            first_video = project.videos[0].video_path
            if first_video and os.path.exists(first_video):
                video_width, video_height = _get_video_resolution(first_video)

        # 构建模糊滤镜
        blur_filter = None
        if enable_remove_subtitle:
            blur_filter = _build_blur_filter(
                mask_settings["mask_x"],
                mask_settings["mask_y"],
                mask_settings["mask_width"],
                mask_settings["mask_height"],
                mask_settings["blur_radius"],
                video_width,
                video_height,
                mask_settings.get("feather", 0),
            )
            logger.info(f"模糊遮罩滤镜: {blur_filter}")

        # ========== 第1步：ffmpeg裁剪所有片段 ==========
        if progress_callback:
            progress_callback("正在裁剪视频片段...", 20)

        total = len(scripts)
        clip_videos = []  # 存储 (视频路径, 内容类型, 配音路径, 时长) 元组
        audio_idx = 0

        for i, script_item in enumerate(scripts):
            if self._canceled:
                raise RuntimeError("导出已取消")

            if progress_callback:
                progress_callback(f"裁剪第 {i+1}/{total} 个片段...", 20 + (i / total) * 40)

            content_type = script_item.get("content_type", "narration")
            segment_id = script_item.get("segment_id", "")
            video_path = seg_video_map.get(segment_id, "")

            if not video_path:
                for vs in project.videos:
                    video_path = vs.video_path
                    break
            if not video_path:
                logger.warning(f"无法找到片段 {i+1} 对应的视频，跳过")
                continue

            raw_start = _normalize_time(script_item.get("start_time", "00:00:00"))
            raw_end = _normalize_time(script_item.get("end_time", "00:00:00"))
            start_sec = _time_to_seconds(raw_start)
            end_sec = _time_to_seconds(raw_end)

            if end_sec <= start_sec:
                logger.warning(f"片段 {i+1} 时长<=0, 跳过")
                continue

            segment_duration = end_sec - start_sec
            if segment_duration < 0.5:
                segment_duration = 0.5

            clip_path = str(clip_dir / f"clip_{i:04d}.mp4")

            if content_type == "original_sound":
                # 原声片段：保留原声，按原始时间戳裁剪
                cut_cmd = [
                    "ffmpeg", "-y",
                    "-ss", raw_start,
                    "-i", video_path,
                    "-t", f"{segment_duration}",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-avoid_negative_ts", "make_zero",
                    clip_path,
                ]
                # 添加模糊滤镜
                if blur_filter:
                    idx = cut_cmd.index("-c:v")
                    cut_cmd.insert(idx, blur_filter)
                    cut_cmd.insert(idx, "-filter:v")
                _run_cmd(cut_cmd, f"裁剪原声片段 {i+1}")
                clip_videos.append((clip_path, content_type, None, segment_duration))
            else:
                # 解说片段：获取配音文件
                audio_path = None
                audio_duration = 0.0
                timestamps = []
                if audio_idx < len(audio_files):
                    audio_info = audio_files[audio_idx]
                    candidate = audio_info.get("path", "") or audio_info.get("audio_path", "")
                    if os.path.exists(candidate):
                        audio_path = candidate
                        audio_duration = _get_media_duration(audio_path)
                        timestamps = audio_info.get("timestamps", [])
                audio_idx += 1

                if not audio_path:
                    logger.warning(f"解说片段 {i+1} 缺少配音文件，跳过")
                    continue

                # 按配音时长处理视频：音频比视频短时加速视频，音频比视频长时延长视频
                if audio_duration < segment_duration:
                    # 音频比视频短：加速视频到音频时长
                    speed = segment_duration / audio_duration
                    filters = [f"setpts={1.0/speed}*PTS"]
                    if blur_filter:
                        filters.append(blur_filter)
                    filter_str = ",".join(filters)
                    cut_cmd = [
                        "ffmpeg", "-y",
                        "-ss", raw_start,
                        "-i", video_path,
                        "-t", f"{segment_duration}",
                        "-filter:v", filter_str,
                        "-t", f"{audio_duration}",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "23",
                        "-an",
                        "-avoid_negative_ts", "make_zero",
                        clip_path,
                    ]
                    _run_cmd(cut_cmd, f"裁剪解说片段 {i+1}（加速 x{speed:.2f}）")
                else:
                    # 音频比视频长：延长视频到音频时长（放慢速度）
                    speed = segment_duration / audio_duration
                    filters = [f"setpts={1.0/speed}*PTS"]
                    if blur_filter:
                        filters.append(blur_filter)
                    filter_str = ",".join(filters)
                    cut_cmd = [
                        "ffmpeg", "-y",
                        "-ss", raw_start,
                        "-i", video_path,
                        "-t", f"{segment_duration}",
                        "-filter:v", filter_str,
                        "-t", f"{audio_duration}",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "23",
                        "-an",
                        "-avoid_negative_ts", "make_zero",
                        clip_path,
                    ]
                    _run_cmd(cut_cmd, f"裁剪解说片段 {i+1}（延长到音频时长）")
                
                clip_videos.append((clip_path, content_type, audio_path, audio_duration))

        if not clip_videos:
            raise RuntimeError("没有有效的片段可导出")

        # ========== 第2步：生成字幕文件 ==========
        subtitle_path = None
        if getattr(project, "enable_subtitle", False) and project.narration_scripts:
            if progress_callback:
                progress_callback("正在生成字幕...", 65)
            subtitle_path = _generate_srt(scripts, audio_files, output_dir)

        # ========== 第3步：添加到剪映草稿 ==========
        if progress_callback:
            progress_callback("正在创建剪映草稿...", 70)

        draft_folder = DraftFolder(jianying_draft_path)
        draft_name = f"{project.name}_{int(time.time())}"
        script = draft_folder.create_draft(draft_name, 1920, 1080)

        # 创建轨道（add_track 返回 ScriptFile 自身，轨道名为字符串）
        script.add_track(TrackType.video, "视频轨道")
        script.add_track(TrackType.audio, "音频轨道")
        if subtitle_path:
            script.add_track(TrackType.text, "字幕轨道")

        # 添加片段到轨道
        current_time = 0.0

        for i, (clip_path, content_type, audio_path, duration) in enumerate(clip_videos):
            if self._canceled:
                raise RuntimeError("导出已取消")

            if progress_callback:
                progress_callback(f"添加到草稿第 {i+1}/{len(clip_videos)} 个片段...", 70 + (i / len(clip_videos)) * 25)

            if not os.path.exists(clip_path):
                logger.warning(f"裁剪后的视频文件不存在: {clip_path}，跳过")
                current_time += duration
                continue

            # 获取实际视频文件时长
            actual_duration = _get_video_duration(clip_path)
            if actual_duration <= 0:
                logger.warning(f"无法获取视频时长: {clip_path}，跳过")
                current_time += duration
                continue

            # 使用实际时长，避免超出范围
            duration = actual_duration

            # 添加到视频轨道
            video_segment = VideoSegment(
                clip_path,
                trange(f"{current_time}s", f"{duration}s")
            )
            script.add_segment(video_segment, "视频轨道")

            # 解说片段：添加配音音频到音频轨道
            if content_type != "original_sound" and audio_path and os.path.exists(audio_path):
                # 获取音频文件的实际时长
                audio_duration = _get_video_duration(audio_path)
                if audio_duration <= 0:
                    audio_duration = duration
                audio_segment = AudioSegment(
                    audio_path,
                    trange(f"{current_time}s", f"{min(duration, audio_duration)}s")
                )
                script.add_segment(audio_segment, "音频轨道")

            current_time += duration

        # 导入字幕
        if subtitle_path and os.path.exists(subtitle_path):
            script.import_srt(subtitle_path, track_name="字幕轨道", time_offset="0s")

        # 保存草稿
        if progress_callback:
            progress_callback("正在保存草稿...", 97)

        script.save()

        draft_path = os.path.join(jianying_draft_path, draft_name)

        if progress_callback:
            progress_callback("导出完成", 100)

        logger.info(f"剪映草稿导出完成: {draft_path}")
        return {"draft_path": draft_path, "draft_name": draft_name}

    def cancel(self):
        self._canceled = True