import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import SecurityEvent


SENSITIVE_METADATA_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "new_password",
    "password",
    "refresh_token",
    "reset_token",
    "secret",
}


class AuditAction:
    USER_REGISTERED = "auth.user_registered"
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT_SUCCEEDED = "auth.logout_succeeded"
    EMAIL_VERIFICATION_REQUESTED = "auth.email_verification_requested"
    EMAIL_VERIFIED = "auth.email_verified"
    PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
    PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"
    SESSION_REFRESHED = "auth.session_refreshed"
    REFRESH_REUSE_DETECTED = "auth.refresh_reuse_detected"
    PROFILE_CREATED = "creator.profile_created"
    PROFILE_UPDATED = "creator.profile_updated"
    PROFILE_SUBMITTED = "creator.profile_submitted"
    PROFILE_REVIEWED = "moderation.profile_reviewed"
    SOCIAL_ACCOUNT_CREATED = "creator.social_account_created"
    SOCIAL_ACCOUNT_RESTORED = "creator.social_account_restored"
    SOCIAL_ACCOUNT_UPDATED = "creator.social_account_updated"
    SOCIAL_ACCOUNT_DELETED = "creator.social_account_deleted"
    SOCIAL_ACCOUNT_REVIEWED = "moderation.social_account_reviewed"
    ADMIN_BOOTSTRAPPED = "admin.user_bootstrapped"
    USER_ROLE_CHANGED = "admin.user_role_changed"
    USER_BLOCKED = "admin.user_blocked"
    USER_UNBLOCKED = "admin.user_unblocked"
    PRODUCT_CREATED = "catalog.product_created"
    PRODUCT_UPDATED = "catalog.product_updated"
    PRODUCT_HIDDEN = "catalog.product_hidden"
    PRODUCT_RESTORED = "catalog.product_restored"
    VIDEO_CARD_CREATED = "content.video_card_created"
    VIDEO_CARD_UPDATED = "content.video_card_updated"
    PUBLICATION_CREATED = "content.publication_created"
    PUBLICATION_UPDATED = "content.publication_updated"
    PUBLICATION_DELETED = "content.publication_deleted"
    PUBLICATION_SUBMITTED = "content.publication_submitted"
    PUBLICATION_REVIEWED = "moderation.publication_reviewed"
    VIEW_READING_CREATED = "readings.created"
    VIEW_READING_UPDATED = "readings.updated"
    VIEW_READING_REVIEWED = "moderation.view_reading_reviewed"
    VIEW_READING_CORRECTED = "moderation.view_reading_corrected"
    BILLING_RATE_CREATED = "billing.rate_created"
    CALCULATION_RECALCULATION_REQUESTED = "billing.recalculation_requested"
    CALCULATION_PERIOD_CONFIRMED = "billing.period_confirmed"
    ACCRUAL_CORRECTED = "billing.accrual_corrected"
    PAYOUT_DETAILS_UPDATED = "payout.details_updated"
    PAYOUT_REQUESTED = "payout.requested"
    PAYOUT_REVIEW_STARTED = "payout.review_started"
    PAYOUT_APPROVED = "payout.approved"
    PAYOUT_REJECTED = "payout.rejected"
    PAYOUT_PAID = "payout.paid"
    PAYOUT_RECEIPT_RECORDED = "payout.receipt_recorded"
    PAYOUT_EXPORT_REQUESTED = "payout.export_requested"
    PAYOUT_EXPORT_DOWNLOADED = "payout.export_downloaded"
    PAYOUT_PII_ANONYMIZED = "payout.pii_anonymized"
    SUPPORT_TICKET_CREATED = "support.ticket_created"
    SUPPORT_MESSAGE_CREATED = "support.message_created"
    SUPPORT_TICKET_ASSIGNED = "support.ticket_assigned"
    SUPPORT_STATUS_CHANGED = "support.status_changed"
    NOTIFICATION_TEMPLATE_UPDATED = "notification.template_updated"
    ACCOUNT_SUSPENDED_FOR_INACTIVITY = "lifecycle.account_suspended"
    ACCOUNT_BLOCKED_FOR_INACTIVITY = "lifecycle.account_blocked"
    ACCOUNT_RECOVERY_APPROVED = "lifecycle.recovery_approved"
    ACCOUNT_RECOVERY_REJECTED = "lifecycle.recovery_rejected"
    ACCOUNT_DELETION_REQUESTED = "account.deletion_requested"
    ACCOUNT_DELETION_CANCELLED = "account.deletion_cancelled"
    ACCOUNT_DELETION_COMPLETED = "account.deletion_completed"
    ACCOUNT_PII_ANONYMIZED = "account.pii_anonymized"
    LEGAL_DOCUMENT_PUBLISHED = "legal.document_published"
    LEGAL_DOCUMENT_ACCEPTED = "legal.document_accepted"
    PERSONAL_DATA_CONSENT_WITHDRAWN = "legal.personal_data_consent_withdrawn"


@dataclass(frozen=True)
class AuditContext:
    request_id: str
    ip_address: str
    user_agent: str | None
    session_id: uuid.UUID | None = None


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in SENSITIVE_METADATA_KEYS or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def record_event(
    db: Session,
    *,
    context: AuditContext,
    action: str,
    result: str = "success",
    actor_user_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> SecurityEvent:
    if result not in {"success", "failure", "denied"}:
        raise ValueError("Unsupported audit result")
    event_metadata = metadata or {}
    if _contains_sensitive_key(event_metadata):
        raise ValueError("Audit metadata contains a sensitive field")
    serialized_metadata = json.dumps(event_metadata)
    if len(serialized_metadata) > 4096:
        raise ValueError("Audit metadata is too large")
    audit_event = SecurityEvent(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=action,
        result=result,
        object_type=object_type,
        object_id=object_id,
        session_id=session_id or context.session_id,
        request_id=context.request_id,
        ip_address=context.ip_address,
        user_agent=context.user_agent,
        event_metadata=event_metadata,
    )
    db.add(audit_event)
    return audit_event
