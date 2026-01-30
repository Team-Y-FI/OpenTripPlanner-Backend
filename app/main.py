import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.api.opt.router import api_router
from app.services.route_service import route_service

# ============================================================
# 1. 리소스 초기화 (Lifespan) 설정
# ============================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # [서버 시작 시] 무거운 리소스(모델, 교통 네트워크 등) 로드
    print("🚀 서버가 시작됩니다. 리소스를 초기화합니다...")
    try:
        route_service.initialize_resources()
        print("✅ 모든 리소스가 성공적으로 로드되었습니다.")
    except Exception as e:
        print(f"❌ 리소스 초기화 중 에러 발생: {e}")
    
    yield
    
    # [서버 종료 시] 정리 작업이 필요하다면 여기에 작성
    print("👋 서버가 종료됩니다.")

# ============================================================
# 2. FastAPI 앱 초기화 (lifespan 연결)
# ============================================================
app = FastAPI(title=settings.APP_NAME)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Static storage for uploaded images (MVP)
os.makedirs(settings.STORAGE_DIR, exist_ok=True)
app.mount("/storage", StaticFiles(directory=settings.STORAGE_DIR), name="storage")

app.include_router(api_router, prefix="/otp")
register_exception_handlers(app)
