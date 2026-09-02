import uuid
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import (
    AccountStatus,
    EmailVerificationToken,
    PasswordResetToken,
    RefreshSession,
    Role,
    User,
)
from app.auth.schemas import RegisterRequest
from app.auth.security import (
    hash_password,
    hash_password_reset_token,
    hash_refresh_token,
    hash_email_verification_token,
    identity_hash,
    is_expired,
    new_email_verification_token,
    new_password_reset_token,
    new_refresh_token,
    utc_now,
    verify_password,
)
from app.config import Settings
from app.errors import APIError
from app.outbox.models import OutboxEvent
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.lifecycle.activity import record_creator_activity
from app.lifecycle.models import ActivityKind, CreatorLifecycle
from app.legal.service import accept_registration_documents


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _queue_email(db: Session, *, recipient: str, subject: str, body: str) -> None:
    db.add(
        OutboxEvent(
            event_type="email_delivery_requested",
            payload={"recipient": recipient, "subject": subject, "body": body},
        )
    )


def _queue_verification_email(db: Session, user: User, settings: Settings) -> None:
    token = issue_email_verification_token(db, user.id, settings)
    link = f"{settings.frontend_url.rstrip('/')}/verify-email#token={token}"
    _queue_email(
        db,
        recipient=user.email,
        subject="Confirm your AMP email",
        body=f"Confirm your email: {link}",
    )


def _queue_password_reset_email(db: Session, user: User, settings: Settings) -> None:
    token = issue_password_reset_token(db, user.id, settings)
    link = f"{settings.frontend_url.rstrip('/')}/reset-password#token={token}"
    _queue_email(
        db,
        recipient=user.email,
        subject="Reset your AMP password",
        body=f"Reset your password: {link}",
    )


def register_user(
    db: Session,
    payload: RegisterRequest,
    audit_context: AuditContext,
    settings: Settings,
) -> User:
    email = normalize_email(str(payload.email))
    user = User(email=email, password_hash=hash_password(payload.password), role=Role.BLOGGER)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        record_event(
            db,
            context=audit_context,
            action=AuditAction.USER_REGISTERED,
            result="failure",
            metadata={"email_identity": identity_hash(email), "reason": "duplicate_email"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered") from error
    db.add(
        CreatorLifecycle(
            blogger_id=user.id,
            last_activity_at=user.created_at or utc_now(),
            last_activity_kind=ActivityKind.ACCOUNT_CREATED,
            activity_revision=1,
        )
    )
    accept_registration_documents(
        db,
        user=user,
        program_terms_document_id=payload.program_terms_document_id,
        personal_data_consent_document_id=payload.personal_data_consent_document_id,
        audit_context=audit_context,
    )
    _queue_verification_email(db, user, settings)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.USER_REGISTERED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="user",
        object_id=user.id,
    )
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=user.id,
            template_code="registration_created",
            context={},
            severity=NotificationSeverity.ACTION_REQUIRED,
            deduplication_key=f"registration:{user.id}",
            related_object_type="user",
            related_object_id=user.id,
            action_path="/verify-email",
            send_email=False,
        ),
    )
    return user


def request_email_verification(
    db: Session, user: User, audit_context: AuditContext, settings: Settings
) -> None:
    if user.email_verified_at:
        return
    if user.status != AccountStatus.EMAIL_PENDING:
        raise APIError(409, "EMAIL_VERIFICATION_NOT_ALLOWED", "Email verification is not available for this account")
    _queue_verification_email(db, user, settings)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.EMAIL_VERIFICATION_REQUESTED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="user",
        object_id=user.id,
    )


def issue_email_verification_token(db: Session, user_id: uuid.UUID, settings: Settings) -> str:
    now = utc_now()
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user_id,
            EmailVerificationToken.used_at.is_(None),
            EmailVerificationToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )
    raw_token = new_email_verification_token()
    db.add(
        EmailVerificationToken(
            user_id=user_id,
            token_hash=hash_email_verification_token(raw_token),
            expires_at=now + timedelta(hours=settings.email_verification_ttl_hours),
        )
    )
    return raw_token


def confirm_email_verification(db: Session, token: str, audit_context: AuditContext) -> User:
    now = utc_now()
    verification = db.scalar(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.token_hash == hash_email_verification_token(token))
        .with_for_update()
    )
    if not verification or verification.invalidated_at or is_expired(verification.expires_at, now=now):
        record_event(
            db,
            context=audit_context,
            action=AuditAction.EMAIL_VERIFIED,
            result="failure",
            metadata={"token_identity": identity_hash(token), "reason": "invalid_or_expired"},
        )
        db.commit()
        raise APIError(400, "INVALID_OR_EXPIRED_TOKEN", "Verification link is invalid or expired")

    user = db.get(User, verification.user_id)
    if not user:
        record_event(
            db,
            context=audit_context,
            action=AuditAction.EMAIL_VERIFIED,
            result="failure",
            metadata={"token_identity": identity_hash(token), "reason": "user_missing"},
        )
        db.commit()
        raise APIError(400, "INVALID_OR_EXPIRED_TOKEN", "Verification link is invalid or expired")
    if verification.used_at:
        if user.email_verified_at:
            return user
        record_event(
            db,
            context=audit_context,
            action=AuditAction.EMAIL_VERIFIED,
            result="failure",
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="user",
            object_id=user.id,
            metadata={"reason": "token_already_used"},
        )
        db.commit()
        raise APIError(400, "INVALID_OR_EXPIRED_TOKEN", "Verification link is invalid or expired")

    verification.used_at = now
    user.email_verified_at = now
    if user.status == AccountStatus.EMAIL_PENDING:
        user.status = AccountStatus.ACTIVE
    record_event(
        db,
        context=audit_context,
        action=AuditAction.EMAIL_VERIFIED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="user",
        object_id=user.id,
    )
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=user.id,
            template_code="email_verified",
            context={},
            severity=NotificationSeverity.INFO,
            deduplication_key=f"email-verified:{user.id}",
            related_object_type="user",
            related_object_id=user.id,
            action_path="/profile",
            send_email=False,
        ),
    )
    return user


def request_password_reset(
    db: Session,
    email: str,
    audit_context: AuditContext,
    settings: Settings,
) -> None:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user and user.status != AccountStatus.DELETED:
        _queue_password_reset_email(db, user, settings)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PASSWORD_RESET_REQUESTED,
        actor_user_id=user.id if user else None,
        actor_role=user.role.value if user else None,
        object_type="user" if user else None,
        object_id=user.id if user else None,
        metadata={"email_identity": identity_hash(email)},
    )


def issue_password_reset_token(db: Session, user_id: uuid.UUID, settings: Settings) -> str:
    now = utc_now()
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )
    raw_token = new_password_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=hash_password_reset_token(raw_token),
            expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    return raw_token


def reset_password(
    db: Session, *, token: str, new_password: str, audit_context: AuditContext
) -> User:
    now = utc_now()
    new_password_hash = hash_password(new_password)
    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_password_reset_token(token))
        .with_for_update()
    )
    if (
        not reset_token
        or reset_token.used_at
        or reset_token.invalidated_at
        or is_expired(reset_token.expires_at, now=now)
    ):
        record_event(
            db,
            context=audit_context,
            action=AuditAction.PASSWORD_RESET_COMPLETED,
            result="failure",
            metadata={"token_identity": identity_hash(token), "reason": "invalid_or_expired"},
        )
        db.commit()
        raise APIError(400, "INVALID_OR_EXPIRED_TOKEN", "Password reset link is invalid or expired")

    user = db.get(User, reset_token.user_id)
    if not user or user.status == AccountStatus.DELETED:
        record_event(
            db,
            context=audit_context,
            action=AuditAction.PASSWORD_RESET_COMPLETED,
            result="failure",
            metadata={"token_identity": identity_hash(token), "reason": "account_unavailable"},
        )
        db.commit()
        raise APIError(400, "INVALID_OR_EXPIRED_TOKEN", "Password reset link is invalid or expired")

    user.password_hash = new_password_hash
    reset_token.used_at = now
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != reset_token.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PASSWORD_RESET_COMPLETED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="user",
        object_id=user.id,
    )
    return user


def _create_refresh_session(
    db: Session,
    user_id: uuid.UUID,
    settings: Settings,
    family_id: uuid.UUID | None = None,
) -> tuple[RefreshSession, str]:
    raw_token = new_refresh_token()
    session = RefreshSession(
        user_id=user_id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_refresh_token(raw_token),
        expires_at=utc_now() + timedelta(days=settings.refresh_token_ttl_days),
    )
    db.add(session)
    db.flush()
    return session, raw_token


def login(
    db: Session,
    *,
    email: str,
    password: str,
    settings: Settings,
    audit_context: AuditContext,
) -> tuple[User, RefreshSession, str]:
    normalized_email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or not verify_password(password, user.password_hash):
        record_event(
            db,
            context=audit_context,
            action=AuditAction.LOGIN_FAILED,
            result="failure",
            actor_user_id=user.id if user else None,
            actor_role=user.role.value if user else None,
            object_type="user" if user else None,
            object_id=user.id if user else None,
            metadata={"email_identity": identity_hash(normalized_email), "reason": "invalid_credentials"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if user.status in {AccountStatus.BLOCKED, AccountStatus.DELETED}:
        record_event(
            db,
            context=audit_context,
            action=AuditAction.LOGIN_FAILED,
            result="denied",
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="user",
            object_id=user.id,
            metadata={"reason": "account_unavailable"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is unavailable")
    session, refresh_token = _create_refresh_session(db, user.id, settings)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.LOGIN_SUCCEEDED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="refresh_session",
        object_id=session.id,
        session_id=session.id,
    )
    record_creator_activity(
        db,
        blogger_id=user.id,
        kind=ActivityKind.LOGIN,
    )
    return user, session, refresh_token


def refresh(
    db: Session,
    *,
    refresh_token: str,
    settings: Settings,
    audit_context: AuditContext,
) -> tuple[User, RefreshSession, str]:
    current = db.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == hash_refresh_token(refresh_token))
        .with_for_update()
    )
    if not current:
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SESSION_REFRESHED,
            result="failure",
            metadata={"token_identity": identity_hash(refresh_token), "reason": "invalid_token"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if current.revoked_at:
        db.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == current.family_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        user = db.get(User, current.user_id)
        record_event(
            db,
            context=audit_context,
            action=AuditAction.REFRESH_REUSE_DETECTED,
            result="denied",
            actor_user_id=user.id if user else None,
            actor_role=user.role.value if user else None,
            object_type="refresh_session",
            object_id=current.id,
            session_id=current.id,
            metadata={"family_id": str(current.family_id)},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected")
    if is_expired(current.expires_at):
        user = db.get(User, current.user_id)
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SESSION_REFRESHED,
            result="failure",
            actor_user_id=user.id if user else None,
            actor_role=user.role.value if user else None,
            object_type="refresh_session",
            object_id=current.id,
            session_id=current.id,
            metadata={"reason": "expired_token"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired refresh token")

    user = db.get(User, current.user_id)
    if not user or user.status in {AccountStatus.BLOCKED, AccountStatus.DELETED}:
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SESSION_REFRESHED,
            result="denied",
            actor_user_id=user.id if user else None,
            actor_role=user.role.value if user else None,
            object_type="refresh_session",
            object_id=current.id,
            session_id=current.id,
            metadata={"reason": "account_unavailable"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is unavailable")

    current.revoked_at = utc_now()
    current.last_used_at = utc_now()
    new_session, raw_token = _create_refresh_session(db, user.id, settings, family_id=current.family_id)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SESSION_REFRESHED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="refresh_session",
        object_id=new_session.id,
        session_id=new_session.id,
        metadata={"previous_session_id": str(current.id)},
    )
    return user, new_session, raw_token


def revoke_session(db: Session, session_id: uuid.UUID) -> None:
    session = db.get(RefreshSession, session_id)
    if session and not session.revoked_at:
        session.revoked_at = utc_now()
