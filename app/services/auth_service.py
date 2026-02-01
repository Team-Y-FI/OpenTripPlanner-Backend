# app/services/auth_service.py
import re
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repo import UserRepository
from app.repositories.token_repo import RefreshTokenRepository
from app.repositories.token_repo import hash_refresh
from app.services.verification_store import VerificationStore

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)


PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")

ACCESS_MINUTES = 30
REFRESH_DAYS = 14


class AuthService:
    def __init__(self, db: AsyncSession, verif_store: VerificationStore):
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)
        self.verif = verif_store

    # 1) 이메일 인증 발송
    async def send_verification(self, *, email: str) -> dict:
        if await self.users.exists_email(email):  # ✅ get_by_email 대신 exists
            raise HTTPException(status_code=400, detail="Email already registered")

        code = self.verif.generate_code()
        await self.verif.set_code(db=self.db, email=email, code=code, ttl_seconds=300)
        print(f"[VERIFICATION] email={email} code={code} (expires in 5 minutes)")
        return {"message": "Verification code sent (simulated)."}

    # 1) 이메일 인증 검증
    async def verify_code(self, *, email: str, code: str) -> dict:
        ok = await self.verif.verify_code(db=self.db, email=email, code=code)
        if not ok:
            raise HTTPException(status_code=400, detail="Invalid or expired verification code")
        return {"verified": True, "message": "Email verified."}

    # 2) 회원가입(이메일 인증 필수)
    async def register(self, *, name: str | None, email: str, password: str, phone_number: str | None = None):
        verified = await self.verif.is_verified(db=self.db, email=email)
        if not verified:
            raise HTTPException(status_code=400, detail="Email verification required")

        if await self.users.get_by_email(email):
            raise HTTPException(status_code=400, detail="Email already registered")

        if not PASSWORD_RE.match(password or ""):
            raise HTTPException(
                status_code=400,
                detail="Password must be >=8 chars and include letter/number/special at least once each",
            )

        pw_hash = hash_password(password)

        # User 모델에 phone_number 컬럼이 있어야 함(없으면 모델에 추가 필요)
        from app.models.user import User
        user = User(
            email=email.lower(),
            password_hash=pw_hash,
            name=name,
            phone_number=phone_number,
        )
        user = await self.users.create(user)

        # 인증 상태 제거(재사용 방지)
        await self.verif.clear(db=self.db, email=email)

        access = create_access_token(str(user.user_id), expires_minutes=ACCESS_MINUTES)

        refresh_token, refresh_exp = create_refresh_token(str(user.user_id))  # type=refresh 포함
        refresh_hash = hash_refresh(refresh_token)
        await self.tokens.create_hash(str(user.user_id), refresh_hash, refresh_exp)

        return user, access, refresh_token

    # 3) 로그인
    async def login(self, *, email: str, password: str):
        user = await self.users.get_by_email(email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        access = create_access_token(str(user.user_id), expires_minutes=ACCESS_MINUTES)

        refresh_token, refresh_exp = create_refresh_token(str(user.user_id))
        refresh_hash = hash_refresh(refresh_token)
        await self.tokens.create_hash(str(user.user_id), refresh_hash, refresh_exp)

        return user, access, refresh_token

    # 4) 토큰 재발급(RTR + reuse detection)
    async def refresh(self, *, refresh_token: str):
        # refresh 전용 타입 체크(잘못된 토큰이면 401)
        payload = decode_refresh_token(refresh_token)  # :contentReference[oaicite:1]{index=1}
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        token_hash = hash_refresh(refresh_token)
        row = await self.tokens.get_by_hash(token_hash)
        if not row:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        now = datetime.now(timezone.utc)
        if row.expires_at <= now:
            await self.tokens.revoke_by_hash(token_hash)
            raise HTTPException(status_code=401, detail="Refresh token expired")

        # Reuse Detection
        if row.revoked:
            await self.tokens.delete_all_for_user(str(row.user_id))
            raise HTTPException(status_code=401, detail="Refresh token reuse detected. Logged out.")

        # 정상 토큰이면 revoke 처리 후 새 토큰 발급/저장
        await self.tokens.revoke_by_hash(token_hash)

        new_access = create_access_token(str(row.user_id), expires_minutes=ACCESS_MINUTES)

        new_refresh, new_exp = create_refresh_token(str(row.user_id))
        new_hash = hash_refresh(new_refresh)
        await self.tokens.create_hash(str(row.user_id), new_hash, new_exp)

        return new_access, new_refresh

    # 5) 로그아웃
    async def logout(self, *, refresh_token: str) -> None:
        token_hash = hash_refresh(refresh_token)
        await self.tokens.revoke_by_hash(token_hash)
