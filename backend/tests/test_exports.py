import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.clock import utc_now
from app.contracts import ExportCommand
from app.exports.models import ExportFormat, ExportJob, ExportType
from app.exports.generator import generate_export, schema_for
from app.exports.processor import cleanup_expired_artifacts, execute_export
from app.exports.storage import ArtifactDownload
from app.scheduling.exports import dispatch_export


PASSWORD = "export-test-password-123"
DATA_FILTERS = {"date_from": "2026-01-01", "date_to": "2026-09-01"}
PAYOUT_FILTERS = {
    "requested_from": "2026-01-01",
    "requested_to": "2026-09-01",
}


class MemoryArtifactStore:
    def __init__(self):
        self.objects = {}

    def upload(self, *, key, path, content_type, content_sha256):
        self.objects[key] = (path.read_bytes(), content_type, content_sha256)

    def download(self, *, key):
        content, content_type, _ = self.objects[key]
        return ArtifactDownload(io.BytesIO(content), len(content), content_type)

    def delete(self, *, key):
        self.objects.pop(key, None)


class CapturingProducer:
    def __init__(self):
        self.command = None

    def send_task(self, task_name, *, args, queue):
        del task_name, queue
        self.command = ExportCommand.model_validate(args[0])


def _seed_staff(client, role: Role, label: str) -> str:
    email = f"export-{label}-{uuid.uuid4()}@example.com"
    with client.app.state.test_session() as db:
        db.add(
            User(
                email=email,
                password_hash=hash_password(PASSWORD),
                role=role,
                status=AccountStatus.ACTIVE,
                email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()
    return email


def _login(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _payload(export_type: ExportType, *, export_format: str = "csv") -> dict:
    filters = dict(
        PAYOUT_FILTERS if export_type in {
            ExportType.PAYOUT_REGISTER, ExportType.PAYOUT_HISTORY
        } else DATA_FILTERS
    )
    return {
        "idempotency_key": str(uuid.uuid4()),
        "export_type": export_type.value,
        "format": export_format,
        "filters": filters,
    }


def _post_export(client, export_type: ExportType):
    return client.post(
        "/api/v1/staff/exports",
        headers={"X-CSRF-Token": client.cookies.get("amp_csrf")},
        json=_payload(export_type),
    )


@pytest.mark.parametrize(
    ("role", "export_type"),
    [
        (Role.ADMIN, ExportType.AUDIT_LOG),
        (Role.FINANCE, ExportType.PAYOUT_HISTORY),
        (Role.MANAGER, ExportType.BLOGGERS),
        (Role.MODERATOR, ExportType.MODERATION_HISTORY),
        (Role.ANALYST, ExportType.ACCRUALS),
    ],
)
def test_export_role_matrix_allows_expected_types(client, role, export_type):
    _login(client, _seed_staff(client, role, f"allowed-{role.value}"))

    response = _post_export(client, export_type)

    assert response.status_code == 202, response.text
    assert response.json()["export_type"] == export_type.value


@pytest.mark.parametrize(
    ("role", "export_type"),
    [
        (Role.FINANCE, ExportType.PUBLICATIONS),
        (Role.MANAGER, ExportType.PAYOUT_REGISTER),
        (Role.MODERATOR, ExportType.BLOGGERS),
        (Role.ANALYST, ExportType.BLOGGERS),
        (Role.ANALYST, ExportType.AUDIT_LOG),
    ],
)
def test_export_role_matrix_rejects_sensitive_types(client, role, export_type):
    _login(client, _seed_staff(client, role, f"denied-{role.value}"))

    response = _post_export(client, export_type)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "EXPORT_FORBIDDEN"


def test_every_export_type_runs_through_worker_with_code_generated_csv(client):
    _login(client, _seed_staff(client, Role.ADMIN, "all-types"))
    store = MemoryArtifactStore()

    for export_type in ExportType:
        created = _post_export(client, export_type)
        assert created.status_code == 202, created.text
        producer = CapturingProducer()
        assert dispatch_export(
            session_factory=client.app.state.test_session,
            task_producer=producer,
        )
        execute_export(producer.command, client.app.state.test_session, store)

        with client.app.state.test_session() as db:
            export_id = uuid.UUID(created.json()["id"])
            job = db.scalar(select(ExportJob).where(ExportJob.id == export_id))
            assert job.status.value == "ready", (export_type, job.last_error_code)
            assert job.artifact_key.startswith("exports/")
            assert job.row_count >= 0
            content = store.objects[job.artifact_key][0]
            assert content.startswith(b"\xef\xbb\xbf")

    deleted = cleanup_expired_artifacts(
        client.app.state.test_session,
        store,
        now=utc_now() + timedelta(days=366),
    )
    assert deleted == len(ExportType)
    assert store.objects == {}
    with client.app.state.test_session() as db:
        jobs = list(db.scalars(select(ExportJob)))
        assert all(job.status.value == "expired" for job in jobs)
        assert all(job.artifact_deleted_at is not None for job in jobs)


def test_general_export_validates_type_specific_filters(client):
    _login(client, _seed_staff(client, Role.ADMIN, "filter-validation"))
    payload = _payload(ExportType.BLOGGERS)
    payload["filters"]["platform"] = "vk"

    response = client.post(
        "/api/v1/staff/exports",
        headers={"X-CSRF-Token": client.cookies.get("amp_csrf")},
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXPORT_FILTER_NOT_SUPPORTED"


def test_every_xlsx_template_is_generated_from_versioned_code(tmp_path):
    for export_type in ExportType:
        sheet_name, columns = schema_for(export_type, 1)
        assert sheet_name
        assert columns
        destination = tmp_path / f"{export_type.value}.xlsx"
        generate_export(
            export_type=export_type,
            export_format=ExportFormat.XLSX,
            schema_version=1,
            rows=[],
            destination=destination,
        )
        assert zipfile.is_zipfile(destination)


def test_analyst_templates_exclude_direct_contact_bank_and_ip_fields():
    forbidden = {"phone", "telegram", "sbp_phone", "bank_name", "ip_address"}
    for export_type in (
        ExportType.PUBLICATIONS,
        ExportType.VIEW_READINGS,
        ExportType.ACCRUALS,
    ):
        _, columns = schema_for(export_type, 1)
        assert forbidden.isdisjoint(column.key for column in columns)


def test_legacy_payout_export_routes_do_not_exist(client):
    assert client.get("/api/v1/staff/payout-exports").status_code == 404
    assert client.post("/api/v1/staff/payout-exports", json={}).status_code == 404
