"""小说改写项目状态服务"""

import json
import logging
import time
import uuid
from pathlib import Path

from clip_synth.core.config import AppConfig

logger = logging.getLogger("clip_synth.novel_rewrite_state")


class NovelRewriteProjectState:
    def __init__(
        self,
        project_id: str = "",
        name: str = "",
        novel_path: str = "",
        chapters: list[dict] | None = None,
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ):
        self.id = project_id
        self.name = name
        self.novel_path = novel_path
        self.chapters = chapters or []
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "novel_path": self.novel_path,
            "chapters": self.chapters,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "NovelRewriteProjectState":
        return NovelRewriteProjectState(
            project_id=data.get("id", ""),
            name=data.get("name", ""),
            novel_path=data.get("novel_path", ""),
            chapters=data.get("chapters", []),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )


class NovelRewriteStateService:
    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            config = AppConfig()
            data_dir = config.data_dir / "novel_rewrite_projects"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("小说改写项目状态服务初始化，数据目录: %s", self.data_dir)

    def create_project(self, name: str) -> str:
        project_id = uuid.uuid4().hex[:12]
        project = NovelRewriteProjectState(project_id=project_id, name=name)
        self._save(project)
        logger.info("创建小说改写项目: %s (%s)", name, project_id)
        return project_id

    def get_project(self, project_id: str) -> NovelRewriteProjectState | None:
        path = self._project_path(project_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return NovelRewriteProjectState.from_dict(data)
        except Exception as e:
            logger.error("加载小说改写项目失败 %s: %s", project_id, e)
            return None

    def save_project(self, project: NovelRewriteProjectState) -> None:
        project.updated_at = time.time()
        self._save(project)

    def delete_project(self, project_id: str) -> None:
        path = self._project_path(project_id)
        if path.exists():
            path.unlink()
            logger.info("删除小说改写项目: %s", project_id)

    def list_projects(self) -> list[dict]:
        projects = []
        for f in sorted(self.data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                projects.append({
                    "id": data.get("id", f.stem),
                    "name": data.get("name", "未命名"),
                    "chapter_count": len(data.get("chapters", [])),
                    "created_at": data.get("created_at", 0.0),
                })
            except Exception as e:
                logger.warning("读取项目文件失败 %s: %s", f.name, e)
        return projects

    def _project_path(self, project_id: str) -> Path:
        return self.data_dir / f"{project_id}.json"

    def _save(self, project: NovelRewriteProjectState) -> None:
        path = self._project_path(project.id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)
