# app/services/verification_store.py (Redis 완전 비활성화 버전)

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_otp_code, hash_otp_code
from app.repositories.email_verification_repo import EmailVerificationRepo


class VerificationStore:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = EmailVerificationRepo(db)

    async def _get_redis(self):
        # ✅ Redis 완전 미사용
        return None

    async def create_code(self, email: str, ttl_seconds: int = 300) -> str:
        code = generate_otp_code()
        code_hash = hash_otp_code(code)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        await self.repo.upsert_code(email=email, code_hash=code_hash, expires_at=expires_at)
        print(f"[OTP] email={email} code={code}")
        return code

    async def verify_code(self, email: str, code: str) -> bool:
        code_hash = hash_otp_code(code)
        return await self.repo.verify_code(email=email, code_hash=code_hash)

    async def is_verified(self, email: str) -> bool:
        return await self.repo.is_verified(email=email)

    async def clear_verified(self, email: str) -> None:
        await self.repo.clear(email=email)
