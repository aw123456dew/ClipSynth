from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AppConfig:
    app_name: str = "FrameCut"
    version: str = "0.1.0"
    data_dir: Path = field(default_factory=lambda: Path.home() / ".frame_cut")
    db_name: str = "frame_cut.db"
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
