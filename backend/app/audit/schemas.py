import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class SecurityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_role: str | None
    action: str
    result: str
    object_type: str | None
    object_id: uuid.UUID | None
    session_id: uuid.UUID | None
    request_id: str
    ip_address: str
    user_agent: str | None
    event_metadata: dict[str, Any]


class SecurityEventListResponse(BaseModel):
    items: list[SecurityEventResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
