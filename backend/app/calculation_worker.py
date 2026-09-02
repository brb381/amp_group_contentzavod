from celery import Celery

from app.billing.calculation import execute_calculation
from app.billing.config import get_calculation_worker_settings
from app.contracts import CALCULATION_TASK, CALCULATIONS_QUEUE, CalculationCommand
from app.database.factory import create_session_factory
from app.logging_config import configure_logging


configure_logging("calculation-worker")
settings = get_calculation_worker_settings()
SessionLocal = create_session_factory(settings.database_url)
celery_app = Celery("amp_calculation_worker", broker=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    task_routes={CALCULATION_TASK: {"queue": CALCULATIONS_QUEUE}},
)


@celery_app.task(name=CALCULATION_TASK, autoretry_for=())
def build_period(payload: dict) -> None:
    execute_calculation(CalculationCommand.model_validate(payload), SessionLocal)
