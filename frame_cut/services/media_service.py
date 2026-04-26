from pathlib import Path

from sqlalchemy.orm import Session

from frame_cut.models.media import MediaFile


class MediaService:
    def __init__(self, session: Session):
        self._session = session

    def import_media(self, file_path: Path) -> MediaFile:
        media = MediaFile(
            file_name=file_path.name,
            file_path=str(file_path.absolute()),
            file_size=file_path.stat().st_size,
        )
        self._session.add(media)
        self._session.commit()
        return media

    def get_media(self, media_id: int) -> MediaFile | None:
        return self._session.query(MediaFile).filter(MediaFile.id == media_id).first()

    def get_all_media(self) -> list[MediaFile]:
        return self._session.query(MediaFile).order_by(MediaFile.created_at.desc()).all()

    def delete_media(self, media_id: int) -> bool:
        media = self.get_media(media_id)
        if media is None:
            return False
        self._session.delete(media)
        self._session.commit()
        return True

    def update_media_info(
        self,
        media_id: int,
        duration: float | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
    ) -> MediaFile | None:
        media = self.get_media(media_id)
        if media is None:
            return None
        if duration is not None:
            media.duration = duration
        if width is not None:
            media.width = width
        if height is not None:
            media.height = height
        if fps is not None:
            media.fps = fps
        self._session.commit()
        return media
