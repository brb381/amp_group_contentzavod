from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.readings.limits import MAX_BIGINT


MOSCOW = ZoneInfo("Europe/Moscow")
MAX_CALCULATION_ATTEMPTS = 5


def month_start(value: date) -> date:
    return value.replace(day=1)


def previous_month(value: date) -> date:
    return month_start(month_start(value) - timedelta(days=1))


def next_month(value: date) -> date:
    return month_start(value + timedelta(days=32))


def last_closed_period(now: datetime) -> date:
    return previous_month(now.astimezone(MOSCOW).date())


def calculation_window_open(now: datetime, delay_minutes: int) -> bool:
    local = now.astimezone(MOSCOW)
    return local.day > 1 or (
        local.day == 1 and local.hour * 60 + local.minute >= delay_minutes
    )


def accrual_amount(
    previous_value: int | None,
    current_value: int | None,
    rate_kopecks_per_view: int,
) -> tuple[int, int, str | None]:
    if current_value is None:
        return 0, 0, "missing_current_reading"
    if previous_value is None:
        return 0, 0, "baseline_only"
    eligible_views = max(0, current_value - previous_value)
    if eligible_views > MAX_BIGINT // rate_kopecks_per_view:
        raise OverflowError("accrual amount exceeds BIGINT")
    return eligible_views, eligible_views * rate_kopecks_per_view, None
