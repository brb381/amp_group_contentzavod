import logging
import uuid
from datetime import datetime, timedelta

from celery import Celery
from sqlalchemy import func, select

from app.billing.models import CalculationJob, CalculationPeriod, RateVersion
from app.billing.locking import lock_billing_control
from app.billing.policy import (
    MAX_CALCULATION_ATTEMPTS,
    calculation_window_open,
    last_closed_period,
    next_month,
)
from app.clock import utc_now
from app.contracts import (
    CALCULATION_TASK,
    CALCULATIONS_QUEUE,
    CalculationCommand,
)
from app.database.factory import create_session_factory
from app.readings.models import ReadingStatus, ViewReading
from app.scheduler_config import get_scheduler_settings


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_calculation_scheduler", broker=settings.redis_url)
CALCULATION_LEASE = timedelta(minutes=30)


def _ensure_closed_period_job(db, now: datetime) -> None:
    if not calculation_window_open(now, settings.calculation_close_delay_minutes):
        return
    lock_billing_control(db)
    last_closed = last_closed_period(now)
    latest_period = db.scalar(select(func.max(CalculationPeriod.period)))
    if latest_period:
        period_value = next_month(latest_period)
        if period_value > last_closed:
            return
    else:
        first_reading_period = db.scalar(
            select(func.min(ViewReading.reporting_period)).where(
                ViewReading.status == ReadingStatus.ACCEPTED
            )
        )
        period_value = min(first_reading_period or last_closed, last_closed)
    period = db.scalar(
        select(CalculationPeriod).where(CalculationPeriod.period == period_value)
    )
    if period:
        return
    rate = db.scalar(
        select(RateVersion)
        .where(RateVersion.effective_from_period <= period_value)
        .order_by(RateVersion.effective_from_period.desc(), RateVersion.created_at.desc())
        .limit(1)
    )
    if not rate:
        logger.error("No billing rate is configured", extra={"period": period_value.isoformat()})
        return
    period = CalculationPeriod(
        period=period_value,
        status="pending",
        rate_version_id=rate.id,
    )
    db.add(period)
    db.flush()
    db.add(CalculationJob(period_id=period.id, state="pending", available_at=now))
    db.flush()


def dispatch_calculation(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> bool:
    now = now or utc_now()
    db = session_factory()
    command: CalculationCommand | None = None
    try:
        _ensure_closed_period_job(db, now)
        expired = list(
            db.scalars(
                select(CalculationJob)
                .where(
                    CalculationJob.state.in_(("queued", "processing")),
                    CalculationJob.lease_until < now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            job.state = (
                "retry_wait"
                if job.attempt_count < MAX_CALCULATION_ATTEMPTS
                else "failed"
            )
            job.available_at = now
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = "worker_lease_expired"

        active_job = db.scalar(
            select(CalculationJob.id)
            .where(
                CalculationJob.state.in_(("queued", "processing")),
                CalculationJob.lease_until >= now,
            )
            .limit(1)
        )
        if active_job:
            db.commit()
            return False

        job = db.scalar(
            select(CalculationJob)
            .where(
                CalculationJob.state.in_(("pending", "retry_wait")),
                CalculationJob.attempt_count < MAX_CALCULATION_ATTEMPTS,
                CalculationJob.available_at <= now,
            )
            .order_by(CalculationJob.created_at, CalculationJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job:
            dispatch_id = uuid.uuid4()
            job.state = "queued"
            job.attempt_count += 1
            job.lease_until = now + CALCULATION_LEASE
            job.dispatch_id = dispatch_id
            job.last_error_code = None
            command = CalculationCommand(period_id=job.period_id, dispatch_id=dispatch_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if not command:
        return False
    try:
        task_producer.send_task(
            CALCULATION_TASK,
            args=[command.model_dump(mode="json")],
            queue=CALCULATIONS_QUEUE,
        )
        return True
    except Exception:
        db = session_factory()
        try:
            job = db.scalar(
                select(CalculationJob)
                .where(
                    CalculationJob.period_id == command.period_id,
                    CalculationJob.dispatch_id == command.dispatch_id,
                    CalculationJob.state == "queued",
                )
                .with_for_update()
            )
            if job:
                job.state = "retry_wait"
                job.attempt_count = max(0, job.attempt_count - 1)
                job.available_at = now
                job.lease_until = None
                job.dispatch_id = None
                job.last_error_code = "broker_publish_failed"
                db.commit()
        finally:
            db.close()
        logger.exception(
            "Could not publish calculation command",
            extra={"period_id": str(command.period_id), "dispatch_id": str(command.dispatch_id)},
        )
        return False
