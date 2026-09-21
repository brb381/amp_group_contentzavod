import hashlib
import json

import pytest

from ops import local_backup


def test_backup_manifest_verifies_database_and_objects(tmp_path, monkeypatch):
    monkeypatch.setenv("PGDATABASE", "amp")
    monkeypatch.setenv("S3_BUCKET", "private-exports")
    database = tmp_path / "database.dump"
    database.write_bytes(b"database snapshot")
    objects = tmp_path / "objects"
    objects.mkdir()
    key = "exports/2026/09/report.csv"
    filename = hashlib.sha256(key.encode()).hexdigest()
    content = b"report data"
    (objects / filename).write_bytes(content)
    records = [{
        "key": key,
        "file": filename,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }]

    local_backup.write_manifest(tmp_path, records)

    manifest = local_backup.verify_stage(tmp_path)
    assert manifest["database"] == "amp"
    assert manifest["objects"] == records
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest

    (objects / filename).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="Object checksum mismatch"):
        local_backup.verify_stage(tmp_path)


def test_restore_requires_confirmation_and_full_snapshot_id(monkeypatch):
    def unexpected_repository_check():
        raise AssertionError("Restore reached repository access")

    monkeypatch.setattr(local_backup, "ensure_repository", unexpected_repository_check)
    with pytest.raises(RuntimeError, match="--confirm"):
        local_backup.restore("a" * 64, confirmed=False)
    with pytest.raises(RuntimeError, match="full 64-character"):
        local_backup.restore("latest", confirmed=True)


def test_restore_refuses_nonempty_targets(monkeypatch):
    monkeypatch.setattr(local_backup, "ensure_repository", lambda: None)
    monkeypatch.setattr(local_backup, "target_database_is_empty", lambda: False)
    monkeypatch.setattr(
        local_backup,
        "target_bucket_is_empty",
        lambda: (_ for _ in ()).throw(AssertionError("Bucket should not be queried")),
    )
    with pytest.raises(RuntimeError, match="must both be empty"):
        local_backup.restore("a" * 64, confirmed=True)
