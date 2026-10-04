"""Topologia do RabbitMQ (exchanges, filas e ligações).

Todos os serviços chamam declare_topology() ao conectar. As declarações são
idempotentes: se a fila já existe com os mesmos argumentos, nada acontece.
Assim, não importa qual serviço sobe primeiro.

    API --(convert)--> [conversion] --> conversion.requests --> workers
                                             |  reject (falha definitiva)
                                             v
                                  [conversion.dlx] --> conversion.dlq

    worker (falha temporária) --> conversion.retry (espera TTL)
                                     |  expira
                                     v
                                  [conversion] --> conversion.requests (nova tentativa)

    worker --(job.completed / job.failed)--> [jobs.events] --> notifications --> notificador
                                                                  |  excedeu 5 entregas
                                                                  v
                                                 [notifications.dlx] --> notifications.dlq
"""
from __future__ import annotations

import logging
import time

import pika

log = logging.getLogger(__name__)

# Conversão
CONVERSION_EXCHANGE = "conversion"
CONVERSION_ROUTING_KEY = "convert"
CONVERSION_QUEUE = "conversion.requests"
CONVERSION_RETRY_QUEUE = "conversion.retry"
CONVERSION_DLX = "conversion.dlx"
CONVERSION_DLQ = "conversion.dlq"
MAX_PRIORITY = 10

# Eventos de término e notificação
EVENTS_EXCHANGE = "jobs.events"
EVENT_COMPLETED = "job.completed"
EVENT_FAILED = "job.failed"
NOTIFY_QUEUE = "notifications"
NOTIFY_DLX = "notifications.dlx"
NOTIFY_DLQ = "notifications.dlq"
NOTIFY_DELIVERY_LIMIT = 5

ALL_QUEUES = [
    CONVERSION_QUEUE,
    CONVERSION_RETRY_QUEUE,
    CONVERSION_DLQ,
    NOTIFY_QUEUE,
    NOTIFY_DLQ,
]

PERSISTENT = pika.DeliveryMode.Persistent


def declare_topology(ch) -> None:
    # --- conversão ---------------------------------------------------------
    ch.exchange_declare(CONVERSION_EXCHANGE, exchange_type="direct", durable=True)
    ch.exchange_declare(CONVERSION_DLX, exchange_type="direct", durable=True)

    ch.queue_declare(
        CONVERSION_QUEUE,
        durable=True,
        arguments={
            "x-max-priority": MAX_PRIORITY,               # fila com prioridade
            "x-dead-letter-exchange": CONVERSION_DLX,     # reject(requeue=False) -> DLQ
        },
    )
    ch.queue_bind(CONVERSION_QUEUE, CONVERSION_EXCHANGE, routing_key=CONVERSION_ROUTING_KEY)

    ch.queue_declare(CONVERSION_DLQ, durable=True)
    ch.queue_bind(CONVERSION_DLQ, CONVERSION_DLX, routing_key=CONVERSION_ROUTING_KEY)

    # Fila de espera para retentativa: ninguém consome. Cada mensagem é
    # publicada com "expiration" (TTL); ao expirar, o RabbitMQ a devolve à
    # exchange de conversão -> retry com atraso, sem sleep no worker.
    ch.queue_declare(
        CONVERSION_RETRY_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": CONVERSION_EXCHANGE,
            "x-dead-letter-routing-key": CONVERSION_ROUTING_KEY,
        },
    )

    # --- eventos / notificação -------------------------------------------
    ch.exchange_declare(EVENTS_EXCHANGE, exchange_type="topic", durable=True)
    ch.exchange_declare(NOTIFY_DLX, exchange_type="fanout", durable=True)

    # Quorum queue: conta as entregas sozinha e, passado o limite, manda a
    # mensagem para a DLX. Mostra um segundo mecanismo de retentativa.
    ch.queue_declare(
        NOTIFY_QUEUE,
        durable=True,
        arguments={
            "x-queue-type": "quorum",
            "x-delivery-limit": NOTIFY_DELIVERY_LIMIT,
            "x-dead-letter-exchange": NOTIFY_DLX,
        },
    )
    ch.queue_bind(NOTIFY_QUEUE, EVENTS_EXCHANGE, routing_key="job.*")

    ch.queue_declare(NOTIFY_DLQ, durable=True)
    ch.queue_bind(NOTIFY_DLQ, NOTIFY_DLX)


def connect(url: str, attempts: int = 30, delay: float = 2.0) -> pika.BlockingConnection:
    """Conecta ao RabbitMQ, tentando de novo enquanto o broker sobe."""
    params = pika.URLParameters(url)
    params.heartbeat = 60
    params.blocked_connection_timeout = 30
    for i in range(1, attempts + 1):
        try:
            return pika.BlockingConnection(params)
        except pika.exceptions.AMQPConnectionError as exc:
            log.warning("RabbitMQ indisponível (tentativa %d/%d): %s", i, attempts, exc)
            time.sleep(delay)
    raise RuntimeError("não foi possível conectar ao RabbitMQ")
