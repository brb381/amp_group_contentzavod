from sqlalchemy import select

from app.readings.models import ReadingDatasetRevision
from app.errors import APIError


def lock_reading_dataset_revision(db):
    revision = db.scalar(
        select(ReadingDatasetRevision)
        .where(ReadingDatasetRevision.id == 1)
        .with_for_update()
    )
    if revision is None:
        revision = ReadingDatasetRevision(id=1, revision=0)
        db.add(revision)
        db.flush()
    return revision


def bump_reading_dataset_revision(db) -> int:
    revision = lock_reading_dataset_revision(db)
    revision.revision += 1
    db.flush()
    return revision.revision


def lock_reading_period_for_mutation(db, reporting_period):
    revision = lock_reading_dataset_revision(db)
    if (
        revision.closed_through_period is not None
        and reporting_period <= revision.closed_through_period
    ):
        raise APIError(
            409,
            "READING_PERIOD_FINANCIALLY_CLOSED",
            "The reading belongs to a financially closed period",
        )
    revision.revision += 1
    db.flush()
    return revision
