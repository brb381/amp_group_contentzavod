from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class YouTubeWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    youtube_api_key: SecretStr | None = None
    youtube_request_timeout_seconds: int = Field(default=15, ge=1, le=60)
    suspicious_monthly_view_growth: int = Field(default=500_000, ge=1)


@lru_cache
def get_youtube_worker_settings() -> YouTubeWorkerSettings:
    return YouTubeWorkerSettings()
