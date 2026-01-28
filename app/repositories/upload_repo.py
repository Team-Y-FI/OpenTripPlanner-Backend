from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.upload import UploadSession, Photo

class UploadRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_upload(self, user_id: str | None) -> UploadSession:
        up = UploadSession(user_id=user_id)
        self.db.add(up)
        await self.db.commit()
        await self.db.refresh(up)
        return up

    async def get_upload(self, upload_id: str) -> UploadSession | None:
        res = await self.db.execute(select(UploadSession).where(UploadSession.upload_id == upload_id))
        return res.scalar_one_or_none()

    async def create_photo(
        self,
        upload_id: str,
        file_name: str,
        storage_key: str,
        status: str,
        exif_lat: float | None,
        exif_lng: float | None,
        taken_at,
    ) -> Photo:
        ph = Photo(
            upload_id=upload_id,
            file_name=file_name,
            storage_path=storage_key,  # keep column name, store relative key
            status=status,
            exif_lat=exif_lat,
            exif_lng=exif_lng,
            taken_at=taken_at,
        )
        self.db.add(ph)
        await self.db.commit()
        await self.db.refresh(ph)
        return ph

    async def list_photos(self, upload_id: str) -> list[Photo]:
        res = await self.db.execute(select(Photo).where(Photo.upload_id == upload_id))
        return list(res.scalars().all())

    async def list_photos_by_ids(self, photo_ids: list[str]) -> list[Photo]:
        if not photo_ids:
            return []
        res = await self.db.execute(select(Photo).where(Photo.photo_id.in_(photo_ids)))
        return list(res.scalars().all())

    async def get_photo(self, photo_id: str) -> Photo | None:
        res = await self.db.execute(select(Photo).where(Photo.photo_id == photo_id))
        return res.scalar_one_or_none()

    async def update_photo_place(self, photo_id: str, place: dict) -> None:
        stmt = (
            update(Photo)
            .where(Photo.photo_id == photo_id)
            .values(
                place_name=place.get("name"),
                place_address=place.get("address"),
                place_category=place.get("category"),
                place_lat=place.get("lat"),
                place_lng=place.get("lng"),
                status="recognized",
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()
