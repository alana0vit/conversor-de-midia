"""Publicação das mensagens de conversão no RabbitMQ.

Garantias do lado do producer:
  * exchange e fila duráveis + mensagem persistente -> sobrevivem a restart do broker;
  * publisher confirms -> basic_publish só retorna quando o broker confirmou
    que gravou a mensagem;
  * mandatory=True -> se a mensagem não tiver fila de destino, é erro (não some calada).
"""
from __future__ import annotations

import logging
import threading

import pika

from mediaconv_common import topology
from mediaconv_common.contract import ConversionRequest

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


class RabbitPublisher:
    def __init__(self, url: str):
        self.url = url
        self._lock = threading.Lock()   # pika BlockingConnection não é thread-safe
        self._conn = None
        self._ch = None

    def _channel(self):
        if self._conn is None or self._conn.is_closed or self._ch is None or self._ch.is_closed:
            self._conn = topology.connect(self.url, attempts=3, delay=1)
            self._ch = self._conn.channel()
            topology.declare_topology(self._ch)
            self._ch.confirm_delivery()
        return self._ch

    def publish(self, message: ConversionRequest, priority: int) -> None:
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=topology.PERSISTENT,
            priority=priority,
            message_id=message.jobId,       # facilita rastrear no painel do RabbitMQ
        )
        with self._lock:
            for attempt in (1, 2):          # 2ª tentativa após reconectar
                try:
                    self._channel().basic_publish(
                        exchange=topology.CONVERSION_EXCHANGE,
                        routing_key=topology.CONVERSION_ROUTING_KEY,
                        body=message.to_json(),
                        properties=props,
                        mandatory=True,
                    )
                    return
                except (pika.exceptions.UnroutableError, pika.exceptions.NackError) as exc:
                    raise PublishError(f"broker recusou a mensagem: {exc}") from exc
                except pika.exceptions.AMQPError as exc:
                    log.warning("falha ao publicar (tentativa %d): %s", attempt, exc)
                    self._conn = None
            raise PublishError("RabbitMQ indisponível")

    def queue_depths(self) -> dict[str, int | None]:
        """Tamanho de cada fila (declaração passiva: só consulta, não cria)."""
        result: dict[str, int | None] = {}
        with self._lock:
            try:
                conn = self._channel().connection
            except Exception:  # noqa: BLE001
                return {q: None for q in topology.ALL_QUEUES}
            for queue in topology.ALL_QUEUES:
                try:
                    ch = conn.channel()   # canal descartável: erro na passiva fecha o canal
                    result[queue] = ch.queue_declare(queue, passive=True).method.message_count
                    ch.close()
                except Exception:  # noqa: BLE001
                    result[queue] = None
        return result

    def close(self) -> None:
        if self._conn and self._conn.is_open:
            self._conn.close()
