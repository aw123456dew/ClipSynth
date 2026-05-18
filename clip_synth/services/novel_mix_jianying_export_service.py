import logging
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.settings_service import SettingsService
from clip_synth.services.subtitle_service import SubtitleService

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

        if not project.material_videos:
            raise RuntimeError("没有素材视频")

        total_duration = self._get_total_audio_duration(project)
        if total_duration <= 0:
            raise RuntimeError("音频总时长为零")

        target_w, target_h = project.resolution

        if progress_callback:
            progress_callback("正在选取素材片段...")

        clips = self._select_random_clips(project, total_duration)
        if not clips:
            raise RuntimeError("未能选取有效素材片段")

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
        for i, (video_path, start, duration) in enumerate(clips):
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")

            if progress_callback:
                progress_callback(f"添加素材 {i+1}/{len(clips)}...")

            if not os.path.exists(video_path):
                current_time += duration
                continue

            actual_duration = _get_media_duration(video_path)
            if actual_duration <= 0:
                current_time += duration
                continue

            use_duration = min(duration, actual_duration)

            video_segment = VideoSegment(
                video_path,
                trange("{}s".format(current_time), "{}s".format(max(0.001, use_duration - 0.005))),
            )
            script.add_segment(video_segment, "视频轨道")

            current_time += use_duration

        audio_path = self._get_audio_path(project)
        if audio_path and os.path.exists(audio_path):
            audio_duration = _get_media_duration(audio_path)
            if audio_duration > 0:
                audio_segment = AudioSegment(
                    audio_path,
                    trange("0s", "{}s".format(max(0.001, min(total_duration, audio_duration) - 0.005))),
                )
                script.add_segment(audio_segment, "音频轨道")

        if has_subtitle:
            subtitle_path = None
            if project.dub_mode == "system":
                subtitle_path = self._generate_subtitle(project)
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
            return sum(
                af.get("duration", 0) or _get_media_duration(af.get("path", ""))
                for af in project.audio_files
                if af.get("path")
            )
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
            from clip_synth.services.doubao_tts_service import merge_audio_files
            merge_audio_files(audio_files, merged_path)
            return merged_path
        elif project.dub_mode == "self":
            return project.self_audio_path
        return ""

    def _select_random_clips(
        self,
        project: NovelMixProjectState,
        total_duration: float,
    ) -> List[Tuple[str, float, float]]:
        clips = []
        remaining = total_duration
        available = list(project.material_videos)

        if not available:
            return clips

        random.shuffle(available)
        idx = 0

        while remaining > 0.1:
            video = available[idx % len(available)]
            idx += 1
            if idx >= len(available):
                random.shuffle(available)

            vid_duration = video.duration
            if vid_duration <= 0:
                vid_duration = _get_media_duration(video.path)

            if vid_duration <= 0:
                continue

            clip_duration = min(remaining, vid_duration)
            clips.append((video.path, 0.0, clip_duration))
            remaining -= clip_duration

        return clips

    def _generate_subtitle(self, project: NovelMixProjectState) -> str | None:
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

        srt_path = os.path.join(tempfile.gettempdir(), "novel_mix_jy_subtitle_{}.srt".format(project.id))
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(renumbered))

        return srt_path
