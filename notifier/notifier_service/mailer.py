"""Montagem e envio do e-mail via SMTP (Mailpit no ambiente local)."""
from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage

from mediaconv_common.contract import CONCLUIDO, JobFinishedEvent


def build_message(evt: JobFinishedEvent, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = evt.notifyEmail
    name = evt.originalFilename
    target = evt.targetFormat.upper()

    if evt.status == CONCLUIDO:
        msg["Subject"] = f"Sua imagem {name} foi convertida para {target}"
        text = (
            f"Olá!\n\nA conversão de {name} para {target} foi concluída.\n\n"
            f"Baixe o arquivo aqui:\n{evt.publicUrl}\n\n"
            f"Pedido: {evt.jobId}\n"
        )
        body = (
            f"<p>Olá!</p><p>A conversão de <strong>{html.escape(name)}</strong> para "
            f"{target} foi concluída.</p>"
            f'<p><a href="{html.escape(evt.publicUrl or "")}">Baixar o arquivo convertido</a></p>'
            f'<p style="color:#666;font-size:12px">Pedido {evt.jobId}</p>'
        )
    else:
        msg["Subject"] = f"Não foi possível converter {name}"
        text = (
            f"Olá!\n\nA conversão de {name} para {target} falhou.\n\n"
            f"Motivo: {evt.error}\n\nPedido: {evt.jobId}\n"
        )
        body = (
            f"<p>Olá!</p><p>A conversão de <strong>{html.escape(name)}</strong> para "
            f"{target} falhou.</p><p>Motivo: {html.escape(evt.error or 'desconhecido')}</p>"
            f'<p style="color:#666;font-size:12px">Pedido {evt.jobId}</p>'
        )

    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    return msg


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, user: str = "", password: str = "",
                 starttls: bool = False):
        self.host, self.port, self.sender = host, port, sender
        self.user, self.password, self.starttls = user, password, starttls

    def send(self, evt: JobFinishedEvent) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(build_message(evt, self.sender))
