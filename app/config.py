import os
import re
from pydantic_settings import BaseSettings


def _parse_database_url(raw: str) -> str:
    """Convert Render's postgres:// URL to asyncpg-compatible format."""
    if raw.startswith("postgresql+asyncpg://"):
        return raw
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/face_signin"
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    BAIDU_API_KEY: str = ""
    BAIDU_SECRET_KEY: str = ""
    BAIDU_GROUP_ID: str = "default"

    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 10

    model_config = {"env_file": ".env"}


settings = Settings()
settings.DATABASE_URL = _parse_database_url(settings.DATABASE_URL)
