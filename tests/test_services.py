
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clip_synth.core.database import Base
from clip_synth.services.media_service import MediaService
from clip_synth.services.project_service import ProjectService


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    sess = session_factory()
    yield sess
    sess.close()


class TestProjectService:
    def test_create_and_get_project(self, session):
        service = ProjectService(session)
        project = service.create_project("Test Project", "Description")

        assert project.id is not None
        assert project.name == "Test Project"

        fetched = service.get_project(project.id)
        assert fetched is not None
        assert fetched.name == "Test Project"

    def test_delete_project(self, session):
        service = ProjectService(session)
        project = service.create_project("To Delete")
        assert service.delete_project(project.id) is True
        assert service.get_project(project.id) is None


class TestMediaService:
    def test_import_media(self, session, tmp_path):
        service = MediaService(session)
        test_file = tmp_path / "test_video.mp4"
        test_file.write_text("fake video content")

        media = service.import_media(test_file)
        assert media.file_name == "test_video.mp4"
        assert media.file_size > 0
