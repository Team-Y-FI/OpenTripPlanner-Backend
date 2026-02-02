import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.api.opt.router import api_router

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

# ✅ import 단계에서 디렉토리 생성/권한 문제로 죽지 않게: check_dir=False
storage_dir = Path(settings.STORAGE_DIR)
app.mount("/storage", StaticFiles(directory=str(storage_dir), check_dir=False), name="storage")

app.include_router(api_router, prefix="/otp")
register_exception_handlers(app)

@app.on_event("startup")
async def startup():
    # ✅ 서버 실행 시점에 디렉토리 생성 시도(권한 없으면 경고만)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"[WARN] STORAGE_DIR 권한 없음: {storage_dir} (파일 업로드/정적 제공이 제한될 수 있음)")
