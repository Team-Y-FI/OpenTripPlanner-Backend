from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import AppError
from app.models.record import Spot
from app.repositories.record_repo import RecordRepository
from app.repositories.upload_repo import UploadRepository

class RecordService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.records = RecordRepository(db)
        self.uploads = UploadRepository(db)

    async def create_spots_from_upload(self, user_id: str, upload_id: str, items: list[dict]):
        upload = await self.uploads.get_upload(upload_id)
        if not upload:
            raise AppError("not_found", "Upload not found", 404)
        if upload.user_id and upload.user_id != user_id:
            raise AppError("not_found", "Upload not found", 404)

        photos = await self.uploads.list_photos(upload_id)
        if not photos:
            raise AppError("not_found", "Upload photos not found", 404)

        # Spec: 미완료사진존재 -> 409
        if any(p.status in ("processing", "needs_manual") for p in photos):
            raise AppError("conflict", "Incomplete photos exist", 409)

        photo_ids = {p.photo_id for p in photos}
        spots = []
        for it in items:
            if it.get("photo_id") not in photo_ids:
                raise AppError("not_found", "Photo not found in upload", 404)
            place = it.get("place") or {}
            if not place.get("name") or not place.get("category") or place.get("lat") is None or place.get("lng") is None:
                raise AppError("bad_request", "Invalid place", 400)

            memo = it.get("memo")
            if isinstance(memo, str) and not memo.strip():
                memo = None

            spots.append(
                Spot(
                    user_id=user_id,
                    photo_id=it.get("photo_id"),
                    name=place["name"],
                    address=place.get("address"),
                    category=place["category"],
                    lat=place["lat"],
                    lng=place["lng"],
                    visited_at=it["visited_at"],
                    memo=memo,
                )
            )

        return await self.records.create_spots(spots)

    async def list_spots(self, user_id: str, q: str | None, category: str | None, limit: int):
        return await self.records.list_spots(user_id, q=q, category=category, limit=limit)

    async def get_spot(self, user_id: str, spot_id: str):
        spot = await self.records.get_spot(user_id, spot_id)
        if not spot:
            raise AppError("not_found", "Spot not found", 404)
        return spot

    async def update_spot(self, user_id: str, spot_id: str, memo: str | None, tags: list[str] | None):
        if not await self.records.get_spot(user_id, spot_id):
            raise AppError("not_found", "Spot not found", 404)
        await self.records.update_spot(user_id, spot_id, memo=memo, tags=tags)

    async def delete_spot(self, user_id: str, spot_id: str):
        if not await self.records.get_spot(user_id, spot_id):
            raise AppError("not_found", "Spot not found", 404)
        await self.records.delete_spot(user_id, spot_id)
