from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExportStorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "private-exports"
    s3_access_key: str
    s3_secret_key: str
    s3_force_path_style: bool = True


class ExportWorkerSettings(ExportStorageSettings):
    database_url: str
    redis_url: str
    export_artifact_ttl_hours: int = Field(default=24, ge=1, le=168)


@lru_cache
def get_export_storage_settings() -> ExportStorageSettings:
    return ExportStorageSettings()


@lru_cache
def get_export_worker_settings() -> ExportWorkerSettings:
    return ExportWorkerSettings()
