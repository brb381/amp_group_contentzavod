import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.catalog.models import Product
from app.content.models import Publication, PublicationStatus, VideoCard
from app.content.schemas import (
    CatalogProductSelection,
    ProductSnapshot,
    ReportedProduct,
    UnlistedProductSelection,
    VideoCardCreateRequest,
    VideoCardResponse,
    VideoCardUpdateRequest,
)
from app.creators.models import CreatorProfile, ProfileStatus
from app.errors import APIError


def _require_blogger(user: User) -> None:
    if user.role != Role.BLOGGER:
        raise APIError(403, "BLOGGER_ROLE_REQUIRED", "This operation is available only to bloggers")


def lock_content_writer(db: Session, user_id: uuid.UUID) -> User:
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or user.role != Role.BLOGGER or user.status != AccountStatus.ACTIVE:
        raise APIError(403, "CONTENT_PERMISSION_CHANGED", "Content permissions changed; authenticate again")
    profile = db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == user_id).with_for_update()
    )
    if not profile or profile.status != ProfileStatus.APPROVED:
        raise APIError(403, "CREATOR_NOT_APPROVED", "An approved creator profile is required")
    return user


def _product_snapshot(product: Product) -> dict:
    return ProductSnapshot(
        brand=product.brand,
        model_name=product.model_name,
        publication_name=product.publication_name,
        sku=product.sku,
        required_hashtags=product.required_hashtags,
    ).model_dump(mode="json")


def _get_active_product_for_update(db: Session, product_id: uuid.UUID) -> Product:
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if not product or not product.is_active:
        raise APIError(404, "PRODUCT_NOT_AVAILABLE", "Product is not available for new video cards")
    return product


def _apply_product_selection(
    db: Session,
    card: VideoCard,
    selection: CatalogProductSelection | UnlistedProductSelection,
) -> bool:
    if isinstance(selection, CatalogProductSelection):
        if (
            card.product_id == selection.product_id
            and card.reported_brand is None
            and card.reported_product_name is None
        ):
            return False
        product = _get_active_product_for_update(db, selection.product_id)
        card.product_id = product.id
        card.product_snapshot = _product_snapshot(product)
        card.reported_brand = None
        card.reported_product_name = None
        return True

    if (
        card.product_id is None
        and card.reported_brand == selection.brand
        and card.reported_product_name == selection.name
    ):
        return False
    card.product_id = None
    card.product_snapshot = None
    card.reported_brand = selection.brand
    card.reported_product_name = selection.name
    return True


def resolve_card_product(
    db: Session, card: VideoCard, product_id: uuid.UUID
) -> bool:
    return _apply_product_selection(
        db,
        card,
        CatalogProductSelection(type="catalog", product_id=product_id),
    )


def _derived_card_status(counts: dict[PublicationStatus, int]) -> str:
    total = sum(counts.values())
    if total == 0 or counts.get(PublicationStatus.DRAFT, 0) == total:
        return "draft"
    if counts.get(PublicationStatus.PENDING_REVIEW, 0):
        return "pending_review"
    if counts.get(PublicationStatus.CHANGES_REQUIRED, 0) or counts.get(
        PublicationStatus.RE_REVIEW_REQUIRED, 0
    ):
        return "changes_required"
    if counts.get(PublicationStatus.APPROVED, 0) == total:
        return "approved"
    if counts.get(PublicationStatus.APPROVED, 0):
        return "partially_approved"
    if counts.get(PublicationStatus.REJECTED, 0) == total:
        return "rejected"
    if counts.get(PublicationStatus.INACTIVE, 0) == total:
        return "inactive"
    return "draft"


def publication_counts_by_card(
    db: Session, card_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[PublicationStatus, int]]:
    if not card_ids:
        return {}
    rows = db.execute(
        select(Publication.video_card_id, Publication.status, func.count())
        .where(Publication.video_card_id.in_(card_ids), Publication.deleted_at.is_(None))
        .group_by(Publication.video_card_id, Publication.status)
    )
    counts: dict[uuid.UUID, dict[PublicationStatus, int]] = {}
    for card_id, publication_status, count in rows:
        counts.setdefault(card_id, {})[publication_status] = count
    return counts


def video_card_response(
    card: VideoCard, counts: dict[PublicationStatus, int] | None = None
) -> VideoCardResponse:
    counts = counts or {}
    reported_product = None
    if card.reported_brand is not None and card.reported_product_name is not None:
        reported_product = ReportedProduct(
            brand=card.reported_brand,
            name=card.reported_product_name,
        )
    return VideoCardResponse(
        id=card.id,
        title=card.title,
        description=card.description,
        product_id=card.product_id,
        product_snapshot=card.product_snapshot,
        reported_product=reported_product,
        is_product_resolved=card.product_id is not None,
        status=_derived_card_status(counts),
        publication_summary={
            "total": sum(counts.values()),
            "approved": counts.get(PublicationStatus.APPROVED, 0),
            "pending_review": counts.get(PublicationStatus.PENDING_REVIEW, 0),
        },
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def list_video_cards(
    db: Session,
    *,
    user: User,
    page: int,
    page_size: int,
) -> tuple[list[VideoCard], int, int]:
    _require_blogger(user)
    filters = [VideoCard.blogger_id == user.id]
    total = db.scalar(select(func.count()).select_from(VideoCard).where(*filters)) or 0
    cards = list(
        db.scalars(
            select(VideoCard)
            .where(*filters)
            .order_by(VideoCard.created_at.desc(), VideoCard.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return cards, total, math.ceil(total / page_size)


def get_video_card(db: Session, *, user: User, card_id: uuid.UUID) -> VideoCard:
    _require_blogger(user)
    card = db.scalar(
        select(VideoCard).where(VideoCard.id == card_id, VideoCard.blogger_id == user.id)
    )
    if not card:
        raise APIError(404, "VIDEO_CARD_NOT_FOUND", "Video card was not found")
    return card


def create_video_card(
    db: Session,
    *,
    actor: User,
    payload: VideoCardCreateRequest,
    audit_context: AuditContext,
) -> VideoCard:
    locked_actor = lock_content_writer(db, actor.id)
    card = VideoCard(
        blogger_id=locked_actor.id,
        title=payload.title,
        description=payload.description,
    )
    _apply_product_selection(db, card, payload.product)
    db.add(card)
    db.flush()
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIDEO_CARD_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="video_card",
        object_id=card.id,
        metadata={"product_source": payload.product.type},
    )
    return card


def update_video_card(
    db: Session,
    *,
    actor: User,
    card_id: uuid.UUID,
    payload: VideoCardUpdateRequest,
    audit_context: AuditContext,
) -> VideoCard:
    locked_actor = lock_content_writer(db, actor.id)
    card = db.scalar(
        select(VideoCard)
        .where(VideoCard.id == card_id, VideoCard.blogger_id == locked_actor.id)
        .with_for_update()
    )
    if not card:
        raise APIError(404, "VIDEO_CARD_NOT_FOUND", "Video card was not found")

    changes: set[str] = set()
    values = payload.model_dump(exclude_unset=True, exclude={"product"})
    for field, value in values.items():
        if getattr(card, field) != value:
            setattr(card, field, value)
            changes.add(field)
    if "product" in payload.model_fields_set and payload.product is not None:
        product_locked = db.scalar(
            select(func.count())
            .select_from(Publication)
            .where(
                Publication.video_card_id == card.id,
                Publication.deleted_at.is_(None),
                Publication.status.not_in(
                    {PublicationStatus.DRAFT, PublicationStatus.CHANGES_REQUIRED}
                ),
            )
        )
        if product_locked:
            raise APIError(
                409,
                "VIDEO_CARD_PRODUCT_LOCKED",
                "Product cannot be changed while a publication is under review or finalized",
            )
        if _apply_product_selection(db, card, payload.product):
            changes.add("product")
    if not changes:
        return card

    card.updated_at = utc_now()
    db.flush()
    record_event(
        db,
        context=audit_context,
        action=AuditAction.VIDEO_CARD_UPDATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="video_card",
        object_id=card.id,
        metadata={"changed_fields": sorted(changes)},
    )
    return card
