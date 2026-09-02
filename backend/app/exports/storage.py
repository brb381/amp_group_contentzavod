from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.exports.config import ExportStorageSettings


@dataclass(frozen=True)
class ArtifactDownload:
    body: BinaryIO
    content_length: int
    content_type: str

    def chunks(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        try:
            while chunk := self.body.read(chunk_size):
                yield chunk
        finally:
            self.body.close()


class ArtifactStore(Protocol):
    def upload(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        content_sha256: str,
    ) -> None: ...

    def download(self, *, key: str) -> ArtifactDownload: ...

    def delete(self, *, key: str) -> None: ...


class S3ArtifactStore:
    def __init__(self, settings: ExportStorageSettings):
        import boto3
        from botocore.config import Config

        addressing_style = "path" if settings.s3_force_path_style else "virtual"
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def upload(
        self,
        *,
        key: str,
        path: Path,
        content_type: str,
        content_sha256: str,
    ) -> None:
        with path.open("rb") as file_handle:
            self._client.upload_fileobj(
                file_handle,
                self._bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": content_sha256},
                },
            )

    def download(self, *, key: str) -> ArtifactDownload:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return ArtifactDownload(
            body=response["Body"],
            content_length=int(response["ContentLength"]),
            content_type=response.get("ContentType") or "application/octet-stream",
        )

    def delete(self, *, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
