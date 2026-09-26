from celery import Celery

from app.contracts import (
    VK_QUEUE,
    VK_TASK,
    VK_VIEWS_TASK,
    VKEnrichmentCommand,
    VKViewCollectionCommand,
)
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.vk.service import execute_vk_enrichment
from app.vk.views import execute_vk_view_collection
from app.vk_config import get_vk_worker_settings


configure_logging("vk-worker")
settings = get_vk_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_vk_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        VK_TASK: {"queue": VK_QUEUE},
        VK_VIEWS_TASK: {"queue": VK_QUEUE},
    },
)


@celery_app.task(name=VK_TASK, autoretry_for=())
def enrich_publication(payload: dict) -> None:
    execute_vk_enrichment(
        VKEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=VK_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_vk_view_collection(
        VKViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
