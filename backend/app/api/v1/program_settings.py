from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.database.session import get_db
from app.program.schemas import ProgramSettingsResponse, ProgramSettingsUpdate
from app.program.service import get_program_settings, update_program_settings


router = APIRouter(prefix="/program-settings", tags=["program settings"])
Administrator = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("", response_model=ProgramSettingsResponse)
def get_settings(_: CurrentUser, db: Session = Depends(get_db, scope="function")) -> ProgramSettingsResponse:
    return get_program_settings(db)


@router.put("", response_model=ProgramSettingsResponse)
def put_settings(
    payload: ProgramSettingsUpdate,
    request: Request,
    administrator: Administrator,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ProgramSettingsResponse:
    return update_program_settings(
        db,
        actor=administrator,
        payload=payload,
        audit_context=context_from_request(request),
    )