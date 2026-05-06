from dataclasses import dataclass, field
from pathlib import Path
import sys
import os


def _get_data_dir() -> Path:
    """获取数据目录
    打包后运行时使用exe所在目录，开发环境使用用户home目录
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        return exe_dir / f"{exe_dir.name}_data"
    return Path.home() / ".clip_synth"


@dataclass
class AppConfig:
    app_name: str = "ClipSynth"
    version: str = "0.1.0"
    data_dir: Path = field(default_factory=_get_data_dir)
    db_name: str = "clip_synth.db"
    log_level: str = "INFO"

    supported_video_formats: tuple = (".mp4", ".avi", ".mov", ".mkv", ".wmv")
    supported_image_formats: tuple = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

    max_recent_files: int = 10
    thumbnail_size: tuple = (160, 90)
    preview_frame_interval: int = 30

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"
