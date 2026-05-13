from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from clip_synth.models.project_state import VideoProjectState


@dataclass
class NarrateProjectState:
    """短剧解说项目状态"""
    id: str
    name: str
    cover_path: Optional[str] = None
    videos: List[VideoProjectState] = field(default_factory=list)
    current_step: int = 0
    version: int = 1
    extra_data: Dict[str, Any] = field(default_factory=dict)
    clipping_style: str = "emotional"
    narration_language: str = "zh"
    original_sound_ratio: int = 0
    narration_speed: int = 3  # 目标语速（字/秒）
    ai_analysis_results: List[dict] = field(default_factory=list)
    narration_scripts: List[dict] = field(default_factory=list)
    audio_files: List[dict] = field(default_factory=list)
    tts_engine: str = "doubao"  # doubao / custom
    custom_audio_files: List[dict] = field(default_factory=list)  # [{text, audio_path, subtitle_path}]
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)
    
    # 字幕相关设置
    enable_subtitle: bool = False
    subtitle_font_size: int = 24
    subtitle_font_color: str = "#FFFFFF"
    subtitle_bg_color: str = "#000000"
    subtitle_bg_opacity: int = 50
    subtitle_position: str = "bottom"  # top, middle, bottom
    subtitle_offset_x: float = 0.5
    subtitle_offset_y: float = 0.9
    subtitle_font: str = "Microsoft YaHei"
    
    # 移除字幕功能
    enable_remove_subtitle: bool = False
    
    # 遮罩区域设置（百分比）
    mask_x: float = 0.2
    mask_y: float = 0.85
    mask_width: float = 0.6
    mask_height: float = 0.1
    mask_blur_radius: int = 20
    mask_feather: int = 5  # 羽化值

    # 分集模式
    episode_mode: bool = False
    episode_count: int = 1
    episode_summaries: List[str] = field(default_factory=list)

    def is_all_videos_ready(self) -> bool:
        for video_state in self.videos:
            if not video_state.is_all_types_selected():
                return False
        return True
