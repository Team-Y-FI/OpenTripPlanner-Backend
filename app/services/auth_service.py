# app/services/auth_service.py
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_refresh_token,
    verify_password,
    get_password_hash,
)
from app.repositories.user_repo import UserRepo
from app.repositories.token_repo import TokenRepository
from app.services.verification_store import VerificationStore


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserRepo(db)
        self.tokens = TokenRepository(db)
        self.verification_store = VerificationStore(db)

    async def send_verification(self, email: str) -> dict:
        if await self.users.get_by_email(email):
            raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")

        await self.verification_store.create_code(email=email, ttl_seconds=300)
        return {"message": "인증코드를 전송했습니다."}

    async def verify_code(self, email: str, code: str) -> dict:
        ok = await self.verification_store.verify_code(email=email, code=code)
        if not ok:
            raise HTTPException(status_code=400, detail="인증코드가 올바르지 않거나 만료되었습니다.")
        return {"message": "인증되었습니다."}

    # ✅ user_id 직접 입력 반영
    async def register(self, *, user_id: str, email: str, password: str, name: str) -> dict:
        verified = await self.verification_store.is_verified(email=email)
        if not verified:
            raise HTTPException(status_code=400, detail="이메일 인증이 필요합니다.")

        if await self.users.get_by_email(email):
            raise HTTPException(status_code=400, detail="이미 가입된 이메일입니다.")

        # ✅ user_id 중복 체크(명세상 식별자이므로 필요)
        if hasattr(self.users, "get_by_user_id"):
            if await self.users.get_by_user_id(user_id):
                raise HTTPException(status_code=400, detail="이미 사용 중인 user_id 입니다.")

        password_hash = get_password_hash(password)

        # ✅ create에 user_id 전달
        await self.users.create(user_id=user_id, email=email, name=name, password_hash=password_hash)

        await self.verification_store.clear_verified(email=email)
        return {"message": "회원가입이 완료되었습니다."}

    async def login(self, *, email: str, password: str, response: Response | None = None) -> dict:
        user = await self.users.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")

        access_token = create_access_token(
            subject=str(user.user_id),
            expires_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )

        refresh_token, _exp_dt = create_refresh_token(
            subject=str(user.user_id),
            expires_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        )

        token_hash = hash_refresh_token(refresh_token)
        await self.tokens.issue(
            user_id=user.user_id,
            token_hash=token_hash,
            expires_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        )

        if response is not None:
            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=False,
                samesite="lax",
                max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            )

        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

    @staticmethod
    def _as_aware_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    async def refresh(self, *, refresh_token: str, response: Response | None = None) -> dict:
        token_hash = hash_refresh_token(refresh_token)
        row = await self.tokens.get_by_hash(token_hash)

        if not row:
            raise HTTPException(status_code=401, detail="Refresh token이 유효하지 않습니다.")

        if row.revoked:
            await self.tokens.revoke_all(user_id=row.user_id)
            raise HTTPException(status_code=401, detail="토큰 재사용이 감지되어 강제 로그아웃 처리되었습니다.")

        now = datetime.now(timezone.utc)
        exp = self._as_aware_utc(row.expires_at)
        if exp <= now:
            await self.tokens.revoke(token_id=row.token_id)
            raise HTTPException(status_code=401, detail="Refresh token expired")

        await self.tokens.revoke(token_id=row.token_id)

        access_token = create_access_token(
            subject=str(row.user_id),
            expires_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )

        new_refresh_token, _new_exp_dt = create_refresh_token(
            subject=str(row.user_id),
            expires_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        )

        new_hash = hash_refresh_token(new_refresh_token)
        await self.tokens.issue(
            user_id=row.user_id,
            token_hash=new_hash,
            expires_days=settings.REFRESH_TOKEN_EXPIRE_DAYS,
        )

        if response is not None:
            response.set_cookie(
                key="refresh_token",
                value=new_refresh_token,
                httponly=True,
                secure=False,
                samesite="lax",
                max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            )

        return {"access_token": access_token, "refresh_token": new_refresh_token, "token_type": "bearer"}

    async def logout(self, *, refresh_token: str | None, response: Response | None = None) -> dict:
        if refresh_token:
            token_hash = hash_refresh_token(refresh_token)
            row = await self.tokens.get_by_hash(token_hash)
            if row:
                await self.tokens.revoke(token_id=row.token_id)

        if response is not None:
            response.delete_cookie("refresh_token")

        return {"message": "로그아웃 되었습니다."}
