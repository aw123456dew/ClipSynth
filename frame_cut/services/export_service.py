from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ExportConfig:
    output_path: Path
    format: str = "mp4"
    codec: str = "h264"
    bitrate: str = "5000k"
    fps: int = 30
    resolution: tuple[int, int] = (1920, 1080)
    include_audio: bool = True


class ExportService:
    def __init__(self):
        self._is_exporting = False

    async def export_video(
        self,
        config: ExportConfig,
        progress_callback: Callable[[float], None] | None = None,
    ) -> Path:
        self._is_exporting = True
        try:
            if progress_callback:
                progress_callback(0.0)

            config.output_path.parent.mkdir(parents=True, exist_ok=True)

            if progress_callback:
                progress_callback(100.0)

            return config.output_path
        finally:
            self._is_exporting = False

    def cancel_export(self) -> None:
        self._is_exporting = False

    @property
    def is_exporting(self) -> bool:
        return self._is_exporting
