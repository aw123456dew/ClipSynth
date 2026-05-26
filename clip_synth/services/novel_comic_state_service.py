import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from clip_synth.core.config import AppConfig
from clip_synth.models.novel_comic_project_state import (
    NovelComicAsset,
    NovelComicChapterState,
    NovelComicProjectState,
)

logger = logging.getLogger(__name__)


class NovelComicStateService:
    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            config = AppConfig()
            data_dir = config.data_dir / "novel_comic_projects"
        self.data_dir = Path(data_dir)
        self.projects_dir = self.data_dir
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        logger.info("小说转漫画项目状态服务初始化，数据目录: %s", self.projects_dir)

    def _get_project_file_path(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.json"

    def get_project_images_dir(self, project_id: str) -> Path:
        images_dir = self.data_dir / "images" / project_id
        images_dir.mkdir(parents=True, exist_ok=True)
        return images_dir

    def create_project(self, name: str) -> NovelComicProjectState:
        project_id = str(uuid4())
        project = NovelComicProjectState(
            id=project_id,
            name=name,
            chapters=[],
            created_at=time.time(),
            updated_at=time.time(),
        )
        self.save_project(project)
        self.get_project_images_dir(project_id)
        logger.info("创建新小说转漫画项目: %s (ID: %s)", name, project_id)
        return project

    def save_project(self, project: NovelComicProjectState) -> None:
        project.updated_at = time.time()
        file_path = self._get_project_file_path(project.id)

        data = {
            "id": project.id,
            "name": project.name,
            "chapters": [ch.to_dict() for ch in project.chapters],
            "assets": [a.to_dict() for a in project.assets],
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "extra_data": self._sanitize_extra_data(project.extra_data),
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _sanitize_extra_data(data: dict) -> dict:
        import re as _re
        def _clean(v):
            if isinstance(v, str):
                return _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
            if isinstance(v, dict):
                return {k: _clean(v) for k, v in v.items()}
            if isinstance(v, list):
                return [_clean(i) for i in v]
            return v
        return _clean(data)

    def load_project(self, project_id: str) -> Optional[NovelComicProjectState]:
        file_path = self._get_project_file_path(project_id)
        if not file_path.exists():
            return None

        try:
            raw = file_path.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error("项目 JSON 文件损坏，无法加载: %s - %s", file_path, str(e))
                return None

            chapters = [
                NovelComicChapterState.from_dict(ch)
                for ch in data.get("chapters", [])
            ]

            assets = [
                NovelComicAsset.from_dict(a)
                for a in data.get("assets", [])
            ]

            project = NovelComicProjectState(
                id=data["id"],
                name=data["name"],
                chapters=chapters,
                assets=assets,
                created_at=data.get("created_at", time.time()),
                updated_at=data.get("updated_at", time.time()),
                extra_data=data.get("extra_data", {}),
            )

            return project
        except Exception as e:
            logger.error("加载小说转漫画项目失败: %s", e, exc_info=True)
            return None

    def list_projects(self) -> list[NovelComicProjectState]:
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

        images_dir = self.data_dir / "images" / project_id
        if images_dir.exists():
            shutil.rmtree(images_dir)

        logger.info("删除小说转漫画项目: %s", project_id)
        return True

    def get_chat_history_path(self, project_id: str, episode_num: int) -> Path:
        chat_dir = self.get_project_images_dir(project_id) / "chat_history"
        chat_dir.mkdir(parents=True, exist_ok=True)
        return chat_dir / f"ep_{episode_num}.jsonl"

    def load_chat_history(self, project_id: str, episode_num: int) -> list[dict]:
        path = self.get_chat_history_path(project_id, episode_num)
        if not path.exists():
            return []
        result = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        result.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return result

    def save_chat_history(self, project_id: str, episode_num: int, messages: list[dict]) -> None:
        path = self.get_chat_history_path(project_id, episode_num)
        with open(path, "a", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
