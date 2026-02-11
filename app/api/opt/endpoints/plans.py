from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import get_current_user
from app.schemas.plan import PlanGenerateRequest, ReplaceSpotsRequest, ReplaceSpotsResponse, RecalculateRouteRequest
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

# [추가] 경로 재계산 엔드포인트
@router.post("/{plan_id}/recalculate", status_code=status.HTTP_200_OK)
async def recalculate_plan_route(
    plan_id: str,
    body: RecalculateRouteRequest,  # request 대신 body로 이름을 맞춰 통일감을 줌
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    사용자가 장소를 삭제한 후, 남은 장소들로 상세 경로를 다시 계산합니다.
    """
    # 다른 엔드포인트들과 동일하게 직접 PlanService 생성
    svc = PlanService(db)
    
    # PlanService의 recalculate_route 호출
    # user.user_id를 사용하여 기존 엔드포인트들의 인증 방식과 통일
    result = await svc.recalculate_route(
        user_id=user.user_id,
        plan_id=plan_id,
        day_key=body.day_key,
        # pydantic 모델 리스트를 dict 리스트로 변환하여 전달
        remaining_places=[p.model_dump() for p in body.remaining_places] 
    )
    return result
