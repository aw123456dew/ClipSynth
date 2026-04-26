import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clip_synth.core.database import Base
from clip_synth.models.clip import Clip
from clip_synth.models.media import MediaFile
from clip_synth.models.project import Project
from clip_synth.models.timeline import TimelineSegment, TimelineTrack


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    sess = session_factory()
    yield sess
    sess.close()


class TestProject:
    def test_create_project(self, session):
        project = Project(name="Test Project", description="A test project")
        session.add(project)
        session.commit()

        saved = session.query(Project).first()
        assert saved.name == "Test Project"
        assert saved.description == "A test project"
        assert saved.id is not None

    def test_project_defaults(self, session):
        project = Project()
        session.add(project)
        session.commit()

        assert project.name == "Untitled Project"
        assert project.description is None


class TestMediaFile:
    def test_create_media(self, session):
        media = MediaFile(
            file_name="test.mp4",
            file_path="/path/to/test.mp4",
            file_size=1024000,
            duration=120.5,
            width=1920,
            height=1080,
            fps=30.0,
        )
        session.add(media)
        session.commit()

        saved = session.query(MediaFile).first()
        assert saved.file_name == "test.mp4"
        assert saved.duration == 120.5


class TestClip:
    def test_create_clip(self, session):
        project = Project(name="Test")
        media = MediaFile(file_name="test.mp4", file_path="/path/to/test.mp4")
        session.add_all([project, media])
        session.commit()

        clip = Clip(
            project_id=project.id,
            media_file_id=media.id,
            name="Test Clip",
            start_time=10.0,
            end_time=30.0,
        )
        session.add(clip)
        session.commit()

        saved = session.query(Clip).first()
        assert saved.name == "Test Clip"
        assert saved.start_time == 10.0
        assert saved.end_time == 30.0
        assert saved.project.id == project.id


class TestTimeline:
    def test_create_track_with_segments(self, session):
        project = Project(name="Test")
        session.add(project)
        session.commit()

        track = TimelineTrack(
            project_id=project.id,
            name="Video Track",
            track_type="video",
            order_index=0,
        )
        session.add(track)
        session.commit()

        segment = TimelineSegment(
            track_id=track.id,
            start_position=0.0,
            duration=10.0,
            order_index=0,
        )
        session.add(segment)
        session.commit()

        saved_track = session.query(TimelineTrack).first()
        assert len(saved_track.segments) == 1
        assert saved_track.segments[0].duration == 10.0
