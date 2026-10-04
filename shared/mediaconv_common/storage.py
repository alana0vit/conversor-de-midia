"""Acesso ao MinIO (S3). Todas as operações usadas são idempotentes."""
from __future__ import annotations

import io
from urllib.parse import unquote, urlparse

from minio import Minio
from minio.error import S3Error

_MISSING = {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}


def parse_object_url(url: str) -> tuple[str, str]:
    """'http://minio:9000/uploads/abc.png' -> ('uploads', 'abc.png')."""
    path = unquote(urlparse(url).path).lstrip("/")
    bucket, _, key = path.partition("/")
    if not bucket or not key:
        raise ValueError(f"URL de objeto inválida: {url}")
    return bucket, key


class ObjectStorage:
    def __init__(self, endpoint_url: str, access_key: str, secret_key: str):
        parsed = urlparse(endpoint_url)
        self.endpoint_url = endpoint_url.rstrip("/")
        self.client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )

    def object_url(self, bucket: str, key: str) -> str:
        """URL interna (rede do Docker). É ela que vai na mensagem."""
        return f"{self.endpoint_url}/{bucket}/{key}"

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(bucket, key, io.BytesIO(data), len(data), content_type=content_type)
        return self.object_url(bucket, key)

    def download_url(self, url: str, dest_path: str) -> None:
        bucket, key = parse_object_url(url)
        self.client.fget_object(bucket, key, dest_path)

    def upload_file(self, bucket: str, key: str, path: str, content_type: str) -> None:
        self.client.fput_object(bucket, key, path, content_type=content_type)

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.client.stat_object(bucket, key)
            return True
        except S3Error as exc:
            if exc.code in _MISSING:
                return False
            raise

    def remove(self, bucket: str, key: str) -> None:
        # Remover um objeto que não existe não é erro no S3 -> idempotente.
        self.client.remove_object(bucket, key)
