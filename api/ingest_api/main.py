"""API de ingestão (producer).

Responsabilidades: validar o pedido, gravar a origem no MinIO, registrar o job
como PENDENTE, publicar a mensagem e responder 202 com o jobId.
Esta API NÃO converte nada e não sabe quantos workers existem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from mediaconv_common.contract import (
    CONTENT_TYPES,
    FINAL_STATUSES,
    SOURCE_TYPES,
    SUPPORTED_CONVERSIONS,
    ConversionRequest,
    is_supported,
    is_valid_email,
    priority_for_size,
    utc_now_iso,
)
from mediaconv_common.settings import env, env_int, setup_logging

log = logging.getLogger("api")

# Assinaturas binárias: validamos o conteúdo, não só a extensão do nome.
MAGIC_BYTES = {"png": b"\x89PNG\r\n\x1a\n"}


@dataclass
class Settings:
    uploads_bucket: str = "uploads"
    max_upload_mb: int = 20

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            uploads_bucket=os.getenv("UPLOADS_BUCKET", "uploads"),
            max_upload_mb=env_int("MAX_UPLOAD_MB", 20),
        )


def serialize(job: dict) -> dict:
    duration = None
    if job.get("started_at") and job.get("finished_at"):
        duration = round((job["finished_at"] - job["started_at"]).total_seconds(), 2)
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "jobId": str(job["id"]),
        "originalFilename": job["original_filename"],
        "sourceFormat": job["source_format"],
        "targetFormat": job["target_format"],
        "status": job["status"],
        "progress": job["progress"],
        "attempts": job["attempts"],
        "workerId": job["worker_id"],
        "publicUrl": job["public_url"],
        "error": job["error_message"],
        "createdAt": iso(job["created_at"]),
        "startedAt": iso(job["started_at"]),
        "finishedAt": iso(job["finished_at"]),
        "durationSeconds": duration,
    }


def create_app(repo=None, storage=None, publisher=None, settings: Optional[Settings] = None) -> FastAPI:
    """Fábrica da aplicação. Nos testes, recebe dublês (fakes) no lugar dos serviços reais."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging("api")
        if app.state.repo is None:
            from mediaconv_common.settings import make_pool
            from mediaconv_common.storage import ObjectStorage

            from .publisher import RabbitPublisher
            from .repository import JobRepository

            app.state.repo = JobRepository(make_pool(env("DATABASE_URL"), autocommit=True))
            app.state.storage = ObjectStorage(
                env("MINIO_ENDPOINT"), env("MINIO_ACCESS_KEY"), env("MINIO_SECRET_KEY")
            )
            app.state.publisher = RabbitPublisher(env("RABBITMQ_URL"))
        yield
        if hasattr(app.state.publisher, "close"):
            app.state.publisher.close()

    app = FastAPI(title="Conversor de Mídia - API de Ingestão", lifespan=lifespan)
    app.state.repo = repo
    app.state.storage = storage
    app.state.publisher = publisher
    app.state.settings = settings or Settings.from_env()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/formats")
    def formats():
        return {src: sorted(targets) for src, targets in SUPPORTED_CONVERSIONS.items()}

    # Rota síncrona (def): o FastAPI a executa num pool de threads, então I/O
    # bloqueante (MinIO, Postgres, RabbitMQ) não trava o servidor.
    @app.post("/api/jobs", status_code=202)
    def create_job(
        file: UploadFile = File(...),
        targetFormat: str = Form(...),
        email: str = Form(...),
    ):
        st: Settings = app.state.settings
        email = email.strip()
        target = targetFormat.strip().lower().lstrip(".")
        filename = os.path.basename(file.filename or "arquivo")
        source = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        # 1) Validação ------------------------------------------------------
        if not is_valid_email(email):
            raise HTTPException(422, "E-mail inválido.")
        if source not in SUPPORTED_CONVERSIONS:
            raise HTTPException(415, f"Formato de origem .{source or '?'} não suportado. Envie um arquivo PNG.")
        if not is_supported(source, target):
            raise HTTPException(422, f"Conversão {source} → {target} não suportada.")

        limit = st.max_upload_mb * 1024 * 1024
        data = file.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"Arquivo maior que {st.max_upload_mb} MB.")
        if not data:
            raise HTTPException(422, "Arquivo vazio.")
        magic = MAGIC_BYTES.get(source)
        if magic and not data.startswith(magic):
            raise HTTPException(415, f"O conteúdo do arquivo não é um {source.upper()} válido.")

        job_id = str(uuid.uuid4())
        key = f"{job_id}.{source}"

        # 2) Origem no MinIO ------------------------------------------------
        try:
            source_url = app.state.storage.put_bytes(st.uploads_bucket, key, data, CONTENT_TYPES[source])
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao gravar no MinIO")
            raise HTTPException(503, "Armazenamento indisponível. Tente novamente.") from exc

        # 3) Registro do job (PENDENTE) ------------------------------------
        try:
            app.state.repo.create(
                job_id=job_id, original_filename=filename, source_bucket=st.uploads_bucket,
                source_key=key, source_type=SOURCE_TYPES[source], source_format=source,
                target_format=target, notify_email=email,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao registrar job")
            app.state.storage.remove(st.uploads_bucket, key)
            raise HTTPException(503, "Banco de dados indisponível. Tente novamente.") from exc

        # 4) Mensagem: só metadados + link, nunca o binário ------------------
        message = ConversionRequest(
            jobId=job_id, sourceUrl=source_url, sourceType=SOURCE_TYPES[source],
            sourceFormat=source, targetFormat=target, notifyEmail=email,
            requestedAt=utc_now_iso(), attempt=1,
        )
        try:
            app.state.publisher.publish(message, priority=priority_for_size(len(data)))
        except Exception as exc:  # noqa: BLE001
            log.exception("falha ao publicar job %s", job_id)
            app.state.repo.mark_enqueue_failed(job_id, "Falha ao enfileirar o pedido.")
            app.state.storage.remove(st.uploads_bucket, key)
            raise HTTPException(503, "Fila de conversão indisponível. Tente novamente.") from exc

        log.info("job %s aceito (%s, %d bytes)", job_id, filename, len(data))
        # 5) Resposta imediata: a conversão ainda nem começou.
        return {
            "jobId": job_id,
            "status": "PENDENTE",
            "statusUrl": f"/api/jobs/{job_id}",
            "eventsUrl": f"/api/jobs/{job_id}/events",
        }

    @app.get("/api/jobs")
    def list_jobs(limit: int = 30):
        return [serialize(j) for j in app.state.repo.list_recent(max(1, min(limit, 200)))]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: uuid.UUID):
        job = app.state.repo.get(str(job_id))
        if job is None:
            raise HTTPException(404, "Pedido não encontrado.")
        return serialize(job)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: uuid.UUID, request: Request):
        """Server-Sent Events: envia o estado do job sempre que ele muda."""

        async def stream():
            last = None
            while not await request.is_disconnected():
                job = await run_in_threadpool(app.state.repo.get, str(job_id))
                if job is None:
                    yield 'event: missing\ndata: {}\n\n'
                    return
                payload = json.dumps(serialize(job))
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                if job["status"] in FINAL_STATUSES:
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/events")
    async def all_events(request: Request):
        """Um único stream SSE com as mudanças de TODOS os jobs.

        Um stream por job esbarraria no limite de ~6 conexões simultâneas por
        servidor que os navegadores impõem em HTTP/1.1.
        """

        async def stream():
            since = datetime.now(timezone.utc) - timedelta(seconds=2)
            sent: dict[str, str] = {}
            yield "retry: 2000\n\n"          # reconexão automática em 2 s
            while not await request.is_disconnected():
                rows = await run_in_threadpool(app.state.repo.changed_since, since)
                for job in rows:
                    since = max(since, job["updated_at"])
                    job_id = str(job["id"])
                    payload = json.dumps(serialize(job))
                    if sent.get(job_id) != payload:      # só envia o que mudou
                        sent[job_id] = payload
                        yield f"data: {payload}\n\n"
                if len(sent) > 5000:
                    sent.clear()
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/metrics")
    def metrics():
        data = app.state.repo.metrics()
        data["queues"] = app.state.publisher.queue_depths()
        return data

    return app


app = create_app()
