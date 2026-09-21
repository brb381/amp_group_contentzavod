import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.request import urlopen

import redis
from celery import Celery
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.logging_config import configure_logging
from app.smtp import send_smtp_message, validate_smtp_transport


logger = logging.getLogger(__name__)
DISPATCHERS = ("email", "calculations", "exports", "lifecycle", "youtube-views", "youtube")
QUEUES = ("email", "youtube", "calculations", "lifecycle", "exports")
STARTUP_GRACE_SECONDS = 90
SCHEDULER_MAX_AGE_SECONDS = 90
REMINDER_SECONDS = 3600


@dataclass(frozen=True)
class JobSpec:
    name: str
    table: str
    state_column: str = "state"
    lease_column: str = "lease_until"
    overdue_minutes: int = 20
    failed_time_column: str = "updated_at"


JOB_SPECS = (
    JobSpec("email", "outbox_events", lease_column="processing_until", overdue_minutes=5, failed_time_column="failed_at"),
    JobSpec("youtube-enrichment", "youtube_enrichment_jobs", overdue_minutes=120),
    JobSpec("youtube-views", "youtube_view_collection_jobs", overdue_minutes=120),
    JobSpec("calculations", "calculation_jobs"),
    JobSpec("lifecycle", "lifecycle_jobs"),
    JobSpec("exports", "export_jobs", state_column="status"),
)


class MonitorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str
    redis_url: str
    api_readiness_url: str = "http://api:8000/health/ready"
    monitor_alert_email: str
    monitor_poll_seconds: int = Field(default=30, ge=10, le=300)
    monitor_metrics_port: int = Field(default=9101, ge=1, le=65535)
    smtp_host: str
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_from_email: str
    smtp_username: str | None = None
    smtp_password: str | None = None

    @model_validator(mode="after")
    def validate_transport(self) -> "MonitorSettings":
        if self.environment == "production" and self.monitor_alert_email.lower().endswith(
            (".test", ".local")
        ):
            raise ValueError("MONITOR_ALERT_EMAIL must use a real address in production")
        validate_smtp_transport(
            environment=self.environment,
            host=self.smtp_host,
            from_email=self.smtp_from_email,
            use_tls=self.smtp_use_tls,
            use_ssl=self.smtp_use_ssl,
            username=self.smtp_username,
            password=self.smtp_password,
        )
        return self

class MetricsSnapshot:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, tuple[int, int, int]] = {}
        self._issues: dict[str, str] = {}
        self._last_poll = 0.0

    def record_job(self, name: str, counts: tuple[int, int, int]) -> None:
        with self._lock:
            self._jobs[name] = counts

    def record_poll(self, issues: dict[str, str], now: datetime) -> None:
        with self._lock:
            self._issues = issues.copy()
            self._last_poll = now.timestamp()

    def render(self) -> bytes:
        with self._lock:
            lines = [
                "# TYPE amp_monitor_last_poll_timestamp_seconds gauge",
                f"amp_monitor_last_poll_timestamp_seconds {self._last_poll}",
                "# TYPE amp_monitor_active_issues gauge",
                f"amp_monitor_active_issues {len(self._issues)}",
            ]
            for name, (overdue, expired, failed) in sorted(self._jobs.items()):
                lines.extend((
                    f'amp_monitor_job_overdue{{queue="{name}"}} {overdue}',
                    f'amp_monitor_job_expired_lease{{queue="{name}"}} {expired}',
                    f'amp_monitor_job_failed_last_hour{{queue="{name}"}} {failed}',
                ))
            for name in sorted(self._issues):
                lines.append(f'amp_monitor_issue{{check="{name}"}} 1')
            return ("\n".join(lines) + "\n").encode("utf-8")


METRICS = MetricsSnapshot()


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = METRICS.render()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


@lru_cache
def get_monitor_settings() -> MonitorSettings:
    return MonitorSettings()


def _job_counts(connection, spec: JobSpec, now: datetime) -> tuple[int, int, int]:
    due_before = now - timedelta(minutes=spec.overdue_minutes)
    lease_before = now - timedelta(minutes=2)
    failed_since = now - timedelta(hours=1)
    pending_states = "('pending', 'retry_wait')" if spec.name != "email" else "('pending')"
    active_states = "('queued', 'processing')" if spec.name != "email" else "('processing', 'delivering')"
    event_filter = " AND event_type = 'email_delivery_requested'" if spec.name == "email" else ""
    query = text(
        f"SELECT "
        f"(SELECT count(*) FROM {spec.table} WHERE {spec.state_column} IN {pending_states} "
        f"AND available_at < :due_before{event_filter}), "
        f"(SELECT count(*) FROM {spec.table} WHERE {spec.state_column} IN {active_states} "
        f"AND {spec.lease_column} < :lease_before{event_filter}), "
        f"(SELECT count(*) FROM {spec.table} WHERE {spec.state_column} = 'failed' "
        f"AND {spec.failed_time_column} >= :failed_since{event_filter})"
    )
    row = connection.execute(
        query,
        {
            "due_before": due_before,
            "lease_before": lease_before,
            "failed_since": failed_since,
        },
    ).one()
    return int(row[0]), int(row[1]), int(row[2])


def _database_issues(settings: MonitorSettings, now: datetime) -> dict[str, str]:
    issues = {}
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": 2},
        poolclass=NullPool,
        hide_parameters=True,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            for spec in JOB_SPECS:
                overdue, expired, failed = _job_counts(connection, spec, now)
                METRICS.record_job(spec.name, (overdue, expired, failed))
                logger.info(
                    "Job counts queue=%s overdue=%d expired_lease=%d failed_last_hour=%d",
                    spec.name, overdue, expired, failed,
                )
                if overdue:
                    issues[f"job:{spec.name}:overdue"] = f"{overdue} overdue"
                if expired:
                    issues[f"job:{spec.name}:expired_lease"] = f"{expired} expired leases"
                if failed >= 3:
                    issues[f"job:{spec.name}:failed"] = f"{failed} failed in the last hour"
    finally:
        engine.dispose()
    return issues


def _redis_issues(settings: MonitorSettings, now: datetime, check_processes: bool) -> dict[str, str]:
    issues = {}
    client = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    client.ping()
    if not check_processes:
        return issues
    heartbeat = client.hgetall("amp:monitor:scheduler")
    for name in DISPATCHERS:
        raw = heartbeat.get(f"{name}:success".encode())
        if raw is None:
            issues[f"scheduler:{name}"] = "no successful heartbeat"
            continue
        try:
            age = (now - datetime.fromisoformat(raw.decode())).total_seconds()
        except (ValueError, UnicodeError):
            age = float("inf")
        if age > SCHEDULER_MAX_AGE_SECONDS or age < -30:
            issues[f"scheduler:{name}"] = "successful heartbeat is stale"
    return issues


def _worker_issues(settings: MonitorSettings) -> dict[str, str]:
    app = Celery("amp_monitor", broker=settings.redis_url)
    try:
        active = app.control.inspect(timeout=3).active_queues() or {}
    finally:
        app.close()
    online = {
        queue["name"]
        for queues in active.values()
        for queue in queues
        if isinstance(queue, dict) and "name" in queue
    }
    return {
        f"worker:{queue}": "no worker consuming queue"
        for queue in QUEUES
        if queue not in online
    }


def collect_issues(
    settings: MonitorSettings, now: datetime, *, check_processes: bool = True
) -> dict[str, str]:
    issues = {}
    if check_processes:
        try:
            with urlopen(settings.api_readiness_url, timeout=3) as response:
                if response.status != 200:
                    issues["api"] = "readiness failed"
        except Exception:
            issues["api"] = "readiness unavailable"

    try:
        issues.update(_database_issues(settings, now))
    except Exception:
        logger.exception("Monitor database probe failed")
        issues["database"] = "database probe failed"

    try:
        issues.update(_redis_issues(settings, now, check_processes))
    except Exception:
        logger.exception("Monitor Redis probe failed")
        issues["redis"] = "Redis probe failed"
        return issues

    if check_processes:
        try:
            issues.update(_worker_issues(settings))
        except Exception:
            logger.exception("Monitor Celery probe failed")
            issues["workers"] = "worker probe failed"
    return issues


def send_alert(settings: MonitorSettings, issues: dict[str, str]) -> None:
    message = EmailMessage()
    message["Subject"] = "[AMP] Monitoring alert" if issues else "[AMP] Monitoring recovered"
    message["From"] = settings.smtp_from_email
    message["To"] = settings.monitor_alert_email
    message.set_content(
        "\n".join(f"{key}: {value}" for key, value in sorted(issues.items()))
        if issues else "All monitored checks recovered."
    )
    send_smtp_message(
        message,
        host=settings.smtp_host,
        port=settings.smtp_port,
        use_tls=settings.smtp_use_tls,
        use_ssl=settings.smtp_use_ssl,
        username=settings.smtp_username,
        password=settings.smtp_password,
        timeout=10,
    )


class AlertState:
    def __init__(self) -> None:
        self.last_issues: dict[str, str] = {}
        self.last_sent_at: datetime | None = None

    def process(self, settings: MonitorSettings, issues: dict[str, str], now: datetime) -> None:
        changed = issues != self.last_issues
        reminder_due = (
            bool(issues)
            and self.last_sent_at is not None
            and (now - self.last_sent_at).total_seconds() >= REMINDER_SECONDS
        )
        if not changed and not reminder_due:
            return
        send_alert(settings, issues)
        self.last_issues = issues.copy()
        self.last_sent_at = now


def run() -> None:
    configure_logging("monitor")
    settings = get_monitor_settings()
    alerts = AlertState()
    metrics_server = ThreadingHTTPServer(("0.0.0.0", settings.monitor_metrics_port), MetricsHandler)
    Thread(target=metrics_server.serve_forever, daemon=True).start()
    started = time.monotonic()
    while True:
        now = datetime.now(timezone.utc)
        issues = collect_issues(
            settings, now,
            check_processes=time.monotonic() - started >= STARTUP_GRACE_SECONDS,
        )
        METRICS.record_poll(issues, now)
        if issues:
            logger.warning("Monitor issues: %s", issues)
        try:
            alerts.process(settings, issues, now)
        except Exception:
            logger.exception("Could not send monitoring alert")
        time.sleep(settings.monitor_poll_seconds)


if __name__ == "__main__":
    run()
