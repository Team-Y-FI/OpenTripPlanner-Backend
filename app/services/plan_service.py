from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import AppError
from app.models.plan import Plan, SavedPlan
from app.repositories.plan_repo import PlanRepository

def _dummy_variants(region: str):
    # MVP: deterministic-ish dummy (replace with real routing/POI later)
    base_lat, base_lng = 37.55, 126.92
    return {
        "A": {
            "title": "감성 카페 위주",
            "stats": {"total_minutes": 190, "move_minutes": 42, "warnings": 1},
            "stops": [
                {
                    "time": "09:00",
                    "place": {"name": "감성 카페", "address": f"{region} 근처", "category": "카페", "lat": base_lat, "lng": base_lng},
                    "stay_minutes": 40,
                    "travel_minutes": 12,
                    "crowd_level": "green",
                },
                {
                    "time": "10:00",
                    "place": {"name": "전시 공간", "address": f"{region} 근처", "category": "전시/미술관", "lat": base_lat+0.01, "lng": base_lng+0.01},
                    "stay_minutes": 70,
                    "travel_minutes": 20,
                    "crowd_level": "orange",
                },
            ],
        },
        "B": {
            "title": "야경/전망 위주",
            "stats": {"total_minutes": 185, "move_minutes": 38, "warnings": 0},
            "stops": [
                {
                    "time": "09:00",
                    "place": {"name": "공원 산책", "address": f"{region} 근처", "category": "공원/산책", "lat": base_lat+0.015, "lng": base_lng-0.005},
                    "stay_minutes": 50,
                    "travel_minutes": 10,
                    "crowd_level": "green",
                },
                {
                    "time": "10:10",
                    "place": {"name": "전망 스팟", "address": f"{region} 근처", "category": "야경/전망", "lat": base_lat+0.02, "lng": base_lng+0.02},
                    "stay_minutes": 60,
                    "travel_minutes": 18,
                    "crowd_level": "green",
                },
            ],
        },
    }

class PlanService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PlanRepository(db)

    async def generate(self, user_id: str, payload: dict) -> Plan:
        # condition conflict example: duration_hours too small
        if payload.get("duration_hours", 0) <= 0:
            raise AppError("invalid_condition", "duration_hours must be > 0", 422)

        plan = Plan(
            user_id=user_id,
            region=payload["region"],
            date=payload["date"],
            start_time=payload["start_time"],
            duration_hours=payload["duration_hours"],
            transport=payload["transport"],
            crowd_mode=payload["crowd_mode"],
            purposes=payload.get("purposes", []),
            categories=payload.get("categories", []),
            seed_spot_ids=payload.get("seed_spot_ids"),
            variants_json=_dummy_variants(payload["region"]),
        )
        return await self.repo.create_plan(plan)

    async def get_plan(self, user_id: str, plan_id: str) -> Plan:
        plan = await self.repo.get_plan(user_id, plan_id)
        if not plan:
            raise AppError("not_found", "Plan not found", 404)
        return plan

    async def save_plan(self, user_id: str, plan_id: str, title: str | None):
        plan = await self.repo.get_plan(user_id, plan_id)
        if not plan:
            raise AppError("not_found", "Plan not found", 404)

        if await self.repo.get_saved_by_plan_id(user_id, plan_id):
            raise AppError("conflict", "Already saved", 409)

        saved = SavedPlan(
            user_id=user_id,
            plan_id=plan.plan_id,
            title=title or f"{plan.region} {plan.duration_hours}시간 코스",
            date=plan.date,
            region=plan.region,
        )
        saved = await self.repo.create_saved_plan(saved)

        # Link spots from seed_spot_ids (if any) to support Records(Spot) -> related plans
        spot_ids = plan.seed_spot_ids or []
        await self.repo.create_plan_spot_links(saved.saved_plan_id, spot_ids)

        return saved

    async def list_saved_plans(self, user_id: str, limit: int = 20):
        return await self.repo.list_saved_plans(user_id, limit=limit)

    async def get_saved_plan(self, user_id: str, saved_plan_id: str):
        saved = await self.repo.get_saved_plan(user_id, saved_plan_id)
        if not saved:
            raise AppError("not_found", "Saved plan not found", 404)
        plan = await self.repo.get_plan(user_id, saved.plan_id)
        if not plan:
            raise AppError("not_found", "Plan not found", 404)
        return saved, plan

    async def list_saved_plans_by_spot(self, user_id: str, spot_id: str, limit: int = 20):
        return await self.repo.list_saved_plans_by_spot(user_id, spot_id, limit=limit)
