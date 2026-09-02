import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import require_active_roles, require_roles
from app.auth.models import Role, User
from app.database.session import get_db
from app.lifecycle.schemas import AccountLifecycleResponse
from app.lifecycle.service import get_account_lifecycle


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    tags=["account-lifecycle"], dependencies=[Depends(_set_private_no_store)]
)
BloggerViewer = Annotated[User, Depends(require_roles(Role.BLOGGER))]
LifecycleReviewer = Annotated[
    User, Depends(require_active_roles(Role.MODERATOR, Role.ADMIN))
]


@router.get("/me/account-lifecycle", response_model=AccountLifecycleResponse)
def get_my_account_lifecycle(
    actor: BloggerViewer,
    db: Session = Depends(get_db, scope="function"),
) -> AccountLifecycleResponse:
    return get_account_lifecycle(db, blogger_id=actor.id)


@router.get(
    "/staff/users/{blogger_id}/account-lifecycle",
    response_model=AccountLifecycleResponse,
)
def get_staff_account_lifecycle(
    blogger_id: uuid.UUID,
    _: LifecycleReviewer,
    db: Session = Depends(get_db, scope="function"),
) -> AccountLifecycleResponse:
    return get_account_lifecycle(db, blogger_id=blogger_id)
