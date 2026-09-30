from datetime import datetime

from sqlalchemy import and_, or_, select

from app.readings.view_slots import ViewCollectionSlot, current_view_collection_slot


def view_job_is_obsolete(job, current_slot: ViewCollectionSlot) -> bool:
    return (job.collection_date, job.collection_slot) < (
        current_slot.collection_date,
        current_slot.index,
    )


def expire_obsolete_view_jobs(db, model, now: datetime) -> int:
    current_slot = current_view_collection_slot(now)
    jobs = list(
        db.scalars(
            select(model)
            .where(
                model.state.in_(("pending", "retry_wait")),
                or_(
                    model.collection_date < current_slot.collection_date,
                    and_(
                        model.collection_date == current_slot.collection_date,
                        model.collection_slot < current_slot.index,
                    ),
                ),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        if not view_job_is_obsolete(job, current_slot):
            continue
        job.state = "failed"
        job.last_error_code = "collection_slot_expired"
        job.lease_until = None
        job.dispatch_id = None
    return len(jobs)
