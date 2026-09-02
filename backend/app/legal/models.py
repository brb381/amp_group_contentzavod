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
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class LegalDocumentType(str, enum.Enum):
    PROGRAM_TERMS = "program_terms"
    PERSONAL_DATA_CONSENT = "personal_data_consent"
    PRIVACY_POLICY = "privacy_policy"


class AcceptanceMethod(str, enum.Enum):
    REGISTRATION = "registration"
    AUTHENTICATED_CLICK = "authenticated_click"
    LEGACY_MIGRATION = "legacy_migration"


class LegalDocument(Base):
    __tablename__ = "legal_documents"
    __table_args__ = (
        UniqueConstraint("document_type", "version", name="uq_legal_documents_type_version"),
        UniqueConstraint("document_type", "revision", name="uq_legal_documents_type_revision"),
        CheckConstraint("revision > 0", name="ck_legal_documents_revision_positive"),
        CheckConstraint(
            "document_type <> 'privacy_policy' OR requires_reacceptance = false",
            name="ck_legal_documents_policy_no_acceptance",
        ),
        Index(
            "uq_legal_documents_current_type",
            "document_type",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
        Index("ix_legal_documents_type_published", "document_type", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_type: Mapped[LegalDocumentType] = mapped_column(
        Enum(
            LegalDocumentType,
            values_callable=enum_values,
            native_enum=False,
            length=32,
        ),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    requires_reacceptance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"
    __table_args__ = (
        Index(
            "uq_legal_acceptances_active_user_document",
            "user_id",
            "document_id",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
            sqlite_where=text("withdrawn_at IS NULL"),
        ),
        Index("ix_legal_acceptances_user_accepted", "user_id", "accepted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="RESTRICT"), nullable=False
    )
    method: Mapped[AcceptanceMethod] = mapped_column(
        Enum(AcceptanceMethod, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


IMMUTABLE_DOCUMENT_FIELDS = (
    "document_type",
    "version",
    "revision",
    "title",
    "content_markdown",
    "content_sha256",
    "requires_reacceptance",
    "change_summary",
    "published_at",
    "published_by_user_id",
    "created_at",
)


def _guard_document_update(mapper: Mapper, connection: object, target: LegalDocument) -> None:
    del mapper, connection
    state = inspect(target)
    changed = [
        name for name in IMMUTABLE_DOCUMENT_FIELDS if state.attrs[name].history.has_changes()
    ]
    if changed:
        raise ValueError(f"Published legal document is immutable: {', '.join(changed)}")
    current_history = state.attrs.is_current.history
    if current_history.has_changes() and not (
        current_history.deleted
        and current_history.deleted[0] is True
        and target.is_current is False
    ):
        raise ValueError("A retired legal document cannot become current again")


def _reject_document_delete(
    mapper: Mapper, connection: object, target: LegalDocument
) -> None:
    del mapper, connection, target
    raise ValueError("Published legal documents cannot be deleted")


def _guard_acceptance_update(
    mapper: Mapper, connection: object, target: LegalAcceptance
) -> None:
    del mapper, connection
    state = inspect(target)
    changed = [attribute.key for attribute in state.attrs if attribute.history.has_changes()]
    withdrawal = state.attrs.withdrawn_at.history
    if changed != ["withdrawn_at"] or not (
        withdrawal.deleted
        and withdrawal.deleted[0] is None
        and target.withdrawn_at is not None
    ):
        raise ValueError("Legal acceptance facts are immutable")


def _reject_acceptance_delete(
    mapper: Mapper, connection: object, target: LegalAcceptance
) -> None:
    del mapper, connection, target
    raise ValueError("Legal acceptances cannot be deleted")


event.listen(LegalDocument, "before_update", _guard_document_update)
event.listen(LegalDocument, "before_delete", _reject_document_delete)
event.listen(LegalAcceptance, "before_update", _guard_acceptance_update)
event.listen(LegalAcceptance, "before_delete", _reject_acceptance_delete)
