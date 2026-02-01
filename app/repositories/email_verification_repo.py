# app/repositories/email_verification_repo.py
import hashlib
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_verification import EmailVerification


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


CODE_TTL_SECONDS = 300
VERIFIED_TTL_SECONDS = 3600  # ✅ 인증 성공 후 가입 허용 유지 시간 (1시간)


class EmailVerificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_code(self, *, email: str, code: str, expires_at: dt.datetime) -> None:
        email_l = email.lower()
        res = await self.db.execute(select(EmailVerification).where(EmailVerification.email == email_l))
        row = res.scalar_one_or_none()

        if row:
            row.code_hash = _sha256_hex(code)
            row.verified = False
            row.expires_at = expires_at
        else:
            row = EmailVerification(
                email=email_l,
                code_hash=_sha256_hex(code),
                verified=False,
                expires_at=expires_at,
            )
            self.db.add(row)

        await self.db.commit()

    async def verify_code(self, *, email: str, code: str, now: dt.datetime) -> bool:
        email_l = email.lower()
        res = await self.db.execute(select(EmailVerification).where(EmailVerification.email == email_l))
        row = res.scalar_one_or_none()
        if not row:
            return False
        if row.expires_at <= now:
            return False
        if row.code_hash != _sha256_hex(code):
            return False

        row.verified = True
        # ✅ 성공하면 가입 가능 상태를 1시간 연장
        row.expires_at = now + dt.timedelta(seconds=VERIFIED_TTL_SECONDS)
        await self.db.commit()
        return True

    async def is_verified(self, *, email: str, now: dt.datetime) -> bool:
        email_l = email.lower()
        res = await self.db.execute(select(EmailVerification).where(EmailVerification.email == email_l))
        row = res.scalar_one_or_none()
        if not row:
            return False
        if row.expires_at <= now:
            return False
        return bool(row.verified)

    async def clear(self, *, email: str) -> None:
        email_l = email.lower()
        res = await self.db.execute(select(EmailVerification).where(EmailVerification.email == email_l))
        row = res.scalar_one_or_none()
        if row:
            await self.db.delete(row)
            await self.db.commit()
