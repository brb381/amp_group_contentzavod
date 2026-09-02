import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_csrf
from app.content.publication_service import (
    create_publication,
    delete_publication,
    get_publication,
    list_publications,
    publication_detail,
    submit_publication,
    update_publication,
)
from app.content.schemas import (
    PublicationCreateRequest,
    PublicationDetailResponse,
    PublicationListResponse,
    PublicationUpdateRequest,
)
from app.database.session import get_db
from app.legal.dependencies import CurrentParticipant


router = APIRouter(tags=["publications"])


@router.get(
    "/me/video-cards/{card_id}/publications",
    response_model=PublicationListResponse,
)
def get_my_publications(
    card_id: uuid.UUID,
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationListResponse:
    publications, total, total_pages = list_publications(
        db, user=user, card_id=card_id, page=page, page_size=page_size
    )
    return PublicationListResponse(
        items=publications,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
    )


@router.post(
    "/me/video-cards/{card_id}/publications",
    response_model=PublicationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_my_publication(
    card_id: uuid.UUID,
    payload: PublicationCreateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationDetailResponse:
    publication = create_publication(
        db,
        actor=user,
        card_id=card_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return publication_detail(db, publication)


@router.get("/me/publications/{publication_id}", response_model=PublicationDetailResponse)
def get_my_publication(
    publication_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> PublicationDetailResponse:
    return publication_detail(db, get_publication(db, user=user, publication_id=publication_id))


@router.patch("/me/publications/{publication_id}", response_model=PublicationDetailResponse)
def patch_my_publication(
    publication_id: uuid.UUID,
    payload: PublicationUpdateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationDetailResponse:
    publication = update_publication(
        db,
        actor=user,
        publication_id=publication_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return publication_detail(db, publication)


@router.delete("/me/publications/{publication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_publication(
    publication_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    delete_publication(
        db,
        actor=user,
        publication_id=publication_id,
        audit_context=context_from_request(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/publications/{publication_id}/submissions",
    response_model=PublicationDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_my_publication_submission(
    publication_id: uuid.UUID,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationDetailResponse:
    publication = submit_publication(
        db,
        actor=user,
        publication_id=publication_id,
        audit_context=context_from_request(request),
    )
    return publication_detail(db, publication)
