import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.auth.models import AccountStatus
from app.lifecycle.models import ActivityKind


class AccountLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blogger_id: uuid.UUID
    account_status: AccountStatus
    last_activity_at: datetime
    last_activity_kind: ActivityKind
    next_transition: Literal["suspension", "blocking"] | None
    next_transition_at: datetime | None
    suspended_at: datetime | None
    restored_at: datetime | None
    blocked_at: datetime | None
    balance_claim_expired_at: datetime | None
