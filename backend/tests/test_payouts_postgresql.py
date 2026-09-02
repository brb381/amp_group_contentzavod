import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import app.payouts.service as payout_service
from app.audit.service import AuditContext
from app.auth.models import AccountStatus, Role, User
from app.billing.models import (
    AccrualCorrection,
    BalanceLedgerEntry,
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.billing.schemas import AccrualCorrectionRequest
from app.billing.service import correct_confirmed_accrual
from app.billing.wallet import lock_creator_balance
from app.catalog.models import Brand
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
    RecipientStatus,
    SocialAccount,
    SocialAccountStatus,
)
from app.database.base import Base
from app.errors import APIError
from app.payouts.models import (
    PayoutDetails,
    PayoutEvent,
    PayoutRequest,
)
from app.payouts.policy import moscow_today
from app.payouts.schemas import (
    PayoutApprovalRequest,
    PayoutCreateRequest,
    PayoutPaymentRequest,
    PayoutRejectionRequest,
    PayoutReviewRequest,
)
from app.payouts.service import (
    approve_payout_request,
    create_my_payout_request,
    record_payout_payment,
    reject_payout_request,
    review_payout_request,
)
from app.platforms import Platform


def _enum_value(value):
    return getattr(value, "value", value)


def _audit(label: str) -> AuditContext:
    return AuditContext(
        request_id=f"postgres-payout-{label}",
        ip_address="127.0.0.1",
        user_agent="pytest-postgresql",
    )


@pytest.fixture()
def postgres_payout_session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")

    schema = f"test_payout_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, poolclass=NullPool)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
        )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def _user(db, *, role: Role, label: str) -> User:
    user = User(
        email=f"postgres-{label}-{uuid.uuid4()}@example.com",
        password_hash="not-used-by-postgresql-payout-tests",
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


def _creator_with_balance(db, *, amount_kopecks: int) -> User:
    creator = _user(db, role=Role.BLOGGER, label="creator")
    db.add_all(
        [
            CreatorProfile(
                user_id=creator.id,
                full_name="PostgreSQL Creator",
                display_name="PostgreSQL Channel",
                recipient_status=RecipientStatus.INDIVIDUAL,
                status=ProfileStatus.APPROVED,
            ),
            CreatorBalance(
                blogger_id=creator.id,
                available_kopecks=amount_kopecks,
                reserved_kopecks=0,
                paid_kopecks=0,
            ),
            PayoutDetails(
                blogger_id=creator.id,
                sbp_phone="+79991234567",
                bank_name="PostgreSQL Test Bank",
            ),
        ]
    )
    db.flush()
    return creator


def _publication(db, blogger: User, label: str) -> Publication:
    identity = f"{label}-{uuid.uuid4()}"
    account = SocialAccount(
        user_id=blogger.id,
        platform=Platform.YOUTUBE,
        url=f"https://youtube.com/@{identity}",
        status=SocialAccountStatus.APPROVED,
    )
    card = VideoCard(
        blogger_id=blogger.id,
        title=f"Card {label}",
        reported_brand=Brand.AMP,
        reported_product_name=f"Product {label}",
    )
    db.add_all([account, card])
    db.flush()
    publication = Publication(
        video_card_id=card.id,
        social_account_id=account.id,
        platform=Platform.YOUTUBE,
        submitted_url=f"https://youtu.be/{identity}",
        normalized_url=f"https://youtube.com/shorts/{identity}",
        external_id=identity,
        status=PublicationStatus.APPROVED,
        parse_status=PublicationParseStatus.PARSED,
        availability=PublicationAvailability.AVAILABLE,
        enrichment_status=PublicationEnrichmentStatus.SUCCEEDED,
    )
    db.add(publication)
    db.flush()
    return publication


def _confirmed_accrual(db, *, creator: User, amount_kopecks: int):
    publication = _publication(db, creator, "correction")
    rate = RateVersion(
        rate_kopecks_per_view=5,
        effective_from_period=date(2026, 8, 1),
        reason="PostgreSQL race test",
    )
    db.add(rate)
    db.flush()
    period = CalculationPeriod(
        period=date(2026, 8, 1),
        status=CalculationPeriodStatus.CONFIRMED,
        rate_version_id=rate.id,
        input_revision=0,
        calculated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        confirmed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        total_views=amount_kopecks // 5,
        total_amount_kopecks=amount_kopecks,
    )
    db.add(period)
    db.flush()
    accrual = PublicationAccrual(
        period_id=period.id,
        publication_id=publication.id,
        blogger_id=creator.id,
        previous_value=0,
        current_value=amount_kopecks // 5,
        eligible_views=amount_kopecks // 5,
        rate_kopecks_per_view=5,
        amount_kopecks=amount_kopecks,
        risk_flags=[],
    )
    db.add_all(
        [
            accrual,
            CreatorPeriodTotal(
                period_id=period.id,
                blogger_id=creator.id,
                eligible_views=amount_kopecks // 5,
                amount_kopecks=amount_kopecks,
                publication_count=1,
                risk_count=0,
            ),
        ]
    )
    db.flush()
    return accrual


def _approved_payout(db, *, amount_kopecks: int):
    creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
    manager = _user(db, role=Role.MANAGER, label="manager")
    finance = _user(db, role=Role.FINANCE, label="finance")
    receipt = create_my_payout_request(
        db,
        actor=creator,
        payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
        audit_context=_audit("approved-seed-create"),
    )
    review_payout_request(
        db,
        actor=manager,
        payout_request_id=receipt.payout_request_id,
        payload=PayoutReviewRequest(idempotency_key=uuid.uuid4()),
        audit_context=_audit("approved-seed-review"),
    )
    approve_payout_request(
        db,
        actor=manager,
        payout_request_id=receipt.payout_request_id,
        payload=PayoutApprovalRequest(
            idempotency_key=uuid.uuid4(),
            requisites_verified=True,
        ),
        audit_context=_audit("approved-seed-approval"),
    )
    db.flush()
    return creator, manager, finance, receipt.payout_request_id


def test_postgresql_concurrent_create_reserves_balance_exactly_once(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 135_700
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
        db.commit()
        creator_id = creator.id

    barrier = threading.Barrier(2)

    def create_request(command_number: int):
        with session_factory() as db:
            actor = db.get(User, creator_id)
            barrier.wait(timeout=10)
            try:
                receipt = create_my_payout_request(
                    db,
                    actor=actor,
                    payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
                    audit_context=_audit(f"create-{command_number}"),
                )
                payout_id = receipt.payout_request_id
                db.commit()
                return "success", payout_id
            except APIError as error:
                db.rollback()
                return "api_error", error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_request, index) for index in range(2)]
        results = [future.result(timeout=30) for future in futures]

    assert [kind for kind, _ in results].count("success") == 1
    assert results.count(("api_error", "PAYOUT_ALREADY_ACTIVE")) == 1

    with session_factory() as db:
        balance = db.get(CreatorBalance, creator_id)
        assert (
            balance.available_kopecks,
            balance.reserved_kopecks,
            balance.paid_kopecks,
        ) == (0, amount_kopecks, 0)
        assert db.scalar(select(func.count()).select_from(PayoutRequest)) == 1
        assert db.scalar(select(func.count()).select_from(PayoutEvent)) == 1
        ledger = list(db.scalars(select(BalanceLedgerEntry)))
        assert len(ledger) == 1
        assert _enum_value(ledger[0].operation_type) == "payout_reserved"


@pytest.mark.parametrize("permission_change", ["role", "status"])
def test_postgresql_command_refreshes_stale_actor_permissions(
    postgres_payout_session_factory,
    permission_change,
):
    session_factory = postgres_payout_session_factory
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=51_000)
        manager = _user(db, role=Role.MANAGER, label="stale-manager")
        receipt = create_my_payout_request(
            db,
            actor=creator,
            payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("stale-create"),
        )
        db.commit()
        payout_id = receipt.payout_request_id
        manager_id = manager.id

    with session_factory() as stale_db:
        stale_actor = stale_db.get(User, manager_id)
        assert stale_actor.role == Role.MANAGER
        assert stale_actor.status == AccountStatus.ACTIVE

        with session_factory() as permission_db:
            current = permission_db.get(User, manager_id)
            if permission_change == "role":
                current.role = Role.BLOGGER
            else:
                current.status = AccountStatus.SUSPENDED
            permission_db.commit()

        with pytest.raises(APIError) as raised:
            review_payout_request(
                stale_db,
                actor=stale_actor,
                payout_request_id=payout_id,
                payload=PayoutReviewRequest(idempotency_key=uuid.uuid4()),
                audit_context=_audit(f"stale-{permission_change}"),
            )
        stale_db.rollback()
        assert raised.value.status_code == 403
        assert raised.value.code == "PAYOUT_REVIEW_PERMISSION_CHANGED"

    with session_factory() as db:
        payout = db.get(PayoutRequest, payout_id)
        assert _enum_value(payout.status) == "requested"
        assert db.scalar(
            select(func.count()).select_from(PayoutEvent).where(
                PayoutEvent.payout_request_id == payout_id
            )
        ) == 1


def test_postgresql_actor_row_lock_uses_bounded_timeout(
    postgres_payout_session_factory,
    monkeypatch,
):
    session_factory = postgres_payout_session_factory
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=42_000)
        manager = _user(db, role=Role.MANAGER, label="locked-manager")
        receipt = create_my_payout_request(
            db,
            actor=creator,
            payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("locked-create"),
        )
        db.commit()
        payout_id = receipt.payout_request_id
        manager_id = manager.id

    monkeypatch.setattr(payout_service, "PAYOUT_LOCK_TIMEOUT_MS", 200)
    monkeypatch.setattr(payout_service, "PAYOUT_STATEMENT_TIMEOUT_MS", 2_000)
    holder = session_factory()
    contender = session_factory()
    try:
        held_actor = holder.scalar(
            select(User).where(User.id == manager_id).with_for_update()
        )
        assert held_actor is not None
        stale_actor = contender.get(User, manager_id)

        started = time.monotonic()
        with pytest.raises(OperationalError) as raised:
            review_payout_request(
                contender,
                actor=stale_actor,
                payout_request_id=payout_id,
                payload=PayoutReviewRequest(idempotency_key=uuid.uuid4()),
                audit_context=_audit("locked-review"),
            )
        elapsed = time.monotonic() - started
        contender.rollback()

        sqlstate = getattr(raised.value.orig, "sqlstate", None) or getattr(
            raised.value.orig,
            "pgcode",
            None,
        )
        assert sqlstate == "55P03"
        assert elapsed < 3
    finally:
        contender.close()
        holder.rollback()
        holder.close()

    with session_factory() as db:
        assert _enum_value(db.get(PayoutRequest, payout_id).status) == "requested"


def test_postgresql_payment_and_rejection_have_one_terminal_winner(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 84_200
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
        manager = _user(db, role=Role.MANAGER, label="manager")
        finance = _user(db, role=Role.FINANCE, label="finance")
        admin = _user(db, role=Role.ADMIN, label="admin")
        db.flush()
        receipt = create_my_payout_request(
            db,
            actor=creator,
            payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("seed-create"),
        )
        review_payout_request(
            db,
            actor=manager,
            payout_request_id=receipt.payout_request_id,
            payload=PayoutReviewRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("seed-review"),
        )
        approve_payout_request(
            db,
            actor=manager,
            payout_request_id=receipt.payout_request_id,
            payload=PayoutApprovalRequest(
                idempotency_key=uuid.uuid4(),
                requisites_verified=True,
            ),
            audit_context=_audit("seed-approval"),
        )
        db.commit()
        payout_id = receipt.payout_request_id
        creator_id = creator.id
        finance_id = finance.id
        admin_id = admin.id

    barrier = threading.Barrier(2)

    def pay():
        with session_factory() as db:
            actor = db.get(User, finance_id)
            barrier.wait(timeout=10)
            try:
                record_payout_payment(
                    db,
                    actor=actor,
                    payout_request_id=payout_id,
                    payload=PayoutPaymentRequest(
                        idempotency_key=uuid.uuid4(),
                        paid_on=moscow_today(),
                        payment_reference="postgres-concurrent-payment",
                    ),
                    audit_context=_audit("concurrent-payment"),
                )
                db.commit()
                return "success", "paid"
            except APIError as error:
                db.rollback()
                return "api_error", error.code

    def reject():
        with session_factory() as db:
            actor = db.get(User, admin_id)
            barrier.wait(timeout=10)
            try:
                reject_payout_request(
                    db,
                    actor=actor,
                    payout_request_id=payout_id,
                    payload=PayoutRejectionRequest(
                        idempotency_key=uuid.uuid4(),
                        reason="Concurrent bank cancellation",
                    ),
                    audit_context=_audit("concurrent-rejection"),
                )
                db.commit()
                return "success", "rejected"
            except APIError as error:
                db.rollback()
                return "api_error", error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(pay), executor.submit(reject)]
        results = [future.result(timeout=30) for future in futures]

    assert [kind for kind, _ in results].count("success") == 1
    assert results.count(("api_error", "PAYOUT_STATE_CONFLICT")) == 1

    with session_factory() as db:
        payout = db.get(PayoutRequest, payout_id)
        balance = db.get(CreatorBalance, creator_id)
        terminal_operation = {
            _enum_value(entry.operation_type)
            for entry in db.scalars(select(BalanceLedgerEntry))
            if _enum_value(entry.operation_type) in {"payout_paid", "payout_released"}
        }
        if _enum_value(payout.status) == "paid":
            assert terminal_operation == {"payout_paid"}
            assert (
                balance.available_kopecks,
                balance.reserved_kopecks,
                balance.paid_kopecks,
            ) == (0, 0, amount_kopecks)
        else:
            assert _enum_value(payout.status) == "rejected"
            assert terminal_operation == {"payout_released"}
            assert (
                balance.available_kopecks,
                balance.reserved_kopecks,
                balance.paid_kopecks,
            ) == (amount_kopecks, 0, 0)
        terminal_events = list(
            db.scalars(
                select(PayoutEvent).where(
                    PayoutEvent.payout_request_id == payout_id,
                    PayoutEvent.action.in_(("paid", "rejected")),
                )
            )
        )
        assert len(terminal_events) == 1


def test_postgresql_negative_correction_serializes_before_create(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 25_000
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
        db.commit()
        creator_id = creator.id

    barrier = threading.Barrier(2)

    def correct_balance():
        with session_factory() as db:
            balance = lock_creator_balance(db, blogger_id=creator_id)
            barrier.wait(timeout=10)
            balance.available_kopecks -= amount_kopecks + 1
            db.add(
                BalanceLedgerEntry(
                    blogger_id=creator_id,
                    operation_type="period_correction",
                    available_delta_kopecks=-(amount_kopecks + 1),
                    reserved_delta_kopecks=0,
                    paid_delta_kopecks=0,
                    reference_type="postgres_concurrent_correction",
                    reference_id=uuid.uuid4(),
                    idempotency_key=f"postgres-correction:{uuid.uuid4()}",
                )
            )
            db.commit()
            return "corrected"

    def create_request():
        with session_factory() as db:
            actor = db.get(User, creator_id)
            barrier.wait(timeout=10)
            try:
                create_my_payout_request(
                    db,
                    actor=actor,
                    payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
                    audit_context=_audit("correction-create"),
                )
                db.commit()
                return "success"
            except APIError as error:
                db.rollback()
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        correction_future = executor.submit(correct_balance)
        create_future = executor.submit(create_request)
        assert correction_future.result(timeout=30) == "corrected"
        assert create_future.result(timeout=30) == "NO_AVAILABLE_BALANCE"

    with session_factory() as db:
        balance = db.get(CreatorBalance, creator_id)
        assert (
            balance.available_kopecks,
            balance.reserved_kopecks,
            balance.paid_kopecks,
        ) == (-1, 0, 0)
        assert db.scalar(select(func.count()).select_from(PayoutRequest)) == 0
        assert db.scalar(select(func.count()).select_from(PayoutEvent)) == 0
        operations = {
            _enum_value(entry.operation_type)
            for entry in db.scalars(select(BalanceLedgerEntry))
        }
        assert operations == {"period_correction"}


def test_postgresql_negative_correction_serializes_before_payment(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 58_000
    with session_factory() as db:
        creator, _, finance, payout_id = _approved_payout(
            db,
            amount_kopecks=amount_kopecks,
        )
        db.commit()
        creator_id = creator.id
        finance_id = finance.id

    barrier = threading.Barrier(2)

    def correct_balance():
        with session_factory() as db:
            balance = lock_creator_balance(db, blogger_id=creator_id)
            barrier.wait(timeout=10)
            balance.available_kopecks -= 1
            db.add(
                BalanceLedgerEntry(
                    blogger_id=creator_id,
                    operation_type="period_correction",
                    available_delta_kopecks=-1,
                    reserved_delta_kopecks=0,
                    paid_delta_kopecks=0,
                    reference_type="postgres_concurrent_correction",
                    reference_id=uuid.uuid4(),
                    idempotency_key=f"postgres-correction:{uuid.uuid4()}",
                )
            )
            db.commit()
            return "corrected"

    def pay_request():
        with session_factory() as db:
            actor = db.get(User, finance_id)
            barrier.wait(timeout=10)
            try:
                record_payout_payment(
                    db,
                    actor=actor,
                    payout_request_id=payout_id,
                    payload=PayoutPaymentRequest(
                        idempotency_key=uuid.uuid4(),
                        paid_on=moscow_today(),
                        payment_reference="must-not-settle-after-correction",
                    ),
                    audit_context=_audit("correction-payment"),
                )
                db.commit()
                return "success"
            except APIError as error:
                db.rollback()
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        correction_future = executor.submit(correct_balance)
        payment_future = executor.submit(pay_request)
        assert correction_future.result(timeout=30) == "corrected"
        assert payment_future.result(timeout=30) == "PAYOUT_BLOCKED_BY_NEGATIVE_BALANCE"

    with session_factory() as db:
        payout = db.get(PayoutRequest, payout_id)
        balance = db.get(CreatorBalance, creator_id)
        assert _enum_value(payout.status) == "approved"
        assert payout.paid_on is None
        assert (
            balance.available_kopecks,
            balance.reserved_kopecks,
            balance.paid_kopecks,
        ) == (-1, amount_kopecks, 0)
        operations = {
            _enum_value(entry.operation_type)
            for entry in db.scalars(select(BalanceLedgerEntry))
        }
        assert operations == {"payout_reserved", "period_correction"}
        assert db.scalar(
            select(func.count()).select_from(PayoutEvent).where(
                PayoutEvent.payout_request_id == payout_id
            )
        ) == 3


def test_postgresql_real_accrual_correction_and_payout_create_do_not_deadlock(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 25_000
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
        admin = _user(db, role=Role.ADMIN, label="correction-admin")
        accrual = _confirmed_accrual(
            db,
            creator=creator,
            amount_kopecks=amount_kopecks,
        )
        db.commit()
        creator_id = creator.id
        admin_id = admin.id
        accrual_id = accrual.id

    barrier = threading.Barrier(2)

    def correct():
        with session_factory() as db:
            actor = db.get(User, admin_id)
            barrier.wait(timeout=10)
            correct_confirmed_accrual(
                db,
                actor=actor,
                accrual_id=accrual_id,
                payload=AccrualCorrectionRequest(
                    idempotency_key=uuid.uuid4(),
                    corrected_current_value=0,
                    reason="PostgreSQL create race correction",
                ),
                audit_context=_audit("real-correction-create"),
            )
            db.commit()
            return "corrected"

    def create():
        with session_factory() as db:
            actor = db.get(User, creator_id)
            barrier.wait(timeout=10)
            try:
                receipt = create_my_payout_request(
                    db,
                    actor=actor,
                    payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
                    audit_context=_audit("real-correction-create-payout"),
                )
                db.commit()
                return "created", receipt.payout_request_id
            except APIError as error:
                db.rollback()
                return error.code, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        correction_future = executor.submit(correct)
        create_future = executor.submit(create)
        assert correction_future.result(timeout=30) == "corrected"
        create_result, _ = create_future.result(timeout=30)

    assert create_result in {"created", "NO_AVAILABLE_BALANCE"}
    with session_factory() as db:
        balance = db.get(CreatorBalance, creator_id)
        assert db.scalar(select(func.count()).select_from(AccrualCorrection)) == 1
        if create_result == "created":
            assert (
                balance.available_kopecks,
                balance.reserved_kopecks,
                balance.paid_kopecks,
            ) == (-amount_kopecks, amount_kopecks, 0)
            assert db.scalar(select(func.count()).select_from(PayoutRequest)) == 1
        else:
            assert (
                balance.available_kopecks,
                balance.reserved_kopecks,
                balance.paid_kopecks,
            ) == (0, 0, 0)
            assert db.scalar(select(func.count()).select_from(PayoutRequest)) == 0


def test_postgresql_real_accrual_correction_and_payment_do_not_deadlock(
    postgres_payout_session_factory,
):
    session_factory = postgres_payout_session_factory
    amount_kopecks = 58_000
    with session_factory() as db:
        creator = _creator_with_balance(db, amount_kopecks=amount_kopecks)
        admin = _user(db, role=Role.ADMIN, label="payment-correction-admin")
        manager = _user(db, role=Role.MANAGER, label="payment-correction-manager")
        finance = _user(db, role=Role.FINANCE, label="payment-correction-finance")
        accrual = _confirmed_accrual(
            db,
            creator=creator,
            amount_kopecks=amount_kopecks,
        )
        receipt = create_my_payout_request(
            db,
            actor=creator,
            payload=PayoutCreateRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("real-correction-payment-create"),
        )
        review_payout_request(
            db,
            actor=manager,
            payout_request_id=receipt.payout_request_id,
            payload=PayoutReviewRequest(idempotency_key=uuid.uuid4()),
            audit_context=_audit("real-correction-payment-review"),
        )
        approve_payout_request(
            db,
            actor=manager,
            payout_request_id=receipt.payout_request_id,
            payload=PayoutApprovalRequest(
                idempotency_key=uuid.uuid4(),
                requisites_verified=True,
            ),
            audit_context=_audit("real-correction-payment-approve"),
        )
        db.commit()
        creator_id = creator.id
        admin_id = admin.id
        finance_id = finance.id
        accrual_id = accrual.id
        payout_id = receipt.payout_request_id

    barrier = threading.Barrier(2)

    def correct():
        with session_factory() as db:
            actor = db.get(User, admin_id)
            barrier.wait(timeout=10)
            correct_confirmed_accrual(
                db,
                actor=actor,
                accrual_id=accrual_id,
                payload=AccrualCorrectionRequest(
                    idempotency_key=uuid.uuid4(),
                    corrected_current_value=(amount_kopecks // 5) - 1,
                    reason="PostgreSQL payment race correction",
                ),
                audit_context=_audit("real-correction-payment"),
            )
            db.commit()
            return "corrected"

    def pay():
        with session_factory() as db:
            actor = db.get(User, finance_id)
            barrier.wait(timeout=10)
            try:
                record_payout_payment(
                    db,
                    actor=actor,
                    payout_request_id=payout_id,
                    payload=PayoutPaymentRequest(
                        idempotency_key=uuid.uuid4(),
                        paid_on=moscow_today(),
                        payment_reference="real-correction-payment-race",
                    ),
                    audit_context=_audit("real-correction-payment-pay"),
                )
                db.commit()
                return "paid"
            except APIError as error:
                db.rollback()
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        correction_future = executor.submit(correct)
        payment_future = executor.submit(pay)
        assert correction_future.result(timeout=30) == "corrected"
        payment_result = payment_future.result(timeout=30)

    assert payment_result in {"paid", "PAYOUT_BLOCKED_BY_NEGATIVE_BALANCE"}
    with session_factory() as db:
        balance = db.get(CreatorBalance, creator_id)
        payout = db.get(PayoutRequest, payout_id)
        assert balance.available_kopecks == -5
        assert db.scalar(select(func.count()).select_from(AccrualCorrection)) == 1
        if payment_result == "paid":
            assert _enum_value(payout.status) == "paid"
            assert (balance.reserved_kopecks, balance.paid_kopecks) == (
                0,
                amount_kopecks,
            )
        else:
            assert _enum_value(payout.status) == "approved"
            assert (balance.reserved_kopecks, balance.paid_kopecks) == (
                amount_kopecks,
                0,
            )
