import hashlib
import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import utc_now
from app.errors import APIError
from app.legal.models import (
    AcceptanceMethod,
    LegalAcceptance,
    LegalDocument,
    LegalDocumentType,
)
from app.legal.schemas import (
    LegalAcceptanceHistoryItemResponse,
    LegalDocumentPublishRequest,
    MyLegalStatusResponse,
    RequiredLegalAcceptanceResponse,
)


ACCEPTED_DOCUMENT_TYPES = {
    LegalDocumentType.PROGRAM_TERMS,
    LegalDocumentType.PERSONAL_DATA_CONSENT,
}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context_hash(value: str | None) -> str | None:
    return _sha256(value.strip()) if value and value.strip() else None


def get_current_document(
    db: Session, document_type: LegalDocumentType
) -> LegalDocument | None:
    return db.scalar(
        select(LegalDocument).where(
            LegalDocument.document_type == document_type,
            LegalDocument.is_current.is_(True),
        )
    )


def get_current_documents(
    db: Session, *, lock_for_acceptance: bool = False
) -> dict[LegalDocumentType, LegalDocument]:
    statement = select(LegalDocument).where(LegalDocument.is_current.is_(True))
    if lock_for_acceptance:
        statement = statement.with_for_update(read=True)
    documents = {
        document.document_type: document
        for document in db.scalars(statement)
    }
    missing = [item.value for item in LegalDocumentType if item not in documents]
    if missing:
        raise APIError(
            503,
            "LEGAL_DOCUMENTS_NOT_CONFIGURED",
            "Current legal documents have not been configured",
            {"missing_document_types": missing},
        )
    return documents


def _active_acceptance(
    db: Session, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> LegalAcceptance | None:
    return db.scalar(
        select(LegalAcceptance).where(
            LegalAcceptance.user_id == user_id,
            LegalAcceptance.document_id == document_id,
            LegalAcceptance.withdrawn_at.is_(None),
        )
    )


def _record_acceptance(
    db: Session,
    *,
    user: User,
    document: LegalDocument,
    method: AcceptanceMethod,
    audit_context: AuditContext,
) -> LegalAcceptance:
    if document.document_type not in ACCEPTED_DOCUMENT_TYPES:
        raise APIError(422, "LEGAL_DOCUMENT_NOT_ACCEPTABLE", "This document is informational")
    existing = _active_acceptance(db, user_id=user.id, document_id=document.id)
    if existing:
        return existing
    acceptance = LegalAcceptance(
        user_id=user.id,
        document_id=document.id,
        method=method,
        request_id=audit_context.request_id,
        ip_hash=_context_hash(audit_context.ip_address) or _sha256("unknown"),
        user_agent_hash=_context_hash(audit_context.user_agent),
    )
    try:
        with db.begin_nested():
            db.add(acceptance)
            db.flush()
    except IntegrityError as error:
        existing = _active_acceptance(db, user_id=user.id, document_id=document.id)
        constraint = getattr(getattr(error, "orig", None), "diag", None)
        sqlite_unique = (
            "legal_acceptances.user_id, legal_acceptances.document_id" in str(error)
        )
        if (
            getattr(constraint, "constraint_name", None)
            != "uq_legal_acceptances_active_user_document"
            and not sqlite_unique
        ) or not existing:
            raise
        return existing
    record_event(
        db,
        context=audit_context,
        action=AuditAction.LEGAL_DOCUMENT_ACCEPTED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="legal_document",
        object_id=document.id,
        metadata={"document_type": document.document_type.value, "version": document.version},
    )
    return acceptance


def accept_registration_documents(
    db: Session,
    *,
    user: User,
    program_terms_document_id: uuid.UUID,
    personal_data_consent_document_id: uuid.UUID,
    audit_context: AuditContext,
) -> tuple[LegalAcceptance, LegalAcceptance]:
    documents = get_current_documents(db, lock_for_acceptance=True)
    program_terms = documents[LegalDocumentType.PROGRAM_TERMS]
    personal_data_consent = documents[LegalDocumentType.PERSONAL_DATA_CONSENT]
    if (
        program_terms.id != program_terms_document_id
        or personal_data_consent.id != personal_data_consent_document_id
    ):
        raise APIError(
            409,
            "LEGAL_DOCUMENT_VERSION_OUTDATED",
            "The legal documents changed; review the current versions",
            {
                "program_terms_document_id": str(program_terms.id),
                "personal_data_consent_document_id": str(personal_data_consent.id),
            },
        )
    return (
        _record_acceptance(
            db,
            user=user,
            document=program_terms,
            method=AcceptanceMethod.REGISTRATION,
            audit_context=audit_context,
        ),
        _record_acceptance(
            db,
            user=user,
            document=personal_data_consent,
            method=AcceptanceMethod.REGISTRATION,
            audit_context=audit_context,
        ),
    )


def publish_document(
    db: Session,
    *,
    actor: User,
    payload: LegalDocumentPublishRequest,
    audit_context: AuditContext,
) -> LegalDocument:
    locked_actor = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        not locked_actor
        or locked_actor.role != Role.ADMIN
        or locked_actor.status != AccountStatus.ACTIVE
    ):
        raise APIError(403, "LEGAL_DOCUMENT_PUBLISH_FORBIDDEN", "Administrator access is required")
    current = db.scalar(
        select(LegalDocument)
        .where(
            LegalDocument.document_type == payload.document_type,
            LegalDocument.is_current.is_(True),
        )
        .with_for_update()
    )
    latest_revision = db.scalar(
        select(func.max(LegalDocument.revision)).where(
            LegalDocument.document_type == payload.document_type
        )
    ) or 0
    version_exists = db.scalar(
        select(LegalDocument.id).where(
            LegalDocument.document_type == payload.document_type,
            LegalDocument.version == payload.version,
        )
    )
    if version_exists:
        raise APIError(409, "LEGAL_DOCUMENT_VERSION_EXISTS", "Document version already exists")
    if not current and payload.document_type in ACCEPTED_DOCUMENT_TYPES and not payload.requires_reacceptance:
        raise APIError(
            422,
            "INITIAL_LEGAL_ACCEPTANCE_REQUIRED",
            "The first version of this document must require acceptance",
        )
    revision = latest_revision + 1
    if current:
        current.is_current = False
    document = LegalDocument(
        document_type=payload.document_type,
        version=payload.version,
        revision=revision,
        title=payload.title.strip(),
        content_markdown=payload.content_markdown,
        content_sha256=_sha256(payload.content_markdown),
        is_current=True,
        requires_reacceptance=payload.requires_reacceptance,
        change_summary=payload.change_summary.strip() if payload.change_summary else None,
        published_by_user_id=actor.id,
    )
    db.add(document)
    try:
        db.flush()
    except IntegrityError as error:
        constraint = getattr(getattr(error, "orig", None), "diag", None)
        sqlite_unique = "UNIQUE constraint failed: legal_documents." in str(error)
        if getattr(constraint, "constraint_name", None) not in {
            "uq_legal_documents_type_version",
            "uq_legal_documents_type_revision",
            "uq_legal_documents_current_type",
        } and not sqlite_unique:
            raise
        raise APIError(
            409,
            "LEGAL_DOCUMENT_CONCURRENT_CHANGE",
            "The document registry changed; reload it and retry",
        ) from error
    record_event(
        db,
        context=audit_context,
        action=AuditAction.LEGAL_DOCUMENT_PUBLISHED,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="legal_document",
        object_id=document.id,
        metadata={
            "document_type": document.document_type.value,
            "version": document.version,
            "requires_reacceptance": document.requires_reacceptance,
        },
    )
    return document


def list_documents(
    db: Session,
    *,
    document_type: LegalDocumentType | None,
    page: int,
    page_size: int,
) -> tuple[list[LegalDocument], int, int]:
    filters = [LegalDocument.document_type == document_type] if document_type else []
    total = db.scalar(select(func.count()).select_from(LegalDocument).where(*filters)) or 0
    items = list(
        db.scalars(
            select(LegalDocument)
            .where(*filters)
            .order_by(LegalDocument.published_at.desc(), LegalDocument.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total, math.ceil(total / page_size)


def get_document(db: Session, *, document_id: uuid.UUID) -> LegalDocument:
    document = db.get(LegalDocument, document_id)
    if not document:
        raise APIError(404, "LEGAL_DOCUMENT_NOT_FOUND", "Legal document was not found")
    return document


def list_acceptance_history(
    db: Session, *, user_id: uuid.UUID, page: int, page_size: int
) -> tuple[list[LegalAcceptanceHistoryItemResponse], int, int]:
    total = db.scalar(
        select(func.count())
        .select_from(LegalAcceptance)
        .where(LegalAcceptance.user_id == user_id)
    ) or 0
    rows = db.execute(
        select(LegalAcceptance, LegalDocument)
        .join(LegalDocument, LegalDocument.id == LegalAcceptance.document_id)
        .where(LegalAcceptance.user_id == user_id)
        .order_by(LegalAcceptance.accepted_at.desc(), LegalAcceptance.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = [
        LegalAcceptanceHistoryItemResponse(
            id=acceptance.id,
            document_id=document.id,
            method=acceptance.method,
            accepted_at=acceptance.accepted_at,
            withdrawn_at=acceptance.withdrawn_at,
            document_type=document.document_type,
            document_version=document.version,
            document_title=document.title,
            document_content_sha256=document.content_sha256,
        )
        for acceptance, document in rows
    ]
    return items, total, math.ceil(total / page_size)


def _required_documents(db: Session) -> list[LegalDocument]:
    documents: list[LegalDocument] = []
    for document_type in ACCEPTED_DOCUMENT_TYPES:
        document = db.scalar(
            select(LegalDocument)
            .where(
                LegalDocument.document_type == document_type,
                LegalDocument.requires_reacceptance.is_(True),
            )
            .order_by(LegalDocument.revision.desc())
            .limit(1)
        )
        if document:
            documents.append(document)
        elif not get_current_document(db, document_type):
            raise APIError(
                503,
                "LEGAL_DOCUMENTS_NOT_CONFIGURED",
                "Required legal documents have not been configured",
                {"missing_document_types": [document_type.value]},
            )
    return documents


def missing_required_documents(db: Session, *, user_id: uuid.UUID) -> list[LegalDocument]:
    missing: list[LegalDocument] = []
    for required in _required_documents(db):
        accepted_revision = db.scalar(
            select(func.max(LegalDocument.revision))
            .select_from(LegalAcceptance)
            .join(LegalDocument, LegalDocument.id == LegalAcceptance.document_id)
            .where(
                LegalAcceptance.user_id == user_id,
                LegalAcceptance.withdrawn_at.is_(None),
                LegalDocument.document_type == required.document_type,
            )
        )
        if accepted_revision is None or accepted_revision < required.revision:
            missing.append(get_current_document(db, required.document_type) or required)
    return sorted(missing, key=lambda item: item.document_type.value)


def my_legal_status(db: Session, *, user_id: uuid.UUID) -> MyLegalStatusResponse:
    missing = missing_required_documents(db, user_id=user_id)
    return MyLegalStatusResponse(
        is_participation_allowed=not missing,
        required_acceptances=[
            RequiredLegalAcceptanceResponse(
                document_id=document.id,
                document_type=document.document_type,
                version=document.version,
            )
            for document in missing
        ],
    )


def accept_current_document(
    db: Session,
    *,
    actor: User,
    document_id: uuid.UUID,
    audit_context: AuditContext,
) -> LegalAcceptance:
    document = db.scalar(
        select(LegalDocument)
        .where(LegalDocument.id == document_id)
        .with_for_update(read=True)
    )
    if not document or not document.is_current:
        current = get_current_document(db, document.document_type) if document else None
        raise APIError(
            409,
            "LEGAL_DOCUMENT_VERSION_OUTDATED",
            "Only the current document can be accepted",
            {"current_document_id": str(current.id) if current else None},
        )
    return _record_acceptance(
        db,
        user=actor,
        document=document,
        method=AcceptanceMethod.AUTHENTICATED_CLICK,
        audit_context=audit_context,
    )


def withdraw_personal_data_consent(
    db: Session, *, actor: User, audit_context: AuditContext
) -> LegalAcceptance:
    acceptances = list(
        db.scalars(
            select(LegalAcceptance)
            .join(LegalDocument, LegalDocument.id == LegalAcceptance.document_id)
            .where(
                LegalAcceptance.user_id == actor.id,
                LegalAcceptance.withdrawn_at.is_(None),
                LegalDocument.document_type == LegalDocumentType.PERSONAL_DATA_CONSENT,
            )
            .order_by(LegalAcceptance.accepted_at.desc(), LegalAcceptance.id.desc())
            .with_for_update()
        )
    )
    if not acceptances:
        raise APIError(409, "PERSONAL_DATA_CONSENT_NOT_ACTIVE", "No active consent exists")
    withdrawn_at = utc_now()
    for acceptance in acceptances:
        acceptance.withdrawn_at = withdrawn_at
    latest_acceptance = acceptances[0]
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PERSONAL_DATA_CONSENT_WITHDRAWN,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="legal_acceptance",
        object_id=latest_acceptance.id,
        metadata={"withdrawn_acceptance_count": len(acceptances)},
    )
    return latest_acceptance
