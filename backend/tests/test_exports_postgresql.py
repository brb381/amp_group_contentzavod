import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.audit.service import AuditContext
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.contracts import ExportCommand
from app.database.factory import create_session_factory
from app.exports.config import ExportStorageSettings
from app.exports.models import ExportJob, ExportJobStatus
from app.exports.processor import execute_export
from app.exports.schemas import ExportCreateRequest
from app.exports.service import create_export_job
from app.exports.storage import S3ArtifactStore
from app.scheduling.exports import dispatch_export


def _session_factory(name: str) -> sessionmaker:
    url = os.getenv(name)
    if not url:
        pytest.skip(f"{name} is not configured")
    return create_session_factory(url)


def _store(prefix: str) -> S3ArtifactStore:
    endpoint = os.getenv("TEST_S3_ENDPOINT_URL")
    access_key = os.getenv(f"TEST_S3_{prefix}_ACCESS_KEY")
    secret_key = os.getenv(f"TEST_S3_{prefix}_SECRET_KEY")
    if not endpoint or not access_key or not secret_key:
        pytest.skip("S3 integration settings are not configured")
    return S3ArtifactStore(
        ExportStorageSettings(
            s3_endpoint_url=endpoint,
            s3_region="us-east-1",
            s3_bucket="private-exports",
            s3_access_key=access_key,
            s3_secret_key=secret_key,
        )
    )


class CapturingProducer:
    def __init__(self):
        self.payload = None

    def send_task(self, task_name, *, args, queue):
        del task_name, queue
        self.payload = args[0]


def test_runtime_roles_complete_empty_export_through_minio():
    owner_factory = _session_factory("TEST_DATABASE_URL")
    api_factory = _session_factory("TEST_API_DATABASE_URL")
    scheduler_factory = _session_factory("TEST_SCHEDULER_DATABASE_URL")
    worker_factory = _session_factory("TEST_EXPORT_DATABASE_URL")
    worker_store = _store("WORKER")
    api_store = _store("API")

    with owner_factory() as db:
        finance = User(
            email=f"export-runtime-{uuid.uuid4()}@example.com",
            password_hash=hash_password("runtime-export-password"),
            role=Role.FINANCE,
            status=AccountStatus.ACTIVE,
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add(finance)
        db.commit()
        finance_id = finance.id

    with api_factory() as db:
        actor = db.get(User, finance_id)
        created = create_export_job(
            db,
            actor=actor,
            payload=ExportCreateRequest.model_validate(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "export_type": "payout_register",
                    "format": "csv",
                    "filters": {
                        "requested_from": "2026-08-01",
                        "requested_to": "2026-08-31",
                    },
                }
            ),
            audit_context=AuditContext(
                request_id="postgres-export-e2e",
                ip_address="127.0.0.1",
                user_agent="pytest",
            ),
        )
        db.commit()
        export_id = created.id

    producer = CapturingProducer()
    assert dispatch_export(
        session_factory=scheduler_factory,
        task_producer=producer,
    )
    command = ExportCommand.model_validate(producer.payload)
    execute_export(command, worker_factory, worker_store)

    with api_factory() as db:
        job = db.get(ExportJob, export_id)
        assert job.status == ExportJobStatus.READY
        assert job.row_count == 0
        assert job.content_sha256
        artifact_key = job.artifact_key

    artifact = api_store.download(key=artifact_key)
    content = b"".join(artifact.chunks()).decode("utf-8-sig")
    assert content.startswith("Номер заявки;Получатель;")
    worker_store.delete(key=artifact_key)
