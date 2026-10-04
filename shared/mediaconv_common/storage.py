"""Acesso ao MinIO (S3). Todas as operações usadas são idempotentes."""
from __future__ import annotations

import io
import re
from typing import Optional
from urllib.parse import quote, unquote, urlparse

from minio import Minio
from minio.error import S3Error

_MISSING = {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}


def attachment_header(filename: str) -> str:
    """Content-Disposition que faz o navegador BAIXAR o arquivo, com nome amigável.

    'filename' é a versão só-ASCII (navegadores antigos); 'filename*' (RFC 5987)
    carrega o nome original em UTF-8, com acentos.
    """
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "arquivo"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


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

    def upload_file(self, bucket: str, key: str, path: str, content_type: str,
                    download_name: Optional[str] = None) -> None:
        # Com download_name, o objeto é gravado com Content-Disposition: attachment.
        # Assim o link público BAIXA o arquivo em vez de só exibi-lo no navegador.
        metadata = {"Content-Disposition": attachment_header(download_name)} if download_name else None
        self.client.fput_object(bucket, key, path, content_type=content_type, metadata=metadata)

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
