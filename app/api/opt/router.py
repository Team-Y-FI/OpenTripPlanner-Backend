from fastapi import APIRouter

api_router = APIRouter()

# ✅ 핵심 라우터는 "한 번만" 명시 include
from app.api.opt.endpoints import auth, users

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])

# ✅ 나머지 라우터만 선택적으로 include (auth/users는 절대 포함하지 않음)
OPTIONAL_ENDPOINTS = [
    ("uploads", None, ["uploads"]),
    ("records", "/records", ["records"]),
    ("plans", "/plans", ["plans"]),
    ("meta", "/meta", ["meta"]),
    ("utils", "/utils", ["utils"]),
]

for mod, prefix, tags in OPTIONAL_ENDPOINTS:
    try:
        m = __import__(f"app.api.opt.endpoints.{mod}", fromlist=["router"])
        r = getattr(m, "router", None)
        if r is None:
            continue

        if prefix is None:
            api_router.include_router(r, tags=tags)
        else:
            api_router.include_router(r, prefix=prefix, tags=tags)
    except Exception as e:
        print(f"[ERROR] 라우터 로드 실패 ({mod}): {e}")
        import traceback
        traceback.print_exc()
        continue
