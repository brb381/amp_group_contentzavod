import logging
import time
from collections.abc import Callable, Iterable

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


def run_iteration(dispatchers: Iterable[Dispatcher] | None = None) -> None:
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
    for name, dispatch in active_dispatchers:
        try:
            dispatch()
        except Exception:
            logger.exception("Scheduler dispatcher failed", extra={"dispatcher": name})


def run() -> None:
    configure_logging("scheduler")
    while True:
        run_iteration()
        time.sleep(settings.scheduler_poll_interval_seconds)


if __name__ == "__main__":
    run()
