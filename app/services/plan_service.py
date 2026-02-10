from datetime import datetime, timedelta
from app.core.exceptions import AppError

# [핵심] 우리가 만든 Route Service 및 스키마 임포트
from app.services.route_service import route_service
from app.schemas.plan import PlanGenerateRequest, FixedEvent

# DB 관련 임포트
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.plan import Plan, SavedPlan
from app.repositories.plan_repo import PlanRepository
from typing import List, Optional

class PlanService:
    def __init__(self, db):
        self.db = db
        self.repo = PlanRepository(db)

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
            
            selected_places = payload.get("selected_places", [])

            # [DEBUG] 로그 추가
            print(f"[DEBUG] 요청 모드 확인: {payload.get('transport_mode')}")
            print(f"[DEBUG] 장소 카테고리(categories) 확인: {payload.get('categories')}")
            print(f"[DEBUG] 여행 목적(purposes) 확인: {payload.get('purposes')}")
            print(f"[DEBUG] 선택된 장소(selected_places) 확인: {len(selected_places)}개")

            # RouteService 요청 객체 생성 (들어온 payload 데이터를 그대로 활용)
            request_data = PlanGenerateRequest(
                region=payload["region"],
                start_date=payload["start_date"],
                end_date=payload["end_date"],
                first_day_start_time=payload["first_day_start_time"],
                last_day_end_time=payload["last_day_end_time"],
                fixed_events=fixed_events,
                selected_places=selected_places,
                categories=payload.get("categories", []),
                purposes=payload.get("purposes", []),
                # ✅ [수정] transport_mode 추가 (기본값 'transport')
                transport_mode=payload.get("transport_mode", "transport") 
            )
        except Exception as e:
            raise AppError("invalid_payload", f"데이터 매핑 에러: {str(e)}", 422)

        # 3. [핵심] 알고리즘 실행 (파이썬 파일 로직 작동 확인용)
        try:
            print(f"[PlanService] 경로 생성 알고리즘 시작 (User: {user_id})")
            # route_service.py 의 generate_plan 호출
            generated_plans_json = route_service.generate_plan(request_data)
            print("[PlanService] 경로 생성 완료")
        except Exception as e:
            print(f"[PlanService] 알고리즘 에러: {e}")
            raise AppError("generation_failed", str(e), 500)

        # 4. DB 저장 로직
        # Plan 모델에 맞춰 데이터 준비
        # variants_json에 summary 정보 포함 (API 응답 구조 유지)
        variants_with_summary = {
            **generated_plans_json,
            "summary": {
                "region": payload["region"],
                "start_date": payload["start_date"],
                "end_date": payload["end_date"],
                "transport": payload.get("transport", "public"),
                "crowd_mode": payload.get("crowd_mode", "default"),
                "transport_mode": request_data.transport_mode,
                "purposes": payload.get("purposes", [])
            }
        }
        
        # Plan 생성 (모델 필드에 맞춤)
        plan = Plan(
            user_id=user_id,
            region=payload["region"],
            date=payload["start_date"],  # 시작일을 date 필드에 저장
            start_time=payload.get("first_day_start_time", "09:00"),  # 기본값 설정
            duration_hours=0.0,  # 다일 여행의 경우 duration_hours 계산 필요하지만 일단 0
            transport=payload.get("transport_mode", "transport"),
            crowd_mode=payload.get("crowd_mode", "default"),
            purposes=payload.get("purposes", []),
            categories=payload.get("categories", []),
            seed_spot_ids=payload.get("seed_spot_ids"),
            variants_json=variants_with_summary  # summary 포함한 variants_json 저장
        )
        
        # DB에 저장
        plan = await self.repo.create_plan(plan)
        
        # 5. API 응답 형식으로 반환
        return {
            "plan_id": plan.plan_id,
            "summary": variants_with_summary["summary"],
            "variants": generated_plans_json  # summary 제외한 순수 variants만 반환
        }

    # -------------------------------------------------------
    # DB 의존 메서드들 (전부 주석 처리 또는 에러 처리)
    # -------------------------------------------------------
    async def get_plan(self, user_id: str, plan_id: str):
        """Plan 조회"""
        plan = await self.repo.get_plan(user_id, plan_id)
        if not plan:
            raise AppError("plan_not_found", f"Plan not found: {plan_id}", 404)
        return plan

    async def save_plan(self, user_id: str, plan_id: str, title: str | None):
        """
        저장된 플랜 생성
        - plan_id로 Plan 조회
        - 이미 저장된 플랜인지 확인 (중복 방지)
        - SavedPlan 생성 및 저장
        """
        # 1. Plan 조회 (plan_id가 "temp_no_db_id"인 경우는 variants_json이 없으므로 에러)
        plan = await self.repo.get_plan(user_id, plan_id)
        if not plan:
            raise AppError("plan_not_found", f"Plan not found: {plan_id}", 404)
        
        # 2. 이미 저장된 플랜인지 확인 (중복 방지)
        existing = await self.repo.get_saved_by_plan_id(user_id, plan_id)
        if existing:
            # 이미 저장된 경우 기존 saved_plan_id 반환
            return existing
        
        # 3. variants_json에서 start_date, end_date 추출 (API 응답 구조에 맞춤)
        variants = plan.variants_json or {}
        start_date = None
        end_date = None
        
        # variants_json에 summary가 있는 경우 (generate에서 저장한 구조)
        if isinstance(variants, dict):
            summary = variants.get("summary")
            if isinstance(summary, dict):
                start_date = summary.get("start_date")
                end_date = summary.get("end_date")
        
        # summary에서 추출 실패 시 plan.date 사용 (단일 날짜)
        if not start_date:
            start_date = plan.date
        if not end_date:
            end_date = plan.date
        
        # 4. SavedPlan 생성
        saved_plan = SavedPlan(
            user_id=user_id,
            plan_id=plan_id,
            title=title,
            region=plan.region,
            date=start_date,  # 시작일 사용
        )
        
        # 5. DB에 저장
        saved_plan = await self.repo.create_saved_plan(saved_plan)
        
        return saved_plan

    async def list_saved_plans(self, user_id: str, limit: int = 20):
        """저장된 플랜 목록 조회"""
        return await self.repo.list_saved_plans(user_id, limit=limit)

    async def get_saved_plan(self, user_id: str, saved_plan_id: str):
        """저장된 플랜 상세 조회"""
        saved_plan = await self.repo.get_saved_plan(user_id, saved_plan_id)
        if not saved_plan:
            raise AppError("saved_plan_not_found", f"Saved plan not found: {saved_plan_id}", 404)
        
        plan = await self.repo.get_plan(user_id, saved_plan.plan_id)
        if not plan:
            raise AppError("plan_not_found", f"Plan not found: {saved_plan.plan_id}", 404)
        
        return saved_plan, plan

    async def delete_saved_plan(self, user_id: str, saved_plan_id: str):
        """저장된 플랜 삭제"""
        deleted = await self.repo.delete_saved_plan(user_id, saved_plan_id)
        if not deleted:
            raise AppError("saved_plan_not_found", f"Saved plan not found: {saved_plan_id}", 404)

    async def list_saved_plans_by_spot(self, user_id: str, spot_id: str, limit: int = 20):
        """특정 장소와 연결된 저장된 플랜 목록 조회"""
        return await self.repo.list_saved_plans_by_spot(user_id, spot_id, limit=limit)

    async def recommend_alternative_spots(self, user_id: str, plan_id: str, day: str, spot_names: List[str], categories: Optional[List[str]] = None, region: Optional[str] = None):
        """
        대체 장소 추천
        - plan_id로 Plan 조회하여 지역 정보 가져오기 (없으면 region 파라미터 사용)
        - 제외할 장소들의 카테고리 추출
        - route_service를 통해 대체 장소 추천
        """
        plan_region = region
        
        # Plan 조회 시도 (plan_id가 "temp_no_db_id"인 경우는 조회 실패)
        if plan_id and plan_id != "temp_no_db_id":
            plan = await self.repo.get_plan(user_id, plan_id)
            if plan:
                plan_region = plan.region
                
                # variants_json에서 해당 날짜의 장소 정보 가져오기
                variants = plan.variants_json or {}
                day_data = variants.get(day)
                
                # 제외할 장소들의 카테고리 추출 (카테고리 미지정 시)
                if not categories and day_data:
                    route = day_data.get('route', [])
                    restaurants = day_data.get('restaurants', [])
                    accommodations = day_data.get('accommodations', [])
                    
                    all_spots = route + restaurants + accommodations
                    spot_map = {s.get('name'): s.get('category') for s in all_spots if isinstance(s, dict)}
                    categories = list(set([spot_map.get(name) for name in spot_names if spot_map.get(name)]))
        
        # region이 없으면 에러
        if not plan_region:
            raise AppError("region_required", "Region is required for alternative spot recommendation", 400)
        
        # route_service를 통해 대체 장소 추천
        alternatives = route_service.recommend_alternative_spots(
            region=plan_region,
            exclude_names=spot_names,
            categories=categories if categories else None,
            limit=10
        )
        
        return alternatives