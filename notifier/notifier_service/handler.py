"""Regra de negócio do notificador: enviar no máximo um e-mail por job."""
from __future__ import annotations

import logging
from contextlib import contextmanager

from mediaconv_common.contract import JobFinishedEvent

log = logging.getLogger("notifier.handler")

SENT = "sent"
DUPLICATE = "duplicate"


class NotificationRepository:
    def __init__(self, pool):
        self.pool = pool   # pool SEM autocommit: usamos transação explícita

    @contextmanager
    def reserve(self, evt: JobFinishedEvent):
        """Reserva o envio do job dentro de uma transação.

        Entrega True se este é o primeiro envio; False se já foi enviado.
        Se o envio falhar (exceção dentro do with), a transação é desfeita e
        a reserva some, permitindo nova tentativa.
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    INSERT INTO notifications (job_id, email, kind) VALUES (%s, %s, %s)
                    ON CONFLICT (job_id) DO NOTHING RETURNING job_id
                    """,
                    (evt.jobId, evt.notifyEmail, evt.status),
                ).fetchone()
                yield row is not None


class NotificationHandler:
    def __init__(self, repo, mailer):
        self.repo = repo
        self.mailer = mailer

    def handle(self, evt: JobFinishedEvent) -> str:
        with self.repo.reserve(evt) as first_time:
            if not first_time:
                return DUPLICATE
            # Janela de risco: se o processo morrer DEPOIS do SMTP aceitar e ANTES
            # do COMMIT, o e-mail pode sair duas vezes (at-least-once). Ver README.
            self.mailer.send(evt)
            return SENT
