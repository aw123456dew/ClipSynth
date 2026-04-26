from dataclasses import dataclass, field

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from frame_cut.core.database import Base


class SettingsModel(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    text_model_name: Mapped[str] = mapped_column(String(255), default="")
    text_api_key: Mapped[str] = mapped_column(String(512), default="")
    text_base_url: Mapped[str] = mapped_column(String(1024), default="")
    vision_model_name: Mapped[str] = mapped_column(String(255), default="")
    vision_api_key: Mapped[str] = mapped_column(String(512), default="")
    vision_base_url: Mapped[str] = mapped_column(String(1024), default="")
    draft_output_dir: Mapped[str] = mapped_column(Text, default="")


@dataclass
class AIModelSettings:
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.model_name and self.api_key and self.base_url)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AIModelSettings":
        return cls(
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
        )


@dataclass
class AppSettings:
    text_model: AIModelSettings = field(default_factory=AIModelSettings)
    vision_model: AIModelSettings = field(default_factory=AIModelSettings)
    draft_output_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "text_model": self.text_model.to_dict(),
            "vision_model": self.vision_model.to_dict(),
            "draft_output_dir": self.draft_output_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            text_model=AIModelSettings.from_dict(data.get("text_model", {})),
            vision_model=AIModelSettings.from_dict(data.get("vision_model", {})),
            draft_output_dir=data.get("draft_output_dir", ""),
        )
