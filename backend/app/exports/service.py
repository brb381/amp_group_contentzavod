import hashlib
import json
import math
import uuid
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.clock import utc_now
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.exports.models import ExportJob, ExportJobStatus, ExportType
from app.exports.schemas import (
    ExportCreateRequest,
    ExportJobListResponse,
    ExportJobResponse,
    PayoutExportFilters,
)


EXPORT_SCHEMA_VERSIONS = {
    ExportType.PAYOUT_REGISTER: 1,
    ExportType.PAYOUT_HISTORY: 1,
}


def _aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _payload_hash(payload: ExportCreateRequest, schema_version: int) -> str:
    canonical = {
        "schema_version": schema_version,
        "export_type": payload.export_type.value,
        "format": payload.format.value,
        "filters": payload.filters.model_dump(mode="json", exclude_none=True),
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_idempotency_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint_name == "uq_export_jobs_requester_idempotency":
        return True
    return (
        "unique constraint failed: export_jobs.requested_by_user_id, "
        "export_jobs.idempotency_key"
    ) in str(error.orig).lower()


def _lock_actor(db: Session, actor: User) -> User:
    set_transaction_timeouts(db, lock_timeout_ms=5_000, statement_timeout_ms=30_000)
    locked = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked or locked.status != AccountStatus.ACTIVE:
        raise APIError(403, "EXPORT_PERMISSION_CHANGED", "Export permission changed")
    return locked


def _authorize_export(actor: User, export_type: ExportType) -> None:
    if export_type == ExportType.PAYOUT_HISTORY:
        allowed = actor.role == Role.ADMIN
    else:
        allowed = actor.role in {Role.FINANCE, Role.ADMIN}
    if not allowed:
        raise APIError(403, "EXPORT_FORBIDDEN", "You cannot create this export")


def _authorize_job(actor: User, job: ExportJob) -> None:
    if actor.status != AccountStatus.ACTIVE or actor.role not in {Role.FINANCE, Role.ADMIN}:
        raise APIError(403, "EXPORT_FORBIDDEN", "Export access is required")
    if actor.role != Role.ADMIN and (
        job.requested_by_user_id != actor.id
        or job.export_type != ExportType.PAYOUT_REGISTER
    ):
        raise APIError(404, "EXPORT_NOT_FOUND", "Export was not found")


def _response(job: ExportJob) -> ExportJobResponse:
    ready = (
        job.status == ExportJobStatus.READY
        and job.expires_at
        and _aware(job.expires_at) > utc_now()
    )
    return ExportJobResponse(
        id=job.id,
        export_type=job.export_type,
        format=job.export_format,
        schema_version=job.schema_version,
        status=job.status,
        filters=PayoutExportFilters.model_validate(job.filters),
        attempt_count=job.attempt_count,
        row_count=job.row_count,
        file_size=job.file_size,
        content_sha256=job.content_sha256,
        data_as_of=job.data_as_of,
        expires_at=job.expires_at,
        error_code=job.last_error_code,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        download_url=f"/api/v1/staff/payout-exports/{job.id}/download" if ready else None,
    )


def create_export_job(
    db: Session,
    *,
    actor: User,
    payload: ExportCreateRequest,
    audit_context: AuditContext,
) -> ExportJobResponse:
    locked_actor = _lock_actor(db, actor)
    _authorize_export(locked_actor, payload.export_type)
    if (
        payload.export_type == ExportType.PAYOUT_REGISTER
        and payload.filters.status not in (None, "approved")
    ):
        raise APIError(
            422,
            "EXPORT_FILTER_NOT_SUPPORTED",
            "The payout register always contains approved requests only",
        )
    schema_version = EXPORT_SCHEMA_VERSIONS[payload.export_type]
    existing = db.scalar(
        select(ExportJob).where(
            ExportJob.requested_by_user_id == locked_actor.id,
            ExportJob.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        if existing.payload_hash != _payload_hash(payload, existing.schema_version):
            raise APIError(
                409,
                "EXPORT_IDEMPOTENCY_CONFLICT",
                "Idempotency key was used for another export request",
            )
        return _response(existing)

    now = utc_now()
    job = ExportJob(
        requested_by_user_id=locked_actor.id,
        export_type=payload.export_type,
        export_format=payload.format,
        schema_version=schema_version,
        filters=payload.filters.model_dump(mode="json", exclude_none=True),
        payload_hash=_payload_hash(payload, schema_version),
        idempotency_key=payload.idempotency_key,
        status=ExportJobStatus.PENDING,
        available_at=now,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError as error:
        if not _is_idempotency_conflict(error):
            raise
        db.rollback()
        existing = db.scalar(
            select(ExportJob).where(
                ExportJob.requested_by_user_id == actor.id,
                ExportJob.idempotency_key == payload.idempotency_key,
            )
        )
        if existing and existing.payload_hash == _payload_hash(payload, existing.schema_version):
            return _response(existing)
        raise APIError(
            409,
            "EXPORT_IDEMPOTENCY_CONFLICT",
            "Idempotency key was used for another export request",
        ) from error
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PAYOUT_EXPORT_REQUESTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="export_job",
        object_id=job.id,
        metadata={
            "export_type": job.export_type.value,
            "format": job.export_format.value,
            "schema_version": job.schema_version,
            "filters": job.filters,
        },
    )
    return _response(job)


def get_export_job(db: Session, *, actor: User, export_id: uuid.UUID) -> ExportJob:
    job = db.get(ExportJob, export_id)
    if not job:
        raise APIError(404, "EXPORT_NOT_FOUND", "Export was not found")
    _authorize_job(actor, job)
    if (
        job.status == ExportJobStatus.READY
        and job.expires_at
        and _aware(job.expires_at) <= utc_now()
    ):
        job.status = ExportJobStatus.EXPIRED
    return job


def get_export_job_response(
    db: Session, *, actor: User, export_id: uuid.UUID
) -> ExportJobResponse:
    return _response(get_export_job(db, actor=actor, export_id=export_id))


def list_export_jobs(
    db: Session,
    *,
    actor: User,
    page: int,
    page_size: int,
) -> ExportJobListResponse:
    if actor.status != AccountStatus.ACTIVE or actor.role not in {Role.FINANCE, Role.ADMIN}:
        raise APIError(403, "EXPORT_FORBIDDEN", "Export access is required")
    filters = []
    if actor.role != Role.ADMIN:
        filters.extend(
            (
                ExportJob.requested_by_user_id == actor.id,
                ExportJob.export_type == ExportType.PAYOUT_REGISTER,
            )
        )
    total = db.scalar(select(func.count()).select_from(ExportJob).where(*filters)) or 0
    jobs = list(
        db.scalars(
            select(ExportJob)
            .where(*filters)
            .order_by(ExportJob.created_at.desc(), ExportJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return ExportJobListResponse(
        items=[_response(job) for job in jobs],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )
