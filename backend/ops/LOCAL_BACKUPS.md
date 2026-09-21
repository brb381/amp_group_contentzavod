# Temporary local backups

This is a same-disk fallback, not disaster recovery. Loss of the host disk,
the host itself, or ransomware may destroy both live data and this repository.
Move the encrypted restic repository off-host as soon as possible. No API
endpoint creates or restores backups.

## Setup

Run commands from `backend/`. Set unique values for `DB_BACKUP_PASSWORD`,
`S3_BACKUP_SECRET_KEY`, and `S3_RESTORE_SECRET_KEY` in `.env`. Keep the
restore key out of the backup container. Set `AMP_BACKUP_DIR` to a folder
outside the Git checkout, for example `../../amp-backups`.

Create a long random restic password in `AMP_BACKUP_PASSWORD_FILE`
(default `./.backup-password`). Keep a separate offline copy of this
password: without it the repository cannot be restored. Do not commit or
send any password in chat.

```powershell
$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
[IO.File]::WriteAllText(
  (Join-Path (Get-Location) '.backup-password'),
  [Convert]::ToBase64String($bytes),
  [Text.UTF8Encoding]::new($false)
)
```

On a new PostgreSQL volume, bootstrap creates `amp_backup` with
`pg_read_all_data` and no write permissions. On an existing volume,
rerun both idempotent bootstrap commands after adding the new secrets:

```powershell
docker compose exec postgres sh /docker-entrypoint-initdb.d/10-runtime-roles.sh
docker compose run --rm minio-init
```

## Backup

```powershell
docker compose --profile backup run --rm backup-local
docker compose --profile backup run --rm --entrypoint restic backup-local snapshots
```

The one-shot container produces a PostgreSQL custom-format dump and copies
every current object in `private-exports` into a temporary staging area.
It records names, sizes and SHA-256 hashes, then creates an encrypted restic
snapshot in `AMP_BACKUP_DIR` and runs `restic check`. A failed command
exits nonzero. Schedule this command outside the API after a successful
manual backup and restore drill. There is no automatic pruning yet; watch
free space and agree a retention policy before enabling deletion.

The temporary staging area contains plaintext while the one-shot container runs.
Restrict Docker host access and use disk encryption where available.
The temporary staging area needs free disk space at least equal to the
uncompressed dump plus all copied objects. The restic repository needs
additional space. The database dump is internally consistent, but PostgreSQL
and MinIO are not one atomic snapshot. Current MinIO objects are short-lived
export artifacts; do not use this procedure unchanged for future permanent
documents.

## Restore drill

Use a fresh environment with an empty database and empty `private-exports`
bucket. Stop API, scheduler and workers. Keep the original repository and
password file. Bootstrap database roles and MinIO users first; the dump restores table grants
for those roles. Then run:

```powershell
docker compose --profile restore run --rm backup-restore restore <full-64-character-snapshot-id> --confirm
```

The command checks that both targets are empty, verifies all staged SHA-256
hashes, restores PostgreSQL, then uploads objects. If it fails partway
through, discard the target environment and retry on a fresh one; it does not
delete or overwrite existing data. After restore, compare application data,
financial totals and `alembic_version`, then run migrations if necessary
before starting application processes.
