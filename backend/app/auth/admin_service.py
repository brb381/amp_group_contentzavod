import math
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import utc_now
from app.errors import APIError


def list_users(
    db: Session,
    *,
    email: str | None,
    role: Role | None,
    account_status: AccountStatus | None,
    page: int,
    page_size: int,
) -> tuple[list[User], int, int]:
    filters = []
    if email:
        filters.append(User.email.contains(email.strip().lower(), autoescape=True))
    if role:
        filters.append(User.role == role)
    if account_status:
        filters.append(User.status == account_status)

    total = db.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    users = list(
        db.scalars(
            select(User)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return users, total, math.ceil(total / page_size)


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if not user:
        raise APIError(404, "USER_NOT_FOUND", "User was not found")
    return user


def _lock_active_admins_and_target(
    db: Session, target_user_id: uuid.UUID
) -> tuple[list[User], User]:
    active_admins = list(
        db.scalars(
            select(User)
            .where(User.role == Role.ADMIN, User.status == AccountStatus.ACTIVE)
            .order_by(User.id)
            .with_for_update()
        )
    )
    target = next((user for user in active_admins if user.id == target_user_id), None)
    if target is None:
        target = db.scalar(select(User).where(User.id == target_user_id).with_for_update())
    if target is None:
        raise APIError(404, "USER_NOT_FOUND", "User was not found")
    return active_admins, target


def _revoke_sessions(db: Session, user_id: uuid.UUID) -> int:
    result = db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    return result.rowcount


def _require_locked_administrator(active_admins: list[User], actor: User) -> None:
    if not any(admin.id == actor.id for admin in active_admins):
        raise APIError(
            403,
            "ADMIN_PERMISSION_CHANGED",
            "Administrator permissions changed; authenticate again",
        )


def change_user_role(
    db: Session,
    *,
    actor: User,
    target_user_id: uuid.UUID,
    new_role: Role,
    reason: str,
    audit_context: AuditContext,
) -> User:
    active_admins, target = _lock_active_admins_and_target(db, target_user_id)
    _require_locked_administrator(active_admins, actor)
    if target.status == AccountStatus.DELETED:
        raise APIError(409, "DELETED_USER_NOT_EDITABLE", "Deleted user cannot be changed")
    if target.role == new_role:
        return target
    if target.id == actor.id:
        raise APIError(409, "SELF_ROLE_CHANGE_NOT_ALLOWED", "Administrator cannot change their own role")
    if new_role != Role.BLOGGER and (
        target.status != AccountStatus.ACTIVE or target.email_verified_at is None
    ):
        raise APIError(
            409,
            "STAFF_ACCOUNT_NOT_ACTIVE",
            "Staff roles can be assigned only to active users with verified email",
        )
    if (
        target.role == Role.ADMIN
        and target.status == AccountStatus.ACTIVE
        and len(active_admins) <= 1
    ):
        raise APIError(409, "LAST_ADMIN_REQUIRED", "The last active administrator cannot be demoted")

    old_role = target.role
    target.role = new_role
    target.updated_at = utc_now()
    revoked_sessions = _revoke_sessions(db, target.id)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.USER_ROLE_CHANGED,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="user",
        object_id=target.id,
        metadata={
            "old_role": old_role.value,
            "new_role": new_role.value,
            "reason": reason,
            "revoked_sessions": revoked_sessions,
        },
    )
    return target


def set_user_blocked(
    db: Session,
    *,
    actor: User,
    target_user_id: uuid.UUID,
    is_blocked: bool,
    reason: str,
    audit_context: AuditContext,
) -> User:
    active_admins, target = _lock_active_admins_and_target(db, target_user_id)
    _require_locked_administrator(active_admins, actor)
    if target.status == AccountStatus.DELETED:
        raise APIError(409, "DELETED_USER_NOT_EDITABLE", "Deleted user cannot be changed")
    if is_blocked and target.status == AccountStatus.BLOCKED:
        return target
    if not is_blocked and target.status != AccountStatus.BLOCKED:
        return target
    if target.id == actor.id:
        raise APIError(409, "SELF_BLOCK_NOT_ALLOWED", "Administrator cannot change their own access")
    if (
        is_blocked
        and target.role == Role.ADMIN
        and target.status == AccountStatus.ACTIVE
        and len(active_admins) <= 1
    ):
        raise APIError(409, "LAST_ADMIN_REQUIRED", "The last active administrator cannot be blocked")

    now = utc_now()
    old_status = target.status
    if is_blocked:
        target.status_before_block = target.status
        target.status = AccountStatus.BLOCKED
        revoked_sessions = _revoke_sessions(db, target.id)
        action = AuditAction.USER_BLOCKED
    else:
        target.status = target.status_before_block or (
            AccountStatus.ACTIVE if target.email_verified_at else AccountStatus.EMAIL_PENDING
        )
        target.status_before_block = None
        revoked_sessions = 0
        action = AuditAction.USER_UNBLOCKED
    target.status_reason = reason
    target.status_changed_at = now
    target.updated_at = now
    record_event(
        db,
        context=audit_context,
        action=action,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="user",
        object_id=target.id,
        metadata={
            "old_status": old_status.value,
            "new_status": target.status.value,
            "reason": reason,
            "revoked_sessions": revoked_sessions,
        },
    )
    return target
