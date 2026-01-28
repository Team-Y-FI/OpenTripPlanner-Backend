from fastapi import APIRouter
from app.api.v1.endpoints import auth, users, uploads, records, plans, meta, utils

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(uploads.router, tags=["Uploads"])
api_router.include_router(records.router, prefix="/records", tags=["Records"])
api_router.include_router(plans.router, prefix="/plans", tags=["Plans"])
api_router.include_router(meta.router, prefix="/meta", tags=["Meta"])
api_router.include_router(utils.router, prefix="/utils", tags=["Utils"])
