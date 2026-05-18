import logging
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

logger = logging.getLogger("clip_synth.utils.ffmpeg_helper")

try:
    from clip_synth.utils.gpu_accel import get_video_encoder_args
    HAS_GPU_ACCEL = True
except ImportError:
    HAS_GPU_ACCEL = False

FFMPEG_FILENAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


def _get_project_root() -> Path:
    """获取项目根目录（开发模式）或 MEIPASS 目录（打包模式）"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def get_bundle_dir() -> str:
    """获取打包后 exe 所在目录（开发环境下返回空字符串）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return ""


def get_resource_path(relative_path: str) -> str:
    """获取资源文件的绝对路径（兼容开发环境和打包环境）

    Args:
        relative_path: 相对于项目根目录的路径，如 "resources/styles/main.qss"

    Returns:
        资源的绝对路径字符串
    """
    root = _get_project_root()
    return str(root / relative_path)


def get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行文件路径

    优先级：
    1. 打包目录下与 exe 同目录的 ffmpeg
    2. 系统 PATH 中的 ffmpeg
    """
    bundle_dir = get_bundle_dir()
    if bundle_dir:
        bundled_path = os.path.join(bundle_dir, FFMPEG_FILENAME)
        if os.path.isfile(bundled_path):
            logger.debug("使用捆绑的 ffmpeg: %s", bundled_path)
            return bundled_path

    system_path = shutil.which(FFMPEG_FILENAME)
    if system_path:
        return system_path

    return FFMPEG_FILENAME


def add_ffmpeg_to_path() -> None:
    """将捆绑的 ffmpeg 所在目录添加到 PATH 环境变量

    使所有通过 subprocess 调用 'ffmpeg' 的地方都能自动找到它。
    """
    bundle_dir = get_bundle_dir()
    if not bundle_dir:
        return

    bundled_path = os.path.join(bundle_dir, FFMPEG_FILENAME)
    if not os.path.isfile(bundled_path):
        return

    path_env = os.environ.get("PATH", "")
    if bundle_dir not in path_env:
        os.environ["PATH"] = bundle_dir + os.pathsep + path_env
        logger.info("已将捆绑的 ffmpeg 目录添加到 PATH: %s", bundle_dir)


def get_video_codec(filepath: str) -> str | None:
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=15,
            creationflags=flags,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


def unify_video_codecs(video_paths: list[str], cache_dir: str) -> list[str]:
    if len(video_paths) <= 1:
        return list(video_paths)

    codecs = []
    for p in video_paths:
        c = get_video_codec(p)
        codecs.append(c)
        logger.info("视频编码检测: %s -> %s", os.path.basename(p), c or "未知")

    valid = [(p, c) for p, c in zip(video_paths, codecs) if c]
    if len(valid) < 2:
        return list(video_paths)

    counter = Counter(c for _, c in valid)
    if len(counter) == 1:
        logger.info("所有视频编码一致: %s", next(iter(counter)))
        return list(video_paths)

    majority_codec = counter.most_common(1)[0][0]
    logger.info("多数编码: %s (%d个), 需要统一 %d 个视频",
                majority_codec, counter[majority_codec],
                len(valid) - counter[majority_codec])

    output_paths = []
    for i, (p, c) in enumerate(zip(video_paths, codecs)):
        if c == majority_codec:
            output_paths.append(p)
            continue

        transcoded = os.path.join(cache_dir, f"unified_{i:04d}_{majority_codec}.mp4")
        logger.info("转码 %s: %s → %s", os.path.basename(p), c or "未知", majority_codec)
        
        if HAS_GPU_ACCEL:
            encoder_args = get_video_encoder_args()
        else:
            encoder_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]
        
        cmd = [
            "ffmpeg", "-y",
            "-i", p,
            *encoder_args,
            "-c:a", "aac", "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
            transcoded,
        ]
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, creationflags=flags,
        )
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
            raise RuntimeError(f"视频编码统一转码失败: {error_msg}")
        output_paths.append(transcoded)

    return output_paths
