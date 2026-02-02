# app/core/security.py
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.repositories.user_repo import UserRepo

# Argon2id 강제
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    # passlib argon2 옵션: type="id"가 Argon2id
    argon2__type="id",
)

PASSWORD_POLICY = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^\w\s]).{8,}$")


def validate_password_policy(password: str) -> None:
    if not PASSWORD_POLICY.match(password):
        raise ValueError("비밀번호는 8자 이상, 영문/숫자/특수문자를 각각 1개 이상 포함해야 합니다.")


def get_password_hash(password: str) -> str:
    validate_password_policy(password)
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_minutes: int = 30) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, expires_days: int = 14) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    exp_dt = now + timedelta(days=expires_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(exp_dt.timestamp()),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, exp_dt


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def hash_refresh_token(token: str) -> str:
    # DB 저장용 (SHA256 hex 64)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_otp_code() -> str:
    # 6자리
    return f"{int.from_bytes(os.urandom(3), 'big') % 1_000_000:06d}"


def hash_otp_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# ✅ Bearer 토큰 의존성 (main.py가 /otp prefix면 tokenUrl도 /otp/auth/login이 맞음)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/otp/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        token_type = payload.get("type")
        if token_type != "access" or not sub:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await UserRepo(db).get_by_id(str(sub))  # ✅ user_id는 문자열
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
