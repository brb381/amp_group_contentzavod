import math
import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.catalog.models import Brand, Product
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationHistory,
    PublicationPromoIssuance,
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
    PublicationDeactivationRequest,
    PromoIssuanceCreate,
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
    blogger_id: uuid.UUID | None,
    blogger: str | None,
    brand: Brand | None,
    product_id: uuid.UUID | None,
    product: str | None,
    date_from: date | None,
    date_to: date | None,
    reason: str | None,
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
    if blogger_id:
        filters.append(VideoCard.blogger_id == blogger_id)
    if blogger:
        pattern = f"%{blogger.strip()}%"
        filters.append(or_(User.email.ilike(pattern), CreatorProfile.full_name.ilike(pattern), CreatorProfile.display_name.ilike(pattern)))
    if brand:
        filters.append((Product.brand == brand) | (VideoCard.reported_brand == brand))
    if product_id:
        filters.append(VideoCard.product_id == product_id)
    if product:
        pattern = f"%{product.strip()}%"
        filters.append(or_(Product.publication_name.ilike(pattern), Product.model_name.ilike(pattern), Product.sku.ilike(pattern), VideoCard.reported_product_name.ilike(pattern)))
    if date_from:
        filters.append(func.date(func.coalesce(Publication.submitted_at, Publication.created_at)) >= date_from)
    if date_to:
        filters.append(func.date(func.coalesce(Publication.submitted_at, Publication.created_at)) <= date_to)
    if reason:
        filters.append(Publication.moderation_reason.ilike(f"%{reason.strip()}%"))

    base = (
        select(Publication, VideoCard, User, CreatorProfile)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .join(User, User.id == VideoCard.blogger_id)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == User.id)
        .outerjoin(Product, Product.id == VideoCard.product_id)
        .where(*filters)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base
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
    promo_issuances = list(
        db.scalars(
            select(PublicationPromoIssuance)
            .where(PublicationPromoIssuance.publication_id == publication.id)
            .order_by(
                PublicationPromoIssuance.issued_at.desc(),
                PublicationPromoIssuance.id.desc(),
            )
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
        promo_issuances=promo_issuances,
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

def _lock_publication_operator(db: Session, user_id: uuid.UUID, allowed_roles: set[Role]) -> User:
    operator = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not operator or operator.status != AccountStatus.ACTIVE or operator.role not in allowed_roles:
        raise APIError(403, "PUBLICATION_OPERATION_PERMISSION_CHANGED", "Publication operation permissions changed; authenticate again")
    return operator


def deactivate_publication(
    db: Session, *, publication_id: uuid.UUID, operator: User,
    payload: PublicationDeactivationRequest, audit_context: AuditContext,
) -> PublicationModerationDetail:
    locked_operator = _lock_publication_operator(db, operator.id, {Role.MANAGER, Role.ADMIN})
    publication, _, creator, _ = _lock_publication_graph(db, publication_id)
    if publication.status != PublicationStatus.APPROVED:
        raise APIError(409, "INVALID_PUBLICATION_DEACTIVATION", "Only an approved publication can be deactivated", {"current_status": publication.status.value})
    now = utc_now()
    publication.status = PublicationStatus.INACTIVE
    publication.moderation_reason = payload.reason
    publication.updated_at = now
    record_publication_history(db, publication, locked_operator.id, "publication_deactivated", from_status=PublicationStatus.APPROVED.value, to_status=PublicationStatus.INACTIVE.value, reason=payload.reason)
    record_event(db, context=audit_context, action=AuditAction.PUBLICATION_DEACTIVATED, actor_user_id=locked_operator.id, actor_role=locked_operator.role.value, object_type="publication", object_id=publication.id, metadata={"reason": payload.reason})
    create_notification(db, NotificationCommand(
        recipient_user_id=creator.id, template_code="publication_status_changed",
        context={"publication_id": str(publication.id), "status": PublicationStatus.INACTIVE.value},
        severity=NotificationSeverity.ACTION_REQUIRED,
        deduplication_key=f"publication:{publication.id}:deactivated:{now.isoformat()}",
        related_object_type="publication", related_object_id=publication.id,
        action_path=f"/publications/{publication.id}",
    ))
    db.flush()
    return get_publication_moderation_detail(db, publication.id)


def record_promo_issuance(
    db: Session, *, publication_id: uuid.UUID, operator: User,
    payload: PromoIssuanceCreate, audit_context: AuditContext,
) -> PublicationModerationDetail:
    locked_operator = _lock_publication_operator(db, operator.id, {Role.MODERATOR, Role.MANAGER, Role.ADMIN})
    publication, _, _, _ = _lock_publication_graph(db, publication_id)
    if publication.status != PublicationStatus.APPROVED:
        raise APIError(409, "PROMO_REQUIRES_APPROVED_PUBLICATION", "A promo code can be marked as issued only for an approved publication", {"current_status": publication.status.value})
    existing = db.scalar(select(PublicationPromoIssuance).where(
        PublicationPromoIssuance.publication_id == publication.id,
        PublicationPromoIssuance.marketplace == payload.marketplace,
    ))
    if existing:
        raise APIError(409, "PROMO_ISSUANCE_ALREADY_RECORDED", "Promo code issuance is already recorded for this marketplace", {"marketplace": payload.marketplace.value})
    db.add(PublicationPromoIssuance(
        publication_id=publication.id, marketplace=payload.marketplace,
        issued_by_user_id=locked_operator.id, note=payload.note,
    ))
    record_publication_history(db, publication, locked_operator.id, "promo_code_issued", from_status=publication.status.value, to_status=publication.status.value, changes={"marketplace": payload.marketplace.value}, reason=payload.note)
    record_event(db, context=audit_context, action=AuditAction.PUBLICATION_PROMO_ISSUED, actor_user_id=locked_operator.id, actor_role=locked_operator.role.value, object_type="publication", object_id=publication.id, metadata={"marketplace": payload.marketplace.value})
    db.flush()
    return get_publication_moderation_detail(db, publication.id)
