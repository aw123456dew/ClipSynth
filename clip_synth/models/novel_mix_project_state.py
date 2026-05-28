from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NovelMixMaterialVideo:
    name: str
    path: str
    duration: float
    size_mb: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "duration": self.duration,
            "size_mb": self.size_mb,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NovelMixMaterialVideo":
        return cls(
            name=data.get("name", ""),
            path=data.get("path", ""),
            duration=data.get("duration", 0.0),
            size_mb=data.get("size_mb", 0.0),
        )


@dataclass
class NovelMixProjectState:
    id: str
    name: str
    material_folder: str = ""
    material_videos: List[NovelMixMaterialVideo] = field(default_factory=list)
    opening_folder: str = ""
    mix_folder: str = ""
    dub_mode: str = ""
    narration_text: str = ""
    voice_type: str = "BV700_V2_streaming"
    emotion: str = ""
    language: str = "cn"
    speed: float = 1.0
    pitch: float = 1.0
    volume: float = 1.0
    audio_files: List[dict] = field(default_factory=list)
    self_audio_path: str = ""
    self_subtitle_path: str = ""
    orientation: str = "landscape"
    aspect_ratio: str = "16:9"
    enable_subtitle: bool = False
    subtitle_font_size: int = 24
    subtitle_font_color: str = "#FFFFFF"
    subtitle_bg_color: str = "#000000"
    subtitle_bg_opacity: int = 50
    subtitle_position: str = "bottom"
    subtitle_offset_x: float = 0.5
    subtitle_offset_y: float = 0.9
    subtitle_font: str = "Microsoft YaHei"
    current_step: int = 0
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)
    extra_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_audio_duration(self) -> float:
        if self.dub_mode == "system":
            return sum(
                af.get("duration", 0)
                for af in self.audio_files
                if af.get("path")
            )
        return 0.0

    @property
    def resolution(self) -> tuple:
        ratios = {
            "16:9": (1920, 1080),
            "4:3": (1440, 1080),
            "21:9": (1920, 822),
            "1:1": (1080, 1080),
            "9:16": (1080, 1920),
            "3:4": (1080, 1440),
            "4:5": (1080, 1350),
        }
        return ratios.get(self.aspect_ratio, (1920, 1080))
