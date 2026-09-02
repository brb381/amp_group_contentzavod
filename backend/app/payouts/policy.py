from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.payouts.models import PayoutStatus, ReceiptStatus, RecipientType


MOSCOW = ZoneInfo("Europe/Moscow")
PAYMENT_TERM_DAYS = 20
RECEIPT_TERM_DAYS = 3


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def moscow_today(now: datetime | None = None) -> date:
    if now is None:
        return datetime.now(MOSCOW).date()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(MOSCOW).date()


def payment_due_date(approved_at: datetime) -> date:
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise ValueError("approved_at must be timezone-aware")
    return approved_at.astimezone(MOSCOW).date() + timedelta(days=PAYMENT_TERM_DAYS)


def receipt_due_date(paid_on: date) -> date:
    return paid_on + timedelta(days=RECEIPT_TERM_DAYS)


def effective_receipt_status(
    payout: object,
    today: date | None = None,
) -> ReceiptStatus:
    recipient_type = _enum_value(getattr(payout, "recipient_type", None))
    status = _enum_value(getattr(payout, "status", None))

    if recipient_type != RecipientType.SELF_EMPLOYED.value or status == PayoutStatus.REJECTED.value:
        return ReceiptStatus.NOT_APPLICABLE
    if getattr(payout, "receipt_received_on", None) is not None:
        return ReceiptStatus.RECEIVED
    if status != PayoutStatus.PAID.value or getattr(payout, "paid_on", None) is None:
        return ReceiptStatus.PENDING_PAYMENT

    due = getattr(payout, "receipt_due_date", None)
    effective_today = today or moscow_today()
    if due is not None and effective_today > due:
        return ReceiptStatus.OVERDUE
    return ReceiptStatus.AWAITING_RECEIPT


def is_payment_overdue(
    payout: object,
    today: date | None = None,
) -> bool:
    if _enum_value(getattr(payout, "status", None)) != PayoutStatus.APPROVED.value:
        return False
    due = getattr(payout, "payment_due_date", None)
    return due is not None and (today or moscow_today()) > due
