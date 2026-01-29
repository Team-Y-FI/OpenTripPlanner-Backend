from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.schemas.auth import RegisterRequest, LoginRequest, RefreshRequest
from app.services.auth_service import AuthService

router = APIRouter()

@router.post("/register")
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    user, access, refresh = await svc.register(body.name, body.email, body.password)
    return {
        "user": {"user_id": user.user_id, "name": user.name, "email": user.email, "created_at": user.created_at.isoformat()},
        "tokens": {"access_token": access, "refresh_token": refresh, "expires_in": 3600},
    }

@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    user, access, refresh = await svc.login(body.email, body.password)
    return {
        "user": {"user_id": user.user_id, "name": user.name, "email": user.email, "created_at": user.created_at.isoformat()},
        "tokens": {"access_token": access, "refresh_token": refresh, "expires_in": 3600},
    }

@router.post("/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    access = await svc.refresh(body.refresh_token)
    return {"access_token": access, "expires_in": 3600}

@router.post("/logout")
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    svc = AuthService(db)
    await svc.logout(body.refresh_token)
    return {"ok": True}
