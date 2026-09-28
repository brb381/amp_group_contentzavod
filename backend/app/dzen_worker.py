from celery import Celery

from app.contracts import (
    DZEN_QUEUE,
    DZEN_TASK,
    DZEN_VIEWS_TASK,
    DzenEnrichmentCommand,
    DzenViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.dzen.service import execute_dzen_enrichment
from app.dzen.views import execute_dzen_view_collection
from app.dzen_config import get_dzen_worker_settings


configure_logging("dzen-worker")
settings = get_dzen_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_dzen_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        DZEN_TASK: {"queue": DZEN_QUEUE},
        DZEN_VIEWS_TASK: {"queue": DZEN_QUEUE},
    },
)


@celery_app.task(name=DZEN_TASK, autoretry_for=())
def enrich_publication(payload: dict) -> None:
    execute_dzen_enrichment(
        DzenEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=DZEN_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_dzen_view_collection(
        DzenViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
