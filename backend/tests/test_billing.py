import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit.service import AuditContext
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.billing.calculation import execute_calculation
from app.billing.models import (
    AccrualCorrection,
    BalanceLedgerEntry,
    CalculationJob,
    CalculationJobState,
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.billing.schemas import AccrualCorrectionRequest, RateCreateRequest
from app.billing.service import (
    confirm_period,
    correct_confirmed_accrual,
    create_rate,
    get_my_balance,
    request_recalculation,
)
from app.catalog.models import Brand
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationParseStatus,
    PublicationStatus,
    VideoCard,
)
from app.contracts import CALCULATION_TASK, CALCULATIONS_QUEUE, CalculationCommand
from app.creators.models import SocialAccount, SocialAccountStatus
from app.database.base import Base
from app.errors import APIError
from app.notifications.catalog import DEFAULT_NOTIFICATION_TEMPLATES
from app.notifications.models import NotificationChannel, NotificationTemplateVersion
from app.platforms import Platform
from app.readings.models import (
    ReadingDatasetRevision,
    ReadingSource,
    ReadingStatus,
    ViewReading,
)
from app.readings.schemas import ReadingCorrectionRequest
from app.readings.service import correct_accepted_reading
from app.scheduling.calculations import dispatch_calculation


PERIOD = date(2026, 8, 1)
AUDIT_CONTEXT = AuditContext(
    request_id="billing-test",
    ip_address="127.0.0.1",
    user_agent="pytest",
)
PASSWORD = "billing-test-password-123"


@pytest.fixture()
def billing_session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    definition = DEFAULT_NOTIFICATION_TEMPLATES["calculation_confirmed"]
    with factory.begin() as db:
        db.add_all(
            [
                NotificationTemplateVersion(
                    code="calculation_confirmed",
                    channel=NotificationChannel.IN_APP,
                    version=1,
                    title_template=definition["title"],
                    body_template=definition["body"],
                    allowed_variables=definition["variables"],
                ),
                NotificationTemplateVersion(
                    code="calculation_confirmed",
                    channel=NotificationChannel.EMAIL,
                    version=1,
                    subject_template=definition["title"],
                    body_template=definition["body"],
                    allowed_variables=definition["variables"],
                ),
            ]
        )
    try:
        yield factory
    finally:
        engine.dispose()


def _enum_value(value):
    return getattr(value, "value", value)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def _user(db, role: Role, label: str) -> User:
    user = User(
        email=f"{label}-{uuid.uuid4()}@example.test",
        password_hash="not-used-by-billing-tests",
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


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


def _reading(
    db,
    publication: Publication,
    blogger: User,
    *,
    period: date,
    value: int,
    captured_day: int = 25,
) -> ViewReading:
    reading = ViewReading(
        publication_id=publication.id,
        reporting_period=period,
        source=ReadingSource.MANUAL,
        reported_value=value,
        accepted_value=value,
        status=ReadingStatus.ACCEPTED,
        risk_flags=[],
        idempotency_key=f"test:{uuid.uuid4()}",
        captured_at=datetime(
            period.year,
            period.month,
            captured_day,
            12,
            tzinfo=timezone.utc,
        ),
        submitted_by_user_id=blogger.id,
    )
    db.add(reading)
    db.flush()
    return reading


def _queued_period(db, *, period: date = PERIOD, rate: int = 5):
    rate_version = RateVersion(
        rate_kopecks_per_view=rate,
        effective_from_period=date(1970, 1, 1),
        reason="Initial test rate",
    )
    db.add(rate_version)
    db.flush()
    calculation_period = CalculationPeriod(
        period=period,
        status=CalculationPeriodStatus.PENDING,
        rate_version_id=rate_version.id,
    )
    db.add(calculation_period)
    db.flush()
    dispatch_id = uuid.uuid4()
    job = CalculationJob(
        period_id=calculation_period.id,
        state=CalculationJobState.QUEUED,
        attempt_count=1,
        available_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        lease_until=datetime(2026, 9, 1, tzinfo=timezone.utc)
        + timedelta(minutes=30),
        dispatch_id=dispatch_id,
    )
    db.add(job)
    db.flush()
    return calculation_period, job, CalculationCommand(
        period_id=calculation_period.id,
        dispatch_id=dispatch_id,
    )


def _seed_growth_calculation(session_factory, *, include_baseline: bool = False):
    with session_factory() as db:
        blogger = _user(db, Role.BLOGGER, "creator")
        reviewer = _user(db, Role.MODERATOR, "reviewer")
        admin = _user(db, Role.ADMIN, "admin")
        growth_publication = _publication(db, blogger, "growth")
        previous = _reading(
            db,
            growth_publication,
            blogger,
            period=date(2026, 7, 1),
            value=100,
        )
        current = _reading(
            db,
            growth_publication,
            blogger,
            period=PERIOD,
            value=180,
        )
        baseline_publication = None
        if include_baseline:
            baseline_publication = _publication(db, blogger, "baseline")
            _reading(
                db,
                baseline_publication,
                blogger,
                period=PERIOD,
                value=50_000,
            )
        db.add(ReadingDatasetRevision(id=1, revision=0))
        period, job, command = _queued_period(db)
        db.commit()
        return {
            "blogger_id": blogger.id,
            "reviewer_id": reviewer.id,
            "admin_id": admin.id,
            "growth_publication_id": growth_publication.id,
            "baseline_publication_id": (
                baseline_publication.id if baseline_publication else None
            ),
            "previous_reading_id": previous.id,
            "current_reading_id": current.id,
            "period_id": period.id,
            "job_id": job.id,
            "command": command,
        }


def _build_growth_calculation(session_factory, *, include_baseline: bool = False):
    scenario = _seed_growth_calculation(
        session_factory,
        include_baseline=include_baseline,
    )
    execute_calculation(scenario["command"], session_factory)
    return scenario


def test_worker_calculates_growth_and_first_reading_as_baseline(
    billing_session_factory,
):
    scenario = _build_growth_calculation(
        billing_session_factory,
        include_baseline=True,
    )

    with billing_session_factory() as db:
        accruals = {
            item.publication_id: item
            for item in db.scalars(
                select(PublicationAccrual).where(
                    PublicationAccrual.period_id == scenario["period_id"]
                )
            )
        }
        growth = accruals[scenario["growth_publication_id"]]
        baseline = accruals[scenario["baseline_publication_id"]]
        total = db.scalar(
            select(CreatorPeriodTotal).where(
                CreatorPeriodTotal.period_id == scenario["period_id"]
            )
        )
        period = db.get(CalculationPeriod, scenario["period_id"])

        assert (growth.previous_value, growth.current_value) == (100, 180)
        assert growth.eligible_views == 80
        assert growth.rate_kopecks_per_view == 5
        assert growth.amount_kopecks == 400
        assert growth.exclusion_reason is None

        assert baseline.previous_value is None
        assert baseline.current_value == 50_000
        assert baseline.eligible_views == 0
        assert baseline.amount_kopecks == 0
        assert _enum_value(baseline.exclusion_reason) == "baseline_only"

        assert total.eligible_views == 80
        assert total.amount_kopecks == 400
        assert total.publication_count == 2
        assert period.total_views == 80
        assert period.total_amount_kopecks == 400
        assert _enum_value(period.status) == "preliminary"


def test_creator_without_ledger_has_zero_balance(billing_session_factory):
    with billing_session_factory() as db:
        blogger = _user(db, Role.BLOGGER, "zero-balance")
        response = get_my_balance(db, actor=blogger)
        assert response.blogger_id == blogger.id
        assert response.available_kopecks == 0
        assert response.reserved_kopecks == 0
        assert response.paid_kopecks == 0
        assert response.updated_at is None


def test_zero_balance_http_response(client):
    email = f"zero-http-{uuid.uuid4()}@example.com"
    with client.app.state.test_session() as db:
        blogger = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.BLOGGER,
            status=AccountStatus.ACTIVE,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(blogger)
        db.commit()
        blogger_id = blogger.id
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    response = client.get("/api/v1/me/balance")
    assert response.status_code == 200
    assert response.json() == {
        "blogger_id": str(blogger_id),
        "available_kopecks": 0,
        "reserved_kopecks": 0,
        "paid_kopecks": 0,
        "claim_expired_at": None,
        "updated_at": None,
    }


def test_worker_ignores_stale_dispatch_and_redelivery_is_idempotent(
    billing_session_factory,
):
    scenario = _seed_growth_calculation(billing_session_factory)
    stale = CalculationCommand(
        period_id=scenario["period_id"],
        dispatch_id=uuid.uuid4(),
    )

    execute_calculation(stale, billing_session_factory)
    with billing_session_factory() as db:
        job = db.get(CalculationJob, scenario["job_id"])
        assert _enum_value(job.state) == "queued"
        assert job.dispatch_id == scenario["command"].dispatch_id
        assert _count(db, PublicationAccrual) == 0

    execute_calculation(scenario["command"], billing_session_factory)
    with billing_session_factory() as db:
        first_accrual_id = db.scalar(select(PublicationAccrual.id))
        first_total_id = db.scalar(select(CreatorPeriodTotal.id))
        assert first_accrual_id is not None
        assert first_total_id is not None

    execute_calculation(scenario["command"], billing_session_factory)
    with billing_session_factory() as db:
        job = db.get(CalculationJob, scenario["job_id"])
        assert _enum_value(job.state) == "succeeded"
        assert job.dispatch_id is None
        assert _count(db, PublicationAccrual) == 1
        assert _count(db, CreatorPeriodTotal) == 1
        assert db.scalar(select(PublicationAccrual.id)) == first_accrual_id
        assert db.scalar(select(CreatorPeriodTotal.id)) == first_total_id


def test_confirmation_is_exactly_once_and_locks_financial_inputs(
    billing_session_factory,
):
    scenario = _build_growth_calculation(billing_session_factory)

    with billing_session_factory() as db:
        reviewer = db.get(User, scenario["reviewer_id"])
        confirmed = confirm_period(
            db,
            actor=reviewer,
            period_id=scenario["period_id"],
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        assert _enum_value(confirmed.status) == "confirmed"

    with billing_session_factory() as db:
        reviewer = db.get(User, scenario["reviewer_id"])
        with pytest.raises(APIError) as repeated:
            confirm_period(
                db,
                actor=reviewer,
                period_id=scenario["period_id"],
                audit_context=AUDIT_CONTEXT,
            )
        assert repeated.value.code == "CALCULATION_NOT_PRELIMINARY"
        db.rollback()

    with billing_session_factory() as db:
        balance = db.get(CreatorBalance, scenario["blogger_id"])
        ledger = list(db.scalars(select(BalanceLedgerEntry)))
        readings = list(
            db.scalars(
                select(ViewReading).where(
                    ViewReading.id.in_(
                        (
                            scenario["previous_reading_id"],
                            scenario["current_reading_id"],
                        )
                    )
                )
            )
        )
        revision = db.get(ReadingDatasetRevision, 1)

        assert balance.available_kopecks == 400
        assert len(ledger) == 1
        assert ledger[0].available_delta_kopecks == 400
        assert ledger[0].idempotency_key == (
            f"period-accrual:{scenario['period_id']}:{scenario['blogger_id']}"
        )
        assert all(item.financial_locked_at is not None for item in readings)
        assert all(
            item.financial_locked_by_period_id == scenario["period_id"]
            for item in readings
        )
        assert revision.closed_through_period == PERIOD


def test_confirmation_rejects_stale_revision_without_financial_side_effects(
    billing_session_factory,
):
    scenario = _build_growth_calculation(billing_session_factory)

    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        correct_accepted_reading(
            db,
            manager=admin,
            reading_id=scenario["current_reading_id"],
            payload=ReadingCorrectionRequest(
                accepted_value=190,
                reason="Verified source correction",
            ),
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()

    with billing_session_factory() as db:
        reviewer = db.get(User, scenario["reviewer_id"])
        with pytest.raises(APIError) as stale:
            confirm_period(
                db,
                actor=reviewer,
                period_id=scenario["period_id"],
                audit_context=AUDIT_CONTEXT,
            )
        assert stale.value.code == "CALCULATION_STALE"
        db.rollback()

    with billing_session_factory() as db:
        period = db.get(CalculationPeriod, scenario["period_id"])
        readings = list(db.scalars(select(ViewReading)))
        revision = db.get(ReadingDatasetRevision, 1)

        assert _enum_value(period.status) == "preliminary"
        assert period.input_revision == 0
        assert revision.revision == 1
        assert _count(db, CreatorBalance) == 0
        assert _count(db, BalanceLedgerEntry) == 0
        assert all(item.financial_locked_at is None for item in readings)


def test_manual_recalculation_resets_worker_retry_budget(billing_session_factory):
    scenario = _build_growth_calculation(billing_session_factory)
    with billing_session_factory() as db:
        job = db.get(CalculationJob, scenario["job_id"])
        job.state = CalculationJobState.FAILED
        job.attempt_count = 5
        db.commit()

    with billing_session_factory() as db:
        reviewer = db.get(User, scenario["reviewer_id"])
        period = request_recalculation(
            db,
            actor=reviewer,
            period_id=scenario["period_id"],
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        job = db.get(CalculationJob, scenario["job_id"])
        assert _enum_value(period.status) == "pending"
        assert _enum_value(job.state) == "pending"
        assert job.attempt_count == 0


def test_rate_cannot_be_added_after_calculation_period_was_created(
    billing_session_factory,
):
    scenario = _seed_growth_calculation(billing_session_factory)
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        with pytest.raises(APIError) as ineffective:
            create_rate(
                db,
                actor=admin,
                payload=RateCreateRequest(
                    rate_kopecks_per_view=7,
                    effective_from_period=PERIOD,
                    reason="Too late for the existing period",
                ),
                audit_context=AUDIT_CONTEXT,
            )
        assert ineffective.value.code == "RATE_PERIOD_ALREADY_CREATED"
        db.rollback()

    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        rate = create_rate(
            db,
            actor=admin,
            payload=RateCreateRequest(
                rate_kopecks_per_view=7,
                effective_from_period=date(2026, 9, 1),
                reason="Future rate change",
            ),
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        assert rate.rate_kopecks_per_view == 7


def test_confirmed_accrual_correction_posts_delta_once_and_keeps_snapshot(
    billing_session_factory,
):
    scenario = _build_growth_calculation(billing_session_factory)
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        confirm_period(
            db,
            actor=admin,
            period_id=scenario["period_id"],
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        accrual_id = db.scalar(
            select(PublicationAccrual.id).where(
                PublicationAccrual.publication_id
                == scenario["growth_publication_id"]
            )
        )

    idempotency_key = uuid.uuid4()
    payload = AccrualCorrectionRequest(
        corrected_current_value=200,
        reason="Final documented adjustment",
        idempotency_key=idempotency_key,
    )
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        first = correct_confirmed_accrual(
            db,
            actor=admin,
            accrual_id=accrual_id,
            payload=payload,
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        first_id = first.id

    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        repeated = correct_confirmed_accrual(
            db,
            actor=admin,
            accrual_id=accrual_id,
            payload=payload,
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        assert repeated.id == first_id

    with billing_session_factory() as db:
        accrual = db.get(PublicationAccrual, accrual_id)
        correction = db.get(AccrualCorrection, first_id)
        balance = db.get(CreatorBalance, scenario["blogger_id"])
        ledger = list(
            db.scalars(
                select(BalanceLedgerEntry).order_by(
                    BalanceLedgerEntry.available_delta_kopecks
                )
            )
        )

        assert (accrual.current_value, accrual.amount_kopecks) == (180, 400)
        assert accrual.adjustment_kopecks == 100
        assert accrual.payable_amount_kopecks == 500
        assert correction.sequence_number == 1
        assert correction.old_current_value == 180
        assert correction.new_current_value == 200
        assert correction.old_amount_kopecks == 400
        assert correction.new_amount_kopecks == 500
        assert correction.delta_kopecks == 100
        assert balance.available_kopecks == 500
        assert _count(db, AccrualCorrection) == 1
        assert len(ledger) == 2
        assert [item.available_delta_kopecks for item in ledger] == [100, 400]
        creator_total = db.scalar(
            select(CreatorPeriodTotal).where(
                CreatorPeriodTotal.period_id == scenario["period_id"]
            )
        )
        period = db.get(CalculationPeriod, scenario["period_id"])
        assert creator_total.adjustment_kopecks == 100
        assert creator_total.payable_amount_kopecks == 500
        assert period.total_adjustment_kopecks == 100
        assert period.total_payable_kopecks == 500

    conflicting_payload = AccrualCorrectionRequest(
        corrected_current_value=210,
        reason="Different request with a reused key",
        idempotency_key=idempotency_key,
    )
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        with pytest.raises(APIError) as reused:
            correct_confirmed_accrual(
                db,
                actor=admin,
                accrual_id=accrual_id,
                payload=conflicting_payload,
                audit_context=AUDIT_CONTEXT,
            )
        assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    second_payload = AccrualCorrectionRequest(
        corrected_current_value=190,
        reason="Second documented adjustment",
        idempotency_key=uuid.uuid4(),
    )
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        second = correct_confirmed_accrual(
            db,
            actor=admin,
            accrual_id=accrual_id,
            payload=second_payload,
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        assert second.sequence_number == 2
        assert second.old_current_value == 200
        assert second.old_amount_kopecks == 500
        assert second.new_amount_kopecks == 450
        assert second.delta_kopecks == -50

    with billing_session_factory() as db:
        accrual = db.get(PublicationAccrual, accrual_id)
        total = db.scalar(
            select(CreatorPeriodTotal).where(
                CreatorPeriodTotal.period_id == scenario["period_id"]
            )
        )
        period = db.get(CalculationPeriod, scenario["period_id"])
        balance = db.get(CreatorBalance, scenario["blogger_id"])
        assert accrual.payable_amount_kopecks == 450
        assert total.payable_amount_kopecks == 450
        assert period.total_payable_kopecks == 450
        assert balance.available_kopecks == 450


def test_correction_does_not_mask_unknown_database_integrity_error(
    billing_session_factory,
    monkeypatch,
):
    scenario = _build_growth_calculation(billing_session_factory)
    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])
        confirm_period(
            db,
            actor=admin,
            period_id=scenario["period_id"],
            audit_context=AUDIT_CONTEXT,
        )
        db.commit()
        accrual_id = db.scalar(
            select(PublicationAccrual.id).where(
                PublicationAccrual.publication_id
                == scenario["growth_publication_id"]
            )
        )

    with billing_session_factory() as db:
        admin = db.get(User, scenario["admin_id"])

        def fail_flush(*args, **kwargs):
            del args, kwargs
            raise IntegrityError(
                "INSERT INTO accrual_corrections (...) VALUES (...) ",
                {},
                RuntimeError("unknown financial constraint"),
            )

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(IntegrityError, match="unknown financial constraint"):
            correct_confirmed_accrual(
                db,
                actor=admin,
                accrual_id=accrual_id,
                payload=AccrualCorrectionRequest(
                    corrected_current_value=200,
                    reason="Unknown database failure must not be hidden",
                    idempotency_key=uuid.uuid4(),
                ),
                audit_context=AUDIT_CONTEXT,
            )


class _FailingProducer:
    def __init__(self):
        self.calls = 0

    def send_task(self, name, *, args, queue):
        self.calls += 1
        raise ConnectionError("broker unavailable")


class _CapturingProducer:
    def __init__(self):
        self.messages = []

    def send_task(self, name, *, args, queue):
        self.messages.append({"name": name, "args": args, "queue": queue})


def _seed_scheduler_source(billing_session_factory):
    with billing_session_factory() as db:
        blogger = _user(db, Role.BLOGGER, "scheduler-creator")
        publication = _publication(db, blogger, "scheduler-publication")
        _reading(
            db,
            publication,
            blogger,
            period=date(2026, 6, 1),
            value=1_000,
        )
        db.add(
            RateVersion(
                rate_kopecks_per_view=5,
                effective_from_period=date(1970, 1, 1),
                reason="Initial scheduler test rate",
            )
        )
        db.commit()


def test_scheduler_dispatches_new_backfill_job_in_the_same_iteration(
    billing_session_factory,
):
    _seed_scheduler_source(billing_session_factory)
    producer = _CapturingProducer()
    now = datetime(2026, 8, 31, 21, 10, tzinfo=timezone.utc)

    assert dispatch_calculation(
        now=now,
        session_factory=billing_session_factory,
        task_producer=producer,
    ) is True
    assert len(producer.messages) == 1
    command = CalculationCommand.model_validate(producer.messages[0]["args"][0])

    with billing_session_factory() as db:
        period = db.get(CalculationPeriod, command.period_id)
        assert period.period == date(2026, 6, 1)


def test_scheduler_releases_failed_broker_dispatch_and_backfills_oldest_period(
    billing_session_factory,
):
    _seed_scheduler_source(billing_session_factory)
    now = datetime(2026, 8, 31, 21, 10, tzinfo=timezone.utc)
    with billing_session_factory() as db:
        rate_id = db.scalar(select(RateVersion.id))
        june_period = CalculationPeriod(
            period=date(2026, 6, 1),
            status=CalculationPeriodStatus.PENDING,
            rate_version_id=rate_id,
        )
        db.add(june_period)
        db.flush()
        db.add(
            CalculationJob(
                period_id=june_period.id,
                state=CalculationJobState.PENDING,
                attempt_count=0,
                available_at=now - timedelta(minutes=1),
                created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()

    failing_producer = _FailingProducer()
    assert dispatch_calculation(
        now=now,
        session_factory=billing_session_factory,
        task_producer=failing_producer,
    ) is False
    assert failing_producer.calls == 1

    with billing_session_factory() as db:
        rows = db.execute(
            select(CalculationPeriod, CalculationJob)
            .join(CalculationJob, CalculationJob.period_id == CalculationPeriod.id)
            .order_by(CalculationPeriod.period)
        ).all()
        assert [period.period for period, _ in rows] == [
            date(2026, 6, 1),
            date(2026, 7, 1),
        ]
        _, june_job = rows[0]
        _, july_job = rows[1]
        assert _enum_value(june_job.state) == "retry_wait"
        assert june_job.attempt_count == 0
        assert june_job.dispatch_id is None
        assert june_job.lease_until is None
        assert june_job.last_error_code == "broker_publish_failed"
        assert _enum_value(july_job.state) == "pending"
        db.commit()

    producer = _CapturingProducer()
    assert dispatch_calculation(
        now=now,
        session_factory=billing_session_factory,
        task_producer=producer,
    ) is True

    assert len(producer.messages) == 1
    message = producer.messages[0]
    command = CalculationCommand.model_validate(message["args"][0])
    assert message["name"] == CALCULATION_TASK
    assert message["queue"] == CALCULATIONS_QUEUE

    with billing_session_factory() as db:
        rows = db.execute(
            select(CalculationPeriod, CalculationJob)
            .join(CalculationJob, CalculationJob.period_id == CalculationPeriod.id)
            .order_by(CalculationPeriod.period)
        ).all()
        assert [period.period for period, _ in rows] == [
            date(2026, 6, 1),
            date(2026, 7, 1),
            date(2026, 8, 1),
        ]
        june_period, june_job = rows[0]
        _, july_job = rows[1]
        _, august_job = rows[2]
        assert command.period_id == june_period.id
        assert _enum_value(june_job.state) == "queued"
        assert _enum_value(july_job.state) == "pending"
        assert _enum_value(august_job.state) == "pending"


def test_scheduler_stops_recovering_worker_after_retry_budget(
    billing_session_factory,
):
    _seed_scheduler_source(billing_session_factory)
    now = datetime(2026, 8, 31, 21, 10, tzinfo=timezone.utc)
    with billing_session_factory() as db:
        rate_id = db.scalar(select(RateVersion.id))
        period = CalculationPeriod(
            period=date(2026, 6, 1),
            status=CalculationPeriodStatus.PENDING,
            rate_version_id=rate_id,
        )
        db.add(period)
        db.flush()
        exhausted_job = CalculationJob(
            period_id=period.id,
            state=CalculationJobState.PROCESSING,
            attempt_count=5,
            available_at=now - timedelta(hours=1),
            lease_until=now - timedelta(minutes=1),
            dispatch_id=uuid.uuid4(),
        )
        db.add(exhausted_job)
        db.commit()
        exhausted_job_id = exhausted_job.id

    producer = _CapturingProducer()
    assert dispatch_calculation(
        now=now,
        session_factory=billing_session_factory,
        task_producer=producer,
    ) is True
    with billing_session_factory() as db:
        exhausted_job = db.get(CalculationJob, exhausted_job_id)
        assert _enum_value(exhausted_job.state) == "failed"
        assert exhausted_job.attempt_count == 5
        assert exhausted_job.dispatch_id is None
        assert exhausted_job.last_error_code == "worker_lease_expired"
