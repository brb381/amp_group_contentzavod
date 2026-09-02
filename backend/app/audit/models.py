import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, JSON, String, Uuid, event, func
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


class SecurityEvent(Base):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_security_events_actor_time", "actor_user_id", "occurred_at"),
        Index("ix_security_events_object", "object_type", "object_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)


def _reject_mutation(mapper: Mapper, connection: object, target: SecurityEvent) -> None:
    del mapper, connection, target
    raise ValueError("Security events are append-only")


event.listen(SecurityEvent, "before_update", _reject_mutation)
event.listen(SecurityEvent, "before_delete", _reject_mutation)
