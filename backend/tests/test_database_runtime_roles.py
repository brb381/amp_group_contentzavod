import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


RUNTIME_ROLES = {
    "amp_api",
    "amp_scheduler",
    "amp_email_worker",
    "amp_youtube_worker",
    "amp_calculation_worker",
    "amp_export_worker",
    "amp_retention_worker",
    "amp_lifecycle_worker",
}


@pytest.fixture(scope="module")
def postgres_connection():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")

    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _has_table_privilege(connection, role: str, table: str, privilege: str) -> bool:
    return bool(
        connection.scalar(
            text("SELECT has_table_privilege(:role, :table, :privilege)"),
            {"role": role, "table": f"public.{table}", "privilege": privilege},
        )
    )


def test_runtime_roles_are_unprivileged_logins_and_own_no_relations(postgres_connection):
    rows = postgres_connection.execute(
        text(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolreplication, rolbypassrls
              FROM pg_roles
             WHERE rolname = ANY (:roles)
            """
        ),
        {"roles": list(RUNTIME_ROLES)},
    ).mappings()
    roles = {row["rolname"]: row for row in rows}

    assert set(roles) == RUNTIME_ROLES
    for role in roles.values():
        assert role["rolcanlogin"] is True
        assert role["rolsuper"] is False
        assert role["rolcreatedb"] is False
        assert role["rolcreaterole"] is False
        assert role["rolreplication"] is False
        assert role["rolbypassrls"] is False

    owned_relations = postgres_connection.scalar(
        text(
            """
            SELECT count(*)
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
              JOIN pg_roles AS owner_role ON owner_role.oid = relation.relowner
             WHERE namespace.nspname = 'public'
               AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
               AND owner_role.rolname = ANY (:roles)
            """
        ),
        {"roles": list(RUNTIME_ROLES)},
    )
    assert owned_relations == 0

    memberships = postgres_connection.scalar(
        text(
            """
            SELECT count(*)
              FROM pg_auth_members AS membership
              JOIN pg_roles AS member ON member.oid = membership.member
             WHERE member.rolname = ANY (:roles)
            """
        ),
        {"roles": list(RUNTIME_ROLES)},
    )
    assert memberships == 0


def test_api_has_dml_without_owner_level_table_privileges(postgres_connection):
    for table in ("payout_requests", "payout_event_details", "export_jobs"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert _has_table_privilege(
                postgres_connection, "amp_api", table, privilege
            )
    for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER"):
        assert not _has_table_privilege(
            postgres_connection, "amp_api", "payout_requests", privilege
        )
    assert postgres_connection.scalar(
        text("SELECT has_schema_privilege('amp_api', 'public', 'USAGE')")
    )
    assert not postgres_connection.scalar(
        text("SELECT has_schema_privilege('amp_api', 'public', 'CREATE')")
    )
    assert postgres_connection.scalar(
        text("SELECT has_database_privilege('amp_api', current_database(), 'CONNECT')")
    )
    assert not postgres_connection.scalar(
        text("SELECT has_database_privilege('amp_api', current_database(), 'TEMP')")
    )
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not _has_table_privilege(
            postgres_connection, "amp_api", "alembic_version", privilege
        )


def test_default_table_privileges_only_include_the_api(postgres_connection):
    rows = postgres_connection.execute(
        text(
            """
            SELECT grantee.rolname, expanded.privilege_type
              FROM pg_default_acl AS defaults
              JOIN pg_namespace AS namespace
                ON namespace.oid = defaults.defaclnamespace
             CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS expanded
              JOIN pg_roles AS grantee ON grantee.oid = expanded.grantee
             WHERE namespace.nspname = 'public'
               AND defaults.defaclobjtype = 'r'
               AND grantee.rolname = ANY (:roles)
            """
        ),
        {"roles": list(RUNTIME_ROLES)},
    )
    grants = {(row[0], row[1]) for row in rows}

    assert grants == {
        ("amp_api", "SELECT"),
        ("amp_api", "INSERT"),
        ("amp_api", "UPDATE"),
        ("amp_api", "DELETE"),
    }


@pytest.mark.parametrize(
    ("role", "table", "allowed", "denied"),
    [
        ("amp_email_worker", "outbox_events", {"SELECT", "UPDATE"}, {"INSERT", "DELETE"}),
        ("amp_youtube_worker", "publications", {"SELECT", "UPDATE"}, {"INSERT", "DELETE"}),
        (
            "amp_calculation_worker",
            "publication_accruals",
            {"SELECT", "INSERT", "DELETE"},
            {"UPDATE"},
        ),
        (
            "amp_scheduler",
            "youtube_view_collection_jobs",
            {"SELECT", "INSERT", "UPDATE"},
            {"DELETE"},
        ),
        (
            "amp_scheduler",
            "export_jobs",
            {"SELECT", "UPDATE"},
            {"INSERT", "DELETE"},
        ),
        (
            "amp_export_worker",
            "export_jobs",
            {"SELECT", "UPDATE"},
            {"INSERT", "DELETE"},
        ),
        (
            "amp_export_worker",
            "payout_requests",
            {"SELECT"},
            {"INSERT", "UPDATE", "DELETE"},
        ),
        (
            "amp_retention_worker",
            "payout_requests",
            {"SELECT", "UPDATE"},
            {"INSERT", "DELETE"},
        ),
        (
            "amp_retention_worker",
            "security_events",
            {"INSERT"},
            {"SELECT", "UPDATE", "DELETE"},
        ),
        (
            "amp_lifecycle_worker",
            "lifecycle_jobs",
            {"SELECT", "UPDATE"},
            {"INSERT", "DELETE"},
        ),
    ],
)
def test_worker_table_grants_are_operation_specific(
    postgres_connection, role, table, allowed, denied
):
    for privilege in allowed:
        assert _has_table_privilege(postgres_connection, role, table, privilege)
    for privilege in denied:
        assert not _has_table_privilege(postgres_connection, role, table, privilege)


def test_lifecycle_worker_can_only_expire_the_balance_claim(postgres_connection):
    assert not _has_table_privilege(
        postgres_connection, "amp_lifecycle_worker", "creator_balances", "SELECT"
    )
    assert not _has_table_privilege(
        postgres_connection, "amp_lifecycle_worker", "creator_balances", "UPDATE"
    )
    for column in ("blogger_id", "available_kopecks", "claim_expired_at"):
        assert postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_lifecycle_worker', 'public.creator_balances', :column, 'SELECT')"
            ),
            {"column": column},
        )
    for column in ("claim_expired_at", "updated_at"):
        assert postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_lifecycle_worker', 'public.creator_balances', :column, 'UPDATE')"
            ),
            {"column": column},
        )
    for column in ("available_kopecks", "reserved_kopecks", "paid_kopecks"):
        assert not postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_lifecycle_worker', 'public.creator_balances', :column, 'UPDATE')"
            ),
            {"column": column},
        )


@pytest.mark.parametrize(
    "role",
    [
        "amp_email_worker",
        "amp_youtube_worker",
        "amp_calculation_worker",
        "amp_scheduler",
        "amp_lifecycle_worker",
    ],
)
@pytest.mark.parametrize(
    "table",
    [
        "payout_requests",
        "payout_events",
        "payout_event_details",
        "creator_balances",
        "balance_ledger",
    ],
)
def test_background_roles_cannot_read_payout_or_ledger_tables(
    postgres_connection, role, table
):
    assert not _has_table_privilege(postgres_connection, role, table, "SELECT")


def test_workers_cannot_read_unrelated_identity_data(postgres_connection):
    assert not _has_table_privilege(
        postgres_connection, "amp_email_worker", "users", "SELECT"
    )
    assert not _has_table_privilege(
        postgres_connection, "amp_youtube_worker", "users", "SELECT"
    )
    assert not _has_table_privilege(
        postgres_connection, "amp_calculation_worker", "users", "SELECT"
    )
    for column in ("id", "status"):
        assert postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_calculation_worker', 'public.users', :column, 'SELECT')"
            ),
            {"column": column},
        )
    for column in ("email", "password_hash", "status_reason"):
        assert not postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_calculation_worker', 'public.users', :column, 'SELECT')"
            ),
            {"column": column},
        )


@pytest.mark.parametrize(
    "table",
    [
        "creator_balances",
        "balance_ledger",
        "payout_events",
        "payout_event_details",
        "security_events",
        "outbox_events",
        "view_readings",
    ],
)
def test_export_worker_cannot_read_unrelated_or_money_tables(
    postgres_connection, table
):
    assert not _has_table_privilege(
        postgres_connection, "amp_export_worker", table, "SELECT"
    )


def test_export_worker_can_resolve_staff_emails(postgres_connection):
    assert _has_table_privilege(
        postgres_connection, "amp_export_worker", "users", "SELECT"
    )


@pytest.mark.parametrize(
    "table",
    [
        "creator_balances",
        "balance_ledger",
        "export_jobs",
        "view_readings",
    ],
)
def test_retention_worker_cannot_access_unrelated_tables(
    postgres_connection, table
):
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not _has_table_privilege(
            postgres_connection, "amp_retention_worker", table, privilege
        )


def test_retention_worker_has_only_bounded_account_pii_access(postgres_connection):
    assert _has_table_privilege(
        postgres_connection, "amp_retention_worker", "outbox_events", "SELECT"
    )
    assert not _has_table_privilege(
        postgres_connection, "amp_retention_worker", "outbox_events", "UPDATE"
    )
    for column in ("payload", "pii_anonymized_at"):
        assert postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_retention_worker', 'public.outbox_events', :column, 'UPDATE')"
            ),
            {"column": column},
        )
    for column in ("state", "attempt_count", "correlation_id"):
        assert not postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_retention_worker', 'public.outbox_events', :column, 'UPDATE')"
            ),
            {"column": column},
        )

    assert not _has_table_privilege(
        postgres_connection, "amp_retention_worker", "users", "SELECT"
    )
    for column in ("id", "email", "status", "collaboration_ended_at"):
        assert postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_retention_worker', 'public.users', :column, 'SELECT')"
            ),
            {"column": column},
        )
    for column in ("role", "created_at"):
        assert not postgres_connection.scalar(
            text(
                "SELECT has_column_privilege("
                "'amp_retention_worker', 'public.users', :column, 'SELECT')"
            ),
            {"column": column},
        )
