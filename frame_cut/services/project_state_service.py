"""
项目状态服务，负责持久化和管理智能剪辑项目的状态
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, Optional

from frame_cut.models import (
    SmartClippingProjectState,
    VideoProjectState,
    VideoSegment,
)

logger = logging.getLogger(__name__)


class ProjectStateService:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir / "smart_clipping_projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"项目状态服务初始化，数据目录: {self.projects_dir}")

    def _get_project_file_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def create_project(
        self,
        video_paths: list[str],
        subtitles: Dict[str, str],
        name: Optional[str] = None,
        cover_path: Optional[str] = None,
    ) -> SmartClippingProjectState:
        """创建新的智能剪辑项目"""
        from uuid import uuid4
        project_id = str(uuid4())
        project_name = name or f"智能剪辑项目_{time.strftime('%Y%m%d_%H%M%S')}"

        videos = []
        for video_path in video_paths:
            subtitle_path = subtitles.get(video_path)
            video_state = VideoProjectState(
                video_path=video_path,
                subtitle_path=subtitle_path,
            )
            videos.append(video_state)

        project = SmartClippingProjectState(
            id=project_id,
            name=project_name,
            cover_path=cover_path,
            videos=videos,
            current_step=0,
            created_at=time.time(),
            updated_at=time.time(),
        )

        self.save_project(project)
        logger.info(f"创建新项目: {project_name} (ID: {project_id})")
        return project

    def save_project(self, project: SmartClippingProjectState) -> None:
        """保存项目状态到文件"""
        project.updated_at = time.time()
        file_path = self._get_project_file_path(project.id)

        data = {
            "id": project.id,
            "name": project.name,
            "cover_path": project.cover_path,
            "videos": [],
            "current_step": project.current_step,
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

        logger.debug(f"项目状态已保存: {file_path}")

    def load_project(self, project_id: str) -> Optional[SmartClippingProjectState]:
        """从文件加载项目状态"""
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

            project = SmartClippingProjectState(
                id=data["id"],
                name=data["name"],
                cover_path=data.get("cover_path"),
                videos=videos,
                current_step=data.get("current_step", 0),
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
            )

            logger.debug(f"项目状态已加载: {project.name}")
            return project
        except Exception as e:
            logger.error(f"加载项目失败: {e}", exc_info=True)
            return None

    def list_projects(self) -> list[SmartClippingProjectState]:
        """列出所有项目"""
        projects = []
        for file in self.projects_dir.glob("*.json"):
            project_id = file.stem
            project = self.load_project(project_id)
            if project:
                projects.append(project)
        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def delete_project(self, project_id: str) -> bool:
        """删除项目及其相关缓存"""
        file_path = self._get_project_file_path(project_id)
        if not file_path.exists():
            return False

        project = self.load_project(project_id)
        file_path.unlink()

        if project:
            self._cleanup_project_cache(project)

        logger.info(f"删除项目: {project_id}")
        return True

    def _cleanup_project_cache(self, project: SmartClippingProjectState) -> None:
        """清理项目的缓存文件"""
        cache_root = Path(__file__).resolve().parent.parent.parent / "cache"

        preprocessed_dir = cache_root / "preprocessed"
        frame_desc_dir = cache_root / "frame_descriptions"

        for video_state in project.videos:
            video_name = Path(video_state.video_path).name

            stem = Path(video_name).stem
            suffix = Path(video_name).suffix
            preprocessed_path = preprocessed_dir / f"{stem}_5fps{suffix}"
            if preprocessed_path.exists():
                preprocessed_path.unlink()
                logger.debug("已删除预处理缓存: %s", preprocessed_path)

            safe_name = re.sub(r'[<>:"/\\|?*]', "_", video_name)
            if safe_name.endswith(".mp4"):
                safe_name = safe_name[:-4]
            frame_cache_dir = frame_desc_dir / safe_name
            if frame_cache_dir.exists():
                import shutil
                shutil.rmtree(frame_cache_dir)
                logger.debug("已删除画面描述缓存: %s", frame_cache_dir)
