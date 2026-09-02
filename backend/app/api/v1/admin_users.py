import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.admin_schemas import (
    AccessChangeRequest,
    AdminUserListResponse,
    AdminUserResponse,
    RoleChangeRequest,
)
from app.auth.admin_service import change_user_role, get_user, list_users, set_user_blocked
from app.auth.dependencies import require_active_roles, require_csrf
from app.auth.models import AccountStatus, Role, User
from app.database.session import get_db


router = APIRouter(prefix="/admin/users", tags=["admin users"])
Administrator = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("", response_model=AdminUserListResponse)
def get_users(
    _: Administrator,
    email: str | None = Query(default=None, min_length=1, max_length=320),
    role: Role | None = Query(default=None),
    account_status: AccountStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> AdminUserListResponse:
    users, total, total_pages = list_users(
        db,
        email=email,
        role=role,
        account_status=account_status,
        page=page,
        page_size=page_size,
    )
    return AdminUserListResponse(
        items=users,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
def get_user_detail(
    user_id: uuid.UUID, _: Administrator, db: Session = Depends(get_db, scope="function")
) -> User:
    return get_user(db, user_id)


@router.put("/{user_id}/role", response_model=AdminUserResponse)
def put_user_role(
    user_id: uuid.UUID,
    payload: RoleChangeRequest,
    request: Request,
    administrator: Administrator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> User:
    return change_user_role(
        db,
        actor=administrator,
        target_user_id=user_id,
        new_role=payload.role,
        reason=payload.reason,
        audit_context=context_from_request(request),
    )


@router.patch("/{user_id}/access", response_model=AdminUserResponse)
def patch_user_access(
    user_id: uuid.UUID,
    payload: AccessChangeRequest,
    request: Request,
    administrator: Administrator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> User:
    return set_user_blocked(
        db,
        actor=administrator,
        target_user_id=user_id,
        is_blocked=payload.is_blocked,
        reason=payload.reason,
        audit_context=context_from_request(request),
    )
