from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CalculationWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str


@lru_cache
def get_calculation_worker_settings() -> CalculationWorkerSettings:
    return CalculationWorkerSettings()
