from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.errors import APIError
from app.program.models import ProgramSettings
from app.program.schemas import ProgramSettingsResponse, ProgramSettingsUpdate


DEFAULTS = ProgramSettingsResponse(
    program_name="AMP Content Factory",
    suspicious_growth_threshold=500_000,
    random_review_percent=10,
    rejection_reasons=[],
)


def get_program_settings(db: Session) -> ProgramSettingsResponse:
    settings = db.get(ProgramSettings, 1)
    return ProgramSettingsResponse.model_validate(settings) if settings else DEFAULTS.model_copy()


def update_program_settings(
    db: Session, *, actor: User, payload: ProgramSettingsUpdate, audit_context: AuditContext
) -> ProgramSettingsResponse:
    locked_actor = db.scalar(select(User).where(User.id == actor.id).with_for_update())
    if not locked_actor or locked_actor.role != Role.ADMIN or locked_actor.status != AccountStatus.ACTIVE:
        raise APIError(403, "PROGRAM_SETTINGS_PERMISSION_CHANGED", "Program settings permissions changed; authenticate again")
    settings = db.scalar(select(ProgramSettings).where(ProgramSettings.id == 1).with_for_update())
    if not settings:
        settings = ProgramSettings(id=1)
        db.add(settings)
    changed_fields: list[str] = []
    for field, value in payload.model_dump().items():
        if getattr(settings, field, None) != value:
            setattr(settings, field, value)
            changed_fields.append(field)
    settings.updated_by_user_id = locked_actor.id
    record_event(
        db,
        context=audit_context,
        action=AuditAction.PROGRAM_SETTINGS_UPDATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="program_settings",
        metadata={"changed_fields": changed_fields},
    )
    db.flush()
    return ProgramSettingsResponse.model_validate(settings)

def suspicious_growth_threshold(db: Session, fallback: int) -> int:
    value = db.scalar(
        select(ProgramSettings.suspicious_growth_threshold).where(ProgramSettings.id == 1)
    )
    return value if value is not None else fallback

def random_review_percent(db: Session, fallback: int = 0) -> int:
    value = db.scalar(
        select(ProgramSettings.random_review_percent).where(ProgramSettings.id == 1)
    )
    return value if value is not None else fallback
