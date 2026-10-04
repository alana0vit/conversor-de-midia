"""Testes do consumidor de notificação: no máximo um e-mail por job."""
from contextlib import contextmanager

import pytest

from mediaconv_common.contract import JobFinishedEvent
from notifier_service.handler import DUPLICATE, SENT, NotificationHandler
from notifier_service.mailer import build_message

EVT = JobFinishedEvent(
    jobId="9f2c1a84-3b77-4e5a-9d21-6c0a8f4b1e30", status="CONCLUIDO",
    notifyEmail="aluno@exemplo.com", originalFilename="foto.png", targetFormat="jpg",
    publicUrl="http://localhost:9000/converted/9f2c1a84-3b77-4e5a-9d21-6c0a8f4b1e30.jpg",
    error=None, workerId="w1", finishedAt="2026-01-01T00:00:00Z",
)


class FakeRepo:
    """Imita a transação: a reserva só 'comita' se o bloco terminar sem erro."""

    def __init__(self):
        self.sent = set()

    @contextmanager
    def reserve(self, evt):
        first = evt.jobId not in self.sent
        yield first
        if first:
            self.sent.add(evt.jobId)


class FakeMailer:
    def __init__(self, fail=False):
        self.outbox, self.fail = [], fail

    def send(self, evt):
        if self.fail:
            raise ConnectionError("SMTP fora do ar")
        self.outbox.append(evt)


def test_sends_once_even_if_event_arrives_twice():
    mailer = FakeMailer()
    h = NotificationHandler(FakeRepo(), mailer)
    assert h.handle(EVT) == SENT
    assert h.handle(EVT) == DUPLICATE
    assert len(mailer.outbox) == 1


def test_smtp_failure_does_not_mark_as_sent():
    repo = FakeRepo()
    with pytest.raises(ConnectionError):
        NotificationHandler(repo, FakeMailer(fail=True)).handle(EVT)
    assert EVT.jobId not in repo.sent                # próxima entrega tenta de novo


def test_email_contains_public_link():
    msg = build_message(EVT, "conversor@local")
    assert msg["To"] == "aluno@exemplo.com"
    assert EVT.publicUrl in msg.get_body(("plain",)).get_content()
