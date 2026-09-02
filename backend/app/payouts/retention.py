from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import case, exists, or_, select, update
from sqlalchemy.orm import Session, load_only

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, User
from app.clock import utc_now
from app.database.locking import set_transaction_timeouts
from app.payouts.models import (
    ANONYMIZED_TEXT,
    PayoutDetails,
    PayoutEvent,
    PayoutEventDetail,
    PayoutRequest,
    PayoutStatus,
)


RETENTION_LOCK_TIMEOUT_MS = 5_000
RETENTION_STATEMENT_TIMEOUT_MS = 60_000
PAYOUT_PII_RETENTION_YEARS = 5


@dataclass(frozen=True)
class RetentionResult:
    bloggers: int
    payout_requests: int
    event_details: int
    payout_details: int


def _minimum_retention_cutoff(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - PAYOUT_PII_RETENTION_YEARS)
    except ValueError:
        return as_of.replace(year=as_of.year - PAYOUT_PII_RETENTION_YEARS, day=28)


def anonymize_eligible_payout_pii(
    db: Session,
    *,
    cutoff: date,
    audit_context: AuditContext,
    max_bloggers: int = 100,
    anonymized_at: datetime | None = None,
) -> RetentionResult:
    """Irreversibly remove payout PII for long-deleted creator accounts.

    The caller owns the transaction. Eligibility is deliberately fail-closed:
    every payout must be terminal and no payout activity may be newer than the
    supplied legal-retention cutoff.
    """
    if max_bloggers < 1 or max_bloggers > 500:
        raise ValueError("max_bloggers must be between 1 and 500")

    set_transaction_timeouts(
        db,
        lock_timeout_ms=RETENTION_LOCK_TIMEOUT_MS,
        statement_timeout_ms=RETENTION_STATEMENT_TIMEOUT_MS,
    )
    effective_at = anonymized_at or utc_now()
    if cutoff > _minimum_retention_cutoff(effective_at.date()):
        raise ValueError("cutoff would shorten the mandatory five-year retention period")
    cutoff_start = datetime.combine(cutoff, time.min, tzinfo=timezone.utc)

    unsafe_payout = exists(
        select(PayoutRequest.id).where(
            PayoutRequest.blogger_id == User.id,
            or_(
                PayoutRequest.status.not_in(
                    (PayoutStatus.PAID, PayoutStatus.REJECTED)
                ),
                PayoutRequest.requested_at >= cutoff_start,
                PayoutRequest.paid_on >= cutoff,
                PayoutRequest.rejected_at >= cutoff_start,
            ),
        )
    )
    has_expired_pii = or_(
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
    bloggers = list(
        db.scalars(
            select(User)
            .options(
                load_only(
                    User.id,
                    User.status,
                    User.collaboration_ended_at,
                )
            )
            .where(
                User.status == AccountStatus.DELETED,
                User.collaboration_ended_at.is_not(None),
                User.collaboration_ended_at < cutoff_start,
                ~unsafe_payout,
                has_expired_pii,
            )
            .order_by(User.id)
            .limit(max_bloggers)
        )
    )
    if not bloggers:
        return RetentionResult(0, 0, 0, 0)

    blogger_ids = [blogger.id for blogger in bloggers]
    payout_rows = list(
        db.execute(
            select(PayoutRequest.id, PayoutRequest.blogger_id)
            .where(
                PayoutRequest.blogger_id.in_(blogger_ids),
                PayoutRequest.status.in_(
                    (PayoutStatus.PAID, PayoutStatus.REJECTED)
                ),
                PayoutRequest.pii_anonymized_at.is_(None),
            )
            .order_by(PayoutRequest.id)
            .with_for_update()
        )
    )
    payout_ids = [row.id for row in payout_rows]
    detail_blogger_ids = list(
        db.scalars(
            select(PayoutDetails.blogger_id)
            .where(
                PayoutDetails.blogger_id.in_(blogger_ids),
                PayoutDetails.pii_anonymized_at.is_(None),
            )
            .order_by(PayoutDetails.blogger_id)
            .with_for_update(skip_locked=True)
        )
    )
    processed_blogger_ids = {
        *(row.blogger_id for row in payout_rows),
        *detail_blogger_ids,
    }
    if not processed_blogger_ids:
        return RetentionResult(0, 0, 0, 0)

    request_count = 0
    event_count = 0
    if payout_ids:
        event_count = db.execute(
            update(PayoutEventDetail)
            .where(
                PayoutEventDetail.event_id.in_(
                    select(PayoutEvent.id).where(
                        PayoutEvent.payout_request_id.in_(payout_ids)
                    )
                ),
                PayoutEventDetail.pii_anonymized_at.is_(None),
            )
            .values(
                comment=case(
                    (PayoutEventDetail.comment.is_not(None), ANONYMIZED_TEXT),
                    else_=None,
                ),
                rejection_reason=case(
                    (
                        PayoutEventDetail.rejection_reason.is_not(None),
                        ANONYMIZED_TEXT,
                    ),
                    else_=None,
                ),
                pii_anonymized_at=effective_at,
            )
        ).rowcount
        request_count = db.execute(
            update(PayoutRequest)
            .where(PayoutRequest.id.in_(payout_ids))
            .values(
                recipient_full_name=ANONYMIZED_TEXT,
                recipient_display_name=ANONYMIZED_TEXT,
                sbp_phone=ANONYMIZED_TEXT,
                bank_name=None,
                manager_comment=None,
                rejection_reason=case(
                    (PayoutRequest.rejection_reason.is_not(None), ANONYMIZED_TEXT),
                    else_=None,
                ),
                pii_anonymized_at=effective_at,
                updated_at=effective_at,
            )
        ).rowcount

    details_count = db.execute(
        update(PayoutDetails)
        .where(
            PayoutDetails.blogger_id.in_(detail_blogger_ids),
            PayoutDetails.pii_anonymized_at.is_(None),
        )
        .values(
            sbp_phone=ANONYMIZED_TEXT,
            bank_name=None,
            pii_anonymized_at=effective_at,
            updated_at=effective_at,
        )
    ).rowcount

    for blogger in bloggers:
        if blogger.id not in processed_blogger_ids:
            continue
        record_event(
            db,
            context=audit_context,
            action=AuditAction.PAYOUT_PII_ANONYMIZED,
            object_type="user",
            object_id=blogger.id,
            metadata={"cutoff": cutoff.isoformat()},
        )
    db.flush()
    return RetentionResult(
        bloggers=len(processed_blogger_ids),
        payout_requests=request_count,
        event_details=event_count,
        payout_details=details_count,
    )
