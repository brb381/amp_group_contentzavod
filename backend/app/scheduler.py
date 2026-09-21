import logging
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

import redis

from app.scheduler_config import get_scheduler_settings
from app.logging_config import configure_logging
from app.scheduling.email import dispatch_email_events
from app.scheduling.calculations import dispatch_calculation
from app.scheduling.youtube import dispatch_youtube_batch, dispatch_youtube_view_batch
from app.scheduling.exports import dispatch_export
from app.scheduling.lifecycle import dispatch_lifecycle


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
Dispatcher = tuple[str, Callable[[], object]]


def run_iteration(
    dispatchers: Iterable[Dispatcher] | None = None,
    report: Callable[[dict[str, bool]], None] | None = None,
) -> None:
    active_dispatchers = (
        dispatchers
        if dispatchers is not None
        else (
            ("email", dispatch_email_events),
            ("calculations", dispatch_calculation),
            ("exports", dispatch_export),
            ("lifecycle", dispatch_lifecycle),
            ("youtube-views", dispatch_youtube_view_batch),
            ("youtube", dispatch_youtube_batch),
        )
    )
    outcomes: dict[str, bool] = {}
    for name, dispatch in active_dispatchers:
        try:
            dispatch()
            outcomes[name] = True
        except Exception:
            outcomes[name] = False
            logger.exception("Scheduler dispatcher failed", extra={"dispatcher": name})
    if report is not None:
        try:
            report(outcomes)
        except Exception:
            logger.exception("Scheduler heartbeat publish failed")


def run() -> None:
    configure_logging("scheduler")
    client = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )

    def report(outcomes: dict[str, bool]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        values = {
            f"{name}:{'success' if succeeded else 'failure'}": now
            for name, succeeded in outcomes.items()
        }
        client.hset("amp:monitor:scheduler", mapping=values)
        client.expire("amp:monitor:scheduler", 3600)

    while True:
        run_iteration(report=report)
        time.sleep(settings.scheduler_poll_interval_seconds)


if __name__ == "__main__":
    run()
