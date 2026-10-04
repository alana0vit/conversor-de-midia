"""Regras de processamento de UMA mensagem de conversão.

Este módulo não conhece o RabbitMQ: recebe a mensagem já interpretada e
devolve uma decisão (ACK, RETRY ou DEAD). Quem traduz a decisão em
ack/reject/publicação é o main.py. Essa separação permite testar todas as
regras de idempotência e falha sem subir broker, banco ou MinIO.

Ordem das etapas (cada uma é segura de repetir):
  1. lock do jobId          -> dois workers nunca processam o mesmo job juntos
  2. status CONCLUIDO/ERRO? -> mensagem duplicada: só finaliza de novo e confirma
  3. claim (+1 tentativa)   -> limite de tentativas vale até para worker que morre
  4. resultado já existe?   -> reentrega após queda: pula a conversão
  5. baixa, converte, grava converted/<jobId>.jpg (nome fixo = sem duplicatas)
  6. marca CONCLUIDO no banco
  7. apaga a origem e publica o evento de término
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from mediaconv_common.contract import (
    CONCLUIDO,
    CONTENT_TYPES,
    ERRO,
    ConversionRequest,
    JobFinishedEvent,
    utc_now_iso,
)

from .converters import PermanentError, get_converter

log = logging.getLogger("worker.processor")


def download_name(original: str, target_format: str) -> str:
    """'ferias.png' + 'jpg' -> 'ferias.jpg' (nome sugerido ao baixar)."""
    stem = original.rsplit(".", 1)[0] if "." in original else original
    return f"{stem or 'arquivo'}.{target_format}"


def describe(exc: Exception) -> str:
    """Resumo legível de uma exceção (erros do S3 trazem texto enorme)."""
    code = getattr(exc, "code", None)
    if code:
        return f"{type(exc).__name__} {code}"
    return str(exc)[:200] or type(exc).__name__


class Outcome(Enum):
    ACK = "ack"        # terminou (com sucesso ou era duplicada): remove da fila
    RETRY = "retry"    # falha temporária: republica na fila de espera e confirma
    DEAD = "dead"      # falha definitiva: reject -> DLQ


@dataclass
class Result:
    outcome: Outcome
    reason: str
    retry_message: Optional[ConversionRequest] = None


@dataclass
class WorkerSettings:
    converted_bucket: str = "converted"
    public_base_url: str = "http://localhost:9000"
    max_attempts: int = 3
    conversion_delay_seconds: float = 0.0


class JobProcessor:
    def __init__(self, repo, storage, events, settings: WorkerSettings, worker_id: str,
                 sleep: Callable[[float], None]):
        self.repo = repo
        self.storage = storage
        self.events = events          # objeto com publish(JobFinishedEvent)
        self.s = settings
        self.worker_id = worker_id
        self.sleep = sleep            # no RabbitMQ: connection.sleep (mantém heartbeats)

    # ------------------------------------------------------------------
    def handle(self, req: ConversionRequest, redelivered: bool = False) -> Result:
        with self.repo.session() as db:
            if not db.try_lock(req.jobId):
                # Outro worker VIVO está com este job (mensagem duplicada chegando
                # em paralelo). Ele vai terminar o trabalho; esta cópia é descartada.
                return Result(Outcome.ACK, "duplicada: outro worker está processando este job")
            try:
                return self._handle_locked(db, req, redelivered)
            finally:
                db.unlock(req.jobId)

    # ------------------------------------------------------------------
    def _handle_locked(self, db, req: ConversionRequest, redelivered: bool) -> Result:
        job = db.get(req.jobId)
        if job is None:
            return Result(Outcome.DEAD, "jobId inexistente no banco")

        if job["status"] == CONCLUIDO:
            # Idempotência: não converte de novo. Refaz só a finalização,
            # caso o worker anterior tenha morrido antes de terminá-la
            # (o notificador descarta e-mail repetido).
            self._finalize(db, req, job)
            return Result(Outcome.ACK, "duplicada: job já concluído")
        if job["status"] == ERRO:
            return Result(Outcome.ACK, "duplicada: job já marcado como erro")

        attempts = db.claim(req.jobId, self.worker_id)
        if redelivered:
            log.warning("job %s REENTREGUE pelo broker (worker anterior caiu?) - tentativa %d",
                        req.jobId, attempts)
        if attempts > self.s.max_attempts:
            return self._fail(db, req, job,
                              f"limite de {self.s.max_attempts} tentativas excedido")

        result_key = f"{req.jobId}.{req.targetFormat}"
        try:
            if self.storage.exists(self.s.converted_bucket, result_key):
                log.info("job %s: resultado já existe no MinIO, pulando conversão", req.jobId)
            else:
                self._convert(db, req, result_key, download_name(job["original_filename"], req.targetFormat))
        except PermanentError as exc:
            return self._fail(db, req, job, str(exc))
        except Exception as exc:  # noqa: BLE001  (rede, MinIO fora do ar, disco...)
            if attempts < self.s.max_attempts:
                db.mark_retry(req.jobId, f"tentativa {attempts} falhou: {describe(exc)}")
                return Result(Outcome.RETRY, describe(exc), req.with_attempt(attempts + 1))
            return self._fail(db, req, job, f"falhou após {attempts} tentativas: {describe(exc)}")

        public_url = f"{self.s.public_base_url.rstrip('/')}/{self.s.converted_bucket}/{result_key}"
        db.mark_done(req.jobId, result_key, public_url)
        self._finalize(db, req, db.get(req.jobId))
        return Result(Outcome.ACK, "convertido")

    # ------------------------------------------------------------------
    def _convert(self, db, req: ConversionRequest, result_key: str, name: str) -> None:
        converter = get_converter(req.sourceFormat, req.targetFormat)
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, f"source.{req.sourceFormat}")
            dst = os.path.join(tmp, f"result.{req.targetFormat}")

            self.storage.download_url(req.sourceUrl, src)          # GET origem
            db.progress(req.jobId, 20)

            self._artificial_delay(db, req.jobId)                   # 20% -> 80%

            converter(src, dst)                                     # Pillow
            db.progress(req.jobId, 90)

            self.storage.upload_file(self.s.converted_bucket, result_key, dst,
                                     CONTENT_TYPES[req.targetFormat],
                                     download_name=name)               # PUT resultado
            # Só seguimos (e depois apagamos a origem) se o resultado está gravado.
            if not self.storage.exists(self.s.converted_bucket, result_key):
                raise RuntimeError("upload do resultado não confirmado pelo MinIO")

    def _artificial_delay(self, db, job_id: str) -> None:
        """Atraso configurável SÓ para demonstração (PNG->JPG leva milissegundos)."""
        total = self.s.conversion_delay_seconds
        if total <= 0:
            return
        steps = max(1, int(total))
        for i in range(steps):
            self.sleep(total / steps)
            db.progress(job_id, 20 + int(60 * (i + 1) / steps))

    def _finalize(self, db, req: ConversionRequest, job: dict) -> None:
        # Limpeza da origem: só depois do resultado persistido (etapa anterior).
        self.storage.remove(job["source_bucket"], job["source_key"])
        self.events.publish(JobFinishedEvent(
            jobId=req.jobId, status=CONCLUIDO, notifyEmail=job["notify_email"],
            originalFilename=job["original_filename"], targetFormat=job["target_format"],
            publicUrl=job["public_url"], error=None, workerId=job["worker_id"],
            finishedAt=utc_now_iso(),
        ))

    def _fail(self, db, req: ConversionRequest, job: dict, error: str) -> Result:
        # Falha definitiva: a origem FICA no bucket uploads para diagnóstico.
        db.mark_failed(req.jobId, error)
        self.events.publish(JobFinishedEvent(
            jobId=req.jobId, status=ERRO, notifyEmail=job["notify_email"],
            originalFilename=job["original_filename"], targetFormat=job["target_format"],
            publicUrl=None, error=error, workerId=self.worker_id, finishedAt=utc_now_iso(),
        ))
        return Result(Outcome.DEAD, error)
