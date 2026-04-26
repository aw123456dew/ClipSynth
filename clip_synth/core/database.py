from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class DatabaseManager:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".clip_synth" / "clip_synth.db"
        self._db_path = db_path
        self._engine = None
        self._session_factory = None

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(
            f"sqlite:///{self._db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        self._session_factory = sessionmaker(bind=self._engine)

        import clip_synth.models  # noqa: F401
        Base.metadata.create_all(self._engine)

    @property
    def engine(self):
        assert self._engine is not None
        return self._engine

    def create_session(self):
        assert self._session_factory is not None
        return self._session_factory()
