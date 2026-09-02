import logging
from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.errors import APIError


logger = logging.getLogger(__name__)

CONSUME_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class RedisRateLimiter:
    def __init__(self, url: str):
        self.client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)

    def consume(self, key: str, *, limit: int, window_seconds: int) -> int | None:
        count, ttl = self.client.eval(CONSUME_SCRIPT, 1, key, window_seconds)
        return max(int(ttl), 1) if int(count) > limit else None

    def clear(self, key: str) -> None:
        self.client.delete(key)


@lru_cache
def get_rate_limiter() -> RedisRateLimiter:
    return RedisRateLimiter(get_settings().rate_limit_redis_url)


def enforce_rate_limit(
    limiter: RedisRateLimiter,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    fail_closed: bool,
) -> None:
    try:
        retry_after = limiter.consume(key, limit=limit, window_seconds=window_seconds)
    except RedisError as error:
        logger.warning("Rate limiter unavailable: %s", type(error).__name__)
        if fail_closed:
            raise APIError(503, "RATE_LIMITER_UNAVAILABLE", "Request protection is temporarily unavailable") from error
        return
    if retry_after is not None:
        raise APIError(
            429,
            "RATE_LIMIT_EXCEEDED",
            "Too many attempts",
            {"retry_after": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


def clear_rate_limit(limiter: RedisRateLimiter, key: str) -> None:
    try:
        limiter.clear(key)
    except RedisError as error:
        logger.warning("Rate limiter unavailable while clearing a key: %s", type(error).__name__)
