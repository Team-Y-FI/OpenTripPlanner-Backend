# app/services/verification_store.py
import secrets
import datetime as dt
import hmac

from app.core.config import settings
from app.repositories.email_verification_repo import EmailVerificationRepository

try:
    import redis.asyncio as redis
except Exception:
    redis = None


class VerificationStore:
    def __init__(self):
        self.redis = None

    async def init(self) -> None:
        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url and redis is not None:
            try:
                self.redis = redis.from_url(redis_url)
                await self.redis.ping()
            except Exception:
                self.redis = None

    @staticmethod
    def _code_key(email: str) -> str:
        return f"verify:code:{email.lower()}"

    @staticmethod
    def _ok_key(email: str) -> str:
        return f"verify:ok:{email.lower()}"

    def generate_code(self) -> str:
        return f"{secrets.randbelow(10**6):06d}"

    async def set_code(self, *, db, email: str, code: str, ttl_seconds: int = 300) -> None:
        email_l = email.lower()
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds)

        if self.redis:
            await self.redis.set(self._code_key(email_l), code, ex=ttl_seconds)
            await self.redis.delete(self._ok_key(email_l))
            return

        await EmailVerificationRepository(db).upsert_code(email=email_l, code=code, expires_at=expires_at)

    async def verify_code(self, *, db, email: str, code: str) -> bool:
        email_l = email.lower()

        if self.redis:
            saved = await self.redis.get(self._code_key(email_l))
            if not saved:
                return False
            saved_str = saved.decode() if isinstance(saved, (bytes, bytearray)) else str(saved)
            if not hmac.compare_digest(saved_str, code):
                return False

            # ✅ 인증 성공 → ok flag 1시간 유지
            await self.redis.set(self._ok_key(email_l), "1", ex=3600)
            await self.redis.delete(self._code_key(email_l))
            return True

        now = dt.datetime.now(dt.timezone.utc)
        return await EmailVerificationRepository(db).verify_code(email=email_l, code=code, now=now)

    async def is_verified(self, *, db, email: str) -> bool:
        email_l = email.lower()

        if self.redis:
            ok = await self.redis.get(self._ok_key(email_l))
            return bool(ok)

        now = dt.datetime.now(dt.timezone.utc)
        return await EmailVerificationRepository(db).is_verified(email=email_l, now=now)

    async def clear(self, *, db, email: str) -> None:
        email_l = email.lower()

        if self.redis:
            await self.redis.delete(self._code_key(email_l))
            await self.redis.delete(self._ok_key(email_l))
            return

        await EmailVerificationRepository(db).clear(email=email_l)
