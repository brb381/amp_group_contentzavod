from celery import Celery

from app.contracts import EXPORTS_QUEUE, EXPORT_TASK, ExportCommand
from app.database.factory import create_session_factory
from app.exports.config import get_export_worker_settings
from app.exports.processor import execute_export
from app.exports.storage import S3ArtifactStore
from app.logging_config import configure_logging


configure_logging("export-worker")
settings = get_export_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
artifact_store = S3ArtifactStore(settings)
celery_app = Celery("amp_export_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={EXPORT_TASK: {"queue": EXPORTS_QUEUE}},
)


@celery_app.task(name=EXPORT_TASK, autoretry_for=())
def build_export(payload: dict) -> None:
    execute_export(
        ExportCommand.model_validate(payload),
        SessionLocal,
        artifact_store,
        artifact_ttl_hours=settings.export_artifact_ttl_hours,
    )
