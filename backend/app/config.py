from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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

    @model_validator(mode="after")
    def validate_deployment(self) -> "Settings":
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("ENVIRONMENT must be development, test, or production")
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            frontend = urlsplit(self.frontend_url)
            if frontend.scheme != "https" or not frontend.hostname:
                raise ValueError("FRONTEND_URL must be an HTTPS URL in production")
            if frontend.hostname in {"localhost", "127.0.0.1"}:
                raise ValueError("FRONTEND_URL must use a public hostname in production")
            if len(self.jwt_secret.encode("utf-8")) < 48 or self.jwt_secret.lower().startswith(
                ("replace-", "local-development", "test-secret")
            ):
                raise ValueError("JWT_SECRET must be a unique random production secret")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
