from celery import Celery

from app.contracts import (
    TIKTOK_QUEUE,
    TIKTOK_TASK,
    TIKTOK_VIEWS_TASK,
    TikTokEnrichmentCommand,
    TikTokViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.tiktok.service import execute_tiktok_enrichment
from app.tiktok.views import execute_tiktok_view_collection
from app.tiktok_config import get_tiktok_worker_settings


configure_logging("tiktok-worker")
settings = get_tiktok_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_tiktok_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        TIKTOK_TASK: {"queue": TIKTOK_QUEUE},
        TIKTOK_VIEWS_TASK: {"queue": TIKTOK_QUEUE},
    },
)


@celery_app.task(name=TIKTOK_TASK, autoretry_for=())
def enrich_publication(payload: dict) -> None:
    execute_tiktok_enrichment(
        TikTokEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=TIKTOK_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_tiktok_view_collection(
        TikTokViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
