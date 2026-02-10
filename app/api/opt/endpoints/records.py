from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.schemas.record import SpotCreateRequest, SpotUpdateRequest
from app.schemas.saved_plan import SavePlanRequest
from app.services.record_service import RecordService
from app.services.plan_service import PlanService
from app.repositories.upload_repo import UploadRepository
from app.services.storage_service import get_storage_service

router = APIRouter()

def _iso(dt):
    if not dt:
        return None
    return dt.isoformat().replace("+00:00", "Z")

@router.post("/spots")
async def create_spots(body: SpotCreateRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = RecordService(db)
    spots = await svc.create_spots_from_upload(user.user_id, body.upload_id, [s.model_dump() for s in body.spots])
    return {"created": len(spots), "spot_ids": [s.spot_id for s in spots]}

@router.get("/spots")
async def list_spots(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    svc = RecordService(db)
    spots = await svc.list_spots(user.user_id, q=q, category=category, limit=limit)

    upload_repo = UploadRepository(db)
    storage = get_storage_service()
    photos = await upload_repo.list_photos_by_ids([s.photo_id for s in spots if s.photo_id])
    photo_map = {p.photo_id: p for p in photos}

    def item_out(s):
        ph = photo_map.get(s.photo_id) if s.photo_id else None
        return {
            "spot_id": s.spot_id,
            "place": {"name": s.name, "address": s.address, "category": s.category, "lat": s.lat, "lng": s.lng},
            "visited_at": _iso(s.visited_at),
            "thumbnail_url": storage.url_for(ph.storage_path) if ph else None,
        }

    return {"items": [item_out(s) for s in spots], "next_cursor": None}

@router.get("/spots/{spot_id}")
async def get_spot(spot_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = RecordService(db)
    spot = await svc.get_spot(user.user_id, spot_id)

    upload_repo = UploadRepository(db)
    storage = get_storage_service()
    photo = await upload_repo.get_photo(spot.photo_id) if spot.photo_id else None

    plan_svc = PlanService(db)
    related = await plan_svc.list_saved_plans_by_spot(user.user_id, spot.spot_id, limit=20)

    return {
        "spot_id": spot.spot_id,
        "place": {"name": spot.name, "address": spot.address, "category": spot.category, "lat": spot.lat, "lng": spot.lng},
        "visited_at": _iso(spot.visited_at),
        "memo": spot.memo,
        "photos": [{"photo_id": photo.photo_id, "url": storage.url_for(photo.storage_path)}] if photo else [],
        "related_plans": [{"plan_id": sp.plan_id, "title": sp.title, "date": sp.date} for sp in related],
    }

@router.patch("/spots/{spot_id}")
async def update_spot(spot_id: str, body: SpotUpdateRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = RecordService(db)
    await svc.update_spot(user.user_id, spot_id, memo=body.memo, tags=body.tags)
    return Response(status_code=204)

@router.delete("/spots/{spot_id}")
async def delete_spot(spot_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = RecordService(db)
    await svc.delete_spot(user.user_id, spot_id)
    return Response(status_code=204)

@router.post("/plans")
async def save_plan(body: SavePlanRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = PlanService(db)
    saved = await svc.save_plan(user.user_id, body.plan_id, body.title)
    return {"saved_plan_id": saved.saved_plan_id}

@router.get("/plans")
async def list_saved_plans(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    svc = PlanService(db)
    items = await svc.list_saved_plans(user.user_id, limit=limit)

    # include variants_summary (A/B title)
    out_items = []
    for s in items:
        plan = await svc.get_plan(user.user_id, s.plan_id)
        variants = plan.variants_json or {}
        summary = {
            "A": (variants.get("A") or {}).get("title"),
            "B": (variants.get("B") or {}).get("title"),
        }
        out_items.append(
            {
                "saved_plan_id": s.saved_plan_id,
                "title": s.title,
                "region": s.region,
                "date": s.date,
                "variants_summary": summary,
            }
        )

    return {"items": out_items, "next_cursor": None}

@router.get("/plans/{saved_plan_id}")
async def get_saved_plan(saved_plan_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = PlanService(db)
    saved, plan = await svc.get_saved_plan(user.user_id, saved_plan_id)
    variants = plan.variants_json or {}
    return {
        "saved_plan_id": saved.saved_plan_id,
        "plan_id": saved.plan_id,
        "title": saved.title,
        "region": saved.region,
        "date": saved.date,
        "summary": {
            "region": plan.region,
            "duration_hours": plan.duration_hours,
            "transport": plan.transport,
            "crowd_mode": plan.crowd_mode,
        },
        "variants": variants,
    }
