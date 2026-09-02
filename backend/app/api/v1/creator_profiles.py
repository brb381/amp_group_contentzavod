import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_csrf, require_roles
from app.auth.models import Role, User
from app.creators.models import CreatorProfile, ProfileStatus
from app.creators.schemas import (
    ModerationReviewRequest,
    ProfileEnvelope,
    ProfileListResponse,
    ProfileResponse,
    ProfileSummaryResponse,
    ProfileUpsertRequest,
    SocialAccountCreateRequest,
    SocialAccountResponse,
    SocialAccountUpdateRequest,
)
from app.creators.service import (
    add_social_account,
    delete_social_account,
    get_user_profile,
    list_social_accounts,
    profile_response,
    review_profile,
    submit_profile,
    update_social_account,
    upsert_profile,
)
from app.database.session import get_db
from app.errors import APIError
from app.legal.dependencies import CurrentParticipant


router = APIRouter(tags=["creator profiles"])
Moderator = Annotated[User, Depends(require_roles(Role.MODERATOR, Role.ADMIN))]


@router.get("/me/profile", response_model=ProfileEnvelope)
def get_my_profile(user: CurrentUser, db: Session = Depends(get_db, scope="function")) -> ProfileEnvelope:
    profile = get_user_profile(db, user.id)
    return ProfileEnvelope(profile=profile_response(db, profile) if profile else None)


@router.put("/me/profile", response_model=ProfileResponse)
def put_my_profile(
    payload: ProfileUpsertRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ProfileResponse:
    return profile_response(db, upsert_profile(db, user, payload, context_from_request(request)))


@router.get("/me/social-accounts", response_model=list[SocialAccountResponse])
def get_my_social_accounts(user: CurrentUser, db: Session = Depends(get_db, scope="function")) -> list[SocialAccountResponse]:
    return list_social_accounts(db, user)


@router.post("/me/social-accounts", response_model=SocialAccountResponse, status_code=status.HTTP_201_CREATED)
def post_my_social_account(
    payload: SocialAccountCreateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> SocialAccountResponse:
    return add_social_account(db, user, payload, context_from_request(request))


@router.patch("/me/social-accounts/{account_id}", response_model=SocialAccountResponse)
def patch_my_social_account(
    account_id: uuid.UUID,
    payload: SocialAccountUpdateRequest,
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> SocialAccountResponse:
    return update_social_account(db, user, account_id, payload, context_from_request(request))


@router.delete("/me/social-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_my_social_account(
    account_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    delete_social_account(db, user, account_id, context_from_request(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/profile-submissions", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def post_profile_submission(
    request: Request,
    user: CurrentParticipant,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ProfileResponse:
    return profile_response(db, submit_profile(db, user, context_from_request(request)))


@router.get("/moderation/profiles", response_model=ProfileListResponse)
def get_moderation_profiles(
    _: Moderator,
    profile_status: ProfileStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> ProfileListResponse:
    filters = [CreatorProfile.status == profile_status] if profile_status else []
    total = db.scalar(select(func.count()).select_from(CreatorProfile).where(*filters)) or 0
    profiles = list(
        db.scalars(
            select(CreatorProfile)
            .where(*filters)
            .order_by(CreatorProfile.submitted_at.desc().nullslast(), CreatorProfile.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return ProfileListResponse(
        items=[ProfileSummaryResponse.model_validate(profile) for profile in profiles],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


@router.get("/moderation/profiles/{profile_id}", response_model=ProfileResponse)
def get_moderation_profile(
    profile_id: uuid.UUID, _: Moderator, db: Session = Depends(get_db, scope="function")
) -> ProfileResponse:
    profile = db.get(CreatorProfile, profile_id)
    if not profile:
        raise APIError(404, "PROFILE_NOT_FOUND", "Profile was not found")
    return profile_response(db, profile)


@router.post("/moderation/profiles/{profile_id}/reviews", response_model=ProfileResponse)
def post_moderation_review(
    profile_id: uuid.UUID,
    payload: ModerationReviewRequest,
    request: Request,
    reviewer: Moderator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ProfileResponse:
    return profile_response(
        db,
        review_profile(db, profile_id, reviewer, payload, context_from_request(request)),
    )
