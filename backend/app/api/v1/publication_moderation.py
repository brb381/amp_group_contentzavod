import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import require_active_roles, require_csrf
from app.auth.models import Role, User
from app.content.models import PublicationParseStatus, PublicationStatus
from app.content.moderation_service import (
    get_publication_moderation_detail,
    list_publications_for_moderation,
    review_publication,
)
from app.content.schemas import (
    PublicationModerationDetail,
    PublicationModerationListResponse,
    PublicationReviewRequest,
)
from app.database.session import get_db
from app.platforms import Platform


router = APIRouter(prefix="/moderation/publications", tags=["publication moderation"])
Moderator = Annotated[
    User,
    Depends(require_active_roles(Role.MODERATOR, Role.ADMIN)),
]


@router.get("", response_model=PublicationModerationListResponse)
def get_publication_queue(
    _: Moderator,
    publication_status: PublicationStatus | None = Query(
        default=PublicationStatus.PENDING_REVIEW,
        alias="status",
    ),
    platform: Platform | None = Query(default=None),
    parse_status: PublicationParseStatus | None = Query(default=None, alias="parseStatus"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationModerationListResponse:
    return list_publications_for_moderation(
        db,
        publication_status=publication_status,
        platform=platform,
        parse_status=parse_status,
        page=page,
        page_size=page_size,
    )


@router.get("/{publication_id}", response_model=PublicationModerationDetail)
def get_publication_moderation_card(
    publication_id: uuid.UUID,
    _: Moderator,
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
