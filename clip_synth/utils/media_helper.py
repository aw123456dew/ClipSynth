from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoInfo:
    file_path: str
    file_name: str
    file_size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None


def get_video_info(file_path: Path) -> VideoInfo:
    info = VideoInfo(
        file_path=str(file_path.absolute()),
        file_name=file_path.name,
        file_size=file_path.stat().st_size,
    )
    return info


def is_supported_media(file_path: Path) -> bool:
    supported_extensions = {
        ".mp4", ".avi", ".mov", ".mkv", ".wmv",
        ".jpg", ".jpeg", ".png", ".bmp", ".webp",
    }
    return file_path.suffix.lower() in supported_extensions
