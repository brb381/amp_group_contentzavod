from celery import Celery

from app.youtube_config import get_youtube_worker_settings
from app.contracts import YOUTUBE_QUEUE, YOUTUBE_TASK, YOUTUBE_VIEWS_TASK, YouTubeEnrichmentCommand, YouTubeViewCollectionCommand
from app.database.factory import create_session_factory
from app.logging_config import configure_logging
from app.youtube.service import execute_youtube_enrichment
from app.youtube.views import execute_youtube_view_collection


configure_logging("youtube-worker")
settings = get_youtube_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_youtube_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={
        YOUTUBE_TASK: {"queue": YOUTUBE_QUEUE},
        YOUTUBE_VIEWS_TASK: {"queue": YOUTUBE_QUEUE},
    },
)


@celery_app.task(name=YOUTUBE_TASK, autoretry_for=())
def enrich_publications(payload: dict) -> None:
    execute_youtube_enrichment(
        YouTubeEnrichmentCommand.model_validate(payload), settings, SessionLocal
    )


@celery_app.task(name=YOUTUBE_VIEWS_TASK, autoretry_for=())
def collect_views(payload: dict) -> None:
    execute_youtube_view_collection(
        YouTubeViewCollectionCommand.model_validate(payload), settings, SessionLocal
    )
