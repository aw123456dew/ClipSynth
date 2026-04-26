import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clip_synth.core.database import Base


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_files.id"))
    name: Mapped[str] = mapped_column(String(255), default="New Clip")
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    project = relationship("Project", back_populates="clips")
    media_file = relationship("MediaFile", back_populates="clips")

    def __repr__(self) -> str:
        return f"<Clip(id={self.id}, name='{self.name}', {self.start_time}-{self.end_time})>"
