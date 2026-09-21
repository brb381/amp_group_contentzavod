"""Grant the monitor read-only access to job state.

Revision ID: 0026_operations_monitor
Revises: 0025_general_exports
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_operations_monitor"
down_revision: Union[str, Sequence[str], None] = "0025_general_exports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_TABLES = (
    "outbox_events",
    "youtube_enrichment_jobs",
    "youtube_view_collection_jobs",
    "calculation_jobs",
    "lifecycle_jobs",
    "export_jobs",
)


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_outbox_events_failed_recent", "outbox_events", ["state", "failed_at"])
    op.execute("UPDATE outbox_events SET failed_at = created_at WHERE state = 'failed'")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            DO $monitor_role_validation$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_roles
                    WHERE rolname = 'amp_monitor'
                      AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb
                      AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls
                      AND NOT EXISTS (
                          SELECT 1 FROM pg_auth_members
                          WHERE member = pg_roles.oid
                      )
                ) OR EXISTS (
                    SELECT 1 FROM pg_class AS relation
                    JOIN pg_roles AS owner_role ON owner_role.oid = relation.relowner
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE owner_role.rolname = 'amp_monitor'
                      AND namespace.nspname = 'public'
                ) THEN
                    RAISE EXCEPTION 'amp_monitor must be an unprivileged login without memberships or owned objects';
                END IF;
            END;
            $monitor_role_validation$;
            """
        )
        op.execute(
            "DO $$ BEGIN EXECUTE format("
            "'REVOKE ALL PRIVILEGES ON DATABASE %I FROM amp_monitor', current_database()); "
            "END; $$;"
        )
        op.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM amp_monitor")
        op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM amp_monitor")
        op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM amp_monitor")
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "REVOKE ALL PRIVILEGES ON TABLES FROM amp_monitor"
        )
        op.execute(
            "DO $$ BEGIN EXECUTE format("
            "'GRANT CONNECT ON DATABASE %I TO amp_monitor', current_database()); END; $$;"
        )
        op.execute("GRANT USAGE ON SCHEMA public TO amp_monitor")
        op.execute(f"GRANT SELECT ON TABLE {', '.join(JOB_TABLES)} TO amp_monitor")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"REVOKE SELECT ON TABLE {', '.join(JOB_TABLES)} FROM amp_monitor")
        op.execute("REVOKE USAGE ON SCHEMA public FROM amp_monitor")
        op.execute(
            "DO $$ BEGIN EXECUTE format("
            "'REVOKE CONNECT ON DATABASE %I FROM amp_monitor', current_database()); END; $$;"
        )
    op.drop_index("ix_outbox_events_failed_recent", table_name="outbox_events")
    op.drop_column("outbox_events", "failed_at")
