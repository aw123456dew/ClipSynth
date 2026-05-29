import logging
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_gpu_cache: Optional[dict] = None
_gpu_accel_enabled: bool = False


def _get_ffmpeg_path() -> str:
    from clip_synth.utils.ffmpeg_helper import get_ffmpeg_path
    return get_ffmpeg_path()


def _run_capture(cmd: list) -> str:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, **kwargs)
        return result.stdout or ""
    except Exception:
        return ""


def is_gpu_accel_enabled() -> bool:
    return _gpu_accel_enabled


def set_gpu_accel_enabled(enabled: bool) -> None:
    global _gpu_accel_enabled
    _gpu_accel_enabled = enabled
    if enabled:
        detect_gpu()


def detect_gpu() -> dict:
    global _gpu_cache
    if _gpu_cache is not None:
        return _gpu_cache

    ffmpeg = _get_ffmpeg_path()
    encoders_output = _run_capture([ffmpeg, "-hide_banner", "-encoders"])

    gpu_type = "none"
    encoder = None

    if "h264_nvenc" in encoders_output:
        gpu_type = "nvidia"
        encoder = "h264_nvenc"
    elif "h264_amf" in encoders_output:
        gpu_type = "amd"
        encoder = "h264_amf"
    elif "h264_qsv" in encoders_output:
        gpu_type = "intel"
        encoder = "h264_qsv"

    _gpu_cache = {"type": gpu_type, "encoder": encoder}
    logger.info("GPU 检测结果: type=%s, encoder=%s", gpu_type, encoder)
    return _gpu_cache


def get_video_encoder_args() -> list:
    if not _gpu_accel_enabled:
        return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]

    gpu = detect_gpu()
    gpu_type = gpu["type"]

    if gpu_type == "nvidia":
        return ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "23"]
    elif gpu_type == "amd":
        return ["-c:v", "h264_amf", "-quality", "speed", "-qp_i", "23", "-qp_p", "23"]
    elif gpu_type == "intel":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23"]

    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]


def apply_gpu_encoder_to_cmd(cmd: list) -> None:
    if not _gpu_accel_enabled:
        return

    gpu = detect_gpu()
    if gpu["type"] == "none":
        return

    try:
        idx = cmd.index("-c:v")
    except ValueError:
        return

    if idx + 6 > len(cmd) or cmd[idx + 1] != "libx264":
        return

    encoder_args = get_video_encoder_args()
    cmd[idx:idx + 6] = encoder_args
    logger.debug("已应用 GPU 编码器: %s", encoder_args[1])
