import calendar
from datetime import datetime, timedelta, timezone


SUSPENSION_MONTHS = 6
BLOCK_MONTHS_AFTER_SUSPENSION = 6
WARNING_DAYS = (30, 7)


def add_calendar_months(value: datetime, months: int) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def suspension_due_at(last_activity_at: datetime) -> datetime:
    return add_calendar_months(last_activity_at, SUSPENSION_MONTHS)


def block_due_at(suspended_at: datetime) -> datetime:
    return add_calendar_months(suspended_at, BLOCK_MONTHS_AFTER_SUSPENSION)


def warning_due_at(transition_due_at: datetime, days: int) -> datetime:
    if days not in WARNING_DAYS:
        raise ValueError("Unsupported lifecycle warning point")
    return transition_due_at - timedelta(days=days)
