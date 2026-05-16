import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.settings_service import SettingsService
from clip_synth.utils.gpu_accel import apply_gpu_encoder_to_cmd

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
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=flags)
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.warning("获取视频分辨率失败: %s", str(e))
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
    apply_gpu_encoder_to_cmd(cmd)
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


def _run_cmd_soft(cmd: List[str], action: str = "处理") -> None:
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


def _has_video_stream(filepath: str) -> bool:
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=10, creationflags=flags,
        )
        return result.returncode == 0 and "video" in result.stdout.lower()
    except Exception:
        return False


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
        
        # 检查是否有用户上传的字幕文件（自定义配音时）
        uploaded_subtitle = audio_info.get("subtitle_path", "")
        if uploaded_subtitle and os.path.exists(uploaded_subtitle):
            # 使用用户上传的字幕文件，不需要验证解说文案
            logger.info("_generate_srt | 片段%d: 使用用户上传的字幕文件: %s", audio_idx - 1, uploaded_subtitle)
            with open(uploaded_subtitle, "r", encoding="utf-8") as f:
                srt_content = f.read()
            
            # 解析用户上传的SRT并调整时间偏移
            sentences = SubtitleService.parse_srt(srt_content)
            if sentences:
                adjusted_srt = SubtitleService.generate_srt(sentences, clip_start_time=time_offset)
                srt_sections.append(adjusted_srt.strip())
                logger.info("_generate_srt | 片段%d: 从上传的SRT解析到%d条字幕", audio_idx - 1, len(sentences))
            time_offset += audio_duration
            continue
        
        # 使用TTS生成的时间戳生成字幕（原有逻辑）
        timestamps = audio_info.get("timestamps", [])

        if not timestamps:
            logger.info("_generate_srt | 片段%d: 无时间戳, time_offset += %.2f", audio_idx - 1, audio_duration)
            time_offset += audio_duration
            continue

        reference = script.get("narration_script", "") or script.get("text", "") or ""
        logger.info("_generate_srt | 片段%d: timestamps=%d, reference=%s",
                     audio_idx - 1, len(timestamps), reference[:80])
        logger.info("_generate_srt | 片段%d: TTS前30词=%s",
                     audio_idx - 1, [t.get("word", "") for t in timestamps[:30]])
        sentences = SubtitleService.merge_words_to_sentences(timestamps, reference_text=reference)
        if sentences:
            srt_content = SubtitleService.generate_srt(sentences, clip_start_time=time_offset)
            logger.info("_generate_srt | 片段%d: 生成%d条字幕, SRT前500字符=\n%s",
                         audio_idx - 1, len(sentences), srt_content[:500])
            srt_sections.append(srt_content.strip())
        else:
            logger.info("_generate_srt | 片段%d: merge_words_to_sentences 返回空列表!", audio_idx - 1)

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
    logger.info("_generate_srt | 字幕生成结果: %s, srt_sections=%d", srt_path, len(srt_sections))
    return srt_path


def _generate_srt_parts(scripts: list, audio_files: list, output_dir: str) -> Optional[str]:
    """从 parts 结构生成合并的SRT字幕文件，只对 narration parts 生成字幕"""
    from clip_synth.services.subtitle_service import SubtitleService

    srt_sections = []
    time_offset = 0.0

    for seg_idx, seg in enumerate(scripts):
        parts = seg.get("parts", [])
        for part_idx, part in enumerate(parts):
            ptype = part.get("type", "")

            if ptype == "original_sound":
                raw_start = _normalize_time(part.get("start_time", "00:00:00"))
                raw_end = _normalize_time(part.get("end_time", "00:00:00"))
                part_dur = max(0.0, _time_to_seconds(raw_end) - _time_to_seconds(raw_start))
                time_offset += part_dur
                continue

            if ptype != "narration":
                continue

            audio = _find_audio_in_list(audio_files, seg_idx, part_idx)
            if not audio:
                logger.info("_generate_srt_parts | seg=%d part=%d 无音频，跳过", seg_idx, part_idx)
                continue

            audio_path = audio.get("path", "") or audio.get("audio_path", "")
            audio_dur = audio.get("duration", 0) or _get_media_duration(audio_path) if audio_path else 0
            timestamps = audio.get("timestamps", [])

            if not timestamps:
                time_offset += audio_dur
                continue

            reference = part.get("script", "") or audio.get("text", "") or ""
            logger.info("_generate_srt_parts | seg=%d part=%d: timestamps=%d", seg_idx, part_idx, len(timestamps))
            sentences = SubtitleService.merge_words_to_sentences(timestamps, reference_text=reference)
            if sentences:
                srt_content = SubtitleService.generate_srt(sentences, clip_start_time=time_offset)
                srt_sections.append(srt_content.strip())

            time_offset += audio_dur

    if not srt_sections:
        return None

    merged_content = "\n\n".join(srt_sections)
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

    logger.info("_generate_srt_parts | 字幕文件已生成: %s", srt_path)
    return srt_path


def _find_audio_in_list(audio_files: list, seg_idx: int, part_idx: int):
    for af in audio_files:
        if af.get("segment_index") == seg_idx and af.get("part_index") == part_idx:
            return af
    return None


def _ensure_merged_video(project: NarrateProjectState) -> str | None:
    """确保合并视频存在，如果不存在则用原始视频重新合并"""
    merged_path = project.extra_data.get("merged_video_path", "")
    if merged_path and os.path.exists(merged_path):
        logger.info("合并视频已存在: %s", merged_path)
        return merged_path

    if not merged_path:
        projects_cache_dir = Path(__file__).resolve().parent.parent.parent / "cache" / "projects"
        merged_path = str(projects_cache_dir / project.id / "merged.mp4")
        logger.info("推断合并视频路径: %s", merged_path)
        if os.path.exists(merged_path):
            project.extra_data["merged_video_path"] = merged_path
            return merged_path

    video_paths = [vs.video_path for vs in project.videos if vs.video_path and os.path.exists(vs.video_path)]
    if not video_paths:
        logger.warning("没有可用的原始视频用于合并")
        return None

    logger.info("合并视频 %s 不存在，重新合并 %d 个视频", merged_path, len(video_paths))
    out_dir = os.path.dirname(merged_path)
    os.makedirs(out_dir, exist_ok=True)

    if len(video_paths) == 1:
        import shutil
        shutil.copy2(video_paths[0], merged_path)
        logger.info("单个视频，直接复制到: %s", merged_path)
    else:
        concat_file = os.path.join(out_dir, "concat.txt")
        try:
            with open(concat_file, "w", encoding="utf-8") as f:
                for p in video_paths:
                    f.write(f"file '{Path(p).resolve()}'\n")
            cmd = [
                "ffmpeg", "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy", "-y", merged_path,
            ]
            _run_cmd(cmd, "合并视频")
        finally:
            if os.path.exists(concat_file):
                os.unlink(concat_file)

    if os.path.exists(merged_path):
        project.extra_data["merged_video_path"] = merged_path
        logger.info("合并视频已生成: %s", merged_path)
        return merged_path
    return None


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
        scripts = project.narration_scripts
        s0 = scripts[0] if scripts else None
        logger.info("export_to_jianying 入口: scripts=%d, s0_keys=%s, has_parts=%s",
                     len(scripts), list(s0.keys()) if isinstance(s0, dict) else type(s0),
                     "parts" in s0 if isinstance(s0, dict) else False)
        if scripts and isinstance(scripts[0], dict) and "parts" in scripts[0]:
            logger.info("  -> 走 _export_to_jianying_parts")
            return self._export_to_jianying_parts(project, output_dir, progress_callback)
        logger.info("  -> 走 _export_to_jianying_legacy")
        return self._export_to_jianying_legacy(project, output_dir, progress_callback)

    def _export_to_jianying_parts(
        self,
        project: NarrateProjectState,
        output_dir: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Dict[str, str]:
        """
        导出项目到剪映草稿（V2 parts 结构）
        每个片段按 parts 顺序：narration(视频+TTS音频) + original_sound(视频含原声) + narration(...)
        """
        try:
            import pyJianYingDraft
            from pyJianYingDraft import DraftFolder, VideoSegment, AudioSegment, trange, TrackType
        except ImportError as e:
            raise ImportError(f"pyJianYingDraft库导入失败: {e}")

        settings = self._settings_service.load()
        jianying_draft_path = settings.draft_output_dir
        if not jianying_draft_path:
            raise ValueError("剪映草稿路径未配置")

        scripts = project.narration_scripts
        audio_files = project.audio_files
        if not scripts:
            raise RuntimeError("没有解说文案")

        seg_video_map = {}
        for video_state in project.videos:
            for seg_list in video_state.segments.values():
                for seg in seg_list:
                    seg_video_map[seg.id] = video_state.video_path

        clip_dir = Path(output_dir) / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)

        _merged_video_path = _ensure_merged_video(project)

        logger.info("_export_to_jianying_parts 开始: %d 个片段, %d 个音频文件", len(scripts), len(audio_files))
        for i, s in enumerate(scripts):
            types = [p.get("type", "?") for p in s.get("parts", [])]
            logger.info("片段 %d: parts=%d, types=%s", i + 1, len(s.get("parts", [])), types)

        total = len(scripts)
        clip_entries = []  # [(video_path, audio_path_or_None, duration)]

        for seg_idx, script_item in enumerate(scripts):
            if self._canceled:
                raise RuntimeError("导出已取消")

            if progress_callback:
                progress_callback(f"处理第 {seg_idx+1}/{total} 个片段...", 20 + (seg_idx / total) * 40)

            parts = script_item.get("parts", [])
            if not parts:
                continue

            seg_start = script_item.get("start_time", "00:00:00.000")
            seg_start_sec = _time_to_seconds(seg_start)
            seg_end = script_item.get("end_time", "00:00:00.000")

            video_path = seg_video_map.get(script_item.get("segment_id", ""))
            if not video_path:
                video_path = _merged_video_path
            if not video_path:
                for vs in project.videos:
                    video_path = vs.video_path
                    break

            seg_duration = _time_to_seconds(seg_end) - seg_start_sec
            seg_video = str(clip_dir / f"seg_{seg_idx:04d}.mp4")
            _run_cmd([
                "ffmpeg", "-y",
                "-ss", seg_start,
                "-i", video_path,
                "-t", str(seg_duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-avoid_negative_ts", "make_zero",
                seg_video,
            ], f"预裁剪 segment {seg_idx+1}")
            if not _has_video_stream(seg_video):
                logger.warning("GPU预裁剪 segment %d 无视频流，使用软件编码重试", seg_idx + 1)
                _run_cmd_soft([
                    "ffmpeg", "-y",
                    "-ss", seg_start,
                    "-i", video_path,
                    "-t", str(seg_duration),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-avoid_negative_ts", "make_zero",
                    seg_video,
                ], f"预裁剪 segment {seg_idx+1} (软件重试)")

            video_offset = 0.0
            _is_last_narration = parts and parts[-1].get("type") == "narration"

            for part_idx, part in enumerate(parts):
                ptype = part.get("type", "")
                _is_last = (part_idx == len(parts) - 1)
                logger.info("  片段 %d part[%d]: type=%s, video_offset=%.2f", seg_idx + 1, part_idx, ptype, video_offset)

                if ptype == "narration":
                    audio = self._find_audio_part(audio_files, seg_idx, part_idx)
                    if not audio or not os.path.exists(audio.get("path", "")):
                        logger.warning("  片段 %d part[%d] 缺少配音", seg_idx + 1, part_idx)
                        continue

                    audio_path = audio["path"]
                    audio_dur = _get_media_duration(audio_path)

                    if _is_last and _is_last_narration:
                        _cut_offset = max(0, seg_duration - audio_dur)
                        logger.info("    最后一个narration: 从末尾倒推 offset=%.2f", _cut_offset)
                    else:
                        _cut_offset = video_offset

                    clip_path = str(clip_dir / f"clip_{seg_idx:04d}_{part_idx:04d}.mp4")
                    cut_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(_cut_offset),
                        "-i", seg_video,
                        "-t", str(audio_dur),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                        "-an",
                        clip_path,
                    ]
                    _run_cmd(cut_cmd, f"part剪辑 {seg_idx+1}.{part_idx+1} narration")
                    clip_entries.append((clip_path, audio_path, audio_dur))
                    video_offset += audio_dur

                elif ptype == "original_sound":
                    raw_start = _normalize_time(part.get("start_time", seg_start))
                    raw_end = _normalize_time(part.get("end_time", seg_end))
                    part_dur = _time_to_seconds(raw_end) - _time_to_seconds(raw_start)
                    if part_dur <= 0:
                        continue

                    seg_offset = _time_to_seconds(raw_start) - seg_start_sec

                    clip_path = str(clip_dir / f"clip_{seg_idx:04d}_{part_idx:04d}.mp4")
                    cut_cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(seg_offset),
                        "-i", seg_video,
                        "-t", str(part_dur),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k",
                        clip_path,
                    ]
                    _run_cmd(cut_cmd, f"part剪辑 {seg_idx+1}.{part_idx+1} original_sound")
                    clip_entries.append((clip_path, None, part_dur))
                    video_offset = seg_offset + part_dur

        if not clip_entries:
            raise RuntimeError("没有有效片段")

        # --- 字幕 ---
        subtitle_path = None
        enable_subtitle_val = getattr(project, "enable_subtitle", False)
        if enable_subtitle_val and scripts:
            if progress_callback:
                progress_callback("正在生成字幕...", 65)
            subtitle_path = _generate_srt_parts(scripts, audio_files, output_dir)

        # --- 添加到剪映草稿 ---
        if progress_callback:
            progress_callback("正在创建剪映草稿...", 70)

        draft_folder = DraftFolder(jianying_draft_path)
        draft_name = f"{project.name}_{int(time.time())}"
        script = draft_folder.create_draft(draft_name, 1920, 1080)

        script.add_track(TrackType.video, "视频轨道")
        script.add_track(TrackType.audio, "音频轨道")
        if subtitle_path:
            script.add_track(TrackType.text, "字幕轨道")

        current_time = 0.0
        for i, (clip_path, audio_path, duration) in enumerate(clip_entries):
            if self._canceled:
                raise RuntimeError("导出已取消")

            if not os.path.exists(clip_path):
                current_time += duration
                continue

            actual_duration = _get_video_duration(clip_path)
            if actual_duration <= 0:
                current_time += duration
                continue

            duration = actual_duration

            video_segment = VideoSegment(clip_path, trange(f"{current_time}s", f"{max(0.001, duration - 0.005)}s"))
            script.add_segment(video_segment, "视频轨道")

            if audio_path and os.path.exists(audio_path):
                audio_dur = _get_video_duration(audio_path)
                if audio_dur <= 0:
                    audio_dur = duration
                audio_segment = AudioSegment(audio_path, trange(f"{current_time}s", f"{max(0.001, min(duration, audio_dur) - 0.005)}s"))
                script.add_segment(audio_segment, "音频轨道")

            current_time += duration

        if subtitle_path and os.path.exists(subtitle_path):
            script.import_srt(subtitle_path, track_name="字幕轨道", time_offset="0s")

        script.save()
        draft_path = os.path.join(jianying_draft_path, draft_name)

        if progress_callback:
            progress_callback("导出完成", 100)

        logger.info(f"剪映草稿(parts)导出完成: {draft_path}")
        return {"draft_path": draft_path, "draft_name": draft_name}

    def _find_audio_part(self, audio_files: list, seg_idx: int, part_idx: int):
        for af in audio_files:
            if af.get("segment_index") == seg_idx and af.get("part_index") == part_idx:
                return af
        return None

    def _export_to_jianying_legacy(
        self,
        project: NarrateProjectState,
        output_dir: str,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Dict[str, str]:
        """
        导出项目到剪映草稿（V1旧版，兼容无 parts 结构）
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

        # 确保合并视频存在，如果缺失则自动重新合并
        _merged_video_path = _ensure_merged_video(project)

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
                video_path = _merged_video_path
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
        enable_subtitle_val = getattr(project, "enable_subtitle", False)
        logger.info("字幕生成检查: enable_subtitle=%s, narraction_scripts=%d, audio_files=%d",
                     enable_subtitle_val, len(project.narration_scripts) if project.narration_scripts else 0,
                     len(audio_files) if audio_files else 0)
        if enable_subtitle_val and project.narration_scripts:
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
                trange(f"{current_time}s", f"{max(0.001, duration - 0.005)}s")
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
                    trange(f"{current_time}s", f"{max(0.001, min(duration, audio_duration) - 0.005)}s")
                )
                script.add_segment(audio_segment, "音频轨道")

            current_time += duration

        # 导入字幕
        if subtitle_path and os.path.exists(subtitle_path):
            logger.info("导入字幕到剪映草稿: %s", subtitle_path)
            script.import_srt(subtitle_path, track_name="字幕轨道", time_offset="0s")
        else:
            logger.info("跳过导入字幕: subtitle_path=%s, exists=%s", subtitle_path, os.path.exists(subtitle_path) if subtitle_path else "N/A")

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