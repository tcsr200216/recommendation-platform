from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    database_url: str | None = None
    redis_url: str | None = None
    cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    cache_namespace: str = "recommendations:v1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
