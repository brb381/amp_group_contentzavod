import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.database.session import get_db
from app.legal.models import LegalDocumentType
from app.legal.schemas import (
    CurrentLegalDocumentsResponse,
    LegalAcceptanceCreateRequest,
    LegalAcceptanceListResponse,
    LegalAcceptanceResponse,
    LegalDocumentListResponse,
    LegalDocumentPublishRequest,
    LegalDocumentResponse,
    MyLegalStatusResponse,
)
from app.legal.service import (
    accept_current_document,
    get_document,
    get_current_documents,
    list_documents,
    list_acceptance_history,
    my_legal_status,
    publish_document,
    withdraw_personal_data_consent,
)


router = APIRouter(tags=["legal documents"])
Administrator = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("/legal-documents/current", response_model=CurrentLegalDocumentsResponse)
def get_current_legal_documents(
    db: Session = Depends(get_db, scope="function"),
) -> CurrentLegalDocumentsResponse:
    documents = get_current_documents(db)
    return CurrentLegalDocumentsResponse(
        program_terms=documents[LegalDocumentType.PROGRAM_TERMS],
        personal_data_consent=documents[LegalDocumentType.PERSONAL_DATA_CONSENT],
        privacy_policy=documents[LegalDocumentType.PRIVACY_POLICY],
    )


@router.get("/legal-documents/{document_id}", response_model=LegalDocumentResponse)
def get_legal_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db, scope="function"),
) -> LegalDocumentResponse:
    return get_document(db, document_id=document_id)


@router.get("/me/legal-status", response_model=MyLegalStatusResponse)
def get_my_legal_status(
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> MyLegalStatusResponse:
    return my_legal_status(db, user_id=user.id)


@router.get("/me/legal-acceptances", response_model=LegalAcceptanceListResponse)
def get_my_legal_acceptances(
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> LegalAcceptanceListResponse:
    items, total, total_pages = list_acceptance_history(
        db,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )
    return LegalAcceptanceListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
    )


@router.post(
    "/me/legal-acceptances",
    response_model=LegalAcceptanceResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_my_legal_acceptance(
    payload: LegalAcceptanceCreateRequest,
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> LegalAcceptanceResponse:
    return accept_current_document(
        db,
        actor=user,
        document_id=payload.document_id,
        audit_context=context_from_request(request),
    )


@router.post(
    "/me/personal-data-consent-withdrawals",
    response_model=LegalAcceptanceResponse,
)
def post_personal_data_consent_withdrawal(
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> LegalAcceptanceResponse:
    return withdraw_personal_data_consent(
        db,
        actor=user,
        audit_context=context_from_request(request),
    )


@router.get("/admin/legal-documents", response_model=LegalDocumentListResponse)
def get_admin_legal_documents(
    _: Administrator,
    document_type: LegalDocumentType | None = Query(default=None, alias="documentType"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> LegalDocumentListResponse:
    items, total, total_pages = list_documents(
        db,
        document_type=document_type,
        page=page,
        page_size=page_size,
    )
    return LegalDocumentListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
    )


@router.post(
    "/admin/legal-documents",
    response_model=LegalDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_admin_legal_document(
    payload: LegalDocumentPublishRequest,
    request: Request,
    administrator: Administrator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> LegalDocumentResponse:
    return publish_document(
        db,
        actor=administrator,
        payload=payload,
        audit_context=context_from_request(request),
    )
