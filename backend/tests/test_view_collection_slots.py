from datetime import datetime, timezone

from app.readings.view_slots import current_view_collection_slot
from app.scheduling.view_collection import view_job_is_obsolete


def test_view_collection_slot_uses_two_hour_moscow_windows():
    first = current_view_collection_slot(
        datetime(2026, 8, 10, 10, 5, tzinfo=timezone.utc)
    )
    same_window = current_view_collection_slot(
        datetime(2026, 8, 10, 10, 59, tzinfo=timezone.utc)
    )
    next_window = current_view_collection_slot(
        datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)
    )

    assert (first.collection_date.isoformat(), first.index) == ("2026-08-10", 6)
    assert same_window == first
    assert (next_window.collection_date.isoformat(), next_window.index) == (
        "2026-08-10",
        7,
    )


def test_view_collection_slot_rolls_over_at_moscow_midnight():
    slot = current_view_collection_slot(
        datetime(2026, 8, 10, 21, 0, tzinfo=timezone.utc)
    )

    assert (slot.collection_date.isoformat(), slot.index) == ("2026-08-11", 0)


def test_previous_collection_slot_is_obsolete():
    current = current_view_collection_slot(
        datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    )

    class Job:
        collection_date = current.collection_date
        collection_slot = current.index - 1

    assert view_job_is_obsolete(Job(), current)


def test_current_collection_slot_is_not_obsolete():
    current = current_view_collection_slot(
        datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    )

    class Job:
        collection_date = current.collection_date
        collection_slot = current.index

    assert not view_job_is_obsolete(Job(), current)
