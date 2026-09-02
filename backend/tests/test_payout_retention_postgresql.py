import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.models import SecurityEvent
from app.audit.service import AuditAction, AuditContext
from app.auth.models import AccountStatus, Role, User
from app.payouts.models import (
    ANONYMIZED_TEXT,
    PayoutDetails,
    PayoutEvent,
    PayoutEventAction,
    PayoutEventDetail,
    PayoutRequest,
    PayoutStatus,
    RecipientType,
)
from app.payouts.retention import anonymize_eligible_payout_pii


@pytest.fixture()
def postgres_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


def test_postgresql_allows_only_controlled_payout_pii_anonymization(
    postgres_engine,
):
    blogger_id = uuid.uuid4()
    payout_id = uuid.uuid4()
    event_id = uuid.uuid4()
    old = datetime(2019, 1, 1, tzinfo=timezone.utc)
    anonymized_at = datetime(2026, 8, 23, tzinfo=timezone.utc)

    with Session(postgres_engine) as db, db.begin():
        db.add(
            User(
                id=blogger_id,
                email=f"retention-{blogger_id}@example.com",
                password_hash="not-used",
                role=Role.BLOGGER,
                status=AccountStatus.DELETED,
                status_changed_at=old,
                email_verified_at=old,
            )
        )
        db.flush()
        db.add(
            PayoutDetails(
                blogger_id=blogger_id,
                sbp_phone="+79991234567",
                bank_name="Private Bank",
            )
        )
        db.add(
            PayoutRequest(
                id=payout_id,
                request_number=f"PAY-20190101-{payout_id.hex[:19].upper()}",
                blogger_id=blogger_id,
                amount_kopecks=12_300,
                status=PayoutStatus.REJECTED,
                recipient_full_name="Private Name",
                recipient_display_name="Private Channel",
                recipient_type=RecipientType.INDIVIDUAL,
                sbp_phone="+79991234567",
                bank_name="Private Bank",
                requested_at=old,
                rejected_at=old,
                rejected_by_user_id=blogger_id,
                rejection_reason="Private rejection reason",
                manager_comment="Private comment",
            )
        )
        db.flush()
        event = PayoutEvent(
            id=event_id,
            payout_request_id=payout_id,
            sequence_number=1,
            action=PayoutEventAction.REJECTED,
            from_status=PayoutStatus.REQUESTED,
            to_status=PayoutStatus.REJECTED,
            actor_user_id=blogger_id,
            idempotency_key=uuid.uuid4(),
            payload_hash="a" * 64,
            event_metadata={},
            created_at=old,
        )
        event.detail = PayoutEventDetail(
            comment="Private comment",
            rejection_reason="Private rejection reason",
        )
        db.add(event)

    with Session(postgres_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                update(PayoutRequest)
                .where(PayoutRequest.id == payout_id)
                .values(recipient_full_name="Rewritten")
            )
            db.commit()
        db.rollback()

    with Session(postgres_engine) as db, db.begin():
        result = anonymize_eligible_payout_pii(
            db,
            cutoff=date(2021, 1, 1),
            anonymized_at=anonymized_at,
            audit_context=AuditContext(
                request_id=f"retention-pg-{payout_id}",
                ip_address="127.0.0.1",
                user_agent="pytest",
            ),
        )
        assert result.bloggers == 1
        assert result.payout_requests == 1
        assert result.payout_details == 1
        assert result.event_details == 1

    with Session(postgres_engine) as db:
        payout = db.get(PayoutRequest, payout_id)
        event_detail = db.get(PayoutEventDetail, event_id)
        assert payout.recipient_full_name == ANONYMIZED_TEXT
        assert payout.rejection_reason == ANONYMIZED_TEXT
        assert payout.payment_reference is None
        assert payout.amount_kopecks == 12_300
        assert event_detail.comment == ANONYMIZED_TEXT
        assert event_detail.rejection_reason == ANONYMIZED_TEXT
        assert db.scalar(
            select(SecurityEvent.id).where(
                SecurityEvent.action == AuditAction.PAYOUT_PII_ANONYMIZED,
                SecurityEvent.object_id == blogger_id,
            )
        )

        with pytest.raises(DBAPIError):
            db.execute(
                update(PayoutEventDetail)
                .where(PayoutEventDetail.event_id == event_id)
                .values(comment="Restored private comment")
            )
            db.commit()
        db.rollback()
