# app/api/opt/endpoints/users.py
from fastapi import APIRouter, Depends
from app.core.security import get_current_user

router = APIRouter(tags=["Users"])

@router.get("/me")
async def me(user = Depends(get_current_user)):
    return {
        "user_id": str(user.user_id),
        "email": user.email,
        "name": getattr(user, "name", None),
        "phone_number": getattr(user, "phone_number", None),
    }
