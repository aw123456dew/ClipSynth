import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

from clip_synth.models.narrate_project_state import NarrateProjectState

logger = logging.getLogger("clip_synth.narrate_export_service")


def _normalize_time(t: str) -> str:
    return t.replace(",", ".")


def _time_to_seconds(t: str) -> float:
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
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


def _get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30)
        if result.returncode == 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                return float(stdout)
    except Exception as e:
        logger.warning("获取时长失败: %s", str(e))
    return 0.0


class NarrateExportService:
    def __init__(self, output_dir: str):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._process = None

    def _build_segment_video_map(self, project: NarrateProjectState) -> Dict[str, str]:
        mapping = {}
        for video_state in project.videos:
            for seg_list in video_state.segments.values():
                for seg in seg_list:
                    mapping[seg.id] = video_state.video_path
        return mapping

    def export(
        self,
        project: NarrateProjectState,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> str:
        scripts = project.narration_scripts
        audio_files = project.audio_files
        if not scripts:
            raise RuntimeError("没有解说文案，无法导出")

        seg_video_map = self._build_segment_video_map(project)
        output_path = str(self._output_dir / f"{project.name}_成品视频.mp4")
        raw_dir = self._output_dir / "clip_raw"
        proc_dir = self._output_dir / "clip_processed"
        raw_dir.mkdir(parents=True, exist_ok=True)
        proc_dir.mkdir(parents=True, exist_ok=True)
        raw_list: List[str] = []
        final_list: List[str] = []

        total = len(scripts)
        try:
            # ---- 第1遍：从原视频按时间戳裁剪出所有片段（保留原声） ----
            if progress_callback:
                progress_callback("正在裁剪片段...", 5.0)

            for i, script in enumerate(scripts):
                if progress_callback:
                    progress_callback(f"裁剪第 {i+1}/{total} 个片段...", 5.0 + (i / total) * 30.0)

                video_path = seg_video_map.get(script.get("segment_id", ""))
                if not video_path:
                    for vs in project.videos:
                        video_path = vs.video_path
                        break
                if not video_path:
                    raise RuntimeError("无法找到片段对应的视频")

                raw_start = _normalize_time(script.get("start_time", "00:00:00"))
                raw_end = _normalize_time(script.get("end_time", "00:00:00"))
                start_sec = _time_to_seconds(raw_start)
                end_sec = _time_to_seconds(raw_end)
                if end_sec <= start_sec:
                    logger.warning(f"[导出] 片段 {i+1} 时长<=0, 跳过")
                    continue

                duration = end_sec - start_sec
                if duration < 0.5:
                    duration = 0.5

                raw_path = str(raw_dir / f"raw_{i:04d}.mp4")
                cut_cmd = [
                    "ffmpeg", "-y",
                    "-ss", raw_start,
                    "-i", video_path,
                    "-t", f"{duration}",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-avoid_negative_ts", "make_zero",
                    raw_path,
                ]
                _run_cmd(cut_cmd, f"裁剪片段 {i+1}")
                raw_list.append(raw_path)

            if not raw_list:
                raise RuntimeError("没有有效的片段可导出")

            # ---- 第2遍：每个片段单独处理 ----
            if progress_callback:
                progress_callback("正在处理片段...", 40.0)

            audio_idx = 0
            for i, script in enumerate(scripts):
                if i >= len(raw_list):
                    break

                if progress_callback:
                    progress_callback(f"处理第 {i+1}/{len(raw_list)} 个片段...", 40.0 + (i / len(raw_list)) * 45.0)

                content_type = script.get("content_type", "narration")
                raw_path = raw_list[i]
                final_path = str(proc_dir / f"final_{i:04d}.mp4")

                if content_type == "original_sound":
                    os.rename(raw_path, final_path)
                    final_list.append(final_path)
                    continue

                audio_path = None
                audio_duration = 0.0
                if audio_idx < len(audio_files):
                    candidate = audio_files[audio_idx].get("path", "")
                    if os.path.exists(candidate):
                        audio_path = candidate
                        audio_duration = _get_media_duration(audio_path)
                audio_idx += 1

                if not audio_path:
                    raise RuntimeError(
                        f"解说片段 {i+1} 缺少配音文件，请检查音频文件后再试"
                    )

                raw_duration = _get_media_duration(raw_path)
                diff = audio_duration - raw_duration

                if diff > 0.5:
                    extend_path = str(raw_dir / f"ext_{i:04d}.mp4")
                    video_path = seg_video_map.get(script.get("segment_id", ""))
                    if not video_path:
                        for vs in project.videos:
                            video_path = vs.video_path
                            break
                    raw_start = _normalize_time(script.get("start_time", "00:00:00"))
                    start_sec = _time_to_seconds(raw_start)
                    extend_cmd = [
                        "ffmpeg", "-y",
                        "-ss", raw_start,
                        "-i", video_path,
                        "-t", f"{audio_duration + 0.5}",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "23",
                        "-an",
                        "-avoid_negative_ts", "make_zero",
                        extend_path,
                    ]
                    _run_cmd(extend_cmd, f"扩展片段 {i+1}")

                    overlay_cmd = [
                        "ffmpeg", "-y",
                        "-i", extend_path,
                        "-i", audio_path,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-shortest",
                        final_path,
                    ]
                    _run_cmd(overlay_cmd, f"配音扩展 {i+1}")
                else:
                    speed = raw_duration / audio_duration
                    if speed > 1.0:
                        speed_path = str(raw_dir / f"speed_{i:04d}.mp4")
                        speed_cmd = [
                            "ffmpeg", "-y",
                            "-i", raw_path,
                            "-filter:v", f"setpts={1.0/speed}*PTS",
                            "-an",
                            "-c:v", "libx264",
                            "-preset", "ultrafast",
                            "-crf", "23",
                            speed_path,
                        ]
                        _run_cmd(speed_cmd, f"加速片段 {i+1}")
                        input_video_path = speed_path
                    else:
                        mute_path = str(raw_dir / f"mute_{i:04d}.mp4")
                        mute_cmd = [
                            "ffmpeg", "-y",
                            "-i", raw_path,
                            "-c:v", "copy",
                            "-an",
                            mute_path,
                        ]
                        _run_cmd(mute_cmd, f"静音 {i+1}")
                        input_video_path = mute_path

                    overlay_cmd = [
                        "ffmpeg", "-y",
                        "-i", input_video_path,
                        "-i", audio_path,
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "128k",
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-shortest",
                        final_path,
                    ]
                    _run_cmd(overlay_cmd, f"配音合成 {i+1}")

                final_list.append(final_path)

            if not final_list:
                raise RuntimeError("没有有效的片段可导出")

            # ---- 第3遍：顺序合并所有片段 ----
            if progress_callback:
                progress_callback("正在合并所有片段...", 88.0)

            n = len(final_list)
            input_parts = []
            for fp in final_list:
                input_parts.extend(["-i", fp])

            filter_labels = "".join(f"[{j}:v][{j}:a]" for j in range(n))
            filter_complex = f"{filter_labels} concat=n={n}:v=1:a=1 [outv][outa]"

            concat_cmd = (
                ["ffmpeg", "-y"]
                + input_parts
                + [
                    "-filter_complex", filter_complex,
                    "-map", "[outv]",
                    "-map", "[outa]",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    output_path,
                ]
            )
            _run_cmd(concat_cmd, "合并所有片段")

            if progress_callback:
                progress_callback("导出完成", 100.0)

            logger.info(f"视频导出完成: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"导出失败: {e}", exc_info=True)
            raise

    def cancel(self):
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
