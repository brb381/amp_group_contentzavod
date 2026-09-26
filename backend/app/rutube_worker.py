from celery import Celery

from app.contracts import (
    RUTUBE_QUEUE,
    RUTUBE_TASK,
    RUTUBE_VIEWS_TASK,
    RutubeEnrichmentCommand,
    RutubeViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.rutube.service import execute_rutube_enrichment
from app.rutube.views import execute_rutube_view_collection
from app.rutube_config import get_rutube_worker_settings


configure_logging("rutube-worker")
settings = get_rutube_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_rutube_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        RUTUBE_TASK: {"queue": RUTUBE_QUEUE},
        RUTUBE_VIEWS_TASK: {"queue": RUTUBE_QUEUE},
    },
)


@celery_app.task(name=RUTUBE_TASK, autoretry_for=())
def enrich_publication(payload: dict) -> None:
    execute_rutube_enrichment(
        RutubeEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=RUTUBE_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_rutube_view_collection(
        RutubeViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
