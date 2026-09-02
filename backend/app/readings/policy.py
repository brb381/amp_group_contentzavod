import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.readings.models import ReadingStatus, ViewReading


MOSCOW = ZoneInfo("Europe/Moscow")


def reporting_period(now: datetime) -> date:
    local = now.astimezone(MOSCOW)
    return date(local.year, local.month, 1)


def manual_submission_open(now: datetime) -> bool:
    return now.astimezone(MOSCOW).day >= 25


def risk_flags(
    db,
    *,
    publication_id: uuid.UUID,
    period: date,
    value: int,
    suspicious_growth_threshold: int,
) -> list[str]:
    start = datetime(period.year, period.month, 1, tzinfo=MOSCOW).astimezone(timezone.utc)
    accepted_points = select(ViewReading.accepted_value).where(
        ViewReading.publication_id == publication_id,
        ViewReading.status == ReadingStatus.ACCEPTED,
        ViewReading.accepted_value.is_not(None),
    )
    last_accepted = db.scalar(
        accepted_points.order_by(
            ViewReading.captured_at.desc(), ViewReading.id.desc()
        ).limit(1)
    )
    period_baseline = db.scalar(
        select(ViewReading.accepted_value)
        .where(
            ViewReading.publication_id == publication_id,
            ViewReading.status == ReadingStatus.ACCEPTED,
            ViewReading.accepted_value.is_not(None),
            ViewReading.captured_at < start,
        )
        .order_by(ViewReading.captured_at.desc(), ViewReading.id.desc())
        .limit(1)
    )
    if last_accepted is None:
        return []
    flags = []
    if value < last_accepted:
        flags.append("views_decreased")
    if (
        period_baseline is not None
        and value - period_baseline >= suspicious_growth_threshold
    ):
        flags.append("unusual_growth")
    return flags
