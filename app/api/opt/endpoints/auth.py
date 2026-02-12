# app/api/endpoints/auth.py
from fastapi import APIRouter, Depends, HTTPException, Response, Request
import logging
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from urllib.parse import urlencode

from app.db.session import get_db
from app.services.auth_service import AuthService
from app.core.config import settings

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)

REFRESH_COOKIE_NAME = "refresh_token"
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_ME_URL = "https://kapi.kakao.com/v2/user/me"


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


class KakaoTokenIn(BaseModel):
    access_token: str


@router.post("/send-verification")
async def send_verification(body: SendVerificationIn, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    await svc.send_verification(email=str(body.email))
    return {"message": "인증코드가 이메일로 발송되었습니다."}


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
        secure=True,
        samesite="none",
        path="/",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
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
        secure=True,
        samesite="none",
        path="/",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
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


@router.get("/kakao/login")
async def kakao_login_redirect(redirect_uri: str | None = None):
    if not getattr(settings, "KAKAO_CLIENT_ID", None):
        raise HTTPException(status_code=500, detail="KAKAO_CLIENT_ID 설정이 필요합니다.")

    # ✅ 프론트가 넘긴 redirect_uri(exp://...)를 사용 (팀원 각자 Expo Go 테스트)
    ru = redirect_uri or getattr(settings, "KAKAO_REDIRECT_URI", None)
    if not ru:
        raise HTTPException(status_code=500, detail="KAKAO_REDIRECT_URI 설정이 필요합니다.")

    logger.info("[kakao] login redirect_uri=%s", ru)

    params = {
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": ru,
        "response_type": "code",
        "scope": "account_email profile_nickname",
    }
    return RedirectResponse(url=f"{KAKAO_AUTH_URL}?{urlencode(params)}")

@router.get("/kakao/callback")
async def kakao_callback(
    code: str | None = None,
    redirect_uri: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if not code:
        raise HTTPException(status_code=400, detail="인가 코드(code)가 없습니다.")

    ru = redirect_uri or getattr(settings, "KAKAO_REDIRECT_URI", None)
    if not ru:
        raise HTTPException(status_code=500, detail="KAKAO_REDIRECT_URI 설정이 필요합니다.")

    logger.info("[kakao] callback redirect_uri=%s", ru)

    data = {
        "grant_type": "authorization_code",
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": ru,
        "code": code,
    }
    if getattr(settings, "KAKAO_CLIENT_SECRET", None):
        data["client_secret"] = settings.KAKAO_CLIENT_SECRET

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_res = await client.post(
            KAKAO_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )
    if token_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao token 교환 실패: {token_res.text}")

    kakao_access_token = (token_res.json() or {}).get("access_token")
    if not kakao_access_token:
        raise HTTPException(status_code=401, detail="Kakao access_token이 없습니다.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        me_res = await client.get(
            KAKAO_ME_URL,
            headers={"Authorization": f"Bearer {kakao_access_token}"},
        )
    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao user 조회 실패: {me_res.text}")

    me = me_res.json() or {}
    kakao_account = me.get("kakao_account") or {}
    profile = kakao_account.get("profile") or {}
    email = kakao_account.get("email")
    nickname = profile.get("nickname") or "KakaoUser"

    svc = AuthService(db)
    tokens = await svc.kakao_login(email=str(email) if email else "", nickname=nickname)

    res = JSONResponse(content={"access_token": tokens["access_token"]})
    res.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens["refresh_token"],
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )
    return res


@router.post("/kakao/token")
async def kakao_token(body: KakaoTokenIn, response: Response, db: AsyncSession = Depends(get_db)):
    # 네이티브 SDK(Dev Client/배포)에서 access_token을 직접 받을 때용
    if not body.access_token:
        raise HTTPException(status_code=400, detail="access_token이 없습니다.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        me_res = await client.get(
            KAKAO_ME_URL,
            headers={"Authorization": f"Bearer {body.access_token}"},
        )
    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao user 조회 실패: {me_res.text}")

    me = me_res.json() or {}
    kakao_account = me.get("kakao_account") or {}
    profile = kakao_account.get("profile") or {}
    email = kakao_account.get("email")
    nickname = profile.get("nickname") or "KakaoUser"

    svc = AuthService(db)
    tokens = await svc.kakao_login(email=str(email) if email else "", nickname=nickname)

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens["refresh_token"],
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )
    return {"access_token": tokens["access_token"]}
