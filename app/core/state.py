from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.verification_store import VerificationStore


# ✅ 전역 VerificationStore() 생성은 DB 세션이 필요해서 구조적으로 위험/크래시 원인
def get_verification_store(db: AsyncSession = Depends(get_db)) -> VerificationStore:
    return VerificationStore(db)
