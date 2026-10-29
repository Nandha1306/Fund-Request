from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Fund Request & Liquidation API"
    environment: str = "local"

    database_url: str = "postgresql+psycopg://fundreq:fundreq@localhost:5433/fundreq"

    jwt_secret: str = "local-dev-secret-change-me"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 7

    cors_origins: list[str] = ["http://localhost:5173"]

    media_root: str = "./media"
    max_upload_mb: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
