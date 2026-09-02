import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AccountStatus, Role, User
from app.errors import APIError
from app.lifecycle.models import CreatorLifecycle
from app.lifecycle.policy import block_due_at, suspension_due_at
from app.lifecycle.schemas import AccountLifecycleResponse


def get_account_lifecycle(
    db: Session, *, blogger_id: uuid.UUID
) -> AccountLifecycleResponse:
    row = db.execute(
        select(User, CreatorLifecycle)
        .join(CreatorLifecycle, CreatorLifecycle.blogger_id == User.id)
        .where(User.id == blogger_id, User.role == Role.BLOGGER)
    ).one_or_none()
    if not row:
        raise APIError(404, "ACCOUNT_LIFECYCLE_NOT_FOUND", "Account lifecycle was not found")
    user, lifecycle = row
    next_transition = None
    next_transition_at = None
    if user.status == AccountStatus.ACTIVE:
        next_transition = "suspension"
        next_transition_at = suspension_due_at(lifecycle.last_activity_at)
    elif user.status == AccountStatus.SUSPENDED and lifecycle.suspended_at:
        next_transition = "blocking"
        next_transition_at = block_due_at(lifecycle.suspended_at)
    return AccountLifecycleResponse(
        blogger_id=user.id,
        account_status=user.status,
        last_activity_at=lifecycle.last_activity_at,
        last_activity_kind=lifecycle.last_activity_kind,
        next_transition=next_transition,
        next_transition_at=next_transition_at,
        suspended_at=lifecycle.suspended_at,
        restored_at=lifecycle.restored_at,
        blocked_at=lifecycle.blocked_at,
        balance_claim_expired_at=lifecycle.balance_claim_expired_at,
    )
