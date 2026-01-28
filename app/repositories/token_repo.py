import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.token import RefreshToken

def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: str, refresh_token: str, expires_at):
        token_hash = hash_refresh(refresh_token)
        row = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def is_active(self, refresh_token: str) -> bool:
        token_hash = hash_refresh(refresh_token)
        res = await self.db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        row = res.scalar_one_or_none()
        if not row:
            return False
        return not row.revoked

    async def revoke(self, refresh_token: str):
        token_hash = hash_refresh(refresh_token)
        await self.db.execute(update(RefreshToken).where(RefreshToken.token_hash == token_hash).values(revoked=True))
        await self.db.commit()
