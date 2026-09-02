from celery import Celery

from app.contracts import LIFECYCLE_QUEUE, LIFECYCLE_TASK, LifecycleCommand
from app.database.factory import create_session_factory
from app.lifecycle.processor import execute_lifecycle_job
from app.logging_config import configure_logging
from app.scheduler_config import get_scheduler_settings


configure_logging("lifecycle-worker")
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_lifecycle_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={LIFECYCLE_TASK: {"queue": LIFECYCLE_QUEUE}},
)


@celery_app.task(name=LIFECYCLE_TASK, autoretry_for=())
def execute_lifecycle(payload: dict) -> None:
    execute_lifecycle_job(LifecycleCommand.model_validate(payload), SessionLocal)
