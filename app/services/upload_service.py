import os
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile
from app.repositories.upload_repo import UploadRepository
from app.services.storage_service import LocalStorageService
from app.utils.exif import extract_exif_lat_lng_taken_at

MAX_PHOTOS = 20

class UploadService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UploadRepository(db)
        self.storage = LocalStorageService()

    async def create_upload_with_photos(self, user_id: str | None, files: list[UploadFile], exif_required: bool = False):
        if len(files) > MAX_PHOTOS:
            raise ValueError("max 20 photos allowed")
        upload = await self.repo.create_upload(user_id=user_id)

        photos_out = []
        for f in files:
            key = await self.storage.save(f)
            abs_path = os.path.join(self.storage_dir(), key)
            lat, lng, taken_at = extract_exif_lat_lng_taken_at(abs_path)

            if lat is not None and lng is not None:
                status = "recognized"
            else:
                status = "needs_manual" if exif_required else "needs_manual"

            photo = await self.repo.create_photo(
                upload_id=upload.upload_id,
                file_name=f.filename or key,
                storage_key=key,
                status=status,
                exif_lat=lat,
                exif_lng=lng,
                taken_at=taken_at,
            )
            photos_out.append(photo)

        return upload, photos_out

    async def get_upload_status(self, user_id: str | None, upload_id: str):
        upload = await self.repo.get_upload(upload_id)
        if not upload:
            return None, None
        # owner check (if user_id provided)
        if user_id and upload.user_id and upload.user_id != user_id:
            return None, None

        photos = await self.repo.list_photos(upload_id)
        return upload, photos

    async def update_photo_place(self, user_id: str | None, photo_id: str, place_data: dict):
        photo = await self.repo.get_photo(photo_id)
        if not photo:
            return None
        # ownership via upload
        upload = await self.repo.get_upload(photo.upload_id)
        if user_id and upload and upload.user_id and upload.user_id != user_id:
            return None

        await self.repo.update_photo_place(photo_id, place_data)
        return await self.repo.get_photo(photo_id)

    def storage_dir(self) -> str:
        # keep a single source of truth
        from app.core.config import settings
        return settings.STORAGE_DIR
