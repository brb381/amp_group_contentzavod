import os
import uuid

import httpx
import pytest

from app.exports.config import ExportStorageSettings
from app.exports.storage import S3ArtifactStore


def _settings(prefix: str) -> ExportStorageSettings:
    endpoint = os.getenv("TEST_S3_ENDPOINT_URL")
    access_key = os.getenv(f"TEST_S3_{prefix}_ACCESS_KEY")
    secret_key = os.getenv(f"TEST_S3_{prefix}_SECRET_KEY")
    if not endpoint or not access_key or not secret_key:
        pytest.skip("S3 integration settings are not configured")
    return ExportStorageSettings(
        s3_endpoint_url=endpoint,
        s3_region="us-east-1",
        s3_bucket="private-exports",
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_force_path_style=True,
    )


def test_minio_worker_writes_api_reads_and_anonymous_access_is_denied(tmp_path):
    worker_store = S3ArtifactStore(_settings("WORKER"))
    api_store = S3ArtifactStore(_settings("API"))
    key = f"integration/{uuid.uuid4()}.csv"
    content = "Номер заявки;Сумма\r\nPAY-TEST;1,00\r\n".encode("utf-8")
    source = tmp_path / "report.csv"
    source.write_bytes(content)

    worker_store.upload(
        key=key,
        path=source,
        content_type="text/csv; charset=utf-8",
        content_sha256="0" * 64,
    )
    try:
        artifact = api_store.download(key=key)
        assert b"".join(artifact.chunks()) == content

        with pytest.raises(Exception):
            api_store.upload(
                key=f"integration/api-write-{uuid.uuid4()}.csv",
                path=source,
                content_type="text/csv",
                content_sha256="0" * 64,
            )

        public_endpoint = os.environ["TEST_S3_PUBLIC_ENDPOINT_URL"].rstrip("/")
        public = httpx.get(
            f"{public_endpoint}/private-exports/{key}", timeout=5
        )
        assert public.status_code == 403
    finally:
        worker_store.delete(key=key)

    with pytest.raises(Exception):
        api_store.download(key=key)
