import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import User
from app.creators.models import (
    CreatorProfile,
    SocialAccount,
    SocialAccountHistory,
    SocialAccountStatus,
)
from app.creators.schemas import (
    SocialAccountModerationDetail,
    SocialAccountModerationItem,
    SocialAccountModerationListResponse,
    SocialAccountResponse,
    SocialAccountReviewRequest,
)
from app.creators.service import record_social_account_history
from app.errors import APIError


def _moderation_item(
    account: SocialAccount, profile: CreatorProfile | None
) -> SocialAccountModerationItem:
    return SocialAccountModerationItem(
        account=SocialAccountResponse.model_validate(account),
        creator_user_id=account.user_id,
        profile_id=profile.id if profile else None,
        creator_full_name=profile.full_name if profile else None,
        creator_display_name=profile.display_name if profile else None,
    )


def list_social_accounts_for_moderation(
    db: Session,
    *,
    account_status: SocialAccountStatus | None,
    page: int,
    page_size: int,
) -> SocialAccountModerationListResponse:
    filters = [SocialAccount.deleted_at.is_(None)]
    if account_status:
        filters.append(SocialAccount.status == account_status)

    total = db.scalar(select(func.count()).select_from(SocialAccount).where(*filters)) or 0
    rows = db.execute(
        select(SocialAccount, CreatorProfile)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == SocialAccount.user_id)
        .where(*filters)
        .order_by(SocialAccount.created_at, SocialAccount.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return SocialAccountModerationListResponse(
        items=[_moderation_item(account, profile) for account, profile in rows],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_social_account_moderation_detail(
    db: Session, account_id: uuid.UUID
) -> SocialAccountModerationDetail:
    row = db.execute(
        select(SocialAccount, CreatorProfile)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == SocialAccount.user_id)
        .where(SocialAccount.id == account_id)
    ).one_or_none()
    if not row:
        raise APIError(404, "SOCIAL_ACCOUNT_NOT_FOUND", "Social account was not found")
    account, profile = row
    history = list(
        db.scalars(
            select(SocialAccountHistory)
            .where(SocialAccountHistory.social_account_id == account.id)
            .order_by(SocialAccountHistory.created_at.desc(), SocialAccountHistory.id.desc())
        )
    )
    item = _moderation_item(account, profile)
    return SocialAccountModerationDetail(**item.model_dump(), history=history)


def review_social_account(
    db: Session,
    account_id: uuid.UUID,
    reviewer: User,
    payload: SocialAccountReviewRequest,
    audit_context: AuditContext,
) -> SocialAccountModerationDetail:
    account = db.scalar(select(SocialAccount).where(SocialAccount.id == account_id).with_for_update())
    if not account:
        raise APIError(404, "SOCIAL_ACCOUNT_NOT_FOUND", "Social account was not found")
    if account.deleted_at:
        raise APIError(409, "SOCIAL_ACCOUNT_DELETED", "Deleted social account cannot be reviewed")
    if account.status != SocialAccountStatus.PENDING:
        raise APIError(
            409,
            "SOCIAL_ACCOUNT_ALREADY_REVIEWED",
            "Social account has already been reviewed or changed",
            {"current_status": account.status.value},
        )

    target = SocialAccountStatus.APPROVED if payload.decision == "approve" else SocialAccountStatus.REJECTED
    account.status = target
    account.moderation_reason = payload.reason if target == SocialAccountStatus.REJECTED else None
    record_social_account_history(
        db,
        account,
        reviewer.id,
        f"social_account_{payload.decision}",
        from_status=SocialAccountStatus.PENDING.value,
        to_status=target.value,
        reason=payload.reason,
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.SOCIAL_ACCOUNT_REVIEWED,
        actor_user_id=reviewer.id,
        actor_role=reviewer.role.value,
        object_type="social_account",
        object_id=account.id,
        metadata={
            "decision": payload.decision,
            "from_status": SocialAccountStatus.PENDING.value,
            "to_status": target.value,
        },
    )
    db.flush()
    return get_social_account_moderation_detail(db, account.id)
