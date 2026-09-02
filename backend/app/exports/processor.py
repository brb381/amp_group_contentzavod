import hashlib
import logging
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import utc_now
from app.contracts import ExportCommand
from app.exports.generator import generate_export
from app.exports.models import ExportFormat, ExportJob, ExportJobStatus, ExportType
from app.exports.payout_data import ExportTooLargeError, load_payout_export_rows
from app.exports.schemas import PayoutExportFilters
from app.exports.storage import ArtifactStore


logger = logging.getLogger(__name__)
MAX_EXPORT_ATTEMPTS = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_properties(job: ExportJob, dispatch_id: uuid.UUID, now: datetime):
    extension = job.export_format.value
    slug = "payout-register" if job.export_type == ExportType.PAYOUT_REGISTER else "payout-history"
    filename = f"{slug}-{now.date().isoformat()}-{str(job.id)[:8]}.{extension}"
    content_type = (
        "text/csv; charset=utf-8"
        if job.export_format == ExportFormat.CSV
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    key = f"payout-exports/{now:%Y/%m/%d}/{job.id}/{dispatch_id}.{extension}"
    return filename, content_type, key


def _claim(
    session_factory: sessionmaker[Session], command: ExportCommand
) -> ExportJob | None:
    now = utc_now()
    with session_factory() as db:
        job = db.scalar(
            select(ExportJob)
            .where(
                ExportJob.id == command.export_id,
                ExportJob.dispatch_id == command.dispatch_id,
                ExportJob.status == ExportJobStatus.QUEUED,
                ExportJob.lease_until >= now,
            )
            .with_for_update()
        )
        if not job:
            db.rollback()
            return None
        job.status = ExportJobStatus.PROCESSING
        job.started_at = now
        db.commit()
        db.expunge(job)
        return job


def _load_rows(
    session_factory: sessionmaker[Session], job: ExportJob
) -> tuple[list[dict], datetime]:
    with session_factory() as db:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        data_as_of = utc_now()
        filters = PayoutExportFilters.model_validate(job.filters)
        rows = load_payout_export_rows(
            db,
            export_type=job.export_type,
            filters=filters,
        )
        db.commit()
        return rows, data_as_of


def _mark_failure(
    session_factory: sessionmaker[Session],
    command: ExportCommand,
    *,
    error_code: str,
) -> None:
    now = utc_now()
    with session_factory() as db:
        job = db.scalar(
            select(ExportJob)
            .where(
                ExportJob.id == command.export_id,
                ExportJob.dispatch_id == command.dispatch_id,
                ExportJob.status == ExportJobStatus.PROCESSING,
            )
            .with_for_update()
        )
        if not job:
            return
        job.last_error_code = error_code
        job.lease_until = None
        job.dispatch_id = None
        if job.attempt_count >= MAX_EXPORT_ATTEMPTS or error_code == "export_too_large":
            job.status = ExportJobStatus.FAILED
        else:
            job.status = ExportJobStatus.RETRY_WAIT
            job.available_at = now + timedelta(minutes=min(15, job.attempt_count * 2))
        db.commit()


def execute_export(
    command: ExportCommand,
    session_factory: sessionmaker[Session],
    artifact_store: ArtifactStore,
    *,
    artifact_ttl_hours: int = 24,
) -> None:
    job = _claim(session_factory, command)
    if not job:
        return
    artifact_key = None
    try:
        rows, data_as_of = _load_rows(session_factory, job)
        filename, content_type, artifact_key = _artifact_properties(
            job, command.dispatch_id, data_as_of
        )
        with tempfile.TemporaryDirectory(prefix="amp-export-") as directory:
            path = Path(directory) / filename
            generate_export(
                export_type=job.export_type,
                export_format=job.export_format,
                schema_version=job.schema_version,
                rows=rows,
                destination=path,
            )
            content_sha256 = _sha256(path)
            file_size = path.stat().st_size
            artifact_store.upload(
                key=artifact_key,
                path=path,
                content_type=content_type,
                content_sha256=content_sha256,
            )

        completed_at = utc_now()
        with session_factory() as db:
            current = db.scalar(
                select(ExportJob)
                .where(
                    ExportJob.id == command.export_id,
                    ExportJob.dispatch_id == command.dispatch_id,
                    ExportJob.status == ExportJobStatus.PROCESSING,
                )
                .with_for_update()
            )
            if not current:
                artifact_store.delete(key=artifact_key)
                return
            current.status = ExportJobStatus.READY
            current.artifact_key = artifact_key
            current.filename = filename
            current.content_type = content_type
            current.content_sha256 = content_sha256
            current.file_size = file_size
            current.row_count = len(rows)
            current.data_as_of = data_as_of
            current.completed_at = completed_at
            current.expires_at = completed_at + timedelta(hours=artifact_ttl_hours)
            current.lease_until = None
            current.dispatch_id = None
            current.last_error_code = None
            db.commit()
    except ExportTooLargeError:
        _mark_failure(session_factory, command, error_code="export_too_large")
    except Exception:
        logger.exception(
            "Export generation failed",
            extra={"export_id": str(command.export_id), "dispatch_id": str(command.dispatch_id)},
        )
        _mark_failure(session_factory, command, error_code="export_generation_failed")
