import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationStatus,
    VideoCard,
)
from app.content.schemas import (
    PublicationCreateRequest,
    PublicationDetailResponse,
    PublicationResponse,
    PublicationUpdateRequest,
)
from app.content.service import _require_blogger, lock_content_writer
from app.content.url_parser import PublicationURLInvalid, parse_publication_url
from app.creators.models import SocialAccount, SocialAccountStatus
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.lifecycle.activity import record_creator_activity
from app.lifecycle.models import ActivityKind
from app.platforms import Platform
from app.youtube.models import YouTubeEnrichmentJob


EDITABLE_STATUSES = {PublicationStatus.DRAFT, PublicationStatus.CHANGES_REQUIRED}
SUBMITTABLE_STATUSES = {
    PublicationStatus.DRAFT,
    PublicationStatus.CHANGES_REQUIRED,
    PublicationStatus.RE_REVIEW_REQUIRED,
}


def record_publication_history(
    db: Session,
    publication: Publication,
    actor_user_id: uuid.UUID,
    event_type: str,
    *,
    from_status: str | None,
    to_status: str,
    reason: str | None = None,
    changes: dict | None = None,
) -> None:
    db.add(
        PublicationHistory(
            publication_id=publication.id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            changes=changes or {},
        )
    )


def _raise_url_conflict(error: IntegrityError) -> None:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    message = str(error.orig).lower()
    known_constraints = {
        "uq_publications_normalized_url",
        "uq_publications_platform_external_id",
    }
    if constraint_name in known_constraints or (
        constraint_name is None
        and ("publications.normalized_url" in message or "publications.platform" in message)
    ):
        raise APIError(409, "PUBLICATION_URL_ALREADY_EXISTS", "This publication URL is already registered") from error
    raise error


def _owned_card(
    db: Session, *, user_id: uuid.UUID, card_id: uuid.UUID, for_update: bool
) -> VideoCard:
    statement = select(VideoCard).where(
        VideoCard.id == card_id,
        VideoCard.blogger_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    card = db.scalar(statement)
    if not card:
        raise APIError(404, "VIDEO_CARD_NOT_FOUND", "Video card was not found")
    return card


def _approved_account(
    db: Session, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> tuple[SocialAccount, Platform]:
    account = db.scalar(
        select(SocialAccount)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.user_id == user_id,
            SocialAccount.status == SocialAccountStatus.APPROVED,
            SocialAccount.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not account:
        raise APIError(
            409,
            "SOCIAL_ACCOUNT_NOT_APPROVED",
            "An approved social account owned by the blogger is required",
        )
    try:
        platform = Platform(account.platform)
    except ValueError as error:
        raise APIError(409, "SOCIAL_PLATFORM_UNSUPPORTED", "Social account platform is not supported") from error
    return account, platform


def _parse_url(platform: Platform, url: str):
    try:
        return parse_publication_url(platform, url)
    except PublicationURLInvalid as error:
        raise APIError(422, "PUBLICATION_URL_INVALID", str(error)) from error


def _owned_publication_for_update(
    db: Session, *, user_id: uuid.UUID, publication_id: uuid.UUID
) -> tuple[VideoCard, Publication]:
    card_id = db.scalar(
        select(Publication.video_card_id).where(
            Publication.id == publication_id,
            Publication.deleted_at.is_(None),
        )
    )
    if not card_id:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    card = _owned_card(db, user_id=user_id, card_id=card_id, for_update=True)
    publication = db.scalar(
        select(Publication)
        .where(Publication.id == publication_id, Publication.deleted_at.is_(None))
        .with_for_update()
    )
    if not publication:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    return card, publication


def publication_detail(db: Session, publication: Publication) -> PublicationDetailResponse:
    db.flush()
    history = list(
        db.scalars(
            select(PublicationHistory)
            .where(PublicationHistory.publication_id == publication.id)
            .order_by(PublicationHistory.created_at.desc(), PublicationHistory.id.desc())
        )
    )
    publication_data = PublicationResponse.model_validate(publication).model_dump()
    return PublicationDetailResponse(**publication_data, history=history)


def list_publications(
    db: Session,
    *,
    user: User,
    card_id: uuid.UUID,
    page: int,
    page_size: int,
) -> tuple[list[Publication], int, int]:
    _require_blogger(user)
    _owned_card(db, user_id=user.id, card_id=card_id, for_update=False)
    filters = [
        Publication.video_card_id == card_id,
        Publication.deleted_at.is_(None),
    ]
    total = db.scalar(select(func.count()).select_from(Publication).where(*filters)) or 0
    publications = list(
        db.scalars(
            select(Publication)
            .where(*filters)
            .order_by(Publication.created_at.desc(), Publication.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return publications, total, math.ceil(total / page_size)


def get_publication(db: Session, *, user: User, publication_id: uuid.UUID) -> Publication:
    _require_blogger(user)
    publication = db.scalar(
        select(Publication)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .where(
            Publication.id == publication_id,
            Publication.deleted_at.is_(None),
            VideoCard.blogger_id == user.id,
        )
    )
    if not publication:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    return publication


def create_publication(
    db: Session,
    *,
    actor: User,
    card_id: uuid.UUID,
    payload: PublicationCreateRequest,
    audit_context: AuditContext,
) -> Publication:
    locked_actor = lock_content_writer(db, actor.id)
    card = _owned_card(db, user_id=locked_actor.id, card_id=card_id, for_update=True)
    account, platform = _approved_account(
        db, user_id=locked_actor.id, account_id=payload.social_account_id
    )
    submitted_url = str(payload.url)
    parsed = _parse_url(platform, submitted_url)
    publication = Publication(
        video_card_id=card.id,
        social_account_id=account.id,
        platform=platform,
        submitted_url=submitted_url,
        normalized_url=parsed.normalized_url,
        external_id=parsed.external_id,
        parse_status=parsed.parse_status,
    )
    db.add(publication)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        _raise_url_conflict(error)
    record_publication_history(
        db,
        publication,
        locked_actor.id,
        "publication_created",
        from_status=None,
        to_status=PublicationStatus.DRAFT.value,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PUBLICATION_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="publication",
        object_id=publication.id,
        metadata={"platform": platform.value, "parse_status": parsed.parse_status.value},
    )
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.PUBLICATION_CHANGED,
    )
    return publication


def update_publication(
    db: Session,
    *,
    actor: User,
    publication_id: uuid.UUID,
    payload: PublicationUpdateRequest,
    audit_context: AuditContext,
) -> Publication:
    locked_actor = lock_content_writer(db, actor.id)
    _, publication = _owned_publication_for_update(
        db, user_id=locked_actor.id, publication_id=publication_id
    )
    if publication.status not in EDITABLE_STATUSES:
        raise APIError(409, "PUBLICATION_NOT_EDITABLE", "Publication cannot be edited in its current status")

    account_id = payload.social_account_id or publication.social_account_id
    account, platform = _approved_account(db, user_id=locked_actor.id, account_id=account_id)
    submitted_url = str(payload.url) if payload.url is not None else publication.submitted_url
    parsed = _parse_url(platform, submitted_url)
    new_values = {
        "social_account_id": account.id,
        "platform": platform,
        "submitted_url": submitted_url,
        "normalized_url": parsed.normalized_url,
        "external_id": parsed.external_id,
        "parse_status": parsed.parse_status,
    }
    changed_fields = [
        field for field, value in new_values.items() if getattr(publication, field) != value
    ]
    if not changed_fields:
        return publication
    for field, value in new_values.items():
        setattr(publication, field, value)
    publication.enrichment_status = PublicationEnrichmentStatus.NOT_REQUESTED
    publication.availability = PublicationAvailability.UNKNOWN
    publication.external_title = None
    publication.external_author_id = None
    publication.external_author_name = None
    publication.external_published_at = None
    publication.external_duration_seconds = None
    publication.external_thumbnail_url = None
    publication.external_etag = None
    publication.enriched_at = None
    publication.enrichment_error_code = None
    publication.updated_at = utc_now()
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        _raise_url_conflict(error)
    record_publication_history(
        db,
        publication,
        locked_actor.id,
        "publication_updated",
        from_status=publication.status.value,
        to_status=publication.status.value,
        changes={"changed_fields": sorted(changed_fields)},
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PUBLICATION_UPDATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="publication",
        object_id=publication.id,
        metadata={"changed_fields": sorted(changed_fields)},
    )
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.PUBLICATION_CHANGED,
        occurred_at=publication.updated_at,
    )
    return publication


def delete_publication(
    db: Session,
    *,
    actor: User,
    publication_id: uuid.UUID,
    audit_context: AuditContext,
) -> None:
    locked_actor = lock_content_writer(db, actor.id)
    _, publication = _owned_publication_for_update(
        db, user_id=locked_actor.id, publication_id=publication_id
    )
    if publication.status not in EDITABLE_STATUSES:
        raise APIError(409, "PUBLICATION_NOT_DELETABLE", "Publication cannot be deleted in its current status")
    previous_status = publication.status.value
    publication.deleted_at = utc_now()
    record_publication_history(
        db,
        publication,
        locked_actor.id,
        "publication_deleted",
        from_status=previous_status,
        to_status="deleted",
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PUBLICATION_DELETED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="publication",
        object_id=publication.id,
    )
    record_creator_activity(
        db,
        blogger_id=locked_actor.id,
        kind=ActivityKind.PUBLICATION_CHANGED,
        occurred_at=publication.deleted_at,
    )


def submit_publication(
    db: Session,
    *,
    actor: User,
    publication_id: uuid.UUID,
    audit_context: AuditContext,
) -> Publication:
    locked_actor = lock_content_writer(db, actor.id)
    _, publication = _owned_publication_for_update(
        db, user_id=locked_actor.id, publication_id=publication_id
    )
    if publication.status not in SUBMITTABLE_STATUSES:
        raise APIError(409, "PUBLICATION_NOT_SUBMITTABLE", "Publication cannot be submitted in its current status")
    _approved_account(
        db,
        user_id=locked_actor.id,
        account_id=publication.social_account_id,
    )
    previous_status = publication.status
    publication.status = PublicationStatus.PENDING_REVIEW
    publication.moderation_reason = None
    publication.submitted_at = utc_now()
    publication.reviewed_at = None
    publication.updated_at = publication.submitted_at
    if publication.platform == Platform.YOUTUBE and publication.external_id:
        job = db.scalar(
            select(YouTubeEnrichmentJob)
            .where(YouTubeEnrichmentJob.publication_id == publication.id)
            .with_for_update()
        )
        if job:
            job.external_id = publication.external_id
            job.state = "pending"
            job.attempt_count = 0
            job.available_at = publication.submitted_at
            job.lease_until = None
            job.dispatch_id = None
            job.last_error_code = None
        else:
            db.add(
                YouTubeEnrichmentJob(
                    publication_id=publication.id,
                    external_id=publication.external_id,
                    available_at=publication.submitted_at,
                )
            )
        publication.enrichment_status = PublicationEnrichmentStatus.PENDING
        publication.availability = PublicationAvailability.UNKNOWN
        publication.enrichment_error_code = None
    elif publication.platform == Platform.YOUTUBE:
        publication.enrichment_status = PublicationEnrichmentStatus.FAILED
        publication.enrichment_error_code = "external_id_missing"
    record_publication_history(
        db,
        publication,
        locked_actor.id,
        "publication_submitted",
        from_status=previous_status.value,
        to_status=PublicationStatus.PENDING_REVIEW.value,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PUBLICATION_SUBMITTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="publication",
        object_id=publication.id,
        metadata={"previous_status": previous_status.value},
    )
    reviewers = list(
        db.scalars(
            select(User).where(
                User.status == AccountStatus.ACTIVE,
                User.role.in_({Role.MODERATOR, Role.ADMIN}),
            )
        )
    )
    for reviewer in reviewers:
        create_notification(
            db,
            NotificationCommand(
                recipient_user_id=reviewer.id,
                template_code="publication_submitted",
                context={"publication_id": str(publication.id)},
                severity=NotificationSeverity.ACTION_REQUIRED,
                deduplication_key=(
                    f"publication:{publication.id}:submitted:{publication.submitted_at}:{reviewer.id}"
                ),
                related_object_type="publication",
                related_object_id=publication.id,
                action_path=f"/staff/publications/{publication.id}",
            ),
        )
    return publication
