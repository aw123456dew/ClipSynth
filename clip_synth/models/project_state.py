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
            "fastest": "仅使用字幕分析，速度最快",
            "quick": "每段3帧（开头/中间/结尾）",
            "precise": "每段6帧（约1.7秒/帧）",
            "deep": "每秒1帧，分析最全面",
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
    analysis_mode: str = "precise"

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
    HIGH_ENERGY = "high_energy"
    HOT_PRELUDE = "hot_prelude"
    GOLDEN_THREE = "golden_three"

    @property
    def display_name(self) -> str:
        return {
            "high_energy": "高燃混剪",
            "hot_prelude": "热点前置",
            "golden_three": "黄金三段",
        }[self.value]

    @property
    def description(self) -> str:
        return {
            "high_energy": (
                "AI智能识别视频高能瞬间，自动提取精彩片段，"
                "一键生成节奏紧凑、情绪炸裂的高燃混剪，瞬间点燃观众热情"
            ),
            "hot_prelude": (
                "用悬念、冲突或反转打造「黄金前3秒」，"
                "强势抓住注意力，迅速激发观看兴趣，再自然衔接原片完整剧情"
            ),
            "golden_three": (
                "高能开场 → 完整叙事 → 引流转化收尾，层层递进，"
                "兼顾吸引力与传播目标，提升完播与转化效率"
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
    clipping_mode: str = "manual"
    clipping_style: str = "high_energy"
    ai_analysis_results: List[dict] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)

    def is_all_videos_ready(self) -> bool:
        """检查是否所有视频都已完成选择"""
        for video_state in self.videos:
            if not video_state.is_all_types_selected():
                return False
        return True
