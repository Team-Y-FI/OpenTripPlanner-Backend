from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "OpenTripPlanner API"
    ENV: str = "dev"

    # 기존
    SECRET_KEY: str = "CHANGE_ME"

    # ✅ 추가: security.py에서 사용하는 키로 통일
    JWT_SECRET_KEY: str = "CHANGE_ME"
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # ✅ OTP 인증 완료 상태 유지 시간(초) - Redis/DB 공통 정책
    EMAIL_VERIFIED_TTL_SECONDS: int = 3600  # 1 hour

    DATABASE_URL: str
    CORS_ORIGINS: str = ""

    STORAGE_DIR: str = "./storage"
    REDIS_URL: str | None = None

    # ✅ SMTP (Gmail)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "OpenTripPlanner <no-reply@example.com>"
    SMTP_USE_TLS: bool = True          # STARTTLS(587)
    SMTP_TIMEOUT: int = 10

    # Kakao OAuth
    KAKAO_CLIENT_ID: str = ""
    KAKAO_CLIENT_SECRET: str = ""
    KAKAO_REDIRECT_URI: str = ""


settings = Settings()

# ✅ 최소 변경으로 안전하게: JWT_SECRET_KEY가 비어 있으면 SECRET_KEY 사용
if not settings.JWT_SECRET_KEY or settings.JWT_SECRET_KEY == "CHANGE_ME":
    settings.JWT_SECRET_KEY = settings.SECRET_KEY
