from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "OpenTripPlanner API"
    ENV: str = "dev"

    SECRET_KEY: str = "CHANGE_ME"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    DATABASE_URL: str
    CORS_ORIGINS: str = ""

    STORAGE_DIR: str = "./storage"
    REDIS_URL: str | None = None

settings = Settings()
