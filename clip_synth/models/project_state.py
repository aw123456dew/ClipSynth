"""
项目状态模型，用于持久化智能剪辑项目的进度状态
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AnalysisMode(Enum):
    """AI视频分析模式"""
    FASTEST = "fastest"  # 极速：只用字幕，不分析画面
    QUICK = "quick"      # 快速：每10秒3帧（开头/中间/结尾）
    PRECISE = "precise"  # 精准：每10秒6帧（约1.7秒/帧）
    DEEP = "deep"        # 深度：每10秒10帧（每秒1帧）

    @property
    def display_name(self) -> str:
        names = {
            "fastest": "极速",
            "quick": "快速",
            "precise": "精准",
            "deep": "深度",
        }
        return names.get(self.value, self.value)

    @property
    def frames_per_slice(self) -> int:
        """每个10秒切片提取的帧数"""
        return {
            "fastest": 0,
            "quick": 3,
            "precise": 6,
            "deep": 10,
        }[self.value]

    @property
    def description(self) -> str:
        descs = {
            "fastest": "仅使用字幕分析，不分析画面内容，速度最快，适合对画面要求不高的场景",
            "quick": "每10秒提取3帧（开头/中间/结尾各1帧），平衡速度与画面分析质量",
            "precise": "每10秒提取6帧（约1.7秒/帧），画面分析更细致，推荐使用",
            "deep": "每秒提取1帧，画面分析最全面，适合对画面细节要求极高的场景",
        }
        return descs.get(self.value, "")


@dataclass
class VideoSegment:
    """视频片段数据类"""
    id: str
    type: str  # "gold_3s", "highlight", "plot", "ending"
    start_time: str  # HH:MM:SS格式
    end_time: str  # HH:MM:SS格式
    description: str
    selected: bool = False


@dataclass
class VideoProjectState:
    """单个视频的项目状态"""
    video_path: str
    subtitle_path: Optional[str] = None
    segments: Dict[str, List[VideoSegment]] = field(default_factory=dict)
    analysis_completed: bool = False
    analysis_mode: str = "fastest"

    def __post_init__(self):
        if not self.segments:
            self.segments = {
                "gold_3s": [],
                "highlight": [],
                "plot": [],
                "ending": []
            }

    def is_all_types_selected(self) -> bool:
        """检查是否所有类型都至少有一个片段（不再需要手动选择）"""
        return True


class ClippingMode(Enum):
    """剪辑模式"""
    MANUAL = "manual"
    AI = "ai"

    @property
    def display_name(self) -> str:
        return {"manual": "手动选择", "ai": "AI智能识别"}[self.value]


class ClippingStyle(Enum):
    """AI剪辑手法"""
    EMOTIONAL = "emotional"
    HUMOROUS = "humorous"
    LOGICAL = "logical"
    FAST_PACED = "fast_paced"

    @property
    def display_name(self) -> str:
        return {
            "emotional": "情感共鸣",
            "humorous": "搞笑幽默",
            "logical": "逻辑严谨",
            "fast_paced": "超快节奏",
        }[self.value]

    @property
    def description(self) -> str:
        return {
            "emotional": (
                "深入挖掘角色情感，通过细腻的情感表达引发观众共鸣，"
                "让观众与角色同悲同喜，获得情感上的触动与释放"
            ),
            "humorous": (
                "以轻松诙谐的方式解读剧情，挖掘笑点和梗，"
                "用幽默的语言风格让观众在欢笑中看完故事"
            ),
            "logical": (
                "严谨梳理剧情逻辑，清晰呈现事件因果，"
                "帮助观众理清复杂的人物关系和故事脉络"
            ),
            "fast_paced": (
                "快节奏、高能输出，简洁有力的语言风格，"
                "信息密度大，保持观众持续观看的好奇心和紧张感"
            ),
        }[self.value]


@dataclass
class SmartClippingProjectState:
    """智能剪辑项目状态"""
    id: str
    name: str
    cover_path: Optional[str] = None
    videos: List[VideoProjectState] = field(default_factory=list)
    current_step: int = 0
    clipping_style: str = "emotional"
    narration_language: str = "zh"
    original_sound_ratio: int = 0
    narration_speed: int = 3  # 目标语速（字/秒）
    ai_analysis_results: List[dict] = field(default_factory=list)
    narration_scripts: List[dict] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)

    def is_all_videos_ready(self) -> bool:
        """检查是否所有视频都已完成选择"""
        for video_state in self.videos:
            if not video_state.is_all_types_selected():
                return False
        return True
