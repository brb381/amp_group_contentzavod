from celery import Celery

from app.contracts import (
    INSTAGRAM_QUEUE,
    INSTAGRAM_TASK,
    INSTAGRAM_VIEWS_TASK,
    InstagramEnrichmentCommand,
    InstagramViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.instagram.service import execute_instagram_enrichment
from app.instagram.views import execute_instagram_view_collection
from app.instagram_config import get_instagram_worker_settings


configure_logging("instagram-worker")
settings = get_instagram_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_instagram_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        INSTAGRAM_TASK: {"queue": INSTAGRAM_QUEUE},
        INSTAGRAM_VIEWS_TASK: {"queue": INSTAGRAM_QUEUE},
    },
)


@celery_app.task(name=INSTAGRAM_TASK, autoretry_for=())
def enrich_publication(payload: dict) -> None:
    execute_instagram_enrichment(
        InstagramEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=INSTAGRAM_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_instagram_view_collection(
        InstagramViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
