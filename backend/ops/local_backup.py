"""One-shot encrypted local backup and guarded restore, never imported by the API."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config


CHUNK_SIZE = 1024 * 1024


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, check=True, text=True, capture_output=capture
    )


def restic(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return run("restic", *args, capture=capture)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=required("S3_ENDPOINT_URL"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        aws_access_key_id=required("S3_ACCESS_KEY"),
        aws_secret_access_key=required("S3_SECRET_KEY"),
        config=Config(s3={"addressing_style": "path"}),
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_repository() -> None:
    required("RESTIC_REPOSITORY")
    password_file = Path(required("RESTIC_PASSWORD_FILE"))
    if not password_file.is_file() or not password_file.read_bytes().strip():
        raise RuntimeError("RESTIC_PASSWORD_FILE must contain a nonempty password")
    if restic("cat", "config", capture=True).returncode != 0:
        raise RuntimeError("Repository check failed")


def init_repository() -> None:
    repository = Path(required("RESTIC_REPOSITORY"))
    if not repository.is_absolute() or repository == Path("/"):
        raise RuntimeError("RESTIC_REPOSITORY must be an absolute non-root path")
    repository.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (repository / "config").exists():
        if any(repository.iterdir()):
            raise RuntimeError("Backup directory is not an empty restic repository")
        restic("init")
    ensure_repository()


def database_name() -> str:
    return required("PGDATABASE")


def dump_database(destination: Path) -> None:
    run(
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--file",
        str(destination),
        database_name(),
    )
    if destination.stat().st_size == 0:
        raise RuntimeError("pg_dump produced an empty file")


def copy_objects(destination: Path) -> list[dict]:
    client = s3_client()
    bucket = required("S3_BUCKET")
    destination.mkdir(mode=0o700)
    objects = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            key = item["Key"]
            name = hashlib.sha256(key.encode("utf-8")).hexdigest()
            path = destination / name
            client.download_file(bucket, key, str(path))
            if path.stat().st_size != item["Size"]:
                raise RuntimeError(f"S3 object changed while backing up: {key}")
            objects.append(
                {
                    "key": key,
                    "file": name,
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    return objects


def write_manifest(stage: Path, objects: list[dict]) -> None:
    database = stage / "database.dump"
    manifest = {
        "format_version": 1,
        "backup_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": database_name(),
        "database_file": "database.dump",
        "database_size": database.stat().st_size,
        "database_sha256": sha256(database),
        "bucket": required("S3_BUCKET"),
        "objects": objects,
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_stage(stage: Path) -> dict:
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise RuntimeError("Unsupported backup format")
    database = stage / "database.dump"
    if database.stat().st_size != manifest["database_size"]:
        raise RuntimeError("Database dump size mismatch")
    if sha256(database) != manifest["database_sha256"]:
        raise RuntimeError("Database dump checksum mismatch")
    for item in manifest["objects"]:
        name = item["file"]
        if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
            raise RuntimeError("Invalid backup object filename")
        path = stage / "objects" / name
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
            raise RuntimeError(f"Object checksum mismatch: {item['key']}")
    return manifest


def backup() -> None:
    init_repository()
    with tempfile.TemporaryDirectory(prefix="amp-backup-") as directory:
        stage = Path(directory)
        dump_database(stage / "database.dump")
        objects = copy_objects(stage / "objects")
        write_manifest(stage, objects)
        verify_stage(stage)
        backup_id = json.loads(
            (stage / "manifest.json").read_text(encoding="utf-8")
        )["backup_id"]
        restic("backup", "--tag", backup_id, str(stage))
        snapshots = json.loads(
            restic("snapshots", "--json", "--tag", backup_id, capture=True).stdout
        )
        if len(snapshots) != 1 or not snapshots[0].get("id"):
            raise RuntimeError("restic did not report one completed snapshot")
        restic("check")
        print(f"Backup complete: {snapshots[0]['id']}")


def target_database_is_empty() -> bool:
    result = run(
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--command",
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p');",
        database_name(),
        capture=True,
    )
    return result.stdout.strip() == "0"


def target_bucket_is_empty() -> bool:
    response = s3_client().list_objects_v2(
        Bucket=required("S3_BUCKET"), MaxKeys=1
    )
    return not response.get("Contents")


def restore(snapshot: str, *, confirmed: bool) -> None:
    if not confirmed:
        raise RuntimeError("Restore requires --confirm")
    if len(snapshot) != 64 or any(
        char not in "0123456789abcdef" for char in snapshot
    ):
        raise RuntimeError("Restore requires a full 64-character snapshot ID")
    ensure_repository()
    if not target_database_is_empty() or not target_bucket_is_empty():
        raise RuntimeError("Restore target database and bucket must both be empty")
    with tempfile.TemporaryDirectory(prefix="amp-restore-") as directory:
        stage = Path(directory)
        restic("restore", snapshot, "--target", str(stage))
        manifest_paths = list(stage.rglob("manifest.json"))
        if len(manifest_paths) != 1:
            raise RuntimeError("Snapshot must contain exactly one manifest")
        data = manifest_paths[0].parent
        manifest = verify_stage(data)
        if (
            manifest["database"] != database_name()
            or manifest["bucket"] != required("S3_BUCKET")
        ):
            raise RuntimeError("Backup database or bucket name does not match target")
        run(
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--dbname",
            database_name(),
            str(data / "database.dump"),
        )
        client = s3_client()
        for item in manifest["objects"]:
            client.upload_file(
                str(data / "objects" / item["file"]),
                manifest["bucket"],
                item["key"],
            )
        print(f"Restore complete: {snapshot}")


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("backup")
    restore_parser = subcommands.add_parser("restore")
    restore_parser.add_argument("snapshot")
    restore_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        backup()
    else:
        restore(args.snapshot, confirmed=args.confirm)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Backup operation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
