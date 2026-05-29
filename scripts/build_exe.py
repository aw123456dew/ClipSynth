"""
ClipSynth 打包脚本
===================
使用 PyInstaller 将 ClipSynth 打包为单个 exe 文件，并捆绑本地 ffmpeg。

使用方法：
    python scripts/build_exe.py

选项：
    --console           显示控制台窗口（调试用，默认隐藏）
    --clean             清理之前的构建缓存
    --name NAME         指定输出 exe 名称（默认 ClipSynth）
    --icon PATH         指定 exe 图标路径
    --ffmpeg-dir PATH   本地 ffmpeg 目录
    --upx-dir PATH      指定 UPX 压缩工具目录
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
logger = logging.getLogger("build_exe")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

FFMPEG_FILENAME = "ffmpeg.exe"
FFPROBE_FILENAME = "ffprobe.exe"
DEFAULT_ICON_PATH = PROJECT_ROOT / "clip_synth/resources/icons/icon.jpg"


def convert_jpg_to_ico(jpg_path: str | Path, ico_path: str | Path) -> None:
    """将 JPG 图标转换为 ICO 格式"""
    try:
        img = Image.open(jpg_path)
        img = img.convert("RGBA")
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        img.save(ico_path, format="ICO", sizes=sizes)
        logger.info("已将图标转换为 ICO 格式: %s", ico_path)
    except Exception as e:
        logger.warning("图标转换失败: %s", e)


def get_icon_path(icon_arg: str | None) -> str | None:
    """获取图标路径，自动转换 JPG 为 ICO"""
    if icon_arg:
        icon_path = Path(icon_arg)
    else:
        icon_path = DEFAULT_ICON_PATH

    if not icon_path.exists():
        logger.warning("图标文件不存在: %s", icon_path)
        return None

    if icon_path.suffix.lower() in [".jpg", ".jpeg"]:
        ico_path = icon_path.with_suffix(".ico")
        convert_jpg_to_ico(icon_path, ico_path)
        return str(ico_path)
    elif icon_path.suffix.lower() == ".ico":
        return str(icon_path)
    else:
        logger.warning("不支持的图标格式: %s", icon_path.suffix)
        return None


def check_python() -> None:
    """检查 Python 版本"""
    if sys.version_info < (3, 10):
        logger.error("需要 Python 3.10 或更高版本")
        sys.exit(1)
    logger.info("Python 版本: %s", sys.version)


def ensure_pyinstaller() -> None:
    """确保 PyInstaller 已安装"""
    try:
        import PyInstaller  # noqa: F401
        logger.info("PyInstaller 已安装")
    except ImportError:
        logger.info("正在安装 PyInstaller...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
        )
        logger.info("PyInstaller 安装完成")


def find_ffmpeg(ffmpeg_dir: str | None) -> str:
    """查找 ffmpeg.exe

    优先级：
    1. --ffmpeg-dir 参数指定目录
    2. 系统 PATH 中的 ffmpeg
    """
    if ffmpeg_dir:
        custom_path = os.path.join(ffmpeg_dir, "bin", FFMPEG_FILENAME)
        if os.path.isfile(custom_path):
            logger.info("使用指定目录的 ffmpeg: %s", custom_path)
            return custom_path
        custom_path = os.path.join(ffmpeg_dir, FFMPEG_FILENAME)
        if os.path.isfile(custom_path):
            logger.info("使用指定目录的 ffmpeg: %s", custom_path)
            return custom_path
        logger.error("在指定目录未找到 ffmpeg.exe: %s", ffmpeg_dir)
        logger.error("请确认路径正确，ffmpeg.exe 应位于 bin\\ 子目录或直接在该目录下")
        sys.exit(1)

    system_path = shutil.which("ffmpeg")
    if system_path:
        logger.info("使用系统 PATH 中的 ffmpeg: %s", system_path)
        return system_path

    logger.error("未找到 ffmpeg！请通过 --ffmpeg-dir 参数指定本地 ffmpeg 目录")
    sys.exit(1)


def check_ffmpeg_version(ffmpeg_path: str) -> None:
    """检查 ffmpeg 版本信息"""
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, text=True, timeout=30,
        )
        first_line = result.stdout.split("\n")[0] if result.stdout else "未知"
        logger.info("ffmpeg 版本: %s", first_line)
    except Exception as e:
        logger.warning("无法获取 ffmpeg 版本: %s", e)


def run_pyinstaller(
    console: bool = False,
    clean: bool = False,
    exe_name: str = "AI推",
    icon_path: str | None = None,
    upx_dir: str | None = None,
) -> None:
    """运行 PyInstaller 打包"""
    logger.info("=" * 60)
    logger.info("开始打包 ClipSynth...")
    logger.info("=" * 60)

    if clean and BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        logger.info("已清理构建缓存: %s", BUILD_DIR)

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", exe_name,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(PROJECT_ROOT / "scripts"),
    ]

    if clean:
        pyinstaller_args.append("--clean")

    if not console:
        pyinstaller_args.append("--windowed")
    else:
        pyinstaller_args.append("--console")

    if icon_path and os.path.isfile(icon_path):
        pyinstaller_args.extend(["--icon", icon_path])

    if upx_dir:
        pyinstaller_args.extend(["--upx-dir", upx_dir])

    hidden_imports = [
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtWidgets",
        "PySide6.QtGui",
        "PySide6.QtNetwork",
        "qasync",
        "sqlalchemy",
        "sqlalchemy.ext.declarative",
        "sqlalchemy.orm",
        "sqlalchemy.sql",
        "aiofiles",
        "cv2",
        "PIL",
        "PIL.Image",
        "PIL.ImageQt",
        "requests",
        "pyJianYingDraft",
        "pkg_resources",
    ]
    for mod in hidden_imports:
        pyinstaller_args.extend(["--hidden-import", mod])

    collected_submodules = [
        "sqlalchemy",
        "cv2",
        "PIL",
    ]
    for mod in collected_submodules:
        pyinstaller_args.extend(["--collect-submodules", mod])

    collected_data = [
        "cv2",
        "PIL",
    ]
    for mod in collected_data:
        pyinstaller_args.extend(["--collect-data", mod])

    data_files = [
        (str(PROJECT_ROOT / "clip_synth/resources/styles/main.qss"), "clip_synth/resources/styles"),
        (str(PROJECT_ROOT / "clip_synth/resources/icons/icon.ico"), "clip_synth/resources/icons"),
        (str(PROJECT_ROOT / "clip_synth/resources/icons/icon.jpg"), "clip_synth/resources/icons"),
    ]
    
    try:
        import pyJianYingDraft
        from PyInstaller.utils.hooks import collect_data_files
        
        pyjianying_data = collect_data_files('pyJianYingDraft')
        if pyjianying_data:
            data_files.extend(pyjianying_data)
            logger.info("添加 pyJianYingDraft 资源文件: %d 个文件", len(pyjianying_data))
    except ImportError as e:
        logger.warning("无法导入 pyJianYingDraft 或 PyInstaller hooks: %s", e)
    for src, dst in data_files:
        pyinstaller_args.extend(["--add-data", f"{src};{dst}"])
    
    runtime_hook = PROJECT_ROOT / "scripts" / "runtime_hook_pyjianying.py"
    if runtime_hook.exists():
        pyinstaller_args.extend(["--runtime-hook", str(runtime_hook)])
        logger.info("添加运行时钩子: %s", runtime_hook)

    pyinstaller_args.append(str(MAIN_SCRIPT))

    pyinstaller_args = [arg for arg in pyinstaller_args if arg]

    logger.info("PyInstaller 命令:")
    logger.info("  %s", " ".join(pyinstaller_args))

    try:
        subprocess.check_call(pyinstaller_args)
        logger.info("PyInstaller 打包完成")
    except subprocess.CalledProcessError as e:
        logger.error("PyInstaller 打包失败: %s", e)
        sys.exit(1)


def copy_ffmpeg_to_dist(ffmpeg_path: str, exe_name: str) -> None:
    """将 ffmpeg.exe 和 ffprobe.exe 复制到打包输出目录"""
    dist_exe_dir = DIST_DIR / exe_name
    dist_exe_dir.mkdir(parents=True, exist_ok=True)
    dist_ffmpeg = dist_exe_dir / FFMPEG_FILENAME

    shutil.copy2(ffmpeg_path, dist_ffmpeg)
    logger.info("ffmpeg 已复制到: %s", dist_ffmpeg)

    ffmpeg_dir = os.path.dirname(ffmpeg_path)
    ffprobe_src = os.path.join(ffmpeg_dir, FFPROBE_FILENAME)
    if os.path.isfile(ffprobe_src):
        dist_ffprobe = dist_exe_dir / FFPROBE_FILENAME
        shutil.copy2(ffprobe_src, dist_ffprobe)
        logger.info("ffprobe 已复制到: %s", dist_ffprobe)

    size_mb = os.path.getsize(dist_ffmpeg) / (1024 * 1024)
    logger.info("ffmpeg 大小: %.1f MB", size_mb)


def verify_bundle(exe_name: str) -> None:
    """验证打包结果"""
    dist_exe_dir = DIST_DIR / exe_name
    exe_path = dist_exe_dir / f"{exe_name}.exe"

    if not exe_path.exists():
        logger.error("打包失败：未找到输出 exe 文件: %s", exe_path)
        sys.exit(1)

    ffmpeg_path = dist_exe_dir / FFMPEG_FILENAME
    if not ffmpeg_path.exists():
        logger.error("打包失败：未找到 ffmpeg.exe: %s", ffmpeg_path)
        sys.exit(1)

    ffprobe_path = dist_exe_dir / FFPROBE_FILENAME
    if not ffprobe_path.exists():
        logger.error("打包失败：未找到 ffprobe.exe: %s", ffprobe_path)
        sys.exit(1)

    exe_size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    ffmpeg_size_mb = os.path.getsize(ffmpeg_path) / (1024 * 1024)
    ffprobe_size_mb = os.path.getsize(ffprobe_path) / (1024 * 1024)
    total_items = len(list(dist_exe_dir.iterdir()))

    logger.info("=" * 60)
    logger.info("打包成功！")
    logger.info("  输出目录: %s", dist_exe_dir)
    logger.info("  主程序: %s (%.1f MB)", exe_path, exe_size_mb)
    logger.info("  ffmpeg: %.1f MB", ffmpeg_size_mb)
    logger.info("  ffprobe: %.1f MB", ffprobe_size_mb)
    logger.info("  文件总数: %d", total_items)
    logger.info("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ClipSynth 打包脚本 - 将应用和本地 ffmpeg 打包为 exe",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="显示控制台窗口（调试用，默认隐藏）",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="清理之前的构建缓存",
    )
    parser.add_argument(
        "--name",
        default="ClipSynth",
        help="指定输出 exe 名称（默认 ClipSynth）",
    )
    parser.add_argument(
        "--icon",
        default=None,
        help=f"指定 exe 图标路径（默认 {DEFAULT_ICON_PATH}）",
    )
    parser.add_argument(
        "--ffmpeg-dir",
        default=r"D:\ffmpeg-8.0.1-essentials_build\ffmpeg-8.0.1-essentials_build",
        help="本地 ffmpeg 目录（默认 D:\\ffmpeg-8.0.1-essentials_build\\...）",
    )
    parser.add_argument(
        "--upx-dir",
        default=None,
        help="指定 UPX 压缩工具目录",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logger.info("ClipSynth 打包工具")
    logger.info("项目路径: %s", PROJECT_ROOT)

    check_python()
    ensure_pyinstaller()

    ffmpeg_path = find_ffmpeg(args.ffmpeg_dir)
    check_ffmpeg_version(ffmpeg_path)

    icon_path = get_icon_path(args.icon)

    run_pyinstaller(
        console=args.console,
        clean=args.clean,
        exe_name=args.name,
        icon_path=icon_path,
        upx_dir=args.upx_dir,
    )

    copy_ffmpeg_to_dist(ffmpeg_path, args.name)
    verify_bundle(args.name)

    logger.info("")
    logger.info("提示：运行 %s\\%s\\%s.exe 启动程序", DIST_DIR, args.name, args.name)


if __name__ == "__main__":
    main()
