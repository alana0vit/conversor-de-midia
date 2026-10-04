"""Worker de conversão: consome a fila e traduz as decisões do JobProcessor em
operações do RabbitMQ.

  * auto_ack=False     -> a mensagem só sai da fila quando damos ack. Se o
                          container morrer antes, o RabbitMQ a entrega a outro worker.
  * prefetch_count=1   -> cada worker recebe UMA mensagem por vez. Com N workers,
                          o broker reparte a fila entre eles (competing consumers).
  * confirm_delivery   -> eventos e retentativas publicados pelo worker têm
                          confirmação do broker antes de darmos ack na original.
"""
from __future__ import annotations

import logging
import signal
import socket
import sys

import pika

from mediaconv_common import topology
from mediaconv_common.contract import (
    CONCLUIDO,
    ContractError,
    ConversionRequest,
    JobFinishedEvent,
)
from mediaconv_common.settings import env, env_float, env_int, make_pool, setup_logging
from mediaconv_common.storage import ObjectStorage

from .processor import JobProcessor, Outcome, WorkerSettings
from .repository import WorkerRepository

log = logging.getLogger("worker")


class EventPublisher:
    def __init__(self, channel):
        self.ch = channel

    def publish(self, event: JobFinishedEvent) -> None:
        routing_key = topology.EVENT_COMPLETED if event.status == CONCLUIDO else topology.EVENT_FAILED
        self.ch.basic_publish(
            exchange=topology.EVENTS_EXCHANGE,
            routing_key=routing_key,
            body=event.to_json(),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=topology.PERSISTENT,
                message_id=event.jobId,
            ),
            mandatory=True,
        )


def publish_retry(ch, req: ConversionRequest, delay_ms: int, priority: int | None) -> None:
    """Coloca a nova tentativa na fila de espera; ela volta à fila principal após o TTL."""
    ch.basic_publish(
        exchange="",                                   # exchange padrão: rota = nome da fila
        routing_key=topology.CONVERSION_RETRY_QUEUE,
        body=req.to_json(),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=topology.PERSISTENT,
            expiration=str(delay_ms),                  # TTL desta mensagem
            priority=priority,
            message_id=req.jobId,
        ),
        mandatory=True,
    )


def run() -> None:
    setup_logging("worker")
    worker_id = socket.gethostname()                   # id do container: aparece na UI
    settings = WorkerSettings(
        converted_bucket=env("CONVERTED_BUCKET", "converted"),
        public_base_url=env("MINIO_PUBLIC_URL", "http://localhost:9000"),
        max_attempts=env_int("MAX_ATTEMPTS", 3),
        conversion_delay_seconds=env_float("CONVERSION_DELAY_SECONDS", 0),
    )
    retry_delay_ms = env_int("RETRY_DELAY_MS", 5000)
    repo = WorkerRepository(make_pool(env("DATABASE_URL"), autocommit=True, max_size=2))
    storage = ObjectStorage(env("MINIO_ENDPOINT"), env("MINIO_ACCESS_KEY"), env("MINIO_SECRET_KEY"))

    conn = topology.connect(env("RABBITMQ_URL"))
    ch = conn.channel()
    topology.declare_topology(ch)
    ch.confirm_delivery()
    ch.basic_qos(prefetch_count=1)

    processor = JobProcessor(repo, storage, EventPublisher(ch), settings, worker_id, sleep=conn.sleep)

    def on_message(channel, method, props, body):
        tag = method.delivery_tag
        try:
            req = ConversionRequest.from_json(body)
        except ContractError as exc:
            log.error("mensagem fora do contrato -> DLQ: %s", exc)
            channel.basic_reject(tag, requeue=False)
            return

        log.info("recebido job %s (tentativa %d%s)", req.jobId, req.attempt,
                 ", REENTREGA" if method.redelivered else "")
        try:
            result = processor.handle(req, redelivered=method.redelivered)
        except Exception:  # noqa: BLE001  (ex.: banco fora do ar antes de qualquer decisão)
            log.exception("erro inesperado no job %s; devolvendo à fila", req.jobId)
            conn.sleep(2)
            channel.basic_nack(tag, requeue=True)
            return

        if result.outcome is Outcome.RETRY:
            publish_retry(channel, result.retry_message, retry_delay_ms, props.priority)
            channel.basic_ack(tag)
            log.warning("job %s: falha temporária (%s) -> retry em %d ms",
                        req.jobId, result.reason, retry_delay_ms)
        elif result.outcome is Outcome.DEAD:
            channel.basic_reject(tag, requeue=False)      # dead-letter -> conversion.dlq
            log.error("job %s: falha definitiva -> DLQ (%s)", req.jobId, result.reason)
        else:
            channel.basic_ack(tag)                        # ack SÓ depois de tudo gravado
            log.info("job %s: ack (%s)", req.jobId, result.reason)

    ch.basic_consume(topology.CONVERSION_QUEUE, on_message, auto_ack=False)
    log.info("worker %s aguardando mensagens em '%s'", worker_id, topology.CONVERSION_QUEUE)
    ch.start_consuming()


def main() -> None:
    # docker stop envia SIGTERM; transformamos em saída limpa. Mensagem sem ack
    # volta para a fila automaticamente quando a conexão fecha.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        run()
    except (KeyboardInterrupt, SystemExit):
        log.info("encerrando worker")


if __name__ == "__main__":
    main()
