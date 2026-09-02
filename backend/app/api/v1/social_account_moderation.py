import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import require_csrf, require_roles
from app.auth.models import Role, User
from app.creators.models import SocialAccountStatus
from app.creators.schemas import (
    SocialAccountModerationDetail,
    SocialAccountModerationListResponse,
    SocialAccountReviewRequest,
)
from app.creators.social_moderation import (
    get_social_account_moderation_detail,
    list_social_accounts_for_moderation,
    review_social_account,
)
from app.database.session import get_db


router = APIRouter(prefix="/moderation/social-accounts", tags=["social account moderation"])
Moderator = Annotated[User, Depends(require_roles(Role.MODERATOR, Role.ADMIN))]


@router.get("", response_model=SocialAccountModerationListResponse)
def get_social_account_queue(
    _: Moderator,
    account_status: SocialAccountStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> SocialAccountModerationListResponse:
    return list_social_accounts_for_moderation(
        db, account_status=account_status, page=page, page_size=page_size
    )


@router.get("/{account_id}", response_model=SocialAccountModerationDetail)
def get_social_account_detail(
    account_id: uuid.UUID, _: Moderator, db: Session = Depends(get_db, scope="function")
) -> SocialAccountModerationDetail:
    return get_social_account_moderation_detail(db, account_id)


@router.post("/{account_id}/reviews", response_model=SocialAccountModerationDetail)
def post_social_account_review(
    account_id: uuid.UUID,
    payload: SocialAccountReviewRequest,
    request: Request,
    reviewer: Moderator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> SocialAccountModerationDetail:
    return review_social_account(
        db, account_id, reviewer, payload, context_from_request(request)
    )
