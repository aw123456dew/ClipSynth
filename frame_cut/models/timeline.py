import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from frame_cut.core.database import Base


class TimelineTrack(Base):
    __tablename__ = "timeline_tracks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(255), default="Track")
    track_type: Mapped[str] = mapped_column(String(32), default="video")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    project = relationship("Project", back_populates="timeline_tracks")
    segments = relationship(
        "TimelineSegment", back_populates="track", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TimelineTrack(id={self.id}, name='{self.name}')>"


class TimelineSegment(Base):
    __tablename__ = "timeline_segments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("timeline_tracks.id"))
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id"), nullable=True)
    start_position: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    track = relationship("TimelineTrack", back_populates="segments")

    def __repr__(self) -> str:
        return f"<TimelineSegment(id={self.id}, pos={self.start_position})>"
