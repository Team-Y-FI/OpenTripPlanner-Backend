from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User

class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_email(self, email: str) -> User | None:
        res = await self.db.execute(select(User).where(User.email == email))
        return res.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> User | None:
        res = await self.db.execute(select(User).where(User.user_id == user_id))
        return res.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user
        
    async def exists_email(self, email: str) -> bool:
        stmt = select(User.user_id).where(User.email == email.lower()).limit(1)
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none() is not None
