import math
import uuid
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.content.models import Publication, PublicationStatus, VideoCard
from app.content.service import lock_content_writer
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.lifecycle.activity import record_creator_activity
from app.lifecycle.models import ActivityKind
from app.platforms import Platform
from app.readings.models import ReadingSource, ReadingStatus, ViewReading, ViewReadingHistory
from app.readings.policy import manual_submission_open, reporting_period, risk_flags
from app.readings.revision import lock_reading_period_for_mutation
from app.readings.schemas import (
    ReadingCorrectionRequest,
    ReadingReviewRequest,
    ViewReadingDetailResponse,
    ViewReadingListResponse,
    ViewReadingResponse,
)


REVIEWER_ROLES = {Role.MODERATOR, Role.MANAGER, Role.ADMIN}


def _history(
    db: Session,
    reading: ViewReading,
    action: str,
    *,
    actor_user_id: uuid.UUID | None,
    old_value: int | None = None,
    new_value: int | None = None,
    reason: str | None = None,
) -> None:
    db.add(
        ViewReadingHistory(
            reading_id=reading.id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            actor_user_id=actor_user_id,
        )
    )


def reading_detail(db: Session, reading: ViewReading) -> ViewReadingDetailResponse:
    db.flush()
    history = list(
        db.scalars(
            select(ViewReadingHistory)
            .where(ViewReadingHistory.reading_id == reading.id)
            .order_by(ViewReadingHistory.created_at.desc(), ViewReadingHistory.id.desc())
        )
    )
    return ViewReadingDetailResponse(
        **ViewReadingResponse.model_validate(reading).model_dump(),
        history=history,
    )


def create_manual_reading(
    db: Session,
    *,
    actor: User,
    publication_id: uuid.UUID,
    value: int,
    suspicious_growth_threshold: int,
    audit_context: AuditContext,
    now: datetime | None = None,
) -> ViewReading:
    now = now or utc_now()
    if not manual_submission_open(now):
        raise APIError(409, "READING_WINDOW_CLOSED", "Manual readings are accepted from the 25th through month end")
    locked_actor = lock_content_writer(db, actor.id)
    row = db.execute(
        select(Publication, VideoCard)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .where(
            Publication.id == publication_id,
            Publication.deleted_at.is_(None),
            VideoCard.blogger_id == locked_actor.id,
        )
        .with_for_update()
    ).one_or_none()
    if not row:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    publication, _ = row
    if publication.status != PublicationStatus.APPROVED:
        raise APIError(409, "PUBLICATION_NOT_ACTIVE", "Only approved publications accept readings")
    if publication.platform == Platform.YOUTUBE:
        raise APIError(409, "READING_IS_AUTOMATIC", "YouTube readings are collected automatically")

    period = reporting_period(now)
    lock_reading_period_for_mutation(db, period)
    key = f"manual:{period.isoformat()}"
    existing = db.scalar(
        select(ViewReading).where(
            ViewReading.publication_id == publication.id,
            ViewReading.idempotency_key == key,
        )
    )
    if existing:
        raise APIError(409, "READING_ALREADY_EXISTS", "A manual reading already exists for this period")
    reading = ViewReading(
        publication_id=publication.id,
        reporting_period=period,
        source=ReadingSource.MANUAL,
        reported_value=value,
        accepted_value=None,
        status=ReadingStatus.PENDING,
        risk_flags=risk_flags(
            db,
            publication_id=publication.id,
            period=period,
            value=value,
            suspicious_growth_threshold=suspicious_growth_threshold,
        ),
        idempotency_key=key,
        captured_at=now,
        submitted_by_user_id=locked_actor.id,
    )
    db.add(reading)
    db.flush()
    _history(db, reading, "created", actor_user_id=locked_actor.id, new_value=value)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIEW_READING_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="view_reading",
        object_id=reading.id,
        metadata={"publication_id": str(publication.id), "source": "manual"},
    )
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.READING_SUBMITTED,
        occurred_at=now,
    )
    return reading


def update_manual_reading(
    db: Session,
    *,
    actor: User,
    reading_id: uuid.UUID,
    value: int,
    suspicious_growth_threshold: int,
    audit_context: AuditContext,
    now: datetime | None = None,
) -> ViewReading:
    now = now or utc_now()
    if not manual_submission_open(now):
        raise APIError(409, "READING_WINDOW_CLOSED", "Manual readings are accepted from the 25th through month end")
    locked_actor = lock_content_writer(db, actor.id)
    candidate = db.get(ViewReading, reading_id)
    if not candidate or candidate.submitted_by_user_id != locked_actor.id:
        raise APIError(404, "VIEW_READING_NOT_FOUND", "View reading was not found")
    lock_reading_period_for_mutation(db, candidate.reporting_period)
    reading = db.scalar(
        select(ViewReading)
        .where(ViewReading.id == reading_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reading.submitted_by_user_id != locked_actor.id:
        raise APIError(404, "VIEW_READING_NOT_FOUND", "View reading was not found")
    if reading.source != ReadingSource.MANUAL or reading.status != ReadingStatus.PENDING:
        raise APIError(409, "VIEW_READING_NOT_EDITABLE", "View reading cannot be edited")
    if reading.reporting_period != reporting_period(now):
        raise APIError(409, "VIEW_READING_PERIOD_CLOSED", "The reading period is closed")
    old_value = reading.reported_value
    reading.reported_value = value
    reading.captured_at = now
    reading.risk_flags = risk_flags(
        db,
        publication_id=reading.publication_id,
        period=reading.reporting_period,
        value=value,
        suspicious_growth_threshold=suspicious_growth_threshold,
    )
    _history(db, reading, "edited", actor_user_id=locked_actor.id, old_value=old_value, new_value=value)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIEW_READING_UPDATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="view_reading",
        object_id=reading.id,
        metadata={"old_value": old_value, "new_value": value},
    )
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.READING_SUBMITTED,
        occurred_at=now,
    )
    return reading


def list_my_readings(
    db: Session,
    *,
    actor: User,
    publication_id: uuid.UUID | None,
    page: int,
    page_size: int,
) -> ViewReadingListResponse:
    filters = [VideoCard.blogger_id == actor.id]
    if publication_id:
        filters.append(ViewReading.publication_id == publication_id)
    total = db.scalar(
        select(func.count())
        .select_from(ViewReading)
        .join(Publication)
        .join(VideoCard)
        .where(*filters)
    ) or 0
    items = list(
        db.scalars(
            select(ViewReading)
            .join(Publication)
            .join(VideoCard)
            .where(*filters)
            .order_by(ViewReading.captured_at.desc(), ViewReading.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    )
    return ViewReadingListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def list_readings_for_review(
    db: Session,
    *,
    status: ReadingStatus | None,
    period: date | None,
    suspicious_only: bool,
    page: int,
    page_size: int,
) -> ViewReadingListResponse:
    filters = []
    if status:
        filters.append(ViewReading.status == status)
    if period:
        filters.append(ViewReading.reporting_period == period)
    if suspicious_only:
        filters.append(ViewReading.risk_flags != [])
    total = db.scalar(select(func.count()).select_from(ViewReading).where(*filters)) or 0
    items = list(
        db.scalars(
            select(ViewReading)
            .where(*filters)
            .order_by(ViewReading.captured_at, ViewReading.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return ViewReadingListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def review_reading(
    db: Session,
    *,
    reviewer: User,
    reading_id: uuid.UUID,
    payload: ReadingReviewRequest,
    audit_context: AuditContext,
) -> ViewReading:
    locked_reviewer = db.scalar(
        select(User).where(User.id == reviewer.id).with_for_update()
    )
    if (
        not locked_reviewer
        or locked_reviewer.status != AccountStatus.ACTIVE
        or locked_reviewer.role not in REVIEWER_ROLES
    ):
        raise APIError(
            403,
            "VIEW_READING_REVIEW_PERMISSION_CHANGED",
            "View reading review permissions changed",
        )
    candidate = db.get(ViewReading, reading_id)
    if not candidate:
        raise APIError(404, "VIEW_READING_NOT_FOUND", "View reading was not found")
    lock_reading_period_for_mutation(db, candidate.reporting_period)
    reading = db.scalar(
        select(ViewReading)
        .where(ViewReading.id == reading_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reading.financial_locked_at is not None:
        raise APIError(409, "VIEW_READING_FINANCIALLY_LOCKED", "View reading is financially locked")
    if reading.status != ReadingStatus.PENDING:
        raise APIError(409, "VIEW_READING_ALREADY_REVIEWED", "View reading has already been reviewed")
    now = utc_now()
    if payload.decision == "accept":
        accepted_value = reading.reported_value
        target = ReadingStatus.ACCEPTED
    elif payload.decision == "correct":
        accepted_value = payload.accepted_value
        target = ReadingStatus.ACCEPTED
    else:
        accepted_value = None
        target = ReadingStatus.REJECTED
    reading.status = target
    reading.accepted_value = accepted_value
    reading.review_reason = payload.reason
    reading.reviewed_by_user_id = locked_reviewer.id
    reading.reviewed_at = now
    _history(
        db,
        reading,
        "corrected" if payload.decision == "correct" else target.value,
        actor_user_id=locked_reviewer.id,
        old_value=reading.reported_value,
        new_value=accepted_value,
        reason=payload.reason,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIEW_READING_REVIEWED,
        actor_user_id=locked_reviewer.id,
        actor_role=locked_reviewer.role.value,
        object_type="view_reading",
        object_id=reading.id,
        metadata={"decision": payload.decision, "publication_id": str(reading.publication_id)},
    )
    blogger_id = db.scalar(
        select(VideoCard.blogger_id)
        .join(Publication, Publication.video_card_id == VideoCard.id)
        .where(Publication.id == reading.publication_id)
    )
    if not blogger_id:
        raise RuntimeError("View reading publication owner is missing")
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=blogger_id,
            template_code="reading_status_changed",
            context={"reading_id": str(reading.id), "status": target.value},
            severity=(
                NotificationSeverity.ACTION_REQUIRED
                if target == ReadingStatus.REJECTED
                else NotificationSeverity.INFO
            ),
            deduplication_key=f"reading:{reading.id}:reviewed:{reading.reviewed_at}",
            related_object_type="view_reading",
            related_object_id=reading.id,
            action_path=f"/readings/{reading.id}",
        ),
    )
    return reading


def correct_accepted_reading(
    db: Session,
    *,
    manager: User,
    reading_id: uuid.UUID,
    payload: ReadingCorrectionRequest,
    audit_context: AuditContext,
) -> ViewReading:
    locked_manager = db.scalar(select(User).where(User.id == manager.id).with_for_update())
    if (
        not locked_manager
        or locked_manager.status != AccountStatus.ACTIVE
        or locked_manager.role not in {Role.MANAGER, Role.ADMIN}
    ):
        raise APIError(
            403,
            "VIEW_READING_CORRECTION_PERMISSION_CHANGED",
            "View reading correction permissions changed",
        )
    candidate = db.get(ViewReading, reading_id)
    if not candidate:
        raise APIError(404, "VIEW_READING_NOT_FOUND", "View reading was not found")
    lock_reading_period_for_mutation(db, candidate.reporting_period)
    reading = db.scalar(
        select(ViewReading)
        .where(ViewReading.id == reading_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reading.financial_locked_at is not None:
        raise APIError(409, "VIEW_READING_FINANCIALLY_LOCKED", "View reading is financially locked")
    if reading.status != ReadingStatus.ACCEPTED:
        raise APIError(
            409,
            "VIEW_READING_NOT_ACCEPTED",
            "Only an accepted reading can be corrected",
        )
    old_value = reading.accepted_value
    reading.accepted_value = payload.accepted_value
    reading.review_reason = payload.reason
    reading.reviewed_by_user_id = locked_manager.id
    reading.reviewed_at = utc_now()
    _history(
        db,
        reading,
        "corrected",
        actor_user_id=locked_manager.id,
        old_value=old_value,
        new_value=payload.accepted_value,
        reason=payload.reason,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIEW_READING_CORRECTED,
        actor_user_id=locked_manager.id,
        actor_role=locked_manager.role.value,
        object_type="view_reading",
        object_id=reading.id,
        metadata={"old_value": old_value, "new_value": payload.accepted_value},
    )
    return reading
