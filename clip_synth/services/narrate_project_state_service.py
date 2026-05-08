import json
import logging
import time
from pathlib import Path
from typing import Optional

from clip_synth.core.config import AppConfig
from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.models.project_state import VideoProjectState, VideoSegment

logger = logging.getLogger(__name__)


class NarrateProjectStateService:
    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            config = AppConfig()
            data_dir = config.data_dir / "narrate_projects"
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"解说项目状态服务初始化，数据目录: {self.projects_dir}")

    def _get_project_file_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def create_project(
        self,
        video_paths: list[str],
        name: Optional[str] = None,
        cover_path: Optional[str] = None,
    ) -> NarrateProjectState:
        from uuid import uuid4
        project_id = str(uuid4())
        project_name = name or f"解说项目_{time.strftime('%Y%m%d_%H%M%S')}"

        videos = [
            VideoProjectState(video_path=video_path)
            for video_path in video_paths
        ]

        project = NarrateProjectState(
            id=project_id,
            name=project_name,
            cover_path=cover_path,
            videos=videos,
            current_step=0,
            created_at=time.time(),
            updated_at=time.time(),
        )

        self.save_project(project)
        logger.info(f"创建新解说项目: {project_name} (ID: {project_id})")
        return project

    def save_project(self, project: NarrateProjectState) -> None:
        project.updated_at = time.time()
        file_path = self._get_project_file_path(project.id)

        data = {
            "id": project.id,
            "name": project.name,
            "cover_path": project.cover_path,
            "videos": [],
            "current_step": project.current_step,
            "clipping_style": project.clipping_style,
            "narration_language": project.narration_language,
            "original_sound_ratio": project.original_sound_ratio,
            "ai_analysis_results": project.ai_analysis_results,
            "narration_scripts": project.narration_scripts,
            "audio_files": project.audio_files,
            "tts_engine": project.tts_engine,
            "custom_audio_files": project.custom_audio_files,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
        }

        for video_state in project.videos:
            video_data = {
                "video_path": video_state.video_path,
                "subtitle_path": video_state.subtitle_path,
                "segments": {},
                "analysis_completed": video_state.analysis_completed,
            }

            for seg_type, segs in video_state.segments.items():
                video_data["segments"][seg_type] = [
                    {
                        "id": seg.id,
                        "type": seg.type,
                        "start_time": seg.start_time,
                        "end_time": seg.end_time,
                        "description": seg.description,
                        "selected": seg.selected,
                    }
                    for seg in segs
                ]

            data["videos"].append(video_data)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.debug(f"解说项目状态已保存: {file_path}")

    def load_project(self, project_id: str) -> Optional[NarrateProjectState]:
        file_path = self._get_project_file_path(project_id)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            videos = []
            for video_data in data.get("videos", []):
                segments = {}
                for seg_type, segs_data in video_data.get("segments", {}).items():
                    segments[seg_type] = [
                        VideoSegment(
                            id=seg["id"],
                            type=seg["type"],
                            start_time=seg["start_time"],
                            end_time=seg["end_time"],
                            description=seg["description"],
                            selected=seg.get("selected", False),
                        )
                        for seg in segs_data
                    ]

                video_state = VideoProjectState(
                    video_path=video_data["video_path"],
                    subtitle_path=video_data.get("subtitle_path"),
                    segments=segments,
                    analysis_completed=video_data.get("analysis_completed", False),
                )
                videos.append(video_state)

            project = NarrateProjectState(
                id=data["id"],
                name=data["name"],
                cover_path=data.get("cover_path"),
                videos=videos,
                current_step=data.get("current_step", 0),
                clipping_style=data.get("clipping_style", "emotional"),
                narration_language=data.get("narration_language", "zh"),
                original_sound_ratio=data.get("original_sound_ratio", 0),
                ai_analysis_results=data.get("ai_analysis_results", []),
                narration_scripts=data.get("narration_scripts", []),
                audio_files=data.get("audio_files", []),
                tts_engine=data.get("tts_engine", "doubao"),
                custom_audio_files=data.get("custom_audio_files", []),
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
            )

            logger.debug(f"解说项目状态已加载: {project.name}")
            return project
        except Exception as e:
            logger.error(f"加载解说项目失败: {e}", exc_info=True)
            return None

    def list_projects(self) -> list[NarrateProjectState]:
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
        logger.info(f"删除解说项目: {project_id}")
        return True
