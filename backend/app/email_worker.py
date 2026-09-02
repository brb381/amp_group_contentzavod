from celery import Celery

from app.contracts import EMAIL_QUEUE, EMAIL_TASK, EmailDeliveryCommand
from app.database.factory import create_session_factory
from app.email.service import execute_email_delivery
from app.email_config import get_email_worker_settings
from app.logging_config import configure_logging


configure_logging("email-worker")
settings = get_email_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_email_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={EMAIL_TASK: {"queue": EMAIL_QUEUE}},
)


@celery_app.task(name=EMAIL_TASK, autoretry_for=())
def deliver_email(payload: dict) -> None:
    execute_email_delivery(
        EmailDeliveryCommand.model_validate(payload),
        settings=settings,
        session_factory=SessionLocal,
    )
