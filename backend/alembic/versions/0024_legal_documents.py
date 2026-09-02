"""Add versioned legal documents and acceptance records.

Revision ID: 0024_legal_documents
Revises: 0023_account_deletion_retention
Create Date: 2026-08-31
"""

import hashlib
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "0024_legal_documents"
down_revision: Union[str, Sequence[str], None] = "0023_account_deletion_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_BODY = "Historical acceptance migrated from the legacy consent table; original body was not stored."


def _create_tables() -> None:
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("requires_reacceptance", sa.Boolean(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_legal_documents_revision_positive"),
        sa.CheckConstraint(
            "document_type <> 'privacy_policy' OR requires_reacceptance = false",
            name="ck_legal_documents_policy_no_acceptance",
        ),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_type", "revision", name="uq_legal_documents_type_revision"),
        sa.UniqueConstraint("document_type", "version", name="uq_legal_documents_type_version"),
    )
    op.create_index(
        "ix_legal_documents_type_published",
        "legal_documents",
        ["document_type", "published_at"],
    )
    op.create_index(
        "uq_legal_documents_current_type",
        "legal_documents",
        ["document_type"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )
    op.create_table(
        "legal_acceptances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["legal_documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_acceptances_user_accepted",
        "legal_acceptances",
        ["user_id", "accepted_at"],
    )
    op.create_index(
        "uq_legal_acceptances_active_user_document",
        "legal_acceptances",
        ["user_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("withdrawn_at IS NULL"),
        sqlite_where=sa.text("withdrawn_at IS NULL"),
    )


def _migrate_legacy_online() -> None:
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, user_id, document_type, document_version, accepted_at "
                "FROM consents ORDER BY accepted_at, id"
            )
        ).mappings()
    )
    documents: dict[tuple[str, str], uuid.UUID] = {}
    revisions: dict[str, int] = {}
    for row in rows:
        key = (row["document_type"], row["document_version"])
        if key in documents:
            continue
        document_id = uuid.uuid5(uuid.NAMESPACE_URL, f"amp:legal:{key[0]}:{key[1]}")
        documents[key] = document_id
        revisions[key[0]] = revisions.get(key[0], 0) + 1
        bind.execute(
            sa.text(
                "INSERT INTO legal_documents "
                "(id, document_type, version, revision, title, content_markdown, "
                "content_sha256, is_current, requires_reacceptance, published_at, created_at) "
                "VALUES (:id, :document_type, :version, :revision, :title, :body, NULL, "
                ":is_current, :requires_reacceptance, :published_at, :published_at)"
            ),
            {
                "id": document_id,
                "document_type": key[0],
                "version": key[1],
                "revision": revisions[key[0]],
                "title": f"Migrated {key[0].replace('_', ' ')}",
                "body": LEGACY_BODY,
                "is_current": False,
                "requires_reacceptance": key[0] == "program_terms",
                "published_at": row["accepted_at"],
            },
        )
    for row in rows:
        document_id = documents[(row["document_type"], row["document_version"])]
        bind.execute(
            sa.text(
                "INSERT INTO legal_acceptances "
                "(id, user_id, document_id, method, accepted_at, request_id, ip_hash) "
                "VALUES (:id, :user_id, :document_id, 'legacy_migration', :accepted_at, "
                "'legacy-migration', :ip_hash)"
            ),
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "document_id": document_id,
                "accepted_at": row["accepted_at"],
                "ip_hash": hashlib.sha256(b"legacy-unknown").hexdigest(),
            },
        )


def _migrate_legacy_offline() -> None:
    op.execute(
        sa.text(
            "INSERT INTO legal_documents "
            "(id, document_type, version, revision, title, content_markdown, content_sha256, "
            "is_current, requires_reacceptance, published_at, created_at) "
            "WITH legacy AS (SELECT document_type, document_version, min(accepted_at) AS accepted_at, "
            "row_number() OVER (PARTITION BY document_type ORDER BY min(accepted_at), document_version) AS revision "
            "FROM consents GROUP BY document_type, document_version) "
            "SELECT md5('amp:legal:' || document_type || ':' || document_version)::uuid, "
            "document_type, document_version, revision, "
            "'Migrated ' || replace(document_type, '_', ' '), "
            f"'{LEGACY_BODY}', NULL, false, document_type = 'program_terms', "
            "accepted_at, accepted_at FROM legacy"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO legal_acceptances "
            "(id, user_id, document_id, method, accepted_at, request_id, ip_hash) "
            "SELECT id, user_id, "
            "md5('amp:legal:' || document_type || ':' || document_version)::uuid, "
            "'legacy_migration', accepted_at, 'legacy-migration', "
            f"'{hashlib.sha256(b'legacy-unknown').hexdigest()}' FROM consents"
        )
    )


def _create_postgresql_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION guard_legal_document_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'published legal documents cannot be deleted';
            END IF;
            IF OLD.document_type IS DISTINCT FROM NEW.document_type
               OR OLD.version IS DISTINCT FROM NEW.version
               OR OLD.revision IS DISTINCT FROM NEW.revision
               OR OLD.title IS DISTINCT FROM NEW.title
               OR OLD.content_markdown IS DISTINCT FROM NEW.content_markdown
               OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
               OR OLD.requires_reacceptance IS DISTINCT FROM NEW.requires_reacceptance
               OR OLD.change_summary IS DISTINCT FROM NEW.change_summary
               OR OLD.published_at IS DISTINCT FROM NEW.published_at
               OR OLD.published_by_user_id IS DISTINCT FROM NEW.published_by_user_id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR NOT (OLD.is_current = true AND NEW.is_current = false) THEN
                RAISE EXCEPTION 'published legal documents are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_guard_legal_documents
        BEFORE UPDATE OR DELETE ON legal_documents
        FOR EACH ROW EXECUTE FUNCTION guard_legal_document_mutation();

        CREATE FUNCTION guard_legal_acceptance_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'legal acceptances cannot be deleted';
            END IF;
            IF OLD.withdrawn_at IS NOT NULL OR NEW.withdrawn_at IS NULL
               OR OLD.id IS DISTINCT FROM NEW.id
               OR OLD.user_id IS DISTINCT FROM NEW.user_id
               OR OLD.document_id IS DISTINCT FROM NEW.document_id
               OR OLD.method IS DISTINCT FROM NEW.method
               OR OLD.accepted_at IS DISTINCT FROM NEW.accepted_at
               OR OLD.request_id IS DISTINCT FROM NEW.request_id
               OR OLD.ip_hash IS DISTINCT FROM NEW.ip_hash
               OR OLD.user_agent_hash IS DISTINCT FROM NEW.user_agent_hash THEN
                RAISE EXCEPTION 'legal acceptance facts are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_guard_legal_acceptances
        BEFORE UPDATE OR DELETE ON legal_acceptances
        FOR EACH ROW EXECUTE FUNCTION guard_legal_acceptance_mutation();
        """
    )
    op.execute("REVOKE DELETE ON legal_documents, legal_acceptances FROM amp_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON legal_documents, legal_acceptances TO amp_api")


def upgrade() -> None:
    _create_tables()
    if context.is_offline_mode():
        _migrate_legacy_offline()
    else:
        _migrate_legacy_online()
    op.drop_index("ix_consents_user_id", table_name="consents")
    op.drop_table("consents")
    _create_postgresql_guards()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_guard_legal_acceptances ON legal_acceptances")
        op.execute("DROP FUNCTION IF EXISTS guard_legal_acceptance_mutation()")
        op.execute("DROP TRIGGER IF EXISTS trg_guard_legal_documents ON legal_documents")
        op.execute("DROP FUNCTION IF EXISTS guard_legal_document_mutation()")
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("document_version", sa.String(length=64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "document_type", "document_version", name="uq_consents_user_document_version"
        ),
    )
    op.create_index("ix_consents_user_id", "consents", ["user_id"])
    if context.is_offline_mode():
        op.execute(
            "INSERT INTO consents (id, user_id, document_type, document_version, accepted_at) "
            "SELECT DISTINCT ON (a.user_id, d.document_type, d.version) a.id, a.user_id, "
            "d.document_type, d.version, a.accepted_at FROM legal_acceptances a "
            "JOIN legal_documents d ON d.id = a.document_id "
            "ORDER BY a.user_id, d.document_type, d.version, a.accepted_at"
        )
    else:
        bind = op.get_bind()
        rows = bind.execute(
            sa.text(
                "SELECT a.id, a.user_id, d.document_type, d.version, a.accepted_at "
                "FROM legal_acceptances a JOIN legal_documents d ON d.id = a.document_id "
                "ORDER BY a.accepted_at, a.id"
            )
        ).mappings()
        seen: set[tuple[object, str, str]] = set()
        for row in rows:
            key = (row["user_id"], row["document_type"], row["version"])
            if key in seen:
                continue
            seen.add(key)
            bind.execute(
                sa.text(
                    "INSERT INTO consents "
                    "(id, user_id, document_type, document_version, accepted_at) "
                    "VALUES (:id, :user_id, :document_type, :version, :accepted_at)"
                ),
                row,
            )
    op.drop_index("uq_legal_acceptances_active_user_document", table_name="legal_acceptances")
    op.drop_index("ix_legal_acceptances_user_accepted", table_name="legal_acceptances")
    op.drop_table("legal_acceptances")
    op.drop_index("uq_legal_documents_current_type", table_name="legal_documents")
    op.drop_index("ix_legal_documents_type_published", table_name="legal_documents")
    op.drop_table("legal_documents")
