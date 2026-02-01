# app/api/opt/endpoints/auth.py
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.state import verif_store
from app.services.auth_service import AuthService

from app.schemas.auth import (
    SendVerificationRequest,
    VerifyCodeRequest,
    RegisterRequest,
    LoginRequest,
)

router = APIRouter(tags=["Auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_MAX_AGE = 14 * 24 * 60 * 60
COOKIE_SECURE = False
COOKIE_SAMESITE = "lax"


def _set_refresh_cookie(resp: Response, refresh_token: str):
    resp.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=REFRESH_MAX_AGE,
        path="/",
    )


def _clear_refresh_cookie(resp: Response):
    resp.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")


@router.post("/send-verification")
async def send_verification(body: SendVerificationRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db, verif_store)
    return await svc.send_verification(email=str(body.email))


@router.post("/verify-code")
async def verify_code(body: VerifyCodeRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db, verif_store)
    return await svc.verify_code(email=str(body.email), code=body.code)


@router.post("/register")
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db, verif_store)
    user, access_token, refresh_token = await svc.register(
        name=getattr(body, "name", None),
        email=str(body.email),
        password=body.password,
        phone_number=getattr(body, "phone_number", None),
    )

    _set_refresh_cookie(response, refresh_token)
    return {
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "name": getattr(user, "name", None),
            "phone_number": getattr(user, "phone_number", None),
        },
        "tokens": {"access_token": access_token, "token_type": "bearer"},
    }


@router.post("/login")
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db, verif_store)
    user, access_token, refresh_token = await svc.login(email=str(body.email), password=body.password)

    _set_refresh_cookie(response, refresh_token)
    return {
        "user": {
            "user_id": str(user.user_id),
            "email": user.email,
            "name": getattr(user, "name", None),
            "phone_number": getattr(user, "phone_number", None),
        },
        "tokens": {"access_token": access_token, "token_type": "bearer"},
    }


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token cookie")

    svc = AuthService(db, verif_store)
    new_access, new_refresh = await svc.refresh(refresh_token=refresh_token)

    _set_refresh_cookie(response, new_refresh)
    return {"tokens": {"access_token": new_access, "token_type": "bearer"}}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        svc = AuthService(db, verif_store)
        await svc.logout(refresh_token=refresh_token)

    _clear_refresh_cookie(response)
    return {"message": "Logged out"}
