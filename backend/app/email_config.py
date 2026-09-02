from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str
    smtp_host: str
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_use_tls: bool = True
    smtp_from_email: str
    smtp_username: str | None = None
    smtp_password: str | None = None


@lru_cache
def get_email_worker_settings() -> EmailWorkerSettings:
    return EmailWorkerSettings()
