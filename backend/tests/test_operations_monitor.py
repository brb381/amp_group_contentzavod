from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from app import health, monitor
from app.billing.reconciliation import reconcile_finances
from app.scheduler import run_iteration
from app.outbox.models import OutboxEvent
from app.scheduling.email import dispatch_email_events


def test_readiness_requires_all_dependencies(client, monkeypatch):
    calls = []

    def ok(name):
        calls.append(name)

    monkeypatch.setattr(health, "check_database", lambda: ok("database"))
    monkeypatch.setattr(health, "check_redis", lambda: ok("redis"))
    monkeypatch.setattr(health, "check_storage", lambda: ok("storage"))

    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert calls == ["database", "redis", "storage"]

    def failed_storage():
        raise ConnectionError("storage unavailable")

    monkeypatch.setattr(health, "check_storage", failed_storage)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_scheduler_reports_individual_outcomes():
    outcomes = []

    def failed():
        raise RuntimeError("failed")

    run_iteration(
        (("email", failed), ("exports", lambda: None)),
        report=outcomes.append,
    )
    assert outcomes == [{"email": False, "exports": True}]


def test_scheduler_telemetry_failure_does_not_stop_dispatch():
    calls = []

    def broken_report(_outcomes):
        raise ConnectionError("redis unavailable")

    run_iteration(
        (("email", lambda: calls.append("ran")),),
        report=broken_report,
    )
    assert calls == ["ran"]


def test_monitor_detects_stale_dispatcher_without_false_empty_queue_alert(monkeypatch):
    now = datetime.now(timezone.utc)

    class FakeRedis:
        def ping(self):
            return True

        def hgetall(self, _key):
            return {
                f"{name}:success".encode(): (
                    now - timedelta(minutes=3 if name == "exports" else 0)
                ).isoformat().encode()
                for name in monitor.DISPATCHERS
            }

    monkeypatch.setattr(monitor.redis.Redis, "from_url", lambda *_args, **_kwargs: FakeRedis())
    settings = monitor.MonitorSettings(
        database_url="postgresql+psycopg://monitor:password@localhost/amp",
        redis_url="redis://localhost:6379/0",
        monitor_alert_email="ops@example.test",
        smtp_host="localhost",
        smtp_from_email="amp@example.test",
    )
    issues = monitor._redis_issues(settings, now, True)
    assert issues == {"scheduler:exports": "successful heartbeat is stale"}


def test_alerts_only_on_change_and_reminder(monkeypatch):
    sent = []
    monkeypatch.setattr(monitor, "send_alert", lambda _settings, issues: sent.append(issues.copy()))
    settings = monitor.MonitorSettings(
        database_url="postgresql+psycopg://monitor:password@localhost/amp",
        redis_url="redis://localhost:6379/0",
        monitor_alert_email="ops@example.test",
        smtp_host="localhost",
        smtp_from_email="amp@example.test",
    )
    state = monitor.AlertState()
    now = datetime.now(timezone.utc)
    state.process(settings, {"api": "down"}, now)
    state.process(settings, {"api": "down"}, now + timedelta(minutes=1))
    state.process(settings, {"api": "down"}, now + timedelta(hours=1))
    state.process(settings, {}, now + timedelta(hours=1, minutes=1))
    assert sent == [{"api": "down"}, {"api": "down"}, {}]


def test_monitor_alerts_when_disk_free_space_is_below_threshold(monkeypatch):
    class Usage:
        total = 100
        free = 9

    monkeypatch.setattr(monitor.shutil, "disk_usage", lambda _path: Usage())
    settings = monitor.MonitorSettings(
        database_url="postgresql+psycopg://monitor:password@localhost/amp",
        redis_url="redis://localhost:6379/0",
        monitor_alert_email="ops@example.test",
        smtp_host="localhost",
        smtp_from_email="amp@example.test",
        monitor_min_disk_free_percent=15,
    )

    assert monitor._disk_issues(settings) == {
        "disk": "free space 9.0% is below 15%"
    }


def test_financial_reconciliation_detects_ledger_and_reserve_mismatches():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE creator_balances (blogger_id TEXT PRIMARY KEY, "
            "available_kopecks INTEGER NOT NULL, reserved_kopecks INTEGER NOT NULL, "
            "paid_kopecks INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE balance_ledger (blogger_id TEXT NOT NULL, "
            "available_delta_kopecks INTEGER NOT NULL, reserved_delta_kopecks INTEGER NOT NULL, "
            "paid_delta_kopecks INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE payout_requests (blogger_id TEXT NOT NULL, "
            "amount_kopecks INTEGER NOT NULL, status TEXT NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO creator_balances VALUES ('ok', 500, 100, 100), ('broken', 1, 50, 0)"
        ))
        connection.execute(text(
            "INSERT INTO balance_ledger VALUES "
            "('ok', 700, 0, 0), ('ok', -200, 200, 0), ('ok', 0, -100, 100), "
            "('broken', 2, 0, 0), ('missing-balance', 10, 0, 0)"
        ))
        connection.execute(text(
            "INSERT INTO payout_requests VALUES "
            "('ok', 100, 'approved'), ('broken', 40, 'requested')"
        ))

        result = reconcile_finances(connection)

    engine.dispose()
    assert result.ledger_mismatches == 2
    assert result.payout_reserve_mismatches == 1
    assert not result.is_consistent


def test_financial_monitor_reuses_result_until_interval_expires(monkeypatch):
    calls = []

    class Result:
        ledger_mismatches = 1
        payout_reserve_mismatches = 0

    monkeypatch.setattr(
        monitor,
        "reconcile_finances",
        lambda _connection: calls.append("checked") or Result(),
    )
    check = monitor.FinancialCheck()
    now = datetime.now(timezone.utc)

    expected = {"finance:ledger": "1 balance snapshots do not match ledger"}
    assert check.collect(object(), now, 3600) == expected
    assert check.collect(object(), now + timedelta(minutes=10), 3600) == expected
    assert check.collect(object(), now + timedelta(hours=1), 3600) == expected
    assert calls == ["checked", "checked"]


def test_job_counts_are_scoped_to_due_and_recent_rows():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE jobs (state TEXT, available_at TIMESTAMP, "
            "lease_until TIMESTAMP, updated_at TIMESTAMP)"
        ))
        for state, available_at, lease_until, updated_at in (
            ("pending", now - timedelta(minutes=30), None, now),
            ("retry_wait", now + timedelta(minutes=30), None, now),
            ("processing", now, now - timedelta(minutes=3), now),
            ("failed", now, None, now - timedelta(minutes=10)),
            ("failed", now, None, now - timedelta(hours=2)),
        ):
            connection.execute(
                text(
                    "INSERT INTO jobs (state, available_at, lease_until, updated_at) "
                    "VALUES (:state, :available_at, :lease_until, :updated_at)"
                ),
                {
                    "state": state,
                    "available_at": available_at,
                    "lease_until": lease_until,
                    "updated_at": updated_at,
                },
            )
        assert monitor._job_counts(
            connection, monitor.JobSpec("test", "jobs"), now
        ) == (1, 1, 1)
    engine.dispose()


def test_email_job_counts_ignore_other_outbox_events():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE outbox_events (state TEXT, event_type TEXT, "
            "available_at TIMESTAMP, processing_until TIMESTAMP, "
            "created_at TIMESTAMP, failed_at TIMESTAMP)"
        ))
        for event_type in ("email_delivery_requested", "account_deleted_by_creator"):
            connection.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(state, event_type, available_at, processing_until, created_at) "
                    "VALUES ('pending', :event_type, :available_at, NULL, :created_at)"
                ),
                {
                    "event_type": event_type,
                    "available_at": now - timedelta(minutes=10),
                    "created_at": now,
                },
            )
        assert monitor._job_counts(
            connection,
            monitor.JobSpec(
                "email", "outbox_events",
                lease_column="processing_until",
                overdue_minutes=5,
                failed_time_column="failed_at",
            ),
            now,
        ) == (1, 0, 0)
    engine.dispose()


def test_metrics_snapshot_exposes_queue_counts_and_active_issues():
    snapshot = monitor.MetricsSnapshot()
    now = datetime.now(timezone.utc)
    snapshot.record_job("email", (2, 1, 3))
    snapshot.record_poll({"api": "readiness unavailable"}, now)
    body = snapshot.render().decode()
    assert 'amp_monitor_job_overdue{queue="email"} 2' in body
    assert 'amp_monitor_job_expired_lease{queue="email"} 1' in body
    assert 'amp_monitor_job_failed_last_hour{queue="email"} 3' in body
    assert 'amp_monitor_issue{check="api"} 1' in body
    assert "amp_monitor_active_issues 1" in body


def test_invalid_email_command_records_failure_time(client):
    session_factory = client.app.state.test_session
    now = datetime.now(timezone.utc)
    with session_factory.begin() as db:
        event = OutboxEvent(
            event_type="email_delivery_requested",
            payload={},
            state="pending",
            available_at=now - timedelta(minutes=1),
        )
        db.add(event)
        db.flush()
        event_id = event.id

    class NoopProducer:
        def send_task(self, *_args, **_kwargs):
            raise AssertionError("invalid command must not be dispatched")

    dispatch_email_events(now=now, session_factory=session_factory, task_producer=NoopProducer())
    with session_factory() as db:
        stored = db.get(OutboxEvent, event_id)
        assert stored.state == "failed"
        assert stored.failed_at.replace(tzinfo=timezone.utc) == now
