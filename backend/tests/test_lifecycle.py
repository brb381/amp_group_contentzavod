import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.audit.models import SecurityEvent
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.billing.models import CreatorBalance
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationParseStatus,
    PublicationStatus,
    VideoCard,
)
from app.contracts import LifecycleCommand
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    ProfileStatus,
    SocialAccount,
    SocialAccountStatus,
)
from app.lifecycle.models import (
    ActivityKind,
    CreatorLifecycle,
    LifecycleAction,
    LifecycleJob,
    LifecycleJobState,
)
from app.lifecycle.policy import block_due_at, suspension_due_at
from app.lifecycle.processor import execute_lifecycle_job
from app.notifications.models import Notification
from app.platforms import Platform
from app.scheduling.lifecycle import dispatch_lifecycle
from app.support.models import SupportTicketEvent


PASSWORD = "lifecycle-test-password-123"


class CapturingProducer:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def send_task(self, name, *, args, queue):
        if self.fail:
            raise RuntimeError("broker unavailable")
        self.calls.append((name, args, queue))


def _csrf(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def _login(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _seed_user(client, role: Role, *, status: AccountStatus = AccountStatus.ACTIVE):
    email = f"lifecycle-{role.value}-{uuid.uuid4()}@example.com"
    with client.app.state.test_session.begin() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            status=status,
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add(user)
        db.flush()
        return user.id, email


def _seed_blogger_domain(client, *, status=AccountStatus.ACTIVE, last_activity_at=None):
    blogger_id, email = _seed_user(client, Role.BLOGGER, status=status)
    last_activity_at = last_activity_at or datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    with client.app.state.test_session.begin() as db:
        profile = CreatorProfile(
            user_id=blogger_id,
            full_name="Lifecycle Creator",
            display_name="lifecycle",
            status=(
                ProfileStatus.APPROVED
                if status == AccountStatus.ACTIVE
                else ProfileStatus.SUSPENDED
            ),
        )
        account = SocialAccount(
            user_id=blogger_id,
            platform=Platform.YOUTUBE,
            url=f"https://youtube.com/@{blogger_id}",
            status=SocialAccountStatus.APPROVED,
        )
        card = VideoCard(
            blogger_id=blogger_id,
            title="Lifecycle card",
            reported_brand="AMP",
            reported_product_name="Test product",
        )
        db.add_all([profile, account, card])
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
            last_activity_at=last_activity_at,
            last_activity_kind=ActivityKind.LOGIN,
            activity_revision=1,
            suspended_at=(last_activity_at if status == AccountStatus.SUSPENDED else None),
        )
        balance = CreatorBalance(blogger_id=blogger_id, available_kopecks=1_000)
        db.add_all([publication, lifecycle, balance])
        db.flush()
        return blogger_id, email, profile.id, publication.id


def _queued_job(client, blogger_id, action, due_at, revision=1):
    with client.app.state.test_session.begin() as db:
        job = LifecycleJob(
            blogger_id=blogger_id,
            action=action,
            basis_revision=revision,
            due_at=due_at,
            available_at=due_at,
            state=LifecycleJobState.QUEUED,
            attempt_count=1,
            dispatch_id=uuid.uuid4(),
        )
        db.add(job)
        db.flush()
        return job.id, job.dispatch_id


def test_lifecycle_uses_calendar_months_at_month_end():
    january = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    suspended = suspension_due_at(january)
    assert suspended == datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    assert block_due_at(suspended) == datetime(2027, 1, 31, 12, tzinfo=timezone.utc)


def test_scheduler_dispatches_warning_once_and_releases_broker_failure(client):
    blogger_id, _, _, _ = _seed_blogger_domain(client)
    now = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    producer = CapturingProducer()
    assert dispatch_lifecycle(
        now=now,
        session_factory=client.app.state.test_session,
        task_producer=producer,
    )
    assert len(producer.calls) == 1
    assert producer.calls[0][0] == "lifecycle.execute"
    execute_lifecycle_job(
        LifecycleCommand.model_validate(producer.calls[0][1][0]),
        client.app.state.test_session,
        now=now,
    )
    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 1

    failing = CapturingProducer(fail=True)
    with client.app.state.test_session.begin() as db:
        queued = db.scalar(select(LifecycleJob).where(LifecycleJob.blogger_id == blogger_id))
        assert queued.state == LifecycleJobState.SUCCEEDED
        next_job = LifecycleJob(
            blogger_id=blogger_id,
            action=LifecycleAction.WARN_SUSPENSION_7,
            basis_revision=1,
            due_at=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            available_at=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        )
        db.add(next_job)
    assert not dispatch_lifecycle(
        now=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        session_factory=client.app.state.test_session,
        task_producer=failing,
    )
    with client.app.state.test_session() as db:
        job = db.scalar(
            select(LifecycleJob).where(LifecycleJob.action == LifecycleAction.WARN_SUSPENSION_7)
        )
        assert job.state == LifecycleJobState.RETRY_WAIT
        assert job.dispatch_id is None
        assert job.attempt_count == 0


def test_worker_suspends_atomically_and_then_blocks_account(client):
    due = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    blogger_id, _, profile_id, publication_id = _seed_blogger_domain(client)
    job_id, dispatch_id = _queued_job(client, blogger_id, LifecycleAction.SUSPEND, due)
    execute_lifecycle_job(
        LifecycleCommand(job_id=job_id, dispatch_id=dispatch_id),
        client.app.state.test_session,
        now=due,
    )
    with client.app.state.test_session() as db:
        user = db.get(User, blogger_id)
        lifecycle = db.get(CreatorLifecycle, blogger_id)
        assert user.status == AccountStatus.SUSPENDED
        assert db.get(CreatorProfile, profile_id).status == ProfileStatus.SUSPENDED
        assert db.get(Publication, publication_id).status == PublicationStatus.RE_REVIEW_REQUIRED
        assert lifecycle.activity_revision == 2
        assert db.scalar(select(func.count()).select_from(ProfileHistory)) == 1
        assert db.scalar(select(func.count()).select_from(PublicationHistory)) == 1
        assert db.scalar(select(func.count()).select_from(Notification)) == 1
        assert db.scalar(select(func.count()).select_from(SecurityEvent)) == 1

    block_at = datetime(2027, 1, 31, 12, tzinfo=timezone.utc)
    block_job_id, block_dispatch_id = _queued_job(
        client, blogger_id, LifecycleAction.BLOCK, block_at, revision=2
    )
    execute_lifecycle_job(
        LifecycleCommand(job_id=block_job_id, dispatch_id=block_dispatch_id),
        client.app.state.test_session,
        now=block_at,
    )
    with client.app.state.test_session() as db:
        user = db.get(User, blogger_id)
        lifecycle = db.get(CreatorLifecycle, blogger_id)
        assert user.status == AccountStatus.BLOCKED
        assert lifecycle.blocked_at is not None
        assert lifecycle.balance_claim_expired_at is not None
        assert db.get(CreatorBalance, blogger_id).claim_expired_at is not None
        assert db.get(CreatorProfile, profile_id).status == ProfileStatus.BLOCKED


def test_recovery_decision_restores_account_but_not_publication(client):
    suspended_at = datetime(2026, 2, 1, 12, tzinfo=timezone.utc)
    blogger_id, blogger_email, _, publication_id = _seed_blogger_domain(
        client, status=AccountStatus.SUSPENDED, last_activity_at=suspended_at
    )
    with client.app.state.test_session.begin() as db:
        db.get(Publication, publication_id).status = PublicationStatus.RE_REVIEW_REQUIRED
    _, moderator_email = _seed_user(client, Role.MODERATOR)

    _login(client, blogger_email)
    created = client.post(
        "/api/v1/me/support-tickets",
        headers=_csrf(client),
        json={
            "idempotency_key": str(uuid.uuid4()),
            "category": "account_recovery",
            "subject": "Restore my account",
            "body": "I am ready to publish again.",
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]

    _login(client, moderator_email)
    decision_key = uuid.uuid4()
    decision = client.post(
        f"/api/v1/staff/support-tickets/{ticket_id}/recovery-decisions",
        headers=_csrf(client),
        json={
            "idempotency_key": str(decision_key),
            "decision": "approve",
            "reason": "Identity and account ownership verified",
        },
    )
    assert decision.status_code == 201, decision.text
    replay = client.post(
        f"/api/v1/staff/support-tickets/{ticket_id}/recovery-decisions",
        headers=_csrf(client),
        json={
            "idempotency_key": str(decision_key),
            "decision": "approve",
            "reason": "Identity and account ownership verified",
        },
    )
    assert replay.status_code == 201

    lifecycle_view = client.get(
        f"/api/v1/staff/users/{blogger_id}/account-lifecycle"
    )
    assert lifecycle_view.status_code == 200
    assert lifecycle_view.json()["account_status"] == "active"
    assert lifecycle_view.json()["next_transition"] == "suspension"
    with client.app.state.test_session() as db:
        assert db.get(User, blogger_id).status == AccountStatus.ACTIVE
        assert db.get(Publication, publication_id).status == PublicationStatus.RE_REVIEW_REQUIRED
        assert db.scalar(select(func.count()).select_from(SupportTicketEvent)) == 2
        assert db.scalar(
            select(func.count()).select_from(SupportTicketEvent).where(
                SupportTicketEvent.recovery_decision == "approve"
            )
        ) == 1
