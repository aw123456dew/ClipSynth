import concurrent.futures
import logging
import os
import subprocess
import sys
import time as time_module
from typing import Callable, Dict, List, Tuple

from clip_synth.models.novel_mix_project_state import NovelMixProjectState

logger = logging.getLogger("clip_synth.novel_mix_material_matcher")

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}

_FFPROBE_MAX_WORKERS = 4

_PROGRESS_THROTTLE = 0.5


class _Rng:
    def __init__(self):
        import random
        self._rng = random.Random(time_module.time() * 1000)

    def shuffle(self, lst: list) -> None:
        self._rng.shuffle(lst)

    def uniform(self, a: float, b: float) -> float:
        return self._rng.uniform(a, b)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)


class _ProgressThrottle:
    def __init__(self, callback: Callable[[str], None] | None):
        self._callback = callback
        self._last = 0.0

    def emit(self, msg: str) -> None:
        if not self._callback:
            return
        now = time_module.time()
        if now - self._last >= _PROGRESS_THROTTLE:
            self._last = now
            self._callback(msg)

    def force(self, msg: str) -> None:
        if self._callback:
            self._callback(msg)


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
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        logger.warning("获取时长失败 %s: %s", path, str(e))
    return 0.0


def _scan_videos(folder: str) -> List[str]:
    videos = []
    try:
        for root, dirs, files in os.walk(folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    videos.append(os.path.join(root, f))
    except Exception as e:
        logger.error("扫描视频文件夹失败 %s: %s", folder, e)
    return videos


def _batch_get_durations(
    video_paths: List[str],
    progress: _ProgressThrottle,
) -> Dict[str, float]:
    durations: Dict[str, float] = {}
    total = len(video_paths)
    lock = __import__("threading").Lock()

    def probe_one(vp: str) -> Tuple[str, float]:
        dur = _get_media_duration(vp)
        return vp, dur

    with concurrent.futures.ThreadPoolExecutor(max_workers=_FFPROBE_MAX_WORKERS) as executor:
        futures = [executor.submit(probe_one, vp) for vp in video_paths]
        done = 0
        for future in concurrent.futures.as_completed(futures):
            done += 1
            if total > 100 and (done % 100 == 0 or done == total):
                progress.emit(f"正在获取视频时长 ({done}/{total})...")
            vp, dur = future.result()
            if dur > 0:
                with lock:
                    durations[vp] = dur

    return durations


def _filter_by_min_duration(
    video_durations: Dict[str, float],
    seg_min: float,
    speed_max: float,
) -> List[str]:
    min_needed = seg_min * speed_max
    return [vp for vp, dur in video_durations.items() if dur >= min_needed]


def _pick_clip_from_video(
    video_path: str,
    video_duration: float,
    params: dict,
    remaining: float,
    rng: _Rng,
) -> dict | None:
    speed_min = params.get("speed_min", 1.0)
    speed_max = params.get("speed_max", 2.0)
    seg_min = params.get("segment_min", 1.0)
    seg_max = params.get("segment_max", 5.0)
    zoom_min = params.get("zoom_min", 1.0)
    zoom_max = params.get("zoom_max", 1.5)

    speed = round(rng.uniform(speed_min, speed_max), 2)

    max_adjusted = min(seg_max, remaining)
    if max_adjusted < seg_min:
        return None

    adjusted_duration = round(rng.uniform(seg_min, max_adjusted), 1)
    original_duration = adjusted_duration * speed

    if original_duration > video_duration:
        return None

    max_start = video_duration - original_duration
    start = round(rng.uniform(0.0, max_start), 1)
    zoom = round(rng.uniform(zoom_min, zoom_max), 2)

    return {
        "video_path": video_path,
        "start": start,
        "original_duration": original_duration,
        "duration": adjusted_duration,
        "speed": speed,
        "zoom": zoom,
    }


def select_clips(
    project: NovelMixProjectState,
    total_duration: float,
    progress_callback: Callable[[str], None] | None = None,
    is_canceled: Callable[[], bool] | None = None,
) -> List[Dict]:
    rng = _Rng()
    progress = _ProgressThrottle(progress_callback)

    opening_params = project.extra_data.get("opening_params", {})
    mix_params = project.extra_data.get("mix_params", {})

    opening_videos = _scan_videos(project.opening_folder) if project.opening_folder else []
    mix_videos = _scan_videos(project.mix_folder) if project.mix_folder else []

    if not opening_videos:
        raise RuntimeError("视频开头素材文件夹中没有视频文件，请检查路径")
    if not mix_videos:
        raise RuntimeError("混剪素材文件夹中没有视频文件，请检查路径")

    rng.shuffle(opening_videos)
    rng.shuffle(mix_videos)

    progress.emit("正在获取视频时长信息...")

    opening_durations = _batch_get_durations(opening_videos, progress)
    mix_durations = _batch_get_durations(mix_videos, progress)

    if not opening_durations:
        raise RuntimeError("开头素材文件夹中无有效视频文件")
    if not mix_durations:
        raise RuntimeError("混剪素材文件夹中无有效视频文件")

    progress.emit("正在过滤可用素材...")

    seg_min = mix_params.get("segment_min", 1.0)
    speed_max = mix_params.get("speed_max", 2.0)

    opening_seg_min = opening_params.get("segment_min", 1.0)
    opening_speed_max = opening_params.get("speed_max", 2.0)

    opening_min_dur = _filter_by_min_duration(
        opening_durations, opening_seg_min, opening_speed_max,
    )
    mix_min_dur = _filter_by_min_duration(
        mix_durations, seg_min, speed_max,
    )

    if not opening_min_dur:
        raise RuntimeError("开头素材视频时长均不满足最小切分要求")
    if not mix_min_dur:
        raise RuntimeError("混剪素材视频时长均不满足最小切分要求")

    rng.shuffle(opening_min_dur)
    rng.shuffle(mix_min_dur)

    clips = []
    remaining = total_duration
    consecutive_failures = 0

    if remaining > 0.1:
        progress.force("正在匹配开头素材...")

        for video_path in opening_min_dur:
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")

            clip = _pick_clip_from_video(
                video_path, opening_durations[video_path],
                opening_params, remaining, rng,
            )
            if clip:
                clips.append(clip)
                remaining -= clip["duration"]
                break

        if not clips:
            logger.warning("未找到符合条件的开头素材，跳过")

    if remaining < seg_min:
        remaining = 0.0

    mix_idx = 0

    while remaining > 0.1:
        if is_canceled and is_canceled():
            raise RuntimeError("导出已取消")

        if consecutive_failures >= len(mix_min_dur):
            logger.warning("无法匹配更多素材，剩余 %.1f 秒未填充", remaining)
            break

        if remaining < seg_min:
            break

        video_path = mix_min_dur[mix_idx % len(mix_min_dur)]
        video_duration = mix_durations.get(video_path, 0.0)

        min_needed = seg_min * mix_params.get("speed_max", 2.0)
        if video_duration < min_needed:
            mix_idx += 1
            if mix_idx >= len(mix_min_dur):
                rng.shuffle(mix_min_dur)
                mix_idx = 0
            consecutive_failures += 1
            continue

        progress.emit(f"正在匹配混剪素材 ({len(clips)+1})...")

        clip = _pick_clip_from_video(
            video_path, video_duration,
            mix_params, remaining, rng,
        )
        if clip:
            clips.append(clip)
            remaining -= clip["duration"]
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        mix_idx += 1
        if mix_idx >= len(mix_min_dur):
            rng.shuffle(mix_min_dur)
            mix_idx = 0

    if not clips:
        raise RuntimeError("未能选取到任何有效的视频片段，请检查素材参数设置")

    logger.info(
        "素材匹配完成: 共 %d 个片段, 总时长 %.2f 秒",
        len(clips), total_duration - remaining,
    )
    return clips
