from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import exists, or_, select, update
from sqlalchemy.orm import Session, load_only

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, User
from app.clock import utc_now
from app.content.models import Publication, PublicationHistory, VideoCard
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    SocialAccount,
    SocialAccountHistory,
)
from app.database.locking import set_transaction_timeouts
from app.notifications.models import Notification
from app.outbox.models import OutboxEvent
from app.payouts.models import PayoutDetails, PayoutRequest, PayoutStatus
from app.support.models import (
    SupportMessage,
    SupportTicket,
    SupportTicketEvent,
)


ANONYMIZED_TEXT = "[anonymized]"
RETENTION_YEARS = 5


@dataclass(frozen=True)
class AccountRetentionResult:
    accounts: int
    profiles: int
    social_accounts: int
    support_records: int
    notifications: int
    outbox_events: int


def retention_cutoff(as_of: date, years: int = RETENTION_YEARS) -> date:
    if years < RETENTION_YEARS:
        raise ValueError("account PII must be retained for at least five years")
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:
        return as_of.replace(year=as_of.year - years, day=28)


def anonymize_eligible_account_pii(
    db: Session,
    *,
    cutoff: date,
    audit_context: AuditContext,
    max_accounts: int = 100,
    anonymized_at: datetime | None = None,
) -> AccountRetentionResult:
    if max_accounts < 1 or max_accounts > 500:
        raise ValueError("max_accounts must be between 1 and 500")
    effective_at = anonymized_at or utc_now()
    if cutoff > retention_cutoff(effective_at.date()):
        raise ValueError("cutoff would shorten the mandatory five-year retention period")
    set_transaction_timeouts(db, lock_timeout_ms=5_000, statement_timeout_ms=60_000)
    cutoff_start = datetime.combine(cutoff, time.min, tzinfo=timezone.utc)
    unsafe_payout = exists(
        select(PayoutRequest.id).where(
            PayoutRequest.blogger_id == User.id,
            or_(
                PayoutRequest.status.not_in((PayoutStatus.PAID, PayoutStatus.REJECTED)),
                PayoutRequest.requested_at >= cutoff_start,
                PayoutRequest.paid_on >= cutoff,
                PayoutRequest.rejected_at >= cutoff_start,
            ),
        )
    )
    payout_pii_remaining = or_(
        exists(
            select(PayoutDetails.blogger_id).where(
                PayoutDetails.blogger_id == User.id,
                PayoutDetails.pii_anonymized_at.is_(None),
            )
        ),
        exists(
            select(PayoutRequest.id).where(
                PayoutRequest.blogger_id == User.id,
                PayoutRequest.pii_anonymized_at.is_(None),
            )
        ),
    )
    users = list(
        db.scalars(
            select(User)
            .options(
                load_only(
                    User.id,
                    User.email,
                    User.status,
                    User.collaboration_ended_at,
                    User.pii_anonymized_at,
                )
            )
            .where(
                User.status == AccountStatus.DELETED,
                User.collaboration_ended_at.is_not(None),
                User.collaboration_ended_at < cutoff_start,
                User.pii_anonymized_at.is_(None),
                ~unsafe_payout,
                ~payout_pii_remaining,
            )
            .order_by(User.collaboration_ended_at, User.id)
            .limit(max_accounts)
            .with_for_update(skip_locked=True)
        )
    )
    if not users:
        return AccountRetentionResult(0, 0, 0, 0, 0, 0)
    user_ids = [user.id for user in users]
    original_emails = [user.email for user in users]

    profiles = list(
        db.scalars(
            select(CreatorProfile)
            .options(
                load_only(
                    CreatorProfile.id,
                    CreatorProfile.user_id,
                    CreatorProfile.pii_anonymized_at,
                )
            )
            .where(CreatorProfile.user_id.in_(user_ids))
            .with_for_update(skip_locked=True)
        )
    )
    profile_ids = [profile.id for profile in profiles]
    for profile in profiles:
        profile.full_name = ANONYMIZED_TEXT
        profile.display_name = ANONYMIZED_TEXT
        profile.phone = None
        profile.telegram = None
        profile.city_country = None
        profile.content_topics = None
        profile.moderation_reason = None
        profile.pii_anonymized_at = effective_at

    social_accounts = list(
        db.scalars(
            select(SocialAccount)
            .options(
                load_only(
                    SocialAccount.id,
                    SocialAccount.user_id,
                    SocialAccount.pii_anonymized_at,
                )
            )
            .where(SocialAccount.user_id.in_(user_ids))
            .with_for_update(skip_locked=True)
        )
    )
    social_ids = [account.id for account in social_accounts]
    for account in social_accounts:
        account.url = f"https://anonymized.invalid/social/{account.id}"
        account.moderation_reason = None
        account.pii_anonymized_at = effective_at

    support_ticket_ids = list(
        db.scalars(select(SupportTicket.id).where(SupportTicket.blogger_id.in_(user_ids)))
    )
    support_count = 0
    if support_ticket_ids:
        support_count += db.execute(
            update(SupportTicket)
            .where(
                SupportTicket.id.in_(support_ticket_ids),
                SupportTicket.pii_anonymized_at.is_(None),
            )
            .values(subject=ANONYMIZED_TEXT, pii_anonymized_at=effective_at)
        ).rowcount
        support_count += db.execute(
            update(SupportMessage)
            .where(
                SupportMessage.ticket_id.in_(support_ticket_ids),
                SupportMessage.pii_anonymized_at.is_(None),
            )
            .values(body=ANONYMIZED_TEXT, pii_anonymized_at=effective_at)
        ).rowcount
        support_count += db.execute(
            update(SupportTicketEvent)
            .where(
                SupportTicketEvent.ticket_id.in_(support_ticket_ids),
                SupportTicketEvent.pii_anonymized_at.is_(None),
            )
            .values(reason=None, pii_anonymized_at=effective_at)
        ).rowcount

    notification_ids = list(
        db.scalars(select(Notification.id).where(Notification.recipient_user_id.in_(user_ids)))
    )
    notification_count = 0
    if notification_ids:
        notification_count = db.execute(
            update(Notification)
            .where(
                Notification.id.in_(notification_ids),
                Notification.pii_anonymized_at.is_(None),
            )
            .values(
                title=ANONYMIZED_TEXT,
                body=ANONYMIZED_TEXT,
                action_path=None,
                pii_anonymized_at=effective_at,
            )
        ).rowcount

    if profile_ids:
        db.execute(
            update(ProfileHistory)
            .where(
                ProfileHistory.profile_id.in_(profile_ids),
                ProfileHistory.pii_anonymized_at.is_(None),
            )
            .values(reason=None, changes={}, pii_anonymized_at=effective_at)
        )
    if social_ids:
        db.execute(
            update(SocialAccountHistory)
            .where(
                SocialAccountHistory.social_account_id.in_(social_ids),
                SocialAccountHistory.pii_anonymized_at.is_(None),
            )
            .values(reason=None, pii_anonymized_at=effective_at)
        )
    publication_ids = list(
        db.scalars(
            select(Publication.id)
            .join(VideoCard, VideoCard.id == Publication.video_card_id)
            .where(VideoCard.blogger_id.in_(user_ids))
        )
    )
    if publication_ids:
        db.execute(
            update(PublicationHistory)
            .where(
                PublicationHistory.publication_id.in_(publication_ids),
                PublicationHistory.pii_anonymized_at.is_(None),
            )
            .values(reason=None, changes={}, pii_anonymized_at=effective_at)
        )

    outbox_rows = list(
        db.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.payload["recipient"].as_string().in_(original_emails))
            .with_for_update(skip_locked=True)
        )
    )
    for event in outbox_rows:
        event.payload = {
            "recipient": "anonymized@invalid.example",
            "subject": ANONYMIZED_TEXT,
            "body": ANONYMIZED_TEXT,
        }
        event.pii_anonymized_at = effective_at

    for user in users:
        user.email = f"deleted-{user.id}@anonymized.invalid"
        user.password_hash = "!account-anonymized!"
        user.status_reason = None
        user.email_verified_at = None
        user.pii_anonymized_at = effective_at
        user.updated_at = effective_at
        record_event(
            db,
            context=audit_context,
            action=AuditAction.ACCOUNT_PII_ANONYMIZED,
            object_type="user",
            object_id=user.id,
            metadata={"cutoff": cutoff.isoformat()},
        )
    db.flush()
    return AccountRetentionResult(
        accounts=len(users),
        profiles=len(profiles),
        social_accounts=len(social_accounts),
        support_records=support_count,
        notifications=notification_count,
        outbox_events=len(outbox_rows),
    )
