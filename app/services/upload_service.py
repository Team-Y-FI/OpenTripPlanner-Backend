import os
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile
from app.core.config import settings
from app.repositories.upload_repo import UploadRepository
from app.services.storage_service import get_storage_service
from app.services.geocoding_service import GeocodingService
from app.utils.exif import extract_exif_lat_lng_taken_at_bytes

def _allowed_exts() -> set[str]:
    raw = settings.UPLOAD_ALLOWED_EXTS or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

class UploadService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = UploadRepository(db)
        self.storage = get_storage_service()

    @staticmethod
    def limits() -> dict:
        return {
            "max_photos": settings.UPLOAD_MAX_PHOTOS,
            "max_file_size_mb": settings.UPLOAD_MAX_FILE_SIZE_MB,
            "allowed_exts": sorted(_allowed_exts()),
        }

    async def create_upload_with_photos(self, user_id: str | None, files: list[UploadFile], exif_required: bool = False):
        max_photos = settings.UPLOAD_MAX_PHOTOS
        if len(files) > max_photos:
            raise ValueError(f"max {max_photos} photos allowed")

        allowed_exts = _allowed_exts()
        max_bytes = settings.UPLOAD_MAX_FILE_SIZE_MB * 1024 * 1024

        prepared: list[tuple[UploadFile, bytes]] = []
        for f in files:
            filename = f.filename or ""
            ext = os.path.splitext(filename)[1].lower().lstrip(".")
            if not ext or (allowed_exts and ext not in allowed_exts):
                raise ValueError(f"unsupported file type: {ext or 'unknown'}")
            content = await f.read()
            if max_bytes and len(content) > max_bytes:
                raise ValueError(f"file too large: {filename}")
            prepared.append((f, content))

        upload = await self.repo.create_upload(user_id=user_id)

        geo_svc = GeocodingService()
        geo_enabled = bool(geo_svc.kakao_rest_api_key or geo_svc.google_api_key)

        photos_out = []
        for f, content in prepared:
            lat, lng, taken_at = extract_exif_lat_lng_taken_at_bytes(content)
            key = await self.storage.save_bytes(f.filename, content)

            status = "recognized" if lat is not None and lng is not None else "needs_manual"

            place_address = None
            if geo_enabled and lat is not None and lng is not None:
                try:
                    geo = await geo_svc.reverse_geocode(lat, lng)
                    place_address = geo.get("road_address") or geo.get("address")
                except Exception:
                    place_address = None

            photo = await self.repo.create_photo(
                upload_id=upload.upload_id,
                file_name=f.filename or key,
                storage_key=key,
                status=status,
                exif_lat=lat,
                exif_lng=lng,
                taken_at=taken_at,
                place_address=place_address,
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
