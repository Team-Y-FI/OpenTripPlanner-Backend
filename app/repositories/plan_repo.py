from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.plan import Plan, SavedPlan, PlanSpotLink

class PlanRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_plan(self, plan: Plan) -> Plan:
        self.db.add(plan)
        await self.db.commit()
        await self.db.refresh(plan)
        return plan

    async def get_plan(self, user_id: str, plan_id: str) -> Plan | None:
        res = await self.db.execute(select(Plan).where(Plan.user_id == user_id, Plan.plan_id == plan_id))
        return res.scalar_one_or_none()

    async def create_saved_plan(self, saved: SavedPlan) -> SavedPlan:
        self.db.add(saved)
        await self.db.commit()
        await self.db.refresh(saved)
        return saved

    async def get_saved_plan(self, user_id: str, saved_plan_id: str) -> SavedPlan | None:
        res = await self.db.execute(select(SavedPlan).where(SavedPlan.user_id == user_id, SavedPlan.saved_plan_id == saved_plan_id))
        return res.scalar_one_or_none()

    async def get_saved_by_plan_id(self, user_id: str, plan_id: str) -> SavedPlan | None:
        res = await self.db.execute(select(SavedPlan).where(SavedPlan.user_id == user_id, SavedPlan.plan_id == plan_id))
        return res.scalar_one_or_none()

    async def list_saved_plans(self, user_id: str, limit: int = 20):
        res = await self.db.execute(
            select(SavedPlan).where(SavedPlan.user_id == user_id).order_by(SavedPlan.created_at.desc()).limit(limit)
        )
        return list(res.scalars().all())

    async def create_plan_spot_links(self, saved_plan_id: str, spot_ids: list[str]):
        if not spot_ids:
            return
        links = [PlanSpotLink(saved_plan_id=saved_plan_id, spot_id=sid) for sid in spot_ids]
        self.db.add_all(links)
        await self.db.commit()

    async def list_saved_plans_by_spot(self, user_id: str, spot_id: str, limit: int = 20):
        stmt = (
            select(SavedPlan)
            .join(PlanSpotLink, PlanSpotLink.saved_plan_id == SavedPlan.saved_plan_id)
            .where(SavedPlan.user_id == user_id, PlanSpotLink.spot_id == spot_id)
            .order_by(SavedPlan.created_at.desc())
            .limit(limit)
        )
        res = await self.db.execute(stmt)
        return list(res.scalars().all())
