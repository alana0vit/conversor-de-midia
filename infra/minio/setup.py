"""Configura o object storage (MinIO/Silo). Idempotente: pode rodar várias vezes.

1. cria os buckets 'uploads' (privado) e 'converted';
2. libera leitura pública (somente GetObject) no bucket 'converted' -> RF04;
3. aplica lifecycle: objetos convertidos expiram após N dias (pontuação extra).

Usa a mesma biblioteca (minio-py) que a API e o worker, então não depende
da imagem do cliente 'mc'.
"""
import json
import os
import sys
import time
from urllib.parse import urlparse

from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
uploads = os.environ.get("UPLOADS_BUCKET", "uploads")
converted = os.environ.get("CONVERTED_BUCKET", "converted")
days = int(os.environ.get("CONVERTED_EXPIRATION_DAYS", "7"))

url = urlparse(endpoint)
client = Minio(
    url.netloc,
    access_key=os.environ["MINIO_ACCESS_KEY"],
    secret_key=os.environ["MINIO_SECRET_KEY"],
    secure=url.scheme == "https",
)


def log(msg: str) -> None:
    print(f"[minio-init] {msg}", flush=True)


# Espera o servidor aceitar conexões (substitui o healthcheck do container).
for attempt in range(1, 61):
    try:
        client.list_buckets()
        break
    except Exception as exc:  # noqa: BLE001
        log(f"aguardando o object storage ({attempt}/60): {type(exc).__name__}")
        time.sleep(2)
else:
    log("object storage não respondeu; abortando")
    sys.exit(1)

for bucket in (uploads, converted):
    if client.bucket_exists(bucket):
        log(f"bucket '{bucket}' já existe")
    else:
        client.make_bucket(bucket)
        log(f"bucket '{bucket}' criado")

public_read = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetObject"],                       # só download; sem listar nem gravar
        "Resource": [f"arn:aws:s3:::{converted}/*"],
    }],
}
client.set_bucket_policy(converted, json.dumps(public_read))
log(f"leitura pública aplicada em '{converted}'")

client.set_bucket_lifecycle(converted, LifecycleConfig([
    Rule(ENABLED, rule_filter=Filter(prefix=""), rule_id="expira-convertidos",
         expiration=Expiration(days=days)),
]))
log(f"lifecycle aplicado: objetos de '{converted}' expiram em {days} dia(s)")
log("concluído")
