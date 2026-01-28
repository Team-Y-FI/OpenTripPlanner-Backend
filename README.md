# OpenTripPlanner Backend (FastAPI Skeleton)

## 외부 Postgres 사용 (RDS / Supabase / Neon 등)

이 버전은 **DB 컨테이너를 띄우지 않습니다.** 대신 외부 Postgres에 연결합니다.

1. 외부 Postgres 준비 (예: RDS/Supabase/Neon)
2. `.env`에 `DATABASE_URL`을 외부 DB로 설정
3. 마이그레이션 실행:

```bash
alembic upgrade head
```

> 참고: Redis는 선택입니다. 코스 생성 캐시/작업큐/레이트리밋이 필요 없으면 `redis` 서비스도 제거해도 됩니다.

기능명세/ API명세 기반 FastAPI 백엔드 스켈레톤입니다.

- FastAPI + SQLAlchemy(Async) + Alembic + JWT(access/refresh)
- Uploads / Photos / Records(Spots) / Plans(A/B) / Meta / Auth(로그인/회원가입)
- 기본 저장소는 로컬 파일시스템(storage/)로 구현되어 있으며, S3/R2/MinIO로 쉽게 교체 가능하도록 추상화되어 있습니다.

## 1) 빠른 실행 (Docker, 외부 DB)

```bash
cd backend
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000/docs

## 2) 로컬 실행 (Python)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # windows는 .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## 3) DB 마이그레이션 (Alembic)

```bash
cd backend
alembic upgrade head
```

## 4) 설계 메모

- refresh token은 DB에 해시로 저장/폐기(revoke) 가능
- Plans 결과(A/B)는 JSON 형태로 `plans.variants_json`에 저장(MVP 속도 우선)
- Spot ↔ SavedPlan 관계는 `plan_spot_links`로 연결(Seed Spot 기반)

## 5) 주요 엔드포인트

- Auth: /otp/auth/register, /otp/auth/login, /otp/auth/refresh, /otp/auth/logout, /otp/users/me
- Uploads: /otp/uploads/photos, /otp/uploads/{upload_id}, /otp/photos/{photo_id}/place
- Records: /otp/records/spots, /otp/records/spots/{spot_id}, /otp/records/spots/{spot_id}/plans, /otp/records/plans
- Plans: /otp/plans/generate, /otp/plans/{plan_id}
- Meta: /otp/meta/options
