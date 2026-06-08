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

    def choice(self, seq: list) -> object:
        return self._rng.choice(seq)


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


def _pick_clip_from_video(
    video_path: str,
    video_duration: float,
    params: dict,
    remaining: float,
    rng: _Rng,
) -> dict | None:
    """从单个视频中随机截取一段，适配剩余音频时长"""
    speed_min = params.get("speed_min", 1.0)
    speed_max = params.get("speed_max", 2.0)
    zoom_min = params.get("zoom_min", 1.0)
    zoom_max = params.get("zoom_max", 1.5)

    speed = round(rng.uniform(speed_min, speed_max), 2)

    # 该视频在时间轴上最多能贡献多少秒
    max_timeline = video_duration / speed
    if max_timeline < 0.5:
        return None

    # 时间轴时长 = 取剩余和视频最大能提供的较小值
    adjusted_duration = round(min(remaining, max_timeline), 1)
    if adjusted_duration < 0.5:
        return None

    original_duration = adjusted_duration * speed

    # 随机起始点，确保不超出视频范围
    max_start = video_duration - original_duration
    start = round(rng.uniform(0.0, max(0.0, max_start)), 1)
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

    clips = []
    remaining = total_duration

    # ---- 开头素材（只取一个片段） ----
    if remaining > 0.1:
        progress.force("正在匹配开头素材...")

        for video_path in opening_videos:
            if is_canceled and is_canceled():
                raise RuntimeError("导出已取消")
            dur = opening_durations.get(video_path, 0.0)
            if dur <= 0:
                continue
            clip = _pick_clip_from_video(
                video_path, dur, opening_params, remaining, rng,
            )
            if clip:
                clips.append(clip)
                remaining -= clip["duration"]
                break

        if not clips:
            logger.warning("未找到符合条件的开头素材，跳过")

    # ---- 混剪素材（每个视频只取一个片段，一轮用完才轮转） ----
    if not mix_videos:
        return clips

    idx = 0

    while remaining > 0.1:
        if is_canceled and is_canceled():
            raise RuntimeError("导出已取消")

        # 获取当前视频
        video_path = mix_videos[idx]
        video_duration = mix_durations.get(video_path, 0.0)

        progress.emit(f"正在匹配混剪素材 ({len(clips)+1})...")

        clip = _pick_clip_from_video(
            video_path, video_duration, mix_params, remaining, rng,
        )
        if clip:
            clips.append(clip)
            remaining -= clip["duration"]
            logger.debug(
                "选取素材: %s, 时长 %.1fs, 变速 %.2fx, 剩余 %.1fs",
                os.path.basename(video_path), clip["duration"], clip["speed"], remaining,
            )

        # 每个视频只取一个片段，换下一个
        idx += 1

        if idx >= len(mix_videos):
            if remaining <= 0.1:
                break
            # 所有视频都用尽了但音频还有剩余 → 重置打乱再来
            rng.shuffle(mix_videos)
            idx = 0
            progress.force("所有素材已用完，重新轮转...")
            logger.info("所有混剪素材已用尽，重新打乱后继续匹配 (剩余 %.1fs)", remaining)

    if not clips:
        raise RuntimeError("未能选取到任何有效的视频片段，请检查素材参数设置")

    logger.info(
        "素材匹配完成: 共 %d 个片段, 总时长 %.2f 秒",
        len(clips), total_duration - remaining,
    )
    return clips
