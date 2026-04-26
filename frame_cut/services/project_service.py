from pathlib import Path

from sqlalchemy.orm import Session

from frame_cut.models.project import Project


class ProjectService:
    def __init__(self, session: Session):
        self._session = session

    def create_project(self, name: str, description: str | None = None) -> Project:
        project = Project(name=name, description=description)
        self._session.add(project)
        self._session.commit()
        return project

    def get_project(self, project_id: int) -> Project | None:
        return self._session.query(Project).filter(Project.id == project_id).first()

    def get_all_projects(self) -> list[Project]:
        return self._session.query(Project).order_by(Project.updated_at.desc()).all()

    def update_project(
        self, project_id: int, name: str | None = None, description: str | None = None
    ) -> Project | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        self._session.commit()
        return project

    def delete_project(self, project_id: int) -> bool:
        project = self.get_project(project_id)
        if project is None:
            return False
        self._session.delete(project)
        self._session.commit()
        return True

    def save_project_file(self, project: Project, file_path: Path) -> None:
        project.file_path = str(file_path.absolute())
        self._session.commit()
