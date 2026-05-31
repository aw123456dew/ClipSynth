import json
import logging
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from clip_synth.core.config import AppConfig
from clip_synth.models.novel_mix_project_state import (
    NovelMixMaterialVideo,
    NovelMixProjectState,
)

logger = logging.getLogger(__name__)


class NovelMixStateService:
    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            config = AppConfig()
            data_dir = config.data_dir / "novel_mix_projects"
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        logger.info("视频配音混剪项目状态服务初始化，数据目录: %s", self.projects_dir)

    def _get_project_file_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def create_project(self, name: str) -> NovelMixProjectState:
        project_id = str(uuid4())
        project = NovelMixProjectState(
            id=project_id,
            name=name,
            current_step=0,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.save_project(project)
        logger.info("创建新视频配音混剪项目: %s (ID: %s)", name, project_id)
        return project

    def save_project(self, project: NovelMixProjectState) -> None:
        project.updated_at = time.time()
        file_path = self._get_project_file_path(project.id)

        data = {
            "id": project.id,
            "name": project.name,
            "material_folder": project.material_folder,
            "material_videos": [mv.to_dict() for mv in project.material_videos],
            "opening_folder": project.opening_folder,
            "mix_folder": project.mix_folder,
            "dub_mode": project.dub_mode,
            "narration_text": project.narration_text,
            "voice_type": project.voice_type,
            "emotion": project.emotion,
            "language": project.language,
            "speed": project.speed,
            "pitch": project.pitch,
            "volume": project.volume,
            "audio_files": project.audio_files,
            "self_audio_path": project.self_audio_path,
            "self_subtitle_path": project.self_subtitle_path,
            "orientation": project.orientation,
            "aspect_ratio": project.aspect_ratio,
            "enable_subtitle": project.enable_subtitle,
            "subtitle_font_size": project.subtitle_font_size,
            "subtitle_font_color": project.subtitle_font_color,
            "subtitle_bg_color": project.subtitle_bg_color,
            "subtitle_bg_opacity": project.subtitle_bg_opacity,
            "subtitle_position": project.subtitle_position,
            "subtitle_offset_x": project.subtitle_offset_x,
            "subtitle_offset_y": project.subtitle_offset_y,
            "subtitle_font": project.subtitle_font,
            "current_step": project.current_step,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "extra_data": project.extra_data,
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_project(self, project_id: str) -> Optional[NovelMixProjectState]:
        file_path = self._get_project_file_path(project_id)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            material_videos = [
                NovelMixMaterialVideo.from_dict(mv)
                for mv in data.get("material_videos", [])
            ]

            project = NovelMixProjectState(
                id=data["id"],
                name=data["name"],
                material_folder=data.get("material_folder", ""),
                material_videos=material_videos,
                opening_folder=data.get("opening_folder", ""),
                mix_folder=data.get("mix_folder", ""),
                dub_mode=data.get("dub_mode", ""),
                narration_text=data.get("narration_text", ""),
                voice_type=data.get("voice_type", "BV700_V2_streaming"),
                emotion=data.get("emotion", ""),
                language=data.get("language", "cn"),
                speed=data.get("speed", 1.0),
                pitch=data.get("pitch", 1.0),
                volume=data.get("volume", 1.0),
                audio_files=data.get("audio_files", []),
                self_audio_path=data.get("self_audio_path", ""),
                self_subtitle_path=data.get("self_subtitle_path", ""),
                orientation=data.get("orientation", "landscape"),
                aspect_ratio=data.get("aspect_ratio", "16:9"),
                enable_subtitle=data.get("enable_subtitle", False),
                subtitle_font_size=data.get("subtitle_font_size", 24),
                subtitle_font_color=data.get("subtitle_font_color", "#FFFFFF"),
                subtitle_bg_color=data.get("subtitle_bg_color", "#000000"),
                subtitle_bg_opacity=data.get("subtitle_bg_opacity", 50),
                subtitle_position=data.get("subtitle_position", "bottom"),
                subtitle_offset_x=data.get("subtitle_offset_x", 0.5),
                subtitle_offset_y=data.get("subtitle_offset_y", 0.9),
                subtitle_font=data.get("subtitle_font", "Microsoft YaHei"),
                current_step=data.get("current_step", 0),
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
                extra_data=data.get("extra_data", {}),
            )

            return project
        except Exception as e:
            logger.error("加载视频配音混剪项目失败: %s", e, exc_info=True)
            return None

    def list_projects(self) -> list[NovelMixProjectState]:
        projects = []
        for file in self.projects_dir.glob("*.json"):
            project_id = file.stem
            project = self.load_project(project_id)
            if project:
                projects.append(project)
        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def delete_project(self, project_id: str) -> bool:
        file_path = self._get_project_file_path(project_id)
        if not file_path.exists():
            return False
        file_path.unlink()
        logger.info("删除视频配音混剪项目: %s", project_id)
        return True
