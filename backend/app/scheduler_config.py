from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    youtube_daily_working_limit: int = Field(default=9000, ge=1, le=10000)
    scheduler_poll_interval_seconds: float = Field(default=2.0, ge=0.1, le=60)
    youtube_collection_hour_moscow: int = Field(default=23, ge=0, le=23)
    calculation_close_delay_minutes: int = Field(default=5, ge=0, le=1440)


@lru_cache
def get_scheduler_settings() -> SchedulerSettings:
    return SchedulerSettings()
