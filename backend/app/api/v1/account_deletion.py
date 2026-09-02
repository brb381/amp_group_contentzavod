import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.account_deletion.models import AccountDeletionRequest
from app.account_deletion.schemas import (
    AccountDeletionCancellationRequest,
    AccountDeletionConfirmationRequest,
    AccountDeletionCreateRequest,
    AccountDeletionListResponse,
    AccountDeletionResponse,
)
from app.account_deletion.service import (
    cancel_deletion_request,
    confirm_deletion_request,
    create_deletion_request,
    list_deletion_requests,
)
from app.audit.http import context_from_request
from app.auth.dependencies import (
    CSRF_COOKIE,
    auth_cookie_names,
    require_csrf,
    require_roles,
)
from app.auth.models import Role, User
from app.auth.rate_limit import RedisRateLimiter, enforce_rate_limit, get_rate_limiter
from app.auth.security import identity_hash
from app.config import Settings, get_settings
from app.database.session import get_db


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    tags=["account-deletion"], dependencies=[Depends(_set_private_no_store)]
)
Blogger = Annotated[User, Depends(require_roles(Role.BLOGGER))]


@router.post(
    "/me/account-deletion-requests",
    response_model=AccountDeletionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_account_deletion_request(
    payload: AccountDeletionCreateRequest,
    request: Request,
    actor: Blogger,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> AccountDeletionRequest:
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:account-deletion:create:user:{actor.id}",
        limit=5,
        window_seconds=3600,
        fail_closed=True,
    )
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:account-deletion:create:ip:{identity_hash(client_ip)}",
        limit=20,
        window_seconds=3600,
        fail_closed=True,
    )
    return create_deletion_request(
        db,
        actor=actor,
        payload=payload,
        settings=settings,
        audit_context=context_from_request(request),
    )


@router.get(
    "/me/account-deletion-requests", response_model=AccountDeletionListResponse
)
def get_account_deletion_requests(
    actor: Blogger,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> AccountDeletionListResponse:
    return list_deletion_requests(db, actor=actor, page=page, page_size=page_size)


@router.post(
    "/me/account-deletion-requests/{request_id}/cancellations",
    response_model=AccountDeletionResponse,
)
def post_account_deletion_cancellation(
    request_id: uuid.UUID,
    payload: AccountDeletionCancellationRequest,
    request: Request,
    actor: Blogger,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> AccountDeletionRequest:
    return cancel_deletion_request(
        db,
        actor=actor,
        request_id=request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/account-deletion-requests/{request_id}/confirmations",
    response_model=AccountDeletionResponse,
)
def post_account_deletion_confirmation(
    request_id: uuid.UUID,
    payload: AccountDeletionConfirmationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> AccountDeletionRequest:
    client_ip = request.client.host if request.client else "unknown"
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:account-deletion:token:{identity_hash(payload.token)}",
        limit=5,
        window_seconds=3600,
        fail_closed=True,
    )
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:account-deletion:ip:{identity_hash(client_ip)}",
        limit=20,
        window_seconds=3600,
        fail_closed=True,
    )
    deletion = confirm_deletion_request(
        db,
        payload=payload,
        request_id=request_id,
        audit_context=context_from_request(request),
    )
    access_cookie, refresh_cookie = auth_cookie_names(settings)
    response.delete_cookie(access_cookie, path="/")
    response.delete_cookie(refresh_cookie, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return deletion
