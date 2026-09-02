import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class NotificationChannel(str, enum.Enum):
    IN_APP = "in_app"
    EMAIL = "email"


class NotificationSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ACTION_REQUIRED = "action_required"


class NotificationTemplateVersion(Base):
    __tablename__ = "notification_template_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_notification_template_version_positive"),
        CheckConstraint("length(trim(code)) > 0", name="ck_notification_template_code_required"),
        CheckConstraint("length(trim(body_template)) > 0", name="ck_notification_template_body_required"),
        CheckConstraint(
            "channel IN ('in_app', 'email')",
            name="ck_notification_template_channel",
        ),
        CheckConstraint(
            "(channel = 'in_app' AND title_template IS NOT NULL AND subject_template IS NULL) "
            "OR (channel = 'email' AND subject_template IS NOT NULL AND title_template IS NULL)",
            name="ck_notification_template_channel_fields",
        ),
        UniqueConstraint(
            "code", "channel", "version", name="uq_notification_template_version"
        ),
        Index(
            "uq_notification_template_active",
            "code",
            "channel",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(
            NotificationChannel,
            values_callable=enum_values,
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title_template: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_template: Mapped[str | None] = mapped_column(String(998), nullable=True)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_variables: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("length(trim(template_code)) > 0", name="ck_notifications_code_required"),
        CheckConstraint("length(trim(title)) > 0", name="ck_notifications_title_required"),
        CheckConstraint("length(trim(body)) > 0", name="ck_notifications_body_required"),
        CheckConstraint(
            "severity IN ('info', 'warning', 'action_required')",
            name="ck_notifications_severity",
        ),
        UniqueConstraint(
            "recipient_user_id", "deduplication_key", name="uq_notifications_recipient_dedupe"
        ),
        Index(
            "ix_notifications_recipient_created",
            "recipient_user_id",
            "created_at",
        ),
        Index(
            "ix_notifications_recipient_unread",
            "recipient_user_id",
            "read_at",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template_code: Mapped[str] = mapped_column(String(100), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    severity: Mapped[NotificationSeverity] = mapped_column(
        Enum(
            NotificationSeverity,
            values_callable=enum_values,
            native_enum=False,
            length=24,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    related_object_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_object_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pii_anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _guard_template_version_mutation(
    mapper: Mapper, connection: object, target: NotificationTemplateVersion
) -> None:
    del mapper, connection
    state = inspect(target)
    changed = [attribute.key for attribute in state.attrs if attribute.history.has_changes()]
    if changed != ["is_active"] or target.is_active:
        raise ValueError("Notification template versions are immutable after creation")


def _reject_notification_delete(
    mapper: Mapper, connection: object, target: object
) -> None:
    del mapper, connection, target
    raise ValueError("Notification history cannot be deleted")


def _guard_notification_update(
    mapper: Mapper, connection: object, target: Notification
) -> None:
    del mapper, connection
    state = inspect(target)
    changed = {attribute.key for attribute in state.attrs if attribute.history.has_changes()}
    read_history = state.attrs.read_at.history
    if changed != {"read_at"} or target.read_at is None or any(
        previous is not None for previous in read_history.deleted
    ):
        raise ValueError("Notification content is immutable")


event.listen(
    NotificationTemplateVersion,
    "before_update",
    _guard_template_version_mutation,
)
event.listen(NotificationTemplateVersion, "before_delete", _reject_notification_delete)
event.listen(Notification, "before_update", _guard_notification_update)
event.listen(Notification, "before_delete", _reject_notification_delete)
