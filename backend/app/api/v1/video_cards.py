import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_csrf
from app.content.schemas import (
    VideoCardCreateRequest,
    VideoCardListResponse,
    VideoCardResponse,
    VideoCardUpdateRequest,
)
from app.content.service import (
    create_video_card,
    get_video_card,
    list_video_cards,
    publication_counts_by_card,
    update_video_card,
    video_card_response,
)
from app.database.session import get_db
from app.legal.dependencies import CurrentParticipant


router = APIRouter(prefix="/me/video-cards", tags=["video cards"])


@router.get("", response_model=VideoCardListResponse)
def get_my_video_cards(
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> VideoCardListResponse:
    cards, total, total_pages = list_video_cards(
        db, user=user, page=page, page_size=page_size
    )
    counts = publication_counts_by_card(db, [card.id for card in cards])
    return VideoCardListResponse(
        items=[video_card_response(card, counts.get(card.id)) for card in cards],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
    )


@router.post("", response_model=VideoCardResponse, status_code=status.HTTP_201_CREATED)
def post_my_video_card(
    payload: VideoCardCreateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> VideoCardResponse:
    card = create_video_card(
        db,
        actor=user,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return video_card_response(card)


@router.get("/{card_id}", response_model=VideoCardResponse)
def get_my_video_card(
    card_id: uuid.UUID,
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> VideoCardResponse:
    card = get_video_card(db, user=user, card_id=card_id)
    counts = publication_counts_by_card(db, [card.id])
    return video_card_response(card, counts.get(card.id))


@router.patch("/{card_id}", response_model=VideoCardResponse)
def patch_my_video_card(
    card_id: uuid.UUID,
    payload: VideoCardUpdateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> VideoCardResponse:
    card = update_video_card(
        db,
        actor=user,
        card_id=card_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    counts = publication_counts_by_card(db, [card.id])
    return video_card_response(card, counts.get(card.id))
