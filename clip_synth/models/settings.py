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
    tencent_asr_secret_id: Mapped[str] = mapped_column(String(512), default="")
    tencent_asr_secret_key: Mapped[str] = mapped_column(String(512), default="")
    tencent_asr_region: Mapped[str] = mapped_column(String(64), default="ap-guangzhou")
    asr_provider: Mapped[str] = mapped_column(String(32), default="volcengine")
    image_model_name: Mapped[str] = mapped_column(String(255), default="")
    image_api_key: Mapped[str] = mapped_column(String(512), default="")
    image_base_url: Mapped[str] = mapped_column(String(1024), default="")
    image_api_type: Mapped[str] = mapped_column(String(32), default="openai")
    image_api_provider: Mapped[str] = mapped_column(String(32), default="newapi")
    image_models_json: Mapped[str] = mapped_column(Text, default="")


@dataclass
class AIModelSettings:
    model_name: str = ""
    api_key: str = ""
    base_url: str = ""
    api_type: str = "openai"
    api_provider: str = "newapi"

    @property
    def is_configured(self) -> bool:
        return bool(self.model_name and self.api_key and self.base_url)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "api_type": self.api_type,
            "api_provider": self.api_provider,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AIModelSettings":
        return cls(
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
            api_type=data.get("api_type", "openai"),
            api_provider=data.get("api_provider", "newapi"),
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
class TencentAsrSettings:
    secret_id: str = ""
    secret_key: str = ""
    region: str = "ap-guangzhou"

    @property
    def is_configured(self) -> bool:
        return bool(self.secret_id and self.secret_key)

    def to_dict(self) -> dict:
        return {
            "secret_id": self.secret_id,
            "secret_key": self.secret_key,
            "region": self.region,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TencentAsrSettings":
        return cls(
            secret_id=data.get("secret_id", ""),
            secret_key=data.get("secret_key", ""),
            region=data.get("region", "ap-guangzhou"),
        )


@dataclass
class NamedImageModelSettings:
    name: str = ""
    config: AIModelSettings = field(default_factory=AIModelSettings)

    def to_dict(self) -> dict:
        return {"name": self.name, "config": self.config.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> "NamedImageModelSettings":
        return cls(
            name=data.get("name", ""),
            config=AIModelSettings.from_dict(data.get("config", {})),
        )


@dataclass
class AppSettings:
    text_model: AIModelSettings = field(default_factory=AIModelSettings)
    vision_model: AIModelSettings = field(default_factory=AIModelSettings)
    image_model: AIModelSettings = field(default_factory=AIModelSettings)
    image_models: list[NamedImageModelSettings] = field(default_factory=list)
    draft_output_dir: str = ""
    doubao_voice: DoubaoVoiceSettings = field(default_factory=DoubaoVoiceSettings)
    tencent_asr: TencentAsrSettings = field(default_factory=TencentAsrSettings)
    asr_provider: str = "volcengine"
    tts_params: str = ""
    gpu_accel_enabled: bool = False

    def get_image_model_by_name(self, name: str) -> AIModelSettings:
        """按名称查找图片模型配置，找不到或名称为空时返回空配置"""
        if not name:
            return AIModelSettings()
        for m in self.image_models:
            if m.name == name:
                return m.config
        return AIModelSettings()

    def to_dict(self) -> dict:
        return {
            "text_model": self.text_model.to_dict(),
            "vision_model": self.vision_model.to_dict(),
            "image_model": self.image_model.to_dict(),
            "image_models": [m.to_dict() for m in self.image_models],
            "draft_output_dir": self.draft_output_dir,
            "doubao_voice": self.doubao_voice.to_dict(),
            "tencent_asr": self.tencent_asr.to_dict(),
            "asr_provider": self.asr_provider,
            "tts_params": self.tts_params,
            "gpu_accel_enabled": self.gpu_accel_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            text_model=AIModelSettings.from_dict(data.get("text_model", {})),
            vision_model=AIModelSettings.from_dict(data.get("vision_model", {})),
            image_model=AIModelSettings.from_dict(data.get("image_model", {})),
            image_models=[NamedImageModelSettings.from_dict(m) for m in data.get("image_models", [])],
            draft_output_dir=data.get("draft_output_dir", ""),
            doubao_voice=DoubaoVoiceSettings.from_dict(data.get("doubao_voice", {})),
            tencent_asr=TencentAsrSettings.from_dict(data.get("tencent_asr", {})),
            asr_provider=data.get("asr_provider", "volcengine"),
            tts_params=data.get("tts_params", ""),
            gpu_accel_enabled=data.get("gpu_accel_enabled", False),
        )
