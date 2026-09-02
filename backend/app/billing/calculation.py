import logging
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select

from app.auth.models import AccountStatus, User
from app.billing.models import (
    CalculationJob,
    CalculationPeriod,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.billing.policy import MAX_CALCULATION_ATTEMPTS, accrual_amount
from app.clock import utc_now
from app.content.models import Publication, VideoCard
from app.contracts import CalculationCommand
from app.readings.models import ReadingStatus, ViewReading
from app.readings.revision import lock_reading_dataset_revision


logger = logging.getLogger(__name__)
def _claim(command: CalculationCommand, session_factory) -> bool:
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
        if not job:
            db.rollback()
            return False
        job.state = "processing"
        db.commit()
        return True
    finally:
        db.close()


def _selected_readings(db, period_value):
    rows = db.execute(
        select(ViewReading, VideoCard.blogger_id)
        .join(Publication, Publication.id == ViewReading.publication_id)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .join(User, User.id == VideoCard.blogger_id)
        .where(
            User.status != AccountStatus.DELETED,
            ViewReading.status == ReadingStatus.ACCEPTED,
            ViewReading.accepted_value.is_not(None),
            ViewReading.reporting_period <= period_value,
        )
        .order_by(
            ViewReading.publication_id,
            ViewReading.reporting_period,
            ViewReading.captured_at,
            ViewReading.id,
        )
    ).all()
    grouped = defaultdict(list)
    owners = {}
    for reading, blogger_id in rows:
        grouped[reading.publication_id].append(reading)
        owners[reading.publication_id] = blogger_id
    return grouped, owners


def _reading_pair(readings: list[ViewReading], period_value):
    before = [item for item in readings if item.reporting_period < period_value]
    current = [item for item in readings if item.reporting_period == period_value]
    if not current:
        return (before[-1] if before else None), None
    if before:
        return before[-1], current[-1]
    if len(current) > 1:
        return current[0], current[-1]
    return None, current[0]


def _build(command: CalculationCommand, session_factory) -> None:
    db = session_factory()
    try:
        revision = lock_reading_dataset_revision(db)
        period = db.scalar(
            select(CalculationPeriod)
            .where(CalculationPeriod.id == command.period_id)
            .with_for_update()
        )
        job = db.scalar(
            select(CalculationJob)
            .where(
                CalculationJob.period_id == command.period_id,
                CalculationJob.dispatch_id == command.dispatch_id,
                CalculationJob.state == "processing",
            )
            .with_for_update()
        )
        if not period or not job:
            db.rollback()
            return
        if getattr(period.status, "value", period.status) == "confirmed":
            job.state = "failed"
            job.last_error_code = "period_already_confirmed"
            job.lease_until = None
            job.dispatch_id = None
            db.commit()
            return
        rate = db.get(RateVersion, period.rate_version_id)
        if not rate:
            raise RuntimeError("calculation_rate_missing")

        grouped, owners = _selected_readings(db, period.period)
        db.execute(delete(CreatorPeriodTotal).where(CreatorPeriodTotal.period_id == period.id))
        db.execute(delete(PublicationAccrual).where(PublicationAccrual.period_id == period.id))

        totals = defaultdict(
            lambda: {"eligible_views": 0, "amount_kopecks": 0, "publication_count": 0, "risk_count": 0}
        )
        for publication_id in sorted(grouped, key=str):
            previous, current = _reading_pair(grouped[publication_id], period.period)
            previous_value = previous.accepted_value if previous else None
            current_value = current.accepted_value if current else None
            eligible_views, amount_kopecks, exclusion_reason = accrual_amount(
                previous_value,
                current_value,
                rate.rate_kopecks_per_view,
            )
            risk_flags = list(current.risk_flags or []) if current else []
            blogger_id = owners[publication_id]
            db.add(
                PublicationAccrual(
                    period_id=period.id,
                    publication_id=publication_id,
                    blogger_id=blogger_id,
                    previous_reading_id=previous.id if previous else None,
                    previous_value=previous_value,
                    previous_reading_updated_at=previous.updated_at if previous else None,
                    current_reading_id=current.id if current else None,
                    current_value=current_value,
                    current_reading_updated_at=current.updated_at if current else None,
                    eligible_views=eligible_views,
                    rate_kopecks_per_view=rate.rate_kopecks_per_view,
                    amount_kopecks=amount_kopecks,
                    exclusion_reason=exclusion_reason,
                    risk_flags=risk_flags,
                )
            )
            total = totals[blogger_id]
            total["eligible_views"] += eligible_views
            total["amount_kopecks"] += amount_kopecks
            total["publication_count"] += 1
            total["risk_count"] += int(bool(risk_flags))

        for blogger_id in sorted(totals, key=str):
            db.add(CreatorPeriodTotal(period_id=period.id, blogger_id=blogger_id, **totals[blogger_id]))

        now = utc_now()
        period.status = "preliminary"
        period.input_revision = revision.revision
        period.calculated_at = now
        period.total_views = sum(item["eligible_views"] for item in totals.values())
        period.total_amount_kopecks = sum(item["amount_kopecks"] for item in totals.values())
        period.total_adjustment_kopecks = 0
        job.state = "succeeded"
        job.lease_until = None
        job.dispatch_id = None
        job.last_error_code = None
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _record_failure(command: CalculationCommand, session_factory) -> None:
    db = session_factory()
    try:
        job = db.scalar(
            select(CalculationJob)
            .where(
                CalculationJob.period_id == command.period_id,
                CalculationJob.dispatch_id == command.dispatch_id,
                CalculationJob.state == "processing",
            )
            .with_for_update()
        )
        if not job:
            return
        now = utc_now()
        job.state = (
            "retry_wait"
            if job.attempt_count < MAX_CALCULATION_ATTEMPTS
            else "failed"
        )
        job.available_at = now + timedelta(minutes=5)
        job.lease_until = None
        job.dispatch_id = None
        job.last_error_code = "calculation_failed"
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not persist calculation failure")
    finally:
        db.close()


def execute_calculation(command: CalculationCommand, session_factory) -> None:
    if not _claim(command, session_factory):
        return
    try:
        _build(command, session_factory)
    except Exception:
        logger.exception("Calculation failed", extra={"period_id": str(command.period_id)})
        _record_failure(command, session_factory)
