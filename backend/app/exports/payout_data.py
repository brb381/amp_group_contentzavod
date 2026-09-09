from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.auth.models import User
from app.exports.data import ExportTooLargeError, MAX_EXPORT_ROWS
from app.exports.models import ExportType
from app.exports.schemas import PayoutExportFilters
from app.payouts.models import PayoutRequest, PayoutStatus, RecipientType
from app.payouts.policy import MOSCOW


def _utc_start(value):
    return datetime.combine(value, time.min, tzinfo=MOSCOW).astimezone(timezone.utc)


def load_payout_export_rows(
    db: Session,
    *,
    export_type: ExportType,
    filters: PayoutExportFilters,
) -> list[dict]:
    approved_by = aliased(User)
    paid_by = aliased(User)
    rejected_by = aliased(User)
    clauses = [
        PayoutRequest.requested_at >= _utc_start(filters.requested_from),
        PayoutRequest.requested_at < _utc_start(filters.requested_to + timedelta(days=1)),
    ]
    if export_type == ExportType.PAYOUT_REGISTER:
        clauses.append(PayoutRequest.status == PayoutStatus.APPROVED)
    elif filters.status:
        clauses.append(PayoutRequest.status == filters.status)
    if filters.recipient_type:
        clauses.append(PayoutRequest.recipient_type == filters.recipient_type)
    if filters.blogger_id:
        clauses.append(PayoutRequest.blogger_id == filters.blogger_id)
    if filters.request_number:
        clauses.append(PayoutRequest.request_number == filters.request_number)
    if filters.approved_from:
        clauses.append(PayoutRequest.approved_at >= _utc_start(filters.approved_from))
    if filters.approved_to:
        clauses.append(
            PayoutRequest.approved_at < _utc_start(filters.approved_to + timedelta(days=1))
        )

    statement = (
        select(
            PayoutRequest,
            approved_by.email.label("approved_by"),
            paid_by.email.label("paid_by"),
            rejected_by.email.label("rejected_by"),
        )
        .outerjoin(approved_by, approved_by.id == PayoutRequest.approved_by_user_id)
        .outerjoin(paid_by, paid_by.id == PayoutRequest.paid_by_user_id)
        .outerjoin(rejected_by, rejected_by.id == PayoutRequest.rejected_by_user_id)
        .where(*clauses)
        .order_by(PayoutRequest.requested_at, PayoutRequest.id)
        .limit(MAX_EXPORT_ROWS + 1)
    )
    results = list(db.execute(statement))
    if len(results) > MAX_EXPORT_ROWS:
        raise ExportTooLargeError

    rows = []
    for payout, approver_email, payer_email, rejector_email in results:
        rows.append(
            {
                "request_number": payout.request_number,
                "recipient_name": payout.recipient_full_name or payout.recipient_display_name,
                "recipient_type": payout.recipient_type,
                "sbp_phone": payout.sbp_phone,
                "bank_name": payout.bank_name,
                "amount_rubles": payout.amount_kopecks,
                "requested_on": payout.requested_at,
                "approved_on": payout.approved_at,
                "payment_due_date": payout.payment_due_date,
                "self_employment_verified": (
                    "not_applicable"
                    if payout.recipient_type != RecipientType.SELF_EMPLOYED
                    else "yes"
                    if payout.self_employment_verified_at
                    else "no"
                ),
                "manager_comment": payout.manager_comment,
                "status": payout.status,
                "paid_on": payout.paid_on,
                "payment_reference": payout.payment_reference,
                "rejected_on": payout.rejected_at,
                "rejection_reason": payout.rejection_reason,
                "receipt_due_date": payout.receipt_due_date,
                "receipt_received_on": payout.receipt_received_on,
                "approved_by": approver_email,
                "paid_by": payer_email,
                "rejected_by": rejector_email,
            }
        )
    return rows
