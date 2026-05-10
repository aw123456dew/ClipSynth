from dataclasses import dataclass, field
import json

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from clip_synth.core.database import Base


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
    doubao_access_key: Mapped[str] = mapped_column(String(512), default="")
    doubao_secret_key: Mapped[str] = mapped_column(String(512), default="")
    doubao_app_id: Mapped[str] = mapped_column(String(255), default="")
    doubao_token: Mapped[str] = mapped_column(String(1024), default="")
    tts_params: Mapped[str] = mapped_column(Text, default="")
    gpu_accel_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


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
class DoubaoVoiceSettings:
    access_key: str = ""
    secret_key: str = ""
    app_id: str = ""
    token: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key and self.secret_key and self.app_id and self.token)

    def to_dict(self) -> dict:
        return {
            "access_key": self.access_key,
            "secret_key": self.secret_key,
            "app_id": self.app_id,
            "token": self.token,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DoubaoVoiceSettings":
        return cls(
            access_key=data.get("access_key", ""),
            secret_key=data.get("secret_key", ""),
            app_id=data.get("app_id", ""),
            token=data.get("token", ""),
        )


@dataclass
class AppSettings:
    text_model: AIModelSettings = field(default_factory=AIModelSettings)
    vision_model: AIModelSettings = field(default_factory=AIModelSettings)
    draft_output_dir: str = ""
    doubao_voice: DoubaoVoiceSettings = field(default_factory=DoubaoVoiceSettings)
    tts_params: str = ""
    gpu_accel_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "text_model": self.text_model.to_dict(),
            "vision_model": self.vision_model.to_dict(),
            "draft_output_dir": self.draft_output_dir,
            "doubao_voice": self.doubao_voice.to_dict(),
            "tts_params": self.tts_params,
            "gpu_accel_enabled": self.gpu_accel_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            text_model=AIModelSettings.from_dict(data.get("text_model", {})),
            vision_model=AIModelSettings.from_dict(data.get("vision_model", {})),
            draft_output_dir=data.get("draft_output_dir", ""),
            doubao_voice=DoubaoVoiceSettings.from_dict(data.get("doubao_voice", {})),
            tts_params=data.get("tts_params", ""),
            gpu_accel_enabled=data.get("gpu_accel_enabled", False),
        )
