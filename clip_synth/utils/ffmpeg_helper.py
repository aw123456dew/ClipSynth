import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger("clip_synth.utils.ffmpeg_helper")

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
