from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.core.exceptions import AppError
from app.schemas.upload import PlaceIn
from app.services.upload_service import UploadService
from app.services.geocoding_service import GeocodingService
from app.services.storage_service import get_storage_service

router = APIRouter()

@router.post("/uploads/photos")
async def upload_photos(
    files: list[UploadFile] = File(...),
    exif_required: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    svc = UploadService(db)
    storage = get_storage_service()

    try:
        upload, photos = await svc.create_upload_with_photos(user.user_id, files, exif_required=exif_required)
    except ValueError as e:
        raise AppError("bad_request", str(e), 400)

    def photo_out(p):
        exif = None
        if p.exif_lat is not None and p.exif_lng is not None:
            exif = {
                "lat": p.exif_lat,
                "lng": p.exif_lng,
                "taken_at": p.taken_at.isoformat().replace("+00:00", "Z") if p.taken_at else None,
            }
        place = None
        if p.place_name and p.place_category and p.place_lat is not None and p.place_lng is not None:
            place = {
                "name": p.place_name,
                "address": p.place_address,
                "category": p.place_category,
                "lat": p.place_lat,
                "lng": p.place_lng,
            }
        return {
            "photo_id": p.photo_id,
            "file_name": p.file_name,
            "status": p.status,
            "exif": exif,
            "place": place,
            "thumbnail_url": storage.url_for(p.storage_path),
        }

    return {
        "upload_id": upload.upload_id,
        "limits": UploadService.limits(),
        "photos": [photo_out(p) for p in photos],
    }

@router.get("/uploads/{upload_id}")
async def get_upload_status(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    svc = UploadService(db)
    storage = get_storage_service()

    upload, photos = await svc.get_upload_status(user.user_id, upload_id)
    if not upload:
        raise AppError("not_found", "Upload not found", 404)

    def photo_out(p):
        exif = None
        if p.exif_lat is not None and p.exif_lng is not None:
            exif = {
                "lat": p.exif_lat,
                "lng": p.exif_lng,
                "taken_at": p.taken_at.isoformat().replace("+00:00", "Z") if p.taken_at else None,
            }
        place = None
        if p.place_name and p.place_category and p.place_lat is not None and p.place_lng is not None:
            place = {
                "name": p.place_name,
                "address": p.place_address,
                "category": p.place_category,
                "lat": p.place_lat,
                "lng": p.place_lng,
            }
        return {
            "photo_id": p.photo_id,
            "file_name": p.file_name,
            "status": p.status,
            "exif": exif,
            "place": place,
            "thumbnail_url": storage.url_for(p.storage_path),
        }

    return {"upload_id": upload.upload_id, "photos": [photo_out(p) for p in photos]}

@router.patch("/photos/{photo_id}/place")
async def set_place(
    photo_id: str,
    place: PlaceIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    svc = UploadService(db)
    storage = get_storage_service()
    place_data = place.model_dump()

    if place_data.get("lat") is None or place_data.get("lng") is None:
        query = (place_data.get("address") or place_data.get("name") or "").strip()
        geo = await GeocodingService().geocode(query)
        lat = geo.get("lat")
        lng = geo.get("lng")
        if lat is None or lng is None:
            raise AppError("bad_request", "주소로 위치를 찾을 수 없습니다.", 400)
        place_data["lat"] = lat
        place_data["lng"] = lng
        if not place_data.get("address"):
            place_data["address"] = geo.get("road_address") or geo.get("address")

    photo = await svc.update_photo_place(user.user_id, photo_id, place_data)
    if not photo:
        raise AppError("not_found", "Photo not found", 404)

    return {
        "photo_id": photo.photo_id,
        "status": photo.status,
        "place": {
            "name": photo.place_name,
            "address": photo.place_address,
            "category": photo.place_category,
            "lat": photo.place_lat,
            "lng": photo.place_lng,
        },
        "thumbnail_url": storage.url_for(photo.storage_path),
    }
