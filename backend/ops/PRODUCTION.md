# Production deployment

This Compose configuration assumes one host, an HTTPS reverse proxy on the host,
and an existing PostgreSQL/MinIO deployment in the base Compose stack. It does
not provide the reverse proxy or an off-host backup target.

## Configuration

Keep `backend/.env` outside Git and readable only by the deployment account.
Supply unique URL-safe passwords for every `DB_*` role and MinIO/S3 user. Do
not reuse the example values. At minimum set:

- `JWT_SECRET`: at least 48 bytes from a cryptographic random generator.
- `FRONTEND_URL`: the real HTTPS origin used in links sent by email.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM_EMAIL`, and either
  `SMTP_USE_TLS=true` (STARTTLS) or `SMTP_USE_SSL=true` (implicit TLS).
- `MONITOR_SMTP_HOST`, `MONITOR_SMTP_PORT`,
  `MONITOR_SMTP_FROM_EMAIL`, and one TLS mode for operational alerts.
- `MONITOR_ALERT_EMAIL`: an address that an operator actually checks.

Set `SMTP_USERNAME`/`SMTP_PASSWORD` and their `MONITOR_` equivalents if
the mail service requires authentication. `ENVIRONMENT=production` and
`COOKIE_SECURE=true` are forced by the production Compose override.
Application startup rejects development JWT values, HTTP frontend URLs,
Mailpit/localhost SMTP, and unencrypted SMTP in production.

Generate a JWT secret in PowerShell without recording it in command history:

```powershell
$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
[Convert]::ToBase64String($bytes)
```

Store the result in the protected environment file or deployment secret store,
not in Git or chat. Rotate credentials by updating that source and recreating
affected containers.

## Start

Run from `backend/`:

```powershell
powershell -NoProfile -File .\ops\validate_production_env.ps1
docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

For an existing PostgreSQL volume that predates the backup/monitor roles, first
start only PostgreSQL and bootstrap roles before the full `up`:

```powershell
docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml up -d postgres
docker compose --env-file .env -f docker-compose.yml -f docker-compose.production.yml exec postgres sh /docker-entrypoint-initdb.d/10-runtime-roles.sh
```

The production override does not start Mailpit, does not publish MinIO ports,
and binds the API only to `127.0.0.1:8000` (override with `API_PORT`).
Configure the host reverse proxy to terminate TLS and forward to this loopback
port. Do not expose port 8000 directly to the internet. Apply HTTPS and
trusted-host rules at that proxy; the frontend origin and cookie settings must
match the deployed site.

Verify `/health/ready` through the proxy, an actual application email, and an
alert email before accepting traffic. Review monitor logs and internal metrics
at `monitor:9101/metrics`. Backups still require a manual restore drill before
any schedule is enabled.

## Scheduled maintenance on Windows

After the production stack is up and `.env` is configured, register the two
host tasks from `backend/`:

```powershell
powershell -NoProfile -File .\ops\register_maintenance_tasks.ps1
```

This registers daily export cleanup at 02:30 and daily creator PII retention
at 03:30, using the host's local time zone. The registration script
refuses to replace existing tasks unless `-Replace` is supplied. Each task
runs a one-shot Compose container with no dependencies started automatically,
uses a lock file to prevent overlap, and returns a nonzero result on failure.

The tasks run as the current interactive Windows user. That account must have
Docker access, remain logged in, and have Docker Desktop running at trigger time.
Check Task Scheduler's Last Run Result and `backend/ops/logs/` after the first
run; rotate these local logs as part of host maintenance.
For an unattended server, use a service-capable Docker runtime and equivalent
host cron/systemd timers instead of an interactive Windows principal. Do not
schedule the temporary same-disk backup until a restore drill succeeds.
