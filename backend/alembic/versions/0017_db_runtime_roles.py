"""Apply least-privilege grants for application database roles.

Revision ID: 0017_db_runtime_roles
Revises: 0016_payouts
Create Date: 2026-08-21
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0017_db_runtime_roles"
down_revision: Union[str, Sequence[str], None] = "0016_payouts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RUNTIME_ROLES = (
    "amp_api",
    "amp_scheduler",
    "amp_email_worker",
    "amp_youtube_worker",
    "amp_calculation_worker",
)

ROLE_TABLE_GRANTS: dict[str, tuple[tuple[str, str], ...]] = {
    "amp_email_worker": (
        ("outbox_events", "SELECT, UPDATE"),
    ),
    "amp_youtube_worker": (
        ("youtube_enrichment_jobs", "SELECT, UPDATE"),
        ("youtube_view_collection_jobs", "SELECT, UPDATE"),
        ("external_provider_states", "SELECT, INSERT, UPDATE"),
        ("publications", "SELECT, UPDATE"),
        ("view_readings", "SELECT, INSERT"),
        ("view_reading_history", "SELECT, INSERT"),
        ("reading_dataset_revision", "SELECT, INSERT, UPDATE"),
    ),
    "amp_calculation_worker": (
        ("calculation_jobs", "SELECT, UPDATE"),
        ("calculation_periods", "SELECT, UPDATE"),
        ("rate_versions", "SELECT"),
        ("reading_dataset_revision", "SELECT, INSERT, UPDATE"),
        ("view_readings", "SELECT"),
        ("publications", "SELECT"),
        ("video_cards", "SELECT"),
        ("publication_accruals", "SELECT, INSERT, DELETE"),
        ("creator_period_totals", "SELECT, INSERT, DELETE"),
    ),
    "amp_scheduler": (
        ("outbox_events", "SELECT, UPDATE"),
        ("youtube_enrichment_jobs", "SELECT, UPDATE"),
        ("youtube_view_collection_jobs", "SELECT, INSERT, UPDATE"),
        ("external_provider_states", "SELECT, INSERT, UPDATE"),
        ("external_quota_usage", "SELECT, INSERT, UPDATE"),
        ("publications", "SELECT"),
        ("billing_control", "SELECT, INSERT"),
        ("calculation_periods", "SELECT, INSERT"),
        ("calculation_jobs", "SELECT, INSERT, UPDATE"),
        ("rate_versions", "SELECT"),
        ("view_readings", "SELECT"),
    ),
}


def _postgresql_only() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _validate_runtime_roles() -> None:
    roles = ", ".join(f"'{role}'" for role in RUNTIME_ROLES)
    op.execute(
        f"""
        DO $runtime_role_validation$
        DECLARE
            missing_roles text;
            unsafe_roles text;
            owning_roles text;
        BEGIN
            SELECT string_agg(required.role_name, ', ' ORDER BY required.role_name)
              INTO missing_roles
              FROM unnest(ARRAY[{roles}]) AS required(role_name)
              LEFT JOIN pg_roles AS existing ON existing.rolname = required.role_name
             WHERE existing.rolname IS NULL;

            IF missing_roles IS NOT NULL THEN
                RAISE EXCEPTION 'Missing AMP runtime database roles: %', missing_roles
                    USING HINT =
                        'Provision roles before migrating. For an existing Docker volume run '
                        'the documented bootstrap-runtime-roles command.';
            END IF;

            SELECT string_agg(role_data.rolname, ', ' ORDER BY role_data.rolname)
              INTO unsafe_roles
              FROM pg_roles AS role_data
             WHERE role_data.rolname = ANY (ARRAY[{roles}])
               AND (
                    NOT role_data.rolcanlogin
                    OR role_data.rolsuper
                    OR role_data.rolcreatedb
                    OR role_data.rolcreaterole
                    OR role_data.rolreplication
                    OR role_data.rolbypassrls
                    OR EXISTS (
                        SELECT 1
                          FROM pg_auth_members AS membership
                         WHERE membership.member = role_data.oid
                    )
               );

            IF unsafe_roles IS NOT NULL THEN
                RAISE EXCEPTION 'Unsafe AMP runtime database role configuration: %', unsafe_roles
                    USING HINT =
                        'Runtime roles must be LOGIN roles without elevated attributes or memberships.';
            END IF;

            IF current_user = ANY (ARRAY[{roles}]) THEN
                RAISE EXCEPTION 'Alembic cannot run as runtime role %', current_user
                    USING HINT = 'Run migrations with the schema-owner credential.';
            END IF;

            SELECT string_agg(DISTINCT owner_role.rolname, ', ' ORDER BY owner_role.rolname)
              INTO owning_roles
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
              JOIN pg_roles AS owner_role ON owner_role.oid = relation.relowner
             WHERE namespace.nspname = 'public'
               AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
               AND owner_role.rolname = ANY (ARRAY[{roles}]);

            IF owning_roles IS NOT NULL THEN
                RAISE EXCEPTION 'Runtime roles own database objects: %', owning_roles
                    USING HINT = 'Reassign object ownership to the migration/schema-owner role.';
            END IF;
        END
        $runtime_role_validation$;
        """
    )


def _grant_database_connect() -> None:
    roles = ", ".join(f"'{role}'" for role in RUNTIME_ROLES)
    op.execute(
        f"""
        DO $runtime_database_grants$
        DECLARE
            runtime_role text;
        BEGIN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC',
                current_database()
            );
            FOREACH runtime_role IN ARRAY ARRAY[{roles}]
            LOOP
                EXECUTE format(
                    'GRANT CONNECT ON DATABASE %I TO %I',
                    current_database(),
                    runtime_role
                );
            END LOOP;
        END
        $runtime_database_grants$;
        """
    )


def upgrade() -> None:
    if not _postgresql_only():
        return

    _validate_runtime_roles()
    _grant_database_connect()

    op.execute("REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")

    for role in RUNTIME_ROLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO amp_api"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE alembic_version FROM amp_api")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO amp_api")

    for role, table_grants in ROLE_TABLE_GRANTS.items():
        for table_name, privileges in table_grants:
            op.execute(f"GRANT {privileges} ON TABLE {table_name} TO {role}")

    # Only the API receives automatic access. A worker must opt in to each future
    # table in the migration that introduces that table.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC"
    )
    for role in RUNTIME_ROLES:
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON TABLES FROM {role}"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {role}"
        )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO amp_api"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO amp_api"
    )


def downgrade() -> None:
    if not _postgresql_only():
        return

    for role in RUNTIME_ROLES:
        op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {role}")
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON TABLES FROM {role}"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE ALL PRIVILEGES ON SEQUENCES FROM {role}"
        )

    # Downgrading must not restore broad PUBLIC privileges.
