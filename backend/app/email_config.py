from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.smtp import validate_smtp_transport


class EmailWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str
    redis_url: str
    smtp_host: str
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_from_email: str
    smtp_username: str | None = None
    smtp_password: str | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "EmailWorkerSettings":
        validate_smtp_transport(
            environment=self.environment,
            host=self.smtp_host,
            from_email=self.smtp_from_email,
            use_tls=self.smtp_use_tls,
            use_ssl=self.smtp_use_ssl,
            username=self.smtp_username,
            password=self.smtp_password,
        )
        return self


@lru_cache
def get_email_worker_settings() -> EmailWorkerSettings:
    return EmailWorkerSettings()
