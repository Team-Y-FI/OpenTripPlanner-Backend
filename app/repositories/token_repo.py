# app/repositories/token_repo.py
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token import RefreshToken


class TokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def issue(self, *, user_id: str, token_hash: str, expires_days: int = 14) -> RefreshToken:
        rt = RefreshToken(
            token_id=uuid4().hex,  # ✅ NOT NULL 충족: 우리가 직접 만든다
            user_id=user_id,
            token_hash=token_hash,
            revoked=False,
            expires_at=datetime.utcnow() + timedelta(days=expires_days),
        )
        self.db.add(rt)
        await self.db.commit()
        await self.db.refresh(rt)
        return rt

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        res = await self.db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        return res.scalar_one_or_none()

    async def revoke(self, *, token_id: str) -> None:
        await self.db.execute(
            update(RefreshToken).where(RefreshToken.token_id == token_id).values(revoked=True)
        )
        await self.db.commit()

    async def revoke_all(self, *, user_id: str) -> None:
        await self.db.execute(
            update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
        )
        await self.db.commit()

    async def delete_all(self, *, user_id: str) -> None:
        await self.db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        await self.db.commit()
