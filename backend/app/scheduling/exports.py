import logging
import uuid
from datetime import datetime, timedelta

from celery import Celery
from sqlalchemy import select

from app.clock import utc_now
from app.contracts import EXPORTS_QUEUE, EXPORT_TASK, ExportCommand
from app.database.factory import create_session_factory
from app.exports.models import ExportJob, ExportJobStatus
from app.exports.processor import MAX_EXPORT_ATTEMPTS
from app.scheduler_config import get_scheduler_settings


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_export_scheduler", broker=settings.redis_url)
EXPORT_LEASE = timedelta(minutes=30)


def dispatch_export(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> bool:
    now = now or utc_now()
    command = None
    with session_factory() as db:
        expired_jobs = list(
            db.scalars(
                select(ExportJob)
                .where(
                    ExportJob.status.in_(
                        (ExportJobStatus.QUEUED, ExportJobStatus.PROCESSING)
                    ),
                    ExportJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired_jobs:
            job.status = (
                ExportJobStatus.RETRY_WAIT
                if job.attempt_count < MAX_EXPORT_ATTEMPTS
                else ExportJobStatus.FAILED
            )
            job.available_at = now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = "worker_lease_expired"

        job = db.scalar(
            select(ExportJob)
            .where(
                ExportJob.status.in_(
                    (ExportJobStatus.PENDING, ExportJobStatus.RETRY_WAIT)
                ),
                ExportJob.attempt_count < MAX_EXPORT_ATTEMPTS,
                ExportJob.available_at <= now,
            )
            .order_by(ExportJob.created_at, ExportJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job:
            dispatch_id = uuid.uuid4()
            job.status = ExportJobStatus.QUEUED
            job.attempt_count += 1
            job.dispatch_id = dispatch_id
            job.lease_until = now + EXPORT_LEASE
            job.last_error_code = None
            command = ExportCommand(export_id=job.id, dispatch_id=dispatch_id)
        db.commit()

    if not command:
        return False
    try:
        task_producer.send_task(
            EXPORT_TASK,
            args=[command.model_dump(mode="json")],
            queue=EXPORTS_QUEUE,
        )
        return True
    except Exception:
        with session_factory() as db:
            job = db.scalar(
                select(ExportJob)
                .where(
                    ExportJob.id == command.export_id,
                    ExportJob.dispatch_id == command.dispatch_id,
                    ExportJob.status == ExportJobStatus.QUEUED,
                )
                .with_for_update()
            )
            if job:
                job.status = ExportJobStatus.RETRY_WAIT
                job.attempt_count = max(0, job.attempt_count - 1)
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
                db.commit()
        logger.exception(
            "Could not publish export command",
            extra={"export_id": str(command.export_id)},
        )
        return False
