"""Serviço de notificação: consome eventos de término e envia e-mails.

É um consumidor separado do worker: se o SMTP estiver fora do ar, as
conversões continuam normalmente e os e-mails ficam esperando na fila.
"""
from __future__ import annotations

import logging
import signal
import sys

from mediaconv_common import topology
from mediaconv_common.contract import ContractError, JobFinishedEvent
from mediaconv_common.settings import env, env_int, make_pool, setup_logging

from .handler import DUPLICATE, NotificationHandler, NotificationRepository
from .mailer import SmtpMailer

log = logging.getLogger("notifier")


def run() -> None:
    setup_logging("notifier")
    handler = NotificationHandler(
        NotificationRepository(make_pool(env("DATABASE_URL"), autocommit=False, max_size=2)),
        SmtpMailer(
            host=env("SMTP_HOST", "mailpit"),
            port=env_int("SMTP_PORT", 1025),
            sender=env("SMTP_FROM", "Conversor de Mídia <nao-responda@conversor.local>"),
            user=env("SMTP_USER", ""),
            password=env("SMTP_PASSWORD", ""),
            starttls=env("SMTP_STARTTLS", "false").lower() == "true",
        ),
    )

    conn = topology.connect(env("RABBITMQ_URL"))
    ch = conn.channel()
    topology.declare_topology(ch)
    ch.basic_qos(prefetch_count=1)

    def on_message(channel, method, _props, body):
        tag = method.delivery_tag
        try:
            evt = JobFinishedEvent.from_json(body)
        except ContractError as exc:
            log.error("evento fora do contrato -> DLQ: %s", exc)
            channel.basic_reject(tag, requeue=False)
            return
        try:
            outcome = handler.handle(evt)
        except Exception as exc:  # noqa: BLE001
            # A quorum queue conta as entregas; após o limite, vai para notifications.dlq.
            log.warning("falha ao notificar job %s (%s); nova tentativa em breve", evt.jobId, exc)
            conn.sleep(3)
            channel.basic_nack(tag, requeue=True)
            return
        channel.basic_ack(tag)
        if outcome == DUPLICATE:
            log.info("job %s: e-mail já enviado antes, evento duplicado ignorado", evt.jobId)
        else:
            log.info("job %s: e-mail (%s) enviado para %s", evt.jobId, evt.status, evt.notifyEmail)

    ch.basic_consume(topology.NOTIFY_QUEUE, on_message, auto_ack=False)
    log.info("notificador aguardando eventos em '%s'", topology.NOTIFY_QUEUE)
    ch.start_consuming()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        run()
    except (KeyboardInterrupt, SystemExit):
        log.info("encerrando notificador")


if __name__ == "__main__":
    main()
