import logging
from functools import lru_cache

import boto3
import redis
from botocore.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.exports.config import get_export_storage_settings


logger = logging.getLogger(__name__)


@lru_cache
def _database_probe_engine():
    settings = get_settings()
    return create_engine(
        settings.database_url,
        connect_args={"connect_timeout": 2} if settings.database_url.startswith("postgresql") else {},
        poolclass=NullPool,
        hide_parameters=True,
    )


def check_database() -> None:
    with _database_probe_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def check_redis() -> None:
    settings = get_settings()
    for url in {settings.redis_url, settings.rate_limit_redis_url}:
        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()


def check_storage() -> None:
    settings = get_export_storage_settings()
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "virtual"},
            connect_timeout=2,
            read_timeout=2,
            retries={"max_attempts": 0},
        ),
    )
    client.head_bucket(Bucket=settings.s3_bucket)


def is_ready() -> bool:
    for name, check in (
        ("database", check_database),
        ("redis", check_redis),
        ("storage", check_storage),
    ):
        try:
            check()
        except Exception as error:
            logger.warning(
                "Readiness check failed component=%s error=%s",
                name, type(error).__name__,
            )
            return False
    return True
