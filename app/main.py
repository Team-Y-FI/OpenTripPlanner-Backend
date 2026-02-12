from pathlib import Path

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
    print("서버가 시작됩니다. 리소스를 초기화합니다...")
    try:
        route_service.initialize_resources()
        print("모든 리소스가 성공적으로 로드되었습니다.")
    except Exception as e:
        print(f"리소스 초기화 중 에러 발생: {e}")
    
    yield
    
    # [서버 종료 시] 정리 작업이 필요하다면 여기에 작성
    print("서버가 종료됩니다.")

# ============================================================
# 2. FastAPI 앱 초기화 (lifespan 연결)
# ============================================================
app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ✅ import 단계에서 디렉토리 생성/권한 문제로 죽지 않게: check_dir=False
storage_dir = Path(settings.STORAGE_DIR)
is_local_storage = (settings.STORAGE_BACKEND or "local").strip().lower() == "local"
if is_local_storage:
    app.mount("/storage", StaticFiles(directory=str(storage_dir), check_dir=False), name="storage")

app.include_router(api_router, prefix="/otp")
register_exception_handlers(app)

@app.on_event("startup")
async def startup():
    if not is_local_storage:
        return
    # ✅ 서버 실행 시점에 디렉토리 생성 시도(권한 없으면 경고만)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"[WARN] STORAGE_DIR 권한 없음: {storage_dir} (파일 업로드/정적 제공이 제한될 수 있음)")
