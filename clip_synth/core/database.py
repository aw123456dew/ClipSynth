from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from clip_synth.core.config import AppConfig


class Base(DeclarativeBase):
    pass


class DatabaseManager:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            config = AppConfig()
            db_path = config.db_path
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
        
        self._migrate_database()

    def _migrate_database(self) -> None:
        with self._engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(settings)"))
            columns = [row[1] for row in result.fetchall()]
            
            if "doubao_access_key" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN doubao_access_key VARCHAR(512) DEFAULT ''"))
            if "doubao_secret_key" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN doubao_secret_key VARCHAR(512) DEFAULT ''"))
            if "doubao_app_id" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN doubao_app_id VARCHAR(255) DEFAULT ''"))
            if "doubao_token" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN doubao_token VARCHAR(1024) DEFAULT ''"))
            if "tts_params" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN tts_params TEXT DEFAULT ''"))
            if "gpu_accel_enabled" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN gpu_accel_enabled BOOLEAN DEFAULT 0"))
            if "tencent_asr_secret_id" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN tencent_asr_secret_id VARCHAR(512) DEFAULT ''"))
            if "tencent_asr_secret_key" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN tencent_asr_secret_key VARCHAR(512) DEFAULT ''"))
            if "tencent_asr_region" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN tencent_asr_region VARCHAR(64) DEFAULT 'ap-guangzhou'"))
            if "asr_provider" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN asr_provider VARCHAR(32) DEFAULT 'volcengine'"))
            if "image_model_name" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_model_name VARCHAR(255) DEFAULT ''"))
            if "image_api_key" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_api_key VARCHAR(512) DEFAULT ''"))
            if "image_base_url" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_base_url VARCHAR(1024) DEFAULT ''"))
            if "image_api_type" not in columns:
                conn.execute(text("ALTER TABLE settings ADD COLUMN image_api_type VARCHAR(32) DEFAULT 'openai'"))

            conn.commit()

    @property
    def engine(self):
        assert self._engine is not None
        return self._engine

    def create_session(self):
        assert self._session_factory is not None
        return self._session_factory()
