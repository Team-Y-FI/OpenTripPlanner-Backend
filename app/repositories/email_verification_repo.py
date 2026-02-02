# app/repositories/email_verification_repo.py
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_verification import EmailVerification


class EmailVerificationRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_code(self, *, email: str, code_hash: str, expires_at: datetime) -> None:
        # 간단히: 기존 row 있으면 삭제 후 생성(업서트)
        await self.db.execute(delete(EmailVerification).where(EmailVerification.email == email))
        ev = EmailVerification(email=email, code_hash=code_hash, verified=False, expires_at=expires_at)
        self.db.add(ev)
        await self.db.commit()

    async def verify_code(self, *, email: str, code_hash: str) -> bool:
        now = datetime.now(timezone.utc)

        res = await self.db.execute(
            select(EmailVerification).where(
                EmailVerification.email == email,
                EmailVerification.code_hash == code_hash,
                EmailVerification.expires_at > now,  # OTP 유효기간 내에만 검증 가능
            )
        )
        row = res.scalar_one_or_none()
        if not row:
            return False

        # ✅ verify 성공 시: verified 표시 + verified window로 expires_at 연장
        row.verified = True
        row.expires_at = now + timedelta(seconds=settings.EMAIL_VERIFIED_TTL_SECONDS)

        await self.db.commit()
        return True

    async def is_verified(self, *, email: str) -> bool:
        now = datetime.now(timezone.utc)
        res = await self.db.execute(
            select(EmailVerification).where(
                EmailVerification.email == email,
                EmailVerification.verified == True,  # noqa
                EmailVerification.expires_at > now,
            )
        )
        return res.scalar_one_or_none() is not None

    async def clear(self, *, email: str) -> None:
        await self.db.execute(delete(EmailVerification).where(EmailVerification.email == email))
        await self.db.commit()
