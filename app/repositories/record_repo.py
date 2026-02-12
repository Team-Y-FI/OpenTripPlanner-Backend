from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, or_
from app.models.record import Spot

class RecordRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_spots(self, spots: list[Spot]) -> list[Spot]:
        self.db.add_all(spots)
        await self.db.commit()
        for s in spots:
            await self.db.refresh(s)
        return spots

    async def list_spots(self, user_id: str, q: str | None = None, category: str | None = None, limit: int = 20):
        stmt = (
            select(Spot)
            .where(Spot.user_id == user_id)
            .order_by(Spot.visited_at.desc().nulls_last())
            .limit(limit)
        )
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Spot.name.ilike(like), Spot.address.ilike(like)))
        if category:
            stmt = stmt.where(Spot.category == category)
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def get_spot(self, user_id: str, spot_id: str) -> Spot | None:
        res = await self.db.execute(select(Spot).where(Spot.user_id == user_id, Spot.spot_id == spot_id))
        return res.scalar_one_or_none()

    async def update_spot(self, user_id: str, spot_id: str, memo: str | None, tags: list[str] | None):
        await self.db.execute(
            update(Spot).where(Spot.user_id == user_id, Spot.spot_id == spot_id).values(memo=memo, tags=tags)
        )
        await self.db.commit()

    async def delete_spot(self, user_id: str, spot_id: str):
        await self.db.execute(delete(Spot).where(Spot.user_id == user_id, Spot.spot_id == spot_id))
        await self.db.commit()
