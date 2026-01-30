from datetime import datetime, timedelta
from app.core.exceptions import AppError

# [핵심] 우리가 만든 Route Service 및 스키마 임포트
from app.services.route_service import route_service
from app.schemas.plan import PlanGenerateRequest, FixedEvent

# [주석 처리] DB 관련 임포트
# from sqlalchemy.ext.asyncio import AsyncSession
# from app.models.plan import Plan, SavedPlan
# from app.repositories.plan_repo import PlanRepository

class PlanService:
    def __init__(self, db):
        # DB 세션은 받지만 사용하지 않음
        self.db = db
        # [주석 처리] 레포지토리 초기화
        # self.repo = PlanRepository(db)

    async def generate(self, user_id: str, payload: dict):
        """
        [NO-DB 모드]
        사용자 요청 -> RouteService(알고리즘) 실행 -> 결과 즉시 반환
        """
        # 1. [주석 처리] 이전 방식의 유효성 검사 (duration_hours가 없는 요청이므로 에러 방지)
        # if payload.get("duration_hours", 0) <= 0:
        #     raise AppError("invalid_condition", "duration_hours must be > 0", 422)

        # 2. 데이터 변환
        # [수정] 들어온 페이로드 구조에 맞춰 직접 매핑하거나 Pydantic 객체 생성
        try:
            # 고정 일정 변환
            fixed_events = []
            if "fixed_events" in payload:
                for evt in payload["fixed_events"]:
                    fixed_events.append(FixedEvent(**evt))

            # RouteService 요청 객체 생성 (들어온 payload 데이터를 그대로 활용)
            request_data = PlanGenerateRequest(
                region=payload["region"],
                start_date=payload["start_date"],
                end_date=payload["end_date"],
                first_day_start_time=payload["first_day_start_time"],
                last_day_end_time=payload["last_day_end_time"],
                fixed_events=fixed_events
            )
        except Exception as e:
            raise AppError("invalid_payload", f"데이터 매핑 에러: {str(e)}", 422)

        # 3. [핵심] 알고리즘 실행 (파이썬 파일 로직 작동 확인용)
        try:
            print(f"🚀 [PlanService] 경로 생성 알고리즘 시작 (User: {user_id})")
            # route_service.py 의 generate_plan 호출
            generated_plans_json = route_service.generate_plan(request_data)
            print("✅ [PlanService] 경로 생성 완료")
        except Exception as e:
            print(f"❌ [PlanService] 알고리즘 에러: {e}")
            raise AppError("generation_failed", str(e), 500)

        # 4. [주석 처리] DB 저장 로직
        # plan = Plan(
        #     user_id=user_id,
        #     region=payload["region"],
        #     variants_json=generated_plans_json,
        # )
        # return await self.repo.create_plan(plan)

        # 5. 결과 반환 (DB 저장 없이 딕셔너리로 바로 리턴)
        return {
            "plan_id": "temp_no_db_id", 
            "summary": {
                "region": payload["region"],
                "start_date": payload["start_date"],
                "end_date": payload["end_date"],
                "transport": payload.get("transport", "public"),
                "crowd_mode": payload.get("crowd_mode", "default"),
            },
            "variants": generated_plans_json # 실제 알고리즘 결과
        }

    # -------------------------------------------------------
    # DB 의존 메서드들 (전부 주석 처리 또는 에러 처리)
    # -------------------------------------------------------

    async def get_plan(self, user_id: str, plan_id: str):
        raise AppError("not_implemented", "DB disabled for testing", 501)

    async def save_plan(self, user_id: str, plan_id: str, title: str | None):
        raise AppError("not_implemented", "DB disabled for testing", 501)

    async def list_saved_plans(self, user_id: str, limit: int = 20):
        return []

    async def get_saved_plan(self, user_id: str, saved_plan_id: str):
        raise AppError("not_implemented", "DB disabled for testing", 501)

    async def list_saved_plans_by_spot(self, user_id: str, spot_id: str, limit: int = 20):
        return []