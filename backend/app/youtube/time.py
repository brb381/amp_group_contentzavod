from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")


def pacific_quota_date(now: datetime) -> date:
    return now.astimezone(PACIFIC).date()


def next_pacific_reset(now: datetime) -> datetime:
    local_now = now.astimezone(PACIFIC)
    next_day = local_now.date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=PACIFIC).astimezone(timezone.utc)
