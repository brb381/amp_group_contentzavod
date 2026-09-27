import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import require_active_roles, require_csrf
from app.auth.models import Role, User
from app.catalog.models import Brand
from app.content.models import PublicationAvailability, PublicationParseStatus, PublicationStatus
from app.content.moderation_service import (
    deactivate_publication,
    get_publication_moderation_detail,
    list_publications_for_moderation,
    record_promo_issuance,
    review_publication,
)
from app.content.schemas import (
    PromoIssuanceCreate,
    PublicationDeactivationRequest,
    PublicationModerationDetail,
    PublicationModerationListResponse,
    PublicationReviewRequest,
)
from app.database.session import get_db
from app.platforms import Platform


router = APIRouter(prefix="/moderation/publications", tags=["publication moderation"])
Viewer = Annotated[
    User,
    Depends(require_active_roles(Role.MODERATOR, Role.MANAGER, Role.ADMIN)),
]
Moderator = Annotated[
    User,
    Depends(require_active_roles(Role.MODERATOR, Role.ADMIN)),
]
Manager = Annotated[
    User,
    Depends(require_active_roles(Role.MANAGER, Role.ADMIN)),
]


@router.get("", response_model=PublicationModerationListResponse)
def get_publication_queue(
    _: Viewer,
    publication_status: PublicationStatus | None = Query(
        default=PublicationStatus.PENDING_REVIEW,
        alias="status",
    ),
    platform: Platform | None = Query(default=None),
    all_statuses: bool = Query(default=False, alias="allStatuses"),
    blogger_id: uuid.UUID | None = Query(default=None, alias="bloggerId"),
    blogger: str | None = Query(default=None, min_length=1, max_length=320),
    brand: Brand | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None, alias="productId"),
    product: str | None = Query(default=None, min_length=1, max_length=255),
    date_from: date | None = Query(default=None, alias="dateFrom"),
    date_to: date | None = Query(default=None, alias="dateTo"),
    reason: str | None = Query(default=None, min_length=1, max_length=500),
    parse_status: PublicationParseStatus | None = Query(default=None, alias="parseStatus"),
    availability: PublicationAvailability | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationListResponse:
    return list_publications_for_moderation(
        db,
        publication_status=None if all_statuses else publication_status,
        platform=platform,
        parse_status=parse_status,
        availability=availability,
        blogger_id=blogger_id,
        blogger=blogger,
        brand=brand,
        product_id=product_id,
        product=product,
        date_from=date_from,
        date_to=date_to,
        reason=reason,
        page=page,
        page_size=page_size,
    )


@router.get("/{publication_id}", response_model=PublicationModerationDetail)
def get_publication_moderation_card(
    publication_id: uuid.UUID,
    _: Viewer,
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationDetail:
    return get_publication_moderation_detail(db, publication_id)


@router.post("/{publication_id}/reviews", response_model=PublicationModerationDetail)
def post_publication_review(
    publication_id: uuid.UUID,
    payload: PublicationReviewRequest,
    request: Request,
    reviewer: Moderator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationDetail:
    return review_publication(
        db,
        publication_id=publication_id,
        reviewer=reviewer,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post("/{publication_id}/deactivations", response_model=PublicationModerationDetail)
def post_publication_deactivation(
    publication_id: uuid.UUID,
    payload: PublicationDeactivationRequest,
    request: Request,
    operator: Manager,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationDetail:
    return deactivate_publication(
        db,
        publication_id=publication_id,
        operator=operator,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post("/{publication_id}/promo-issuances", response_model=PublicationModerationDetail)
def post_publication_promo_issuance(
    publication_id: uuid.UUID,
    payload: PromoIssuanceCreate,
    request: Request,
    operator: Viewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationDetail:
    return record_promo_issuance(
        db,
        publication_id=publication_id,
        operator=operator,
        payload=payload,
        audit_context=context_from_request(request),
    )
