import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.settings_service import SettingsService
from clip_synth.services.subtitle_service import SubtitleService
from clip_synth.utils.gpu_accel import apply_gpu_encoder_to_cmd

logger = logging.getLogger("clip_synth.novel_mix_jianying_export")


def _get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                return float(stdout)
    except Exception:
        pass
    return 0.0


def _get_video_resolution(path: str) -> Tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            output = result.stdout.decode("utf-8", errors="replace").strip()
            if output:
                parts = output.split(",")
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1920, 1080


def _get_aspect_ratio(w: int, h: int) -> float:
    if h == 0:
        return 1.0
    return w / h


def _srt_time_to_seconds(t: str) -> float:
    parts = t.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


def _seconds_to_srt_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return "{:02d}:{:02d}:{:06.3f}".format(h, m, s).replace(".", ",")


def _run_cmd(cmd: List[str], action: str = "处理") -> None:
    apply_gpu_encoder_to_cmd(cmd)
    logger.info("[ffmpeg] %s: %s", action, " ".join(cmd))
    flags = 0
    if sys.platform == "win32":
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
        raise RuntimeError("ffmpeg {}失败: {}".format(action, error_msg))


class NovelMixJianyingExportService:
    def __init__(self, settings_service: SettingsService):
        self._settings_service = settings_service
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def export_to_jianying(
        self,
        project: NovelMixProjectState,
        output_dir: str,
        progress_callback: Callable[[str], None] | None = None,
        is_canceled: Callable[[], bool] | None = None,
    ) -> Dict[str, str]:
        try:
            import pyJianYingDraft
            from pyJianYingDraft import (
                AudioSegment,
                DraftFolder,
                TrackType,
                VideoSegment,
                trange,
            )
        except ImportError as e:
            raise ImportError("pyJianYingDraft库导入失败: {}".format(e))

        settings = self._settings_service.load()
        jianying_draft_path = settings.draft_output_dir
        if not jianying_draft_path:
            raise ValueError("剪映草稿路径未配置，请在系统配置中设置")

        total_duration = self._get_total_audio_duration(project)
        if total_duration <= 0:
            raise RuntimeError("音频总时长为零")

        target_w, target_h = project.resolution

        from clip_synth.services.novel_mix_material_matcher import select_clips

        if progress_callback:
            progress_callback("正在选取素材片段...")

        clips = select_clips(project, total_duration, progress_callback, is_canceled)
        if not clips:
            raise RuntimeError("未能选取有效素材片段")

        if progress_callback:
            progress_callback("正在预处理素材片段...")

        processed_clips = []
        total = len(clips)
        target_ratio = target_w / target_h
        for i, clip_info in enumerate(clips):
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")

            video_path = clip_info["video_path"]
            start = clip_info["start"]
            original_duration = clip_info["original_duration"]
            adjusted_duration = clip_info["duration"]
            speed = clip_info["speed"]
            zoom = clip_info["zoom"]

            vid_w, vid_h = _get_video_resolution(video_path)
            vid_ratio = _get_aspect_ratio(vid_w, vid_h)

            out_path = os.path.join(tempfile.gettempdir(), "novel_mix_jy_clip_{}_{:04d}.mp4".format(project.id, i))

            vf_parts = []
            zoom_w = int(target_w * zoom)
            zoom_h = int(target_h * zoom)

            ratio_tolerance = 0.02
            if abs(vid_ratio - target_ratio) > ratio_tolerance:
                scale_w = max(zoom_w, int(zoom_h * vid_ratio))
                scale_h = max(zoom_h, int(zoom_w / vid_ratio))
                vf_parts.append(
                    "scale={}:{},crop={}:{}:({}-{})/2:({}-{})/2".format(
                        scale_w, scale_h,
                        zoom_w, zoom_h,
                        scale_w, zoom_w,
                        scale_h, zoom_h,
                    )
                )
            else:
                vf_parts.append("scale={}:{}".format(zoom_w, zoom_h))

            if zoom > 1.01:
                vf_parts.append(
                    "crop={}:{}:iw/2-{}/2:ih/2-{}/2".format(
                        target_w, target_h, target_w, target_h
                    )
                )

            if abs(speed - 1.0) > 0.01:
                vf_parts.append("setpts={}*PTS".format(1.0 / speed))

            vf_parts.append("format=yuv420p")
            vf_filter = ",".join(vf_parts)

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", video_path,
                "-t", str(original_duration),
                "-vf", vf_filter,
                "-map", "0:v:0",
                "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                out_path,
            ]
            _run_cmd(cmd, "预处理素材 {}/{}".format(i + 1, total))

            processed_clips.append((out_path, adjusted_duration))

        if processed_clips and project.extra_data.get("cover_dir"):
            from clip_synth.services.novel_mix_export_service import _get_random_cover, _replace_first_frame_with_cover
            first_clip_path = processed_clips[0][0]
            cover_img = _get_random_cover(project.extra_data["cover_dir"])
            if cover_img and os.path.exists(first_clip_path):
                logger.info("剪映草稿: 使用封面图片替换第一片段封面: %s", cover_img)
                _replace_first_frame_with_cover(first_clip_path, cover_img)

        if progress_callback:
            progress_callback("正在创建剪映草稿...")

        draft_folder = DraftFolder(jianying_draft_path)
        draft_name = "{}_{}".format(project.name, int(time.time()))
        script = draft_folder.create_draft(draft_name, target_w, target_h)

        script.add_track(TrackType.video, "视频轨道")
        script.add_track(TrackType.audio, "音频轨道")

        has_subtitle = project.enable_subtitle
        if has_subtitle:
            script.add_track(TrackType.text, "字幕轨道")

        current_time = 0.0
        for processed_path, clip_duration in processed_clips:
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")

            if not os.path.exists(processed_path):
                continue

            video_segment = VideoSegment(
                processed_path,
                trange("{}s".format(current_time), "{}s".format(max(0.001, clip_duration - 0.005))),
            )
            script.add_segment(video_segment, "视频轨道")
            current_time += clip_duration

        actual_total_duration = current_time

        audio_path = self._get_audio_path(project)
        if audio_path and os.path.exists(audio_path):
            audio_duration = _get_media_duration(audio_path)
            if audio_duration > 0:
                audio_segment = AudioSegment(
                    audio_path,
                    trange("0s", "{}s".format(max(0.001, min(actual_total_duration, audio_duration) - 0.005))),
                )
                script.add_segment(audio_segment, "音频轨道")

        if has_subtitle:
            subtitle_path = None
            if project.dub_mode == "system":
                subtitle_path = self._generate_subtitle(project, actual_total_duration)
            elif project.dub_mode == "self" and project.self_subtitle_path:
                subtitle_path = project.self_subtitle_path

            if subtitle_path and os.path.exists(subtitle_path):
                script.import_srt(subtitle_path, track_name="字幕轨道", time_offset="0s")

        script.save()
        draft_path = os.path.join(jianying_draft_path, draft_name)

        if progress_callback:
            progress_callback("导出完成")

        logger.info("剪映草稿导出完成: %s", draft_path)
        return {"draft_path": draft_path, "draft_name": draft_name}

    def _get_total_audio_duration(self, project: NovelMixProjectState) -> float:
        if project.dub_mode == "system":
            total = 0.0
            for af in project.audio_files:
                path = af.get("path", "")
                if not path:
                    continue
                dur = af.get("duration")
                if dur is None or dur <= 0:
                    dur = _get_media_duration(path)
                total += dur
            return total
        elif project.dub_mode == "self":
            audio_path = project.self_audio_path
            if audio_path and os.path.exists(audio_path):
                return _get_media_duration(audio_path)
        return 0.0

    def _get_audio_path(self, project: NovelMixProjectState) -> str:
        if project.dub_mode == "system":
            audio_files = [af["path"] for af in project.audio_files if af.get("path") and os.path.exists(af["path"])]
            if not audio_files:
                return ""
            if len(audio_files) == 1:
                return audio_files[0]
            merged_path = os.path.join(tempfile.gettempdir(), "novel_mix_jy_audio_{}.mp3".format(project.id))
            concat_file = os.path.join(tempfile.gettempdir(), "novel_mix_jy_audio_concat_{}.txt".format(project.id))
            try:
                with open(concat_file, "w", encoding="utf-8") as f:
                    for af in audio_files:
                        abs_path = Path(af).resolve().as_posix()
                        f.write("file '{}'\n".format(abs_path))
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", concat_file,
                    "-c", "copy",
                    merged_path,
                ]
                _run_cmd(cmd, "合并音频")
            finally:
                try:
                    os.unlink(concat_file)
                except Exception:
                    pass
            return merged_path
        elif project.dub_mode == "self":
            return project.self_audio_path
        return ""

    def _generate_subtitle(self, project: NovelMixProjectState, max_duration: float = 0.0) -> str | None:
        srt_sections = []
        time_offset = 0.0

        for af in project.audio_files:
            path = af.get("path", "")
            timestamps = af.get("timestamps", [])
            text = af.get("text", "")
            duration = af.get("duration", 0)
            if duration is None or duration <= 0:
                duration = _get_media_duration(path) if path else 0

            if not timestamps:
                time_offset += duration
                continue

            sentences = SubtitleService.merge_words_to_sentences(timestamps, reference_text=text)
            if sentences:
                srt_content = SubtitleService.generate_srt(sentences, clip_start_time=time_offset)
                srt_sections.append(srt_content.strip())

            time_offset += duration

        if not srt_sections:
            return None

        merged = "\n\n".join(srt_sections)
        lines = merged.split("\n")
        renumbered = []
        idx = 1
        for line in lines:
            if line.strip().isdigit():
                renumbered.append(str(idx))
                idx += 1
            else:
                renumbered.append(line)

        srt_text = "\n".join(renumbered)

        if max_duration > 0:
            final_lines = []
            for line in srt_text.split("\n"):
                if "-->" in line:
                    parts = line.split(" --> ")
                    if len(parts) == 2:
                        end_time_str = parts[1].strip()
                        end_seconds = _srt_time_to_seconds(end_time_str)
                        if end_seconds > max_duration:
                            parts[1] = _seconds_to_srt_time(max_duration)
                            line = " --> ".join(parts)
                final_lines.append(line)
            srt_text = "\n".join(final_lines)

        srt_path = os.path.join(tempfile.gettempdir(), "novel_mix_jy_subtitle_{}.srt".format(project.id))
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_text)

        return srt_path
