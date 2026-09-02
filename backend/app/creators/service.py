import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import Role, User
from app.auth.security import utc_now
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    ProfileStatus,
    SocialAccount,
    SocialAccountHistory,
    SocialAccountStatus,
)
from app.creators.schemas import (
    ModerationReviewRequest,
    ProfileResponse,
    ProfileUpsertRequest,
    SocialAccountCreateRequest,
    SocialAccountUpdateRequest,
)
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification


EDITABLE_PROFILE_STATUSES = {
    ProfileStatus.DRAFT,
    ProfileStatus.SUBMITTED,
    ProfileStatus.IN_REVIEW,
    ProfileStatus.REJECTED,
}


def require_blogger(user: User) -> None:
    if user.role != Role.BLOGGER:
        raise APIError(403, "BLOGGER_ROLE_REQUIRED", "This operation is available only to bloggers")


def get_user_profile(db: Session, user_id: uuid.UUID, *, for_update: bool = False) -> CreatorProfile | None:
    statement = select(CreatorProfile).where(CreatorProfile.user_id == user_id)
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def profile_response(db: Session, profile: CreatorProfile) -> ProfileResponse:
    db.flush()
    accounts = list(
        db.scalars(
            select(SocialAccount)
            .where(SocialAccount.user_id == profile.user_id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at)
        )
    )
    history = list(
        db.scalars(
            select(ProfileHistory)
            .where(ProfileHistory.profile_id == profile.id)
            .order_by(ProfileHistory.created_at.desc())
        )
    )
    response = ProfileResponse.model_validate(profile)
    response.social_accounts = accounts
    response.history = history
    return response


def _history(
    db: Session,
    profile: CreatorProfile,
    actor_user_id: uuid.UUID,
    event_type: str,
    *,
    from_status: ProfileStatus | None = None,
    reason: str | None = None,
    changes: dict | None = None,
) -> None:
    db.add(
        ProfileHistory(
            profile_id=profile.id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            from_status=from_status.value if from_status else None,
            to_status=profile.status.value,
            reason=reason,
            changes=changes or {},
        )
    )


def _record_social_change(
    db: Session,
    user: User,
    account: SocialAccount,
    action: str,
    *,
    locked_profile: CreatorProfile | None = None,
) -> None:
    profile = locked_profile or get_user_profile(db, user.id, for_update=True)
    if not profile:
        return
    previous_status = profile.status
    if profile.status in {ProfileStatus.SUBMITTED, ProfileStatus.IN_REVIEW}:
        profile.status = ProfileStatus.DRAFT
        profile.moderation_reason = None
        profile.reviewed_at = None
    _history(
        db,
        profile,
        user.id,
        "social_account_changed",
        from_status=previous_status,
        changes={"social_account": {"action": action, "id": str(account.id)}},
    )


def record_social_account_history(
    db: Session,
    account: SocialAccount,
    actor_user_id: uuid.UUID,
    event_type: str,
    *,
    from_status: str | None,
    to_status: str,
    reason: str | None = None,
) -> None:
    db.add(
        SocialAccountHistory(
            social_account_id=account.id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
        )
    )


def upsert_profile(
    db: Session, user: User, payload: ProfileUpsertRequest, audit_context: AuditContext
) -> CreatorProfile:
    require_blogger(user)
    profile = get_user_profile(db, user.id, for_update=True)
    values = payload.model_dump()
    if not profile:
        profile = CreatorProfile(user_id=user.id, **values)
        db.add(profile)
        try:
            db.flush()
        except IntegrityError as error:
            db.rollback()
            raise APIError(409, "PROFILE_ALREADY_EXISTS", "Profile was created by another request; retry") from error
        _history(db, profile, user.id, "profile_created")
        record_event(
            db,
            context=audit_context,
            action=AuditAction.PROFILE_CREATED,
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="creator_profile",
            object_id=profile.id,
        )
        return profile

    if profile.status not in EDITABLE_PROFILE_STATUSES:
        raise APIError(409, "PROFILE_NOT_EDITABLE", "Profile cannot be edited in its current status")

    changes = {}
    for field, new_value in values.items():
        old_value = getattr(profile, field)
        if old_value != new_value:
            changes[field] = {
                "old": old_value.value if hasattr(old_value, "value") else old_value,
                "new": new_value.value if hasattr(new_value, "value") else new_value,
            }
            setattr(profile, field, new_value)

    if not changes:
        return profile

    previous_status = profile.status
    if profile.status in {ProfileStatus.SUBMITTED, ProfileStatus.IN_REVIEW}:
        profile.status = ProfileStatus.DRAFT
        profile.moderation_reason = None
        profile.reviewed_at = None
    _history(db, profile, user.id, "profile_updated", from_status=previous_status, changes=changes)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PROFILE_UPDATED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="creator_profile",
        object_id=profile.id,
        metadata={"changed_fields": sorted(changes), "previous_status": previous_status.value},
    )
    return profile


def add_social_account(
    db: Session, user: User, payload: SocialAccountCreateRequest, audit_context: AuditContext
) -> SocialAccount:
    require_blogger(user)
    profile = get_user_profile(db, user.id, for_update=True)
    url = str(payload.url)
    existing = db.scalar(
        select(SocialAccount)
        .where(SocialAccount.user_id == user.id, SocialAccount.url == url)
        .with_for_update()
    )
    if existing:
        if not existing.deleted_at:
            raise APIError(409, "SOCIAL_ACCOUNT_ALREADY_EXISTS", "This social account is already added")
        existing.platform = payload.platform
        existing.follower_count = payload.follower_count
        existing.status = SocialAccountStatus.PENDING
        existing.moderation_reason = None
        existing.deleted_at = None
        db.flush()
        record_social_account_history(
            db,
            existing,
            user.id,
            "social_account_restored",
            from_status="deleted",
            to_status=SocialAccountStatus.PENDING.value,
        )
        _record_social_change(db, user, existing, "restored", locked_profile=profile)
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SOCIAL_ACCOUNT_RESTORED,
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="social_account",
            object_id=existing.id,
        )
        return existing

    account = SocialAccount(
        user_id=user.id,
        platform=payload.platform,
        url=url,
        follower_count=payload.follower_count,
    )
    db.add(account)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise APIError(409, "SOCIAL_ACCOUNT_ALREADY_EXISTS", "This social account is already added") from error
    record_social_account_history(
        db,
        account,
        user.id,
        "social_account_created",
        from_status=None,
        to_status=SocialAccountStatus.PENDING.value,
    )
    _record_social_change(db, user, account, "added", locked_profile=profile)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SOCIAL_ACCOUNT_CREATED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="social_account",
        object_id=account.id,
    )
    return account


def list_social_accounts(db: Session, user: User) -> list[SocialAccount]:
    require_blogger(user)
    return list(
        db.scalars(
            select(SocialAccount)
            .where(SocialAccount.user_id == user.id, SocialAccount.deleted_at.is_(None))
            .order_by(SocialAccount.created_at)
        )
    )


def update_social_account(
    db: Session,
    user: User,
    account_id: uuid.UUID,
    payload: SocialAccountUpdateRequest,
    audit_context: AuditContext,
) -> SocialAccount:
    require_blogger(user)
    profile = get_user_profile(db, user.id, for_update=True)
    account = db.scalar(
        select(SocialAccount)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.user_id == user.id,
            SocialAccount.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not account:
        raise APIError(404, "SOCIAL_ACCOUNT_NOT_FOUND", "Social account was not found")
    values = payload.model_dump(exclude_unset=True)
    if "url" in values:
        values["url"] = str(values["url"])
    previous_status = account.status.value
    for field, value in values.items():
        setattr(account, field, value)
    if values:
        account.status = SocialAccountStatus.PENDING
        account.moderation_reason = None
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise APIError(409, "SOCIAL_ACCOUNT_ALREADY_EXISTS", "This social account is already added") from error
    if values:
        record_social_account_history(
            db,
            account,
            user.id,
            "social_account_updated",
            from_status=previous_status,
            to_status=SocialAccountStatus.PENDING.value,
        )
        _record_social_change(db, user, account, "updated", locked_profile=profile)
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SOCIAL_ACCOUNT_UPDATED,
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="social_account",
            object_id=account.id,
            metadata={"changed_fields": sorted(values)},
        )
    return account


def delete_social_account(
    db: Session, user: User, account_id: uuid.UUID, audit_context: AuditContext
) -> None:
    require_blogger(user)
    profile = get_user_profile(db, user.id, for_update=True)
    account = db.scalar(
        select(SocialAccount)
        .where(
            SocialAccount.id == account_id,
            SocialAccount.user_id == user.id,
            SocialAccount.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if account:
        previous_status = account.status.value
        account.deleted_at = utc_now()
        record_social_account_history(
            db,
            account,
            user.id,
            "social_account_deleted",
            from_status=previous_status,
            to_status="deleted",
        )
        _record_social_change(db, user, account, "deleted", locked_profile=profile)
        record_event(
            db,
            context=audit_context,
            action=AuditAction.SOCIAL_ACCOUNT_DELETED,
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="social_account",
            object_id=account.id,
        )


def submit_profile(db: Session, user: User, audit_context: AuditContext) -> CreatorProfile:
    require_blogger(user)
    profile = get_user_profile(db, user.id, for_update=True)
    if not profile:
        raise APIError(409, "PROFILE_INCOMPLETE", "Create the profile before submitting it")
    if not user.email_verified_at:
        raise APIError(409, "EMAIL_NOT_VERIFIED", "Confirm the email before submitting the profile")
    if profile.status not in {ProfileStatus.DRAFT, ProfileStatus.REJECTED}:
        raise APIError(409, "PROFILE_NOT_SUBMITTABLE", "Profile cannot be submitted in its current status")

    required = ["full_name", "display_name", "phone", "telegram", "recipient_status"]
    missing = [field for field in required if not getattr(profile, field)]
    has_social_account = db.scalar(
        select(func.count())
        .select_from(SocialAccount)
        .where(SocialAccount.user_id == user.id, SocialAccount.deleted_at.is_(None))
    )
    if not has_social_account:
        missing.append("social_accounts")
    if missing:
        raise APIError(422, "PROFILE_INCOMPLETE", "Required profile data is missing", {"fields": missing})

    previous_status = profile.status
    profile.status = ProfileStatus.SUBMITTED
    profile.submitted_at = utc_now()
    profile.reviewed_at = None
    profile.moderation_reason = None
    _history(db, profile, user.id, "profile_submitted", from_status=previous_status)
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PROFILE_SUBMITTED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="creator_profile",
        object_id=profile.id,
        metadata={"previous_status": previous_status.value},
    )
    return profile


TRANSITIONS = {
    "start_review": ({ProfileStatus.SUBMITTED}, ProfileStatus.IN_REVIEW),
    "approve": ({ProfileStatus.IN_REVIEW}, ProfileStatus.APPROVED),
    "reject": ({ProfileStatus.IN_REVIEW}, ProfileStatus.REJECTED),
    "suspend": ({ProfileStatus.APPROVED}, ProfileStatus.SUSPENDED),
    "block": ({ProfileStatus.APPROVED, ProfileStatus.SUSPENDED}, ProfileStatus.BLOCKED),
}


def review_profile(
    db: Session,
    profile_id: uuid.UUID,
    reviewer: User,
    payload: ModerationReviewRequest,
    audit_context: AuditContext,
) -> CreatorProfile:
    profile = db.scalar(select(CreatorProfile).where(CreatorProfile.id == profile_id).with_for_update())
    if not profile:
        raise APIError(404, "PROFILE_NOT_FOUND", "Profile was not found")
    allowed_from, target = TRANSITIONS[payload.decision]
    if profile.status not in allowed_from:
        raise APIError(
            409,
            "INVALID_PROFILE_TRANSITION",
            "Profile cannot accept this moderation decision in its current status",
            {"current_status": profile.status.value, "decision": payload.decision},
        )
    previous_status = profile.status
    profile.status = target
    profile.moderation_reason = payload.reason if target != ProfileStatus.APPROVED else None
    if payload.decision != "start_review":
        profile.reviewed_at = utc_now()
    _history(
        db,
        profile,
        reviewer.id,
        f"profile_{payload.decision}",
        from_status=previous_status,
        reason=payload.reason,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PROFILE_REVIEWED,
        actor_user_id=reviewer.id,
        actor_role=reviewer.role.value,
        object_type="creator_profile",
        object_id=profile.id,
        metadata={
            "decision": payload.decision,
            "from_status": previous_status.value,
            "to_status": target.value,
        },
    )
    create_notification(
        db,
        NotificationCommand(
            recipient_user_id=profile.user_id,
            template_code="profile_status_changed",
            context={"status": target.value},
            severity=(
                NotificationSeverity.ACTION_REQUIRED
                if target in {ProfileStatus.REJECTED, ProfileStatus.SUSPENDED, ProfileStatus.BLOCKED}
                else NotificationSeverity.INFO
            ),
            deduplication_key=(
                f"profile:{profile.id}:status:{target.value}:{profile.reviewed_at or profile.submitted_at}"
            ),
            related_object_type="creator_profile",
            related_object_id=profile.id,
            action_path="/profile",
        ),
    )
    return profile
