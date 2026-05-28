import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.subtitle_service import SubtitleService
from clip_synth.utils.gpu_accel import apply_gpu_encoder_to_cmd

logger = logging.getLogger("clip_synth.novel_mix_export_service")


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
    except Exception as e:
        logger.warning("获取时长失败: %s", str(e))
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
    except Exception as e:
        logger.warning("获取视频分辨率失败: %s", str(e))
    return 1920, 1080


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


def _run_cmd_with_cwd(cmd: List[str], cwd: str, action: str = "处理") -> None:
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
        cwd=cwd,
    )
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
        raise RuntimeError("ffmpeg {}失败: {}".format(action, error_msg))


def _get_aspect_ratio(w: int, h: int) -> float:
    if h == 0:
        return 1.0
    return w / h


class NovelMixExportService:
    def __init__(self):
        self._canceled = False

    def cancel(self):
        self._canceled = True

    def export(
        self,
        project: NovelMixProjectState,
        progress_callback: Callable[[str], None] | None = None,
        is_canceled: Callable[[], bool] | None = None,
    ) -> str:
        target_w, target_h = project.resolution

        total_duration = self._get_total_audio_duration(project)
        if total_duration <= 0:
            raise RuntimeError("音频总时长为零，无法导出")

        from clip_synth.services.novel_mix_material_matcher import select_clips

        if progress_callback:
            progress_callback("正在选取素材片段...")

        clips = select_clips(project, total_duration, progress_callback, is_canceled)

        if not clips:
            raise RuntimeError("未能选取有效素材片段")

        if progress_callback:
            progress_callback("正在处理素材片段...")

        clip_paths = self._process_clips(clips, target_w, target_h, project, progress_callback, is_canceled)

        if progress_callback:
            progress_callback("正在合并视频...")

        config = __import__("clip_synth.core.config", fromlist=["AppConfig"]).AppConfig()
        export_dir = Path(config.export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

        output_name = f"{project.name}_{int(__import__('time').time())}.mp4"
        output_path = str(export_dir / output_name)

        concat_file = self._build_concat_file(clip_paths)

        audio_path = self._get_audio_path(project)

        subtitle_path = None
        if project.enable_subtitle:
            if project.dub_mode == "system":
                subtitle_path = self._generate_subtitle_from_timestamps(project)
            elif project.dub_mode == "self" and project.self_subtitle_path:
                subtitle_path = project.self_subtitle_path

        self._merge_all(concat_file, audio_path, subtitle_path, target_w, target_h, output_path, project)

        for p in clip_paths:
            try:
                os.unlink(p)
            except Exception:
                pass
        try:
            os.unlink(concat_file)
        except Exception:
            pass

        logger.info("导出完成: %s", output_path)
        return output_path

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
            merged_path = os.path.join(tempfile.gettempdir(), f"novel_mix_merged_audio_{project.id}.mp3")
            concat_file = os.path.join(tempfile.gettempdir(), f"novel_mix_audio_concat_{project.id}.txt")
            try:
                with open(concat_file, "w", encoding="utf-8") as f:
                    for af in audio_files:
                        abs_path = Path(af).resolve().as_posix()
                        f.write(f"file '{abs_path}'\n")
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

    def _process_clips(
        self,
        clips: List[dict],
        target_w: int,
        target_h: int,
        project: NovelMixProjectState,
        progress_callback: Callable[[str], None] | None = None,
        is_canceled: Callable[[], bool] | None = None,
    ) -> List[str]:
        target_ratio = target_w / target_h
        clip_paths = []
        total = len(clips)

        for i, clip_info in enumerate(clips):
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")

            if progress_callback:
                progress_callback(f"处理素材 {i+1}/{total}...")

            video_path = clip_info["video_path"]
            start = clip_info["start"]
            original_duration = clip_info["original_duration"]
            speed = clip_info["speed"]
            zoom = clip_info["zoom"]

            vid_w, vid_h = _get_video_resolution(video_path)
            vid_ratio = _get_aspect_ratio(vid_w, vid_h)

            out_path = os.path.join(tempfile.gettempdir(), f"novel_mix_clip_{project.id}_{i:04d}.mp4")

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
            _run_cmd(cmd, f"预处理素材 {i+1}/{total}")

            clip_paths.append(out_path)

        return clip_paths

    def _build_concat_file(self, clip_paths: List[str]) -> str:
        concat_path = os.path.join(tempfile.gettempdir(), "novel_mix_concat.txt")
        with open(concat_path, "w", encoding="utf-8") as f:
            for p in clip_paths:
                abs_path = Path(p).resolve().as_posix()
                f.write(f"file '{abs_path}'\n")
        return concat_path

    def _generate_subtitle_from_timestamps(self, project: NovelMixProjectState) -> str | None:
        srt_sections = []
        time_offset = 0.0

        for af in project.audio_files:
            path = af.get("path", "")
            timestamps = af.get("timestamps", [])
            text = af.get("text", "")
            duration = af.get("duration", 0) or _get_media_duration(path) if path else 0

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

        srt_path = os.path.join(tempfile.gettempdir(), f"novel_mix_subtitle_{project.id}.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(renumbered))

        return srt_path

    def _merge_all(
        self,
        concat_file: str,
        audio_path: str,
        subtitle_path: str | None,
        target_w: int,
        target_h: int,
        output_path: str,
        project: NovelMixProjectState,
    ) -> None:
        has_audio = audio_path and os.path.exists(audio_path)
        has_subtitle = subtitle_path and os.path.exists(subtitle_path)

        if has_subtitle:
            import shutil
            work_dir = tempfile.mkdtemp(prefix="novel_mix_")
            try:
                shutil.copy2(concat_file, os.path.join(work_dir, "concat.txt"))
                shutil.copy2(subtitle_path, os.path.join(work_dir, "sub.srt"))
                if has_audio:
                    shutil.copy2(audio_path, os.path.join(work_dir, "audio.mp3"))

                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", "concat.txt",
                ]
                if has_audio:
                    cmd += ["-i", "audio.mp3"]
                cmd += [
                    "-vf", "subtitles=sub.srt",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                ]
                if has_audio:
                    cmd += [
                        "-c:a", "aac", "-b:a", "128k",
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-shortest",
                    ]
                cmd += ["output.mp4"]
                _run_cmd_with_cwd(cmd, work_dir, "渲染字幕+合并音频")

                local_output = os.path.join(work_dir, "output.mp4")
                if os.path.exists(local_output):
                    shutil.move(local_output, output_path)

            finally:
                try:
                    shutil.rmtree(work_dir, ignore_errors=True)
                except Exception:
                    pass
        else:
            cmd = ["ffmpeg", "-y"]
            cmd += ["-f", "concat", "-safe", "0", "-i", concat_file]
            if has_audio:
                cmd += ["-i", audio_path]
                cmd += [
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                ]
            else:
                cmd += ["-c:v", "copy"]
            cmd += ["-shortest", output_path]
            _run_cmd(cmd, "合并导出")

    def _build_subtitle_style_str(self, project: NovelMixProjectState) -> str:
        font_name = project.subtitle_font or "Microsoft YaHei"
        font_size = project.subtitle_font_size or 24
        color = project.subtitle_font_color or "#FFFFFF"

        alignment = 2

        return (
            f"FontName={font_name},"
            f"FontSize={font_size},"
            f"PrimaryColour=&H{color[5:7]}{color[3:5]}{color[1:3]},"
            f"Alignment={alignment}"
        )
