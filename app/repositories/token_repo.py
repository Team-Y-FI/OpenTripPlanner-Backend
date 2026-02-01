# app/repositories/token_repo.py
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete

from app.models.token import RefreshToken


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_hash(self, user_id: str, token_hash: str, expires_at):
        row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def get_by_hash(self, token_hash: str):
        res = await self.db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        return res.scalar_one_or_none()

    async def revoke_by_hash(self, token_hash: str):
        await self.db.execute(
            update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(revoked=True)
        )
        await self.db.commit()

    async def delete_all_for_user(self, user_id: str):
        await self.db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await self.db.commit()
