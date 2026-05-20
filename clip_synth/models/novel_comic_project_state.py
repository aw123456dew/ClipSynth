from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class NovelComicChapterState:
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NovelComicChapterState":
        return cls(
            text=data.get("text", ""),
        )


@dataclass
class NovelComicAsset:
    name: str = ""
    desc: str = ""
    asset_type: str = ""
    image_path: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "desc": self.desc,
            "asset_type": self.asset_type,
            "image_path": self.image_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NovelComicAsset":
        return cls(
            name=data.get("name", ""),
            desc=data.get("desc", ""),
            asset_type=data.get("asset_type", ""),
            image_path=data.get("image_path", ""),
        )


@dataclass
class NovelComicProjectState:
    id: str
    name: str
    chapters: List[NovelComicChapterState] = field(default_factory=list)
    assets: List[NovelComicAsset] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: 0.0)
    updated_at: float = field(default_factory=lambda: 0.0)
    extra_data: Dict[str, Any] = field(default_factory=dict)
