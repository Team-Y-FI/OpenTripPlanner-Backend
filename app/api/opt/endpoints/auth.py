# app/api/endpoints/auth.py
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.auth_service import AuthService

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"


class SendVerificationIn(BaseModel):
    email: EmailStr


class VerifyCodeIn(BaseModel):
    email: EmailStr
    code: str


class RegisterIn(BaseModel):
    # ✅ user_id를 직접 입력받아 저장 (명세 반영)
    user_id: str = Field(min_length=1, max_length=64)

    email: EmailStr
    password: str
    name: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/send-verification")
async def send_verification(body: SendVerificationIn, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    await svc.send_verification(email=str(body.email))
    return {"message": "인증코드가 발송되었습니다. (콘솔 출력/가상 발송)"}


@router.post("/verify-code")
async def verify_code(body: VerifyCodeIn, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    await svc.verify_code(email=str(body.email), code=body.code)
    return {"message": "인증되었습니다."}


@router.post("/register")
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    # ✅ user_id 전달
    await svc.register(user_id=body.user_id, email=str(body.email), password=body.password, name=body.name)
    return {"message": "회원가입이 완료되었습니다."}


@router.post("/login")
async def login(body: LoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    tokens = await svc.login(email=str(body.email), password=body.password)

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return {"access_token": tokens["access_token"]}


@router.post("/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh Token 쿠키가 없습니다.")

    svc = AuthService(db)
    tokens = await svc.refresh(refresh_token=refresh_token)

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens["refresh_token"],
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return {"access_token": tokens["access_token"]}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        svc = AuthService(db)
        await svc.logout(refresh_token=refresh_token)

    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")
    return {"message": "로그아웃 되었습니다."}
