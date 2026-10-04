"""Contrato das mensagens trocadas pelo broker.

Este arquivo é a ÚNICA interface entre a API, os workers e o notificador.
Nenhum serviço importa código de outro serviço: todos falam apenas por
estas duas mensagens.

Regra de ouro: a mensagem transporta metadados e a *referência* ao arquivo
(sourceUrl). O conteúdo binário nunca passa pelo broker.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Optional

CONTRACT_VERSION = 1

SUPPORTED_CONVERSIONS: dict[str, set[str]] = {
    "png": {"jpg"},
    "jpg": {"png"},
}

SOURCE_TYPES = {"png": "image", "jpg": "image"}        # formato -> tipo de mídia
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg"}

# Extensões diferentes para o mesmo formato.
FORMAT_ALIASES = {"jpeg": "jpg"}


def normalize_format(value: str) -> str:
    value = (value or "").strip().lower().lstrip(".")
    return FORMAT_ALIASES.get(value, value)


# Status persistidos no banco (sem acento para evitar problemas de encoding;
# a interface exibe com acento).
PENDENTE = "PENDENTE"
PROCESSANDO = "PROCESSANDO"
CONCLUIDO = "CONCLUIDO"
ERRO = "ERRO"
FINAL_STATUSES = {CONCLUIDO, ERRO}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContractError(ValueError):
    """Mensagem fora do contrato (irrecuperável: vai direto para a DLQ)."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_supported(source_format: str, target_format: str) -> bool:
    return target_format in SUPPORTED_CONVERSIONS.get(source_format, set())


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value or ""))


def priority_for_size(size_bytes: int) -> int:
    """Prioridade na fila (0-10): arquivos menores passam na frente.

    Adaptação da pontuação extra "imagens antes de vídeos longos" para um
    projeto com um único formato: o critério passa a ser o tamanho.
    """
    mb = size_bytes / (1024 * 1024)
    if mb < 1:
        return 9
    if mb < 5:
        return 5
    return 1


def _require(data: dict, field: str, kind: type):
    if field not in data:
        raise ContractError(f"campo obrigatório ausente: {field}")
    value = data[field]
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ContractError(f"campo {field} deveria ser {kind.__name__}")
    return value


def _parse_json(body: bytes | str) -> dict:
    try:
        data = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("a mensagem deve ser um objeto JSON")
    return data


def _check_uuid(value: str) -> None:
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise ContractError(f"jobId não é um UUID: {value}") from exc


@dataclass(frozen=True)
class ConversionRequest:
    """Mensagem da fila de conversão (API -> workers)."""

    jobId: str
    sourceUrl: str
    sourceType: str
    sourceFormat: str
    targetFormat: str
    notifyEmail: str
    requestedAt: str
    attempt: int = 1

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    def with_attempt(self, attempt: int) -> "ConversionRequest":
        return replace(self, attempt=attempt)

    @classmethod
    def from_json(cls, body: bytes | str) -> "ConversionRequest":
        data = _parse_json(body)
        msg = cls(
            jobId=_require(data, "jobId", str),
            sourceUrl=_require(data, "sourceUrl", str),
            sourceType=_require(data, "sourceType", str),
            sourceFormat=normalize_format(_require(data, "sourceFormat", str)),
            targetFormat=normalize_format(_require(data, "targetFormat", str)),
            notifyEmail=_require(data, "notifyEmail", str),
            requestedAt=_require(data, "requestedAt", str),
            attempt=int(data.get("attempt", 1)),
        )
        _check_uuid(msg.jobId)
        if not msg.sourceUrl.startswith(("http://", "https://")):
            raise ContractError("sourceUrl deve ser uma URL http(s)")
        if not is_supported(msg.sourceFormat, msg.targetFormat):
            raise ContractError(f"conversão não suportada: {msg.sourceFormat} -> {msg.targetFormat}")
        if msg.attempt < 1:
            raise ContractError("attempt deve ser >= 1")
        return msg


@dataclass(frozen=True)
class JobFinishedEvent:
    """Evento de término (workers -> notificador), publicado no sucesso e na falha definitiva."""

    jobId: str
    status: str                     # CONCLUIDO ou ERRO
    notifyEmail: str
    originalFilename: str
    targetFormat: str
    publicUrl: Optional[str]
    error: Optional[str]
    workerId: Optional[str]
    finishedAt: str

    def to_json(self) -> bytes:
        return json.dumps(asdict(self)).encode("utf-8")

    @classmethod
    def from_json(cls, body: bytes | str) -> "JobFinishedEvent":
        data = _parse_json(body)
        evt = cls(
            jobId=_require(data, "jobId", str),
            status=_require(data, "status", str),
            notifyEmail=_require(data, "notifyEmail", str),
            originalFilename=_require(data, "originalFilename", str),
            targetFormat=_require(data, "targetFormat", str),
            publicUrl=data.get("publicUrl"),
            error=data.get("error"),
            workerId=data.get("workerId"),
            finishedAt=_require(data, "finishedAt", str),
        )
        _check_uuid(evt.jobId)
        if evt.status not in FINAL_STATUSES:
            raise ContractError(f"status final inválido: {evt.status}")
        if evt.status == CONCLUIDO and not evt.publicUrl:
            raise ContractError("evento CONCLUIDO sem publicUrl")
        return evt
