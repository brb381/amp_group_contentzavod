#!/bin/sh
set -eu

required_variables="
DB_API_PASSWORD
DB_SCHEDULER_PASSWORD
DB_EMAIL_WORKER_PASSWORD
DB_YOUTUBE_WORKER_PASSWORD
DB_CALCULATION_WORKER_PASSWORD
DB_EXPORT_WORKER_PASSWORD
DB_RETENTION_WORKER_PASSWORD
DB_LIFECYCLE_WORKER_PASSWORD
"

for variable_name in $required_variables; do
    if [ -z "$(printenv "$variable_name" 2>/dev/null || true)" ]; then
        echo "Database bootstrap error: $variable_name is required." >&2
        exit 1
    fi
done

psql \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 \
    --set=owner_role="$POSTGRES_USER" \
    --set=owner_password="$POSTGRES_PASSWORD" \
    --set=api_password="$DB_API_PASSWORD" \
    --set=scheduler_password="$DB_SCHEDULER_PASSWORD" \
    --set=email_worker_password="$DB_EMAIL_WORKER_PASSWORD" \
    --set=youtube_worker_password="$DB_YOUTUBE_WORKER_PASSWORD" \
    --set=calculation_worker_password="$DB_CALCULATION_WORKER_PASSWORD" \
    --set=export_worker_password="$DB_EXPORT_WORKER_PASSWORD" \
    --set=retention_worker_password="$DB_RETENTION_WORKER_PASSWORD" \
    --set=lifecycle_worker_password="$DB_LIFECYCLE_WORKER_PASSWORD" <<'SQL'
-- POSTGRES_PASSWORD is ignored by the image when a data volume already exists.
-- Rotating it here keeps the documented existing-volume bootstrap deterministic.
SELECT format('ALTER ROLE %I PASSWORD %L', :'owner_role', :'owner_password')
\gexec

SELECT format(
    'CREATE ROLE amp_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'api_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_api')
\gexec
SELECT format(
    'ALTER ROLE amp_api WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'api_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_scheduler LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'scheduler_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_scheduler')
\gexec
SELECT format(
    'ALTER ROLE amp_scheduler WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'scheduler_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_email_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'email_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_email_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_email_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'email_worker_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_youtube_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'youtube_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_youtube_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_youtube_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'youtube_worker_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_calculation_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'calculation_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_calculation_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_calculation_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'calculation_worker_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_export_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'export_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_export_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_export_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'export_worker_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_retention_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'retention_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_retention_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_retention_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'retention_worker_password'
)
\gexec

SELECT format(
    'CREATE ROLE amp_lifecycle_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'lifecycle_worker_password'
)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'amp_lifecycle_worker')
\gexec
SELECT format(
    'ALTER ROLE amp_lifecycle_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'lifecycle_worker_password'
)
\gexec

SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
  FROM pg_auth_members AS membership
  JOIN pg_roles AS granted ON granted.oid = membership.roleid
  JOIN pg_roles AS member ON member.oid = membership.member
 WHERE member.rolname IN (
    'amp_api',
    'amp_scheduler',
    'amp_email_worker',
    'amp_youtube_worker',
    'amp_calculation_worker',
    'amp_export_worker',
    'amp_retention_worker',
    'amp_lifecycle_worker'
 )
\gexec
SQL
