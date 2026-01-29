from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.schemas.plan import PlanGenerateRequest
from app.services.plan_service import PlanService

router = APIRouter()

@router.post("/generate")
async def generate(body: PlanGenerateRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    svc = PlanService(db)
    plan = await svc.generate(user.user_id, body.model_dump())

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
