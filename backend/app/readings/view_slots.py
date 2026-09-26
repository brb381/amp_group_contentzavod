from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


MOSCOW = ZoneInfo("Europe/Moscow")
VIEW_COLLECTION_INTERVAL_HOURS = 2
VIEW_COLLECTION_SLOTS_PER_DAY = 24 // VIEW_COLLECTION_INTERVAL_HOURS


@dataclass(frozen=True)
class ViewCollectionSlot:
    collection_date: date
    index: int


def current_view_collection_slot(now: datetime) -> ViewCollectionSlot:
    local = now.astimezone(MOSCOW)
    return ViewCollectionSlot(
        collection_date=local.date(),
        index=local.hour // VIEW_COLLECTION_INTERVAL_HOURS,
    )


def view_reading_idempotency_key(prefix: str, collection_date: date, slot: int) -> str:
    return f"{prefix}:{collection_date.isoformat()}:slot-{slot:02d}"