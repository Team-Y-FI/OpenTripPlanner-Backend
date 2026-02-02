# app/api/opt/endpoints/users.py
from fastapi import APIRouter, Depends
from app.core.security import get_current_user

# ✅ router.py에서 prefix="/users"를 주고 있으므로 여기선 prefix 제거
router = APIRouter(tags=["users"])


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return {
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
    }
