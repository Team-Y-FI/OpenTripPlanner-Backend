from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.schemas.plan import PlanGenerateRequest, ReplaceSpotsRequest, ReplaceSpotsResponse
from app.services.plan_service import PlanService

router = APIRouter()

@router.post("/generate")
async def generate(
    body: PlanGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    코스 생성 (DB에 Plan 저장 + 생성된 plan_id 반환)
    """
    svc = PlanService(db)
    result = await svc.generate(user.user_id, body.model_dump())
    return result

@router.get("/{plan_id}")
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = PlanService(db)
    plan = await svc.get_plan(user.user_id, plan_id)
    return {
        "plan_id": plan.plan_id,
        "summary": {
            "region": plan.region,
            "duration_hours": plan.duration_hours,
            "transport": plan.transport,
            "crowd_mode": plan.crowd_mode,
        },
        "variants": plan.variants_json,
    }

@router.post("/replace-spots")
async def replace_spots(body: ReplaceSpotsRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """대체 장소 추천"""
    svc = PlanService(db)
    alternatives = await svc.recommend_alternative_spots(
        user_id=user.user_id,
        plan_id=body.plan_id,
        day=body.day,
        spot_names=body.spot_names,
        categories=body.categories,
        region=body.region
    )
    return ReplaceSpotsResponse(alternatives=alternatives)
