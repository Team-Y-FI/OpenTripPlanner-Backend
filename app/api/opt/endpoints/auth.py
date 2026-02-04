# app/api/endpoints/auth.py
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from urllib.parse import urlencode

from app.db.session import get_db
from app.services.auth_service import AuthService
from app.core.config import settings

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_ME_URL = "https://kapi.kakao.com/v2/user/me"


def _refresh_cookie_params() -> dict:
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

    env = (settings.ENV or "").lower()
    if env in {"dev", "local", "development"}:
        return {
            "httponly": True,
            "secure": False,
            "samesite": "lax",
            "path": "/",
            "max_age": max_age,
        }

    return {
        "httponly": True,
        "secure": True,
        "samesite": "none",
        "path": "/",
        "max_age": max_age,
    }


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
    access_token: str = Field(min_length=1)


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
        **_refresh_cookie_params(),
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
        **_refresh_cookie_params(),
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
async def kakao_login_redirect():
    if not settings.KAKAO_CLIENT_ID or not settings.KAKAO_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="KAKAO_CLIENT_ID / KAKAO_REDIRECT_URI 설정이 필요합니다.")

    params = {
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": settings.KAKAO_REDIRECT_URI,
        "response_type": "code",
        "scope": "account_email profile_nickname",
    }
    return RedirectResponse(url=f"{KAKAO_AUTH_URL}?{urlencode(params)}")

@router.get("/kakao/callback")
async def kakao_callback(code: str | None = None, db: AsyncSession = Depends(get_db)):
    if not code:
        raise HTTPException(status_code=400, detail="인가 코드(code)가 없습니다.")

    data = {
        "grant_type": "authorization_code",
        "client_id": settings.KAKAO_CLIENT_ID,
        "redirect_uri": settings.KAKAO_REDIRECT_URI,
        "code": code,
    }
    if settings.KAKAO_CLIENT_SECRET:
        data["client_secret"] = settings.KAKAO_CLIENT_SECRET

    async with httpx.AsyncClient(timeout=10.0) as client:
        token_res = await client.post(
            KAKAO_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        )

    if token_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao token 교환 실패: {token_res.text}")

    kakao_access_token = token_res.json().get("access_token")
    if not kakao_access_token:
        raise HTTPException(status_code=401, detail="Kakao access_token이 없습니다.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        me_res = await client.get(
            KAKAO_ME_URL,
            headers={"Authorization": f"Bearer {kakao_access_token}"},
        )

    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao user 조회 실패: {me_res.text}")

    me = me_res.json()
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
        **_refresh_cookie_params(),
    )
    return res


@router.post("/kakao/token")
async def kakao_token_login(body: KakaoTokenIn, db: AsyncSession = Depends(get_db)):
    """
    모바일/앱 클라이언트용: Kakao SDK로 발급받은 access_token을 받아
    서버에서 사용자 정보를 조회한 뒤, 우리 서비스 JWT를 발급합니다.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        me_res = await client.get(
            KAKAO_ME_URL,
            headers={"Authorization": f"Bearer {body.access_token}"},
        )

    if me_res.status_code != 200:
        raise HTTPException(status_code=401, detail=f"Kakao user 조회 실패: {me_res.text}")

    me = me_res.json()
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
        **_refresh_cookie_params(),
    )
    return res
