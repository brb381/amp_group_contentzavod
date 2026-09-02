import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now
from app.auth.service import normalize_email
from app.errors import APIError


BOOTSTRAP_LOCK_ID = 4_172_009


def bootstrap_first_admin(db: Session, *, email: str, password: str) -> User:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": BOOTSTRAP_LOCK_ID})
    admin_count = db.scalar(
        select(func.count()).select_from(User).where(User.role == Role.ADMIN)
    ) or 0
    if admin_count:
        raise APIError(409, "ADMIN_ALREADY_EXISTS", "An administrator already exists")

    now = utc_now()
    admin = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        role=Role.ADMIN,
        status=AccountStatus.ACTIVE,
        email_verified_at=now,
        status_changed_at=now,
        status_reason="Initial administrator bootstrap",
    )
    db.add(admin)
    try:
        db.flush()
    except IntegrityError as error:
        raise APIError(409, "EMAIL_ALREADY_EXISTS", "A user with this email already exists") from error
    record_event(
        db,
        context=AuditContext(
            request_id=f"cli:{uuid.uuid4()}",
            ip_address="local",
            user_agent="create-admin",
        ),
        action=AuditAction.ADMIN_BOOTSTRAPPED,
        actor_user_id=admin.id,
        actor_role=admin.role.value,
        object_type="user",
        object_id=admin.id,
    )
    return admin
