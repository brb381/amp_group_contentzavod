from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str
    jwt_secret: str
    jwt_issuer: str = "amp-content-factory"
    jwt_audience: str = "amp-content-factory-web"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    email_verification_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 30
    account_deletion_confirmation_ttl_hours: int = Field(default=24, ge=1, le=168)
    cookie_secure: bool = True
    redis_url: str
    rate_limit_redis_url: str
    frontend_url: str
    suspicious_monthly_view_growth: int = Field(default=500_000, ge=1)

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET must be at least 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
