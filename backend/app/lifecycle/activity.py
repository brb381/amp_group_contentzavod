import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.lifecycle.models import ActivityKind, CreatorLifecycle


def record_creator_activity(
    db: Session,
    *,
    blogger_id: uuid.UUID,
    kind: ActivityKind,
    occurred_at: datetime | None = None,
) -> CreatorLifecycle | None:
    user = db.scalar(select(User).where(User.id == blogger_id).with_for_update())
    if not user or user.role != Role.BLOGGER or user.status != AccountStatus.ACTIVE:
        return None
    occurred_at = occurred_at or utc_now()
    lifecycle = db.scalar(
        select(CreatorLifecycle)
        .where(CreatorLifecycle.blogger_id == blogger_id)
        .with_for_update()
    )
    if lifecycle is None:
        lifecycle = CreatorLifecycle(
            blogger_id=blogger_id,
            last_activity_at=occurred_at,
            last_activity_kind=kind,
            activity_revision=1,
        )
        db.add(lifecycle)
    elif occurred_at > (
        lifecycle.last_activity_at
        if lifecycle.last_activity_at.tzinfo
        else lifecycle.last_activity_at.replace(tzinfo=timezone.utc)
    ):
        lifecycle.last_activity_at = occurred_at
        lifecycle.last_activity_kind = kind
        lifecycle.activity_revision += 1
    return lifecycle
