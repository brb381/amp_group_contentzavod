import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.audit.service import AuditAction, record_event
from app.auth.dependencies import require_active_roles, require_csrf
from app.auth.models import Role, User
from app.database.session import get_db
from app.errors import APIError
from app.exports.config import get_export_storage_settings
from app.exports.models import ExportJobStatus
from app.exports.schemas import ExportCreateRequest, ExportJobListResponse, ExportJobResponse
from app.exports.service import (
    create_export_job,
    get_export_job,
    get_export_job_response,
    list_export_jobs,
)
from app.exports.storage import ArtifactStore, S3ArtifactStore


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["exports"], dependencies=[Depends(_set_private_no_store)])
Exporter = Annotated[
    User, Depends(require_active_roles(Role.FINANCE, Role.ADMIN))
]


@lru_cache
def get_artifact_store() -> ArtifactStore:
    return S3ArtifactStore(get_export_storage_settings())


@router.post(
    "/staff/payout-exports",
    response_model=ExportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_payout_export(
    payload: ExportCreateRequest,
    request: Request,
    actor: Exporter,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ExportJobResponse:
    return create_export_job(
        db,
        actor=actor,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.get("/staff/payout-exports", response_model=ExportJobListResponse)
def get_payout_exports(
    actor: Exporter,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db, scope="function"),
) -> ExportJobListResponse:
    if page < 1 or not 1 <= page_size <= 100:
        raise APIError(422, "INVALID_PAGINATION", "Invalid export pagination")
    return list_export_jobs(db, actor=actor, page=page, page_size=page_size)


@router.get("/staff/payout-exports/{export_id}", response_model=ExportJobResponse)
def get_payout_export(
    export_id: uuid.UUID,
    actor: Exporter,
    db: Session = Depends(get_db, scope="function"),
) -> ExportJobResponse:
    return get_export_job_response(db, actor=actor, export_id=export_id)


@router.get("/staff/payout-exports/{export_id}/download")
def download_payout_export(
    export_id: uuid.UUID,
    request: Request,
    actor: Exporter,
    store: ArtifactStore = Depends(get_artifact_store),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    job = get_export_job(db, actor=actor, export_id=export_id)
    if job.status != ExportJobStatus.READY:
        raise APIError(409, "EXPORT_NOT_READY", "Export is not ready for download")
    if not job.artifact_key or not job.filename or not job.content_type:
        raise APIError(503, "EXPORT_ARTIFACT_UNAVAILABLE", "Export artifact is unavailable")
    try:
        artifact = store.download(key=job.artifact_key)
    except Exception as error:
        raise APIError(
            503, "EXPORT_ARTIFACT_UNAVAILABLE", "Export artifact is unavailable"
        ) from error
    record_event(
        db,
        context=context_from_request(request),
        action=AuditAction.PAYOUT_EXPORT_DOWNLOADED,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="export_job",
        object_id=job.id,
        metadata={
            "export_type": job.export_type.value,
            "format": job.export_format.value,
            "row_count": job.row_count,
            "content_sha256": job.content_sha256,
        },
    )
    return StreamingResponse(
        artifact.chunks(),
        media_type=job.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{job.filename}"',
            "Content-Length": str(artifact.content_length),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
