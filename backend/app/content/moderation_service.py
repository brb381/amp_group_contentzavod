import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationHistory,
    PublicationParseStatus,
    PublicationStatus,
    VideoCard,
)
from app.content.publication_service import record_publication_history
from app.content.schemas import (
    PublicationModerationDetail,
    PublicationModerationItem,
    PublicationModerationListResponse,
    PublicationResponse,
    PublicationReviewRequest,
)
from app.content.service import publication_counts_by_card, resolve_card_product, video_card_response
from app.creators.models import CreatorProfile, ProfileStatus, SocialAccount, SocialAccountStatus
from app.creators.schemas import SocialAccountResponse
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.platforms import Platform
from app.readings.models import YouTubeViewCollectionJob
from app.readings.policy import MOSCOW


REVIEWER_ROLES = {Role.MODERATOR, Role.ADMIN}
REVIEW_TARGETS = {
    "approve": PublicationStatus.APPROVED,
    "request_changes": PublicationStatus.CHANGES_REQUIRED,
    "reject": PublicationStatus.REJECTED,
}


def _lock_reviewer(db: Session, reviewer_id: uuid.UUID) -> User:
    reviewer = db.scalar(select(User).where(User.id == reviewer_id).with_for_update())
    if (
        not reviewer
        or reviewer.status != AccountStatus.ACTIVE
        or reviewer.role not in REVIEWER_ROLES
    ):
        raise APIError(
            403,
            "PUBLICATION_MODERATION_PERMISSION_CHANGED",
            "Publication moderation permissions changed; authenticate again",
        )
    return reviewer


def _moderation_item(
    publication: Publication,
    card: VideoCard,
    creator: User,
    profile: CreatorProfile | None,
) -> PublicationModerationItem:
    return PublicationModerationItem(
        publication=PublicationResponse.model_validate(publication),
        card_title=card.title,
        is_product_resolved=card.product_id is not None,
        creator_user_id=creator.id,
        creator_email=creator.email,
        creator_full_name=profile.full_name if profile else None,
        creator_display_name=profile.display_name if profile else None,
    )


def list_publications_for_moderation(
    db: Session,
    *,
    publication_status: PublicationStatus | None,
    platform: Platform | None,
    parse_status: PublicationParseStatus | None,
    page: int,
    page_size: int,
) -> PublicationModerationListResponse:
    filters = [Publication.deleted_at.is_(None)]
    if publication_status:
        filters.append(Publication.status == publication_status)
    if platform:
        filters.append(Publication.platform == platform)
    if parse_status:
        filters.append(Publication.parse_status == parse_status)

    total = db.scalar(select(func.count()).select_from(Publication).where(*filters)) or 0
    rows = db.execute(
        select(Publication, VideoCard, User, CreatorProfile)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .join(User, User.id == VideoCard.blogger_id)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == User.id)
        .where(*filters)
        .order_by(
            func.coalesce(Publication.submitted_at, Publication.created_at),
            Publication.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return PublicationModerationListResponse(
        items=[
            _moderation_item(publication, card, creator, profile)
            for publication, card, creator, profile in rows
        ],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_publication_moderation_detail(
    db: Session, publication_id: uuid.UUID
) -> PublicationModerationDetail:
    row = db.execute(
        select(Publication, VideoCard, SocialAccount, User, CreatorProfile)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .join(SocialAccount, SocialAccount.id == Publication.social_account_id)
        .join(User, User.id == VideoCard.blogger_id)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == User.id)
        .where(Publication.id == publication_id, Publication.deleted_at.is_(None))
    ).one_or_none()
    if not row:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    publication, card, account, creator, profile = row
    history = list(
        db.scalars(
            select(PublicationHistory)
            .where(PublicationHistory.publication_id == publication.id)
            .order_by(PublicationHistory.created_at.desc(), PublicationHistory.id.desc())
        )
    )
    counts = publication_counts_by_card(db, [card.id])
    return PublicationModerationDetail(
        publication=PublicationResponse.model_validate(publication),
        card=video_card_response(card, counts.get(card.id)),
        social_account=SocialAccountResponse.model_validate(account),
        creator_user_id=creator.id,
        creator_email=creator.email,
        creator_full_name=profile.full_name if profile else None,
        creator_display_name=profile.display_name if profile else None,
        history=history,
    )


def _lock_publication_graph(
    db: Session, publication_id: uuid.UUID
) -> tuple[Publication, VideoCard, User, CreatorProfile | None]:
    identifiers = db.execute(
        select(Publication.video_card_id, VideoCard.blogger_id)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .where(
            Publication.id == publication_id,
            Publication.deleted_at.is_(None),
        )
    ).one_or_none()
    if not identifiers:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    card_id, blogger_id = identifiers
    creator = db.scalar(select(User).where(User.id == blogger_id).with_for_update())
    profile = db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.user_id == blogger_id)
        .with_for_update()
    )
    card = db.scalar(select(VideoCard).where(VideoCard.id == card_id).with_for_update())
    publication = db.scalar(
        select(Publication)
        .where(
            Publication.id == publication_id,
            Publication.video_card_id == card_id,
            Publication.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not creator or not card or not publication:
        raise APIError(404, "PUBLICATION_NOT_FOUND", "Publication was not found")
    return publication, card, creator, profile


def _validate_approval_account(
    db: Session, publication: Publication, card: VideoCard
) -> None:
    account = db.scalar(
        select(SocialAccount)
        .where(SocialAccount.id == publication.social_account_id)
        .with_for_update()
    )
    if (
        not account
        or account.user_id != card.blogger_id
        or account.status != SocialAccountStatus.APPROVED
        or account.deleted_at is not None
        or Platform(account.platform) != publication.platform
    ):
        raise APIError(
            409,
            "PUBLICATION_ACCOUNT_NOT_APPROVED",
            "Publication social account is no longer approved",
        )


def _validate_approval_creator(
    creator: User, profile: CreatorProfile | None
) -> None:
    if (
        creator.role != Role.BLOGGER
        or creator.status != AccountStatus.ACTIVE
        or not profile
        or profile.status != ProfileStatus.APPROVED
    ):
        raise APIError(
            409,
            "PUBLICATION_CREATOR_NOT_APPROVED",
            "Publication creator is no longer approved for participation",
        )


def review_publication(
    db: Session,
    *,
    publication_id: uuid.UUID,
    reviewer: User,
    payload: PublicationReviewRequest,
    audit_context: AuditContext,
) -> PublicationModerationDetail:
    locked_reviewer = _lock_reviewer(db, reviewer.id)
    publication, card, creator, profile = _lock_publication_graph(db, publication_id)
    if publication.status != PublicationStatus.PENDING_REVIEW:
        raise APIError(
            409,
            "INVALID_PUBLICATION_TRANSITION",
            "Publication cannot accept this moderation decision in its current status",
            {"current_status": publication.status.value, "decision": payload.decision},
        )

    target = REVIEW_TARGETS[payload.decision]
    changed_product = False
    if target == PublicationStatus.APPROVED:
        _validate_approval_creator(creator, profile)
        _validate_approval_account(db, publication, card)
        if publication.availability == PublicationAvailability.UNAVAILABLE:
            raise APIError(
                409,
                "PUBLICATION_UNAVAILABLE",
                "An unavailable publication cannot be approved",
            )
        if payload.resolved_product_id is not None:
            if card.product_id is not None and card.product_id != payload.resolved_product_id:
                raise APIError(
                    409,
                    "VIDEO_CARD_PRODUCT_ALREADY_RESOLVED",
                    "Request changes before replacing the video card product",
                )
            if card.product_id is None:
                changed_product = resolve_card_product(db, card, payload.resolved_product_id)
        if card.product_id is None:
            raise APIError(
                409,
                "VIDEO_CARD_PRODUCT_UNRESOLVED",
                "Resolve the video card product before approval",
            )

    now = utc_now()
    publication.status = target
    publication.moderation_reason = payload.reason if target != PublicationStatus.APPROVED else None
    publication.reviewed_at = now
    publication.updated_at = now
    if (
        target == PublicationStatus.APPROVED
        and publication.platform == Platform.YOUTUBE
        and publication.external_id
    ):
        collection_date = now.astimezone(MOSCOW).date()
        baseline_job = db.scalar(
            select(YouTubeViewCollectionJob).where(
                YouTubeViewCollectionJob.publication_id == publication.id,
                YouTubeViewCollectionJob.collection_date == collection_date,
            )
        )
        if not baseline_job:
            db.add(
                YouTubeViewCollectionJob(
                    publication_id=publication.id,
                    external_id=publication.external_id,
                    collection_date=collection_date,
                    available_at=now,
                )
            )
    if changed_product:
        card.updated_at = now
    changes = (
        {"resolved_product_id": str(payload.resolved_product_id)}
        if payload.resolved_product_id is not None
        else None
    )
    record_publication_history(
        db,
        publication,
        locked_reviewer.id,
        f"publication_{payload.decision}",
        from_status=PublicationStatus.PENDING_REVIEW.value,
        to_status=target.value,
        reason=payload.reason,
        changes=changes,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PUBLICATION_REVIEWED,
        actor_user_id=locked_reviewer.id,
        actor_role=locked_reviewer.role.value,
        object_type="publication",
        object_id=publication.id,
        metadata={
            "decision": payload.decision,
            "from_status": PublicationStatus.PENDING_REVIEW.value,
            "to_status": target.value,
            "product_resolved": card.product_id is not None,
        },
    )
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=creator.id,
            template_code="publication_status_changed",
            context={
                "publication_id": str(publication.id),
                "status": target.value,
            },
            severity=(
                NotificationSeverity.ACTION_REQUIRED
                if target in {PublicationStatus.CHANGES_REQUIRED, PublicationStatus.REJECTED}
                else NotificationSeverity.INFO
            ),
            deduplication_key=(
                f"publication:{publication.id}:status:{target.value}:{publication.reviewed_at}"
            ),
            related_object_type="publication",
            related_object_id=publication.id,
            action_path=f"/publications/{publication.id}",
        ),
    )
    db.flush()
    return get_publication_moderation_detail(db, publication.id)
