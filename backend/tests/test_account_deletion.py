import uuid
from datetime import date, datetime, timezone
from urllib.parse import parse_qs

import pytest
from sqlalchemy import func, select

from app.account_deletion.models import (
    AccountDeletionEvent,
    AccountDeletionRequest,
    DeletionEventAction,
    DeletionRequestStatus,
)
from app.audit.models import SecurityEvent
from app.audit.service import AuditContext
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import hash_password
from app.billing.models import (
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    RateVersion,
)
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationParseStatus,
    PublicationStatus,
    VideoCard,
)
from app.creators.models import (
    CreatorProfile,
    ProfileStatus,
    SocialAccount,
    SocialAccountStatus,
)
from app.lifecycle.models import ActivityKind, CreatorLifecycle, LifecycleAction, LifecycleJob
from app.legal.models import AcceptanceMethod, LegalAcceptance, LegalDocument, LegalDocumentType
from app.notifications.models import Notification, NotificationChannel, NotificationSeverity, NotificationTemplateVersion
from app.outbox.models import OutboxEvent
from app.payouts.models import PayoutRequest, PayoutStatus, RecipientType
from app.platforms import Platform
from app.retention.service import ANONYMIZED_TEXT, anonymize_eligible_account_pii
from app.support.models import (
    SupportCategory,
    SupportEventType,
    SupportMessage,
    SupportStatus,
    SupportTicket,
    SupportTicketEvent,
)


PASSWORD = "deletion-test-password-123"


def _csrf(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def _seed_creator(client, *, balance=0, status=AccountStatus.ACTIVE):
    blogger_id = uuid.uuid4()
    email = f"delete-{blogger_id}@example.com"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with client.app.state.test_session.begin() as db:
        blogger = User(
            id=blogger_id,
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.BLOGGER,
            status=status,
            email_verified_at=now,
        )
        profile = CreatorProfile(
            user_id=blogger_id,
            full_name="Deletion Creator",
            display_name="deletion-channel",
            phone="+79990000000",
            telegram="@private",
            status=ProfileStatus.APPROVED,
        )
        account = SocialAccount(
            user_id=blogger_id,
            platform=Platform.YOUTUBE,
            url=f"https://youtube.com/@{blogger_id}",
            status=SocialAccountStatus.APPROVED,
        )
        card = VideoCard(
            blogger_id=blogger_id,
            title="Retained statistical card",
            reported_brand="AMP",
            reported_product_name="Product",
        )
        db.add_all([blogger, profile, account, card])
        db.flush()
        publication = Publication(
            video_card_id=card.id,
            social_account_id=account.id,
            platform=Platform.YOUTUBE,
            submitted_url=f"https://youtube.com/watch?v={blogger_id.hex[:11]}",
            normalized_url=f"https://www.youtube.com/watch?v={blogger_id.hex[:11]}",
            external_id=blogger_id.hex[:11],
            status=PublicationStatus.APPROVED,
            parse_status=PublicationParseStatus.PARSED,
            availability=PublicationAvailability.AVAILABLE,
            enrichment_status=PublicationEnrichmentStatus.SUCCEEDED,
        )
        lifecycle = CreatorLifecycle(
            blogger_id=blogger_id,
            last_activity_at=now,
            last_activity_kind=ActivityKind.LOGIN,
            activity_revision=1,
        )
        lifecycle_job = LifecycleJob(
            blogger_id=blogger_id,
            action=LifecycleAction.WARN_SUSPENSION_30,
            basis_revision=1,
            due_at=now,
            available_at=now,
        )
        db.add_all(
            [publication, lifecycle, lifecycle_job, CreatorBalance(blogger_id=blogger_id, available_kopecks=balance)]
        )
        db.flush()
        return blogger_id, email, profile.id, account.id, publication.id, lifecycle_job.id


def _login(client, email):
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _request_deletion(client):
    response = client.post(
        "/api/v1/me/account-deletion-requests",
        json={"idempotency_key": str(uuid.uuid4()), "password": PASSWORD},
        headers=_csrf(client),
    )
    assert response.status_code == 202, response.text
    with client.app.state.test_session() as db:
        outbox = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.correlation_type == "account_deletion_request")
            .order_by(OutboxEvent.created_at.desc())
        )
        fragment = outbox.payload["body"].split("#", 1)[1]
    return response.json(), parse_qs(fragment)["token"][0]


def _confirm(client, request_id, token, idempotency_key=None):
    return client.post(
        f"/api/v1/account-deletion-requests/{request_id}/confirmations",
        json={
            "idempotency_key": str(idempotency_key or uuid.uuid4()),
            "token": token,
        },
    )


def test_deletion_request_is_private_idempotent_and_cancellable(client):
    _, email, *_ = _seed_creator(client)
    _login(client, email)
    key = uuid.uuid4()
    payload = {"idempotency_key": str(key), "password": PASSWORD}
    first = client.post(
        "/api/v1/me/account-deletion-requests", json=payload, headers=_csrf(client)
    )
    replay = client.post(
        "/api/v1/me/account-deletion-requests", json=payload, headers=_csrf(client)
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert "token" not in first.text.lower()
    assert first.headers["Cache-Control"] == "private, no-store"

    cancelled = client.post(
        f"/api/v1/me/account-deletion-requests/{first.json()['id']}/cancellations",
        json={"idempotency_key": str(uuid.uuid4())},
        headers=_csrf(client),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    listed = client.get("/api/v1/me/account-deletion-requests")
    assert listed.json()["items"][0]["status"] == "cancelled"


def test_confirmation_refuses_unsettled_balance_without_partial_deletion(client):
    blogger_id, email, *_ = _seed_creator(client, balance=1)
    _login(client, email)
    deletion, token = _request_deletion(client)
    response = _confirm(client, deletion["id"], token)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_DELETION_BALANCE_NOT_SETTLED"
    with client.app.state.test_session() as db:
        assert db.get(User, blogger_id).status == AccountStatus.ACTIVE
        assert db.get(AccountDeletionRequest, uuid.UUID(deletion["id"])).status == DeletionRequestStatus.AWAITING_CONFIRMATION
        assert db.scalar(select(func.count()).select_from(AccountDeletionEvent)) == 1


@pytest.mark.parametrize(
    ("blocker", "error_code"),
    [
        ("payout", "ACCOUNT_DELETION_PAYOUT_IN_PROGRESS"),
        ("preliminary", "ACCOUNT_DELETION_EARNINGS_NOT_SETTLED"),
    ],
)
def test_confirmation_refuses_unsettled_financial_work(client, blocker, error_code):
    blogger_id, email, *_ = _seed_creator(client)
    with client.app.state.test_session.begin() as db:
        if blocker == "payout":
            db.add(
                PayoutRequest(
                    request_number=f"TEST-{blogger_id.hex[:12]}",
                    blogger_id=blogger_id,
                    amount_kopecks=1,
                    status=PayoutStatus.REQUESTED,
                    recipient_full_name="Creator",
                    recipient_display_name="Channel",
                    recipient_type=RecipientType.INDIVIDUAL,
                    sbp_phone="+79990000000",
                )
            )
        else:
            rate = RateVersion(
                rate_kopecks_per_view=5,
                effective_from_period=date(2026, 1, 1),
            )
            db.add(rate)
            db.flush()
            period = CalculationPeriod(
                period=date(2026, 1, 1),
                status=CalculationPeriodStatus.PRELIMINARY,
                rate_version_id=rate.id,
                total_amount_kopecks=1,
            )
            db.add(period)
            db.flush()
            db.add(
                CreatorPeriodTotal(
                    period_id=period.id,
                    blogger_id=blogger_id,
                    amount_kopecks=1,
                )
            )
    _login(client, email)
    deletion, token = _request_deletion(client)
    response = _confirm(client, deletion["id"], token)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == error_code


def test_confirmation_soft_deletes_domain_revokes_access_and_replays(client):
    blogger_id, email, profile_id, account_id, publication_id, lifecycle_job_id = _seed_creator(client)
    _login(client, email)
    deletion, token = _request_deletion(client)
    key = uuid.uuid4()
    response = _confirm(client, deletion["id"], token, key)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    ).status_code in {401, 403}
    replay = _confirm(client, deletion["id"], token, key)
    assert replay.status_code == 200
    assert replay.json() == response.json()

    with client.app.state.test_session() as db:
        user = db.get(User, blogger_id)
        assert user.status == AccountStatus.DELETED
        assert user.deleted_at is not None and user.collaboration_ended_at is not None
        assert db.get(CreatorProfile, profile_id).status == ProfileStatus.DELETED
        assert db.get(SocialAccount, account_id).deleted_at is not None
        assert db.get(Publication, publication_id).status == PublicationStatus.INACTIVE
        assert db.get(LifecycleJob, lifecycle_job_id).state.value == "obsolete"
        assert not db.scalars(
            select(RefreshSession).where(
                RefreshSession.user_id == blogger_id,
                RefreshSession.revoked_at.is_(None),
            )
        ).first()
        actions = set(
            db.scalars(
                select(AccountDeletionEvent.action).where(
                    AccountDeletionEvent.request_id == uuid.UUID(deletion["id"])
                )
            )
        )
        assert actions == {DeletionEventAction.REQUESTED, DeletionEventAction.COMPLETED}


def test_confirmation_rolls_back_if_audit_write_fails(client, monkeypatch):
    blogger_id, email, *_ = _seed_creator(client)
    _login(client, email)
    deletion, token = _request_deletion(client)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.account_deletion.service.record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        _confirm(client, deletion["id"], token)
    with client.app.state.test_session() as db:
        assert db.get(User, blogger_id).status == AccountStatus.ACTIVE
        assert db.get(AccountDeletionRequest, uuid.UUID(deletion["id"])).status == DeletionRequestStatus.AWAITING_CONFIRMATION


def test_existing_confirmation_token_remains_valid_after_administrative_block(client):
    blogger_id, email, *_ = _seed_creator(client)
    _login(client, email)
    deletion, token = _request_deletion(client)
    with client.app.state.test_session.begin() as db:
        blogger = db.get(User, blogger_id)
        blogger.status = AccountStatus.BLOCKED
        blogger.status_before_block = AccountStatus.ACTIVE
        blogger.status_changed_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    response = _confirm(client, deletion["id"], token)
    assert response.status_code == 200, response.text
    with client.app.state.test_session() as db:
        assert db.get(User, blogger_id).status == AccountStatus.DELETED


def test_retention_anonymizes_identity_after_five_years_and_preserves_facts(client):
    blogger_id, email, profile_id, account_id, publication_id, _ = _seed_creator(client)
    ended_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with client.app.state.test_session.begin() as db:
        user = db.get(User, blogger_id)
        user.status = AccountStatus.DELETED
        user.status_changed_at = ended_at
        user.collaboration_ended_at = ended_at
        user.deleted_at = ended_at
        document = db.scalar(
            select(LegalDocument).where(
                LegalDocument.document_type == LegalDocumentType.PROGRAM_TERMS,
                LegalDocument.is_current.is_(True),
            )
        )
        db.add(
            LegalAcceptance(
                user_id=blogger_id,
                document_id=document.id,
                method=AcceptanceMethod.AUTHENTICATED_CLICK,
                request_id="retention-test",
                ip_hash="a" * 64,
            )
        )
        db.add(
            OutboxEvent(
                event_type="email_delivery_requested",
                payload={"recipient": email, "subject": "Private", "body": "Private body"},
            )
        )
        template = db.scalar(
            select(NotificationTemplateVersion)
            .where(NotificationTemplateVersion.channel == NotificationChannel.IN_APP)
            .limit(1)
        )
        ticket = SupportTicket(
            ticket_number=f"RET-{blogger_id.hex[:16]}",
            blogger_id=blogger_id,
            category=SupportCategory.GENERAL,
            subject="Private subject",
            status=SupportStatus.CLOSED,
            creation_idempotency_key=uuid.uuid4(),
            creation_payload_hash="a" * 64,
            last_message_at=ended_at,
            resolved_at=ended_at,
            closed_at=ended_at,
        )
        db.add(ticket)
        db.flush()
        message = SupportMessage(
            ticket_id=ticket.id,
            author_user_id=blogger_id,
            author_role=Role.BLOGGER.value,
            body="Private support body",
            idempotency_key=uuid.uuid4(),
            payload_hash="b" * 64,
        )
        support_event = SupportTicketEvent(
            ticket_id=ticket.id,
            event_type=SupportEventType.CREATED,
            actor_user_id=blogger_id,
            to_status=SupportStatus.NEW,
            reason="Private support reason",
            idempotency_key=uuid.uuid4(),
            payload_hash="c" * 64,
        )
        notification = Notification(
            recipient_user_id=blogger_id,
            template_code=template.code,
            template_version_id=template.id,
            severity=NotificationSeverity.INFO,
            title="Private notification",
            body="Private notification body",
            action_path="/private/path",
            deduplication_key=f"retention:{blogger_id}",
        )
        db.add_all([message, support_event, notification])
        db.flush()
        ticket_id = ticket.id
        message_id = message.id
        support_event_id = support_event.id
        notification_id = notification.id

    with client.app.state.test_session.begin() as db:
        too_early = anonymize_eligible_account_pii(
            db,
            cutoff=date(2019, 12, 31),
            anonymized_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            audit_context=AuditContext("retention-early", "127.0.0.1", "pytest"),
        )
    assert too_early.accounts == 0

    with client.app.state.test_session.begin() as db:
        result = anonymize_eligible_account_pii(
            db,
            cutoff=date(2021, 1, 1),
            anonymized_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            audit_context=AuditContext("retention", "127.0.0.1", "pytest"),
        )
    assert result.accounts == 1
    with client.app.state.test_session() as db:
        user = db.get(User, blogger_id)
        profile = db.get(CreatorProfile, profile_id)
        social = db.get(SocialAccount, account_id)
        publication = db.get(Publication, publication_id)
        assert user.email == f"deleted-{blogger_id}@anonymized.invalid"
        assert user.pii_anonymized_at is not None
        assert profile.full_name == profile.display_name == ANONYMIZED_TEXT
        assert profile.phone is None and profile.telegram is None
        assert social.url == f"https://anonymized.invalid/social/{account_id}"
        assert publication.external_id == blogger_id.hex[:11]
        assert db.scalar(select(func.count()).select_from(LegalAcceptance)) == 1
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.pii_anonymized_at.is_not(None)))
        assert outbox.payload["recipient"] == "anonymized@invalid.example"
        assert db.get(SupportTicket, ticket_id).subject == ANONYMIZED_TEXT
        assert db.get(SupportMessage, message_id).body == ANONYMIZED_TEXT
        assert db.get(SupportTicketEvent, support_event_id).reason is None
        stored_notification = db.get(Notification, notification_id)
        assert stored_notification.title == stored_notification.body == ANONYMIZED_TEXT
        assert stored_notification.action_path is None
        assert db.scalar(
            select(func.count()).select_from(SecurityEvent).where(
                SecurityEvent.action == "account.pii_anonymized"
            )
        ) == 1
