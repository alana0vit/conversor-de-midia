"""Acesso ao banco pelo worker.

Cada mensagem é processada dentro de uma "sessão" que segura UMA conexão do
Postgres do começo ao fim. Nela o worker pega um *advisory lock* com o jobId.

Por que advisory lock? Ele é da sessão do banco: se o container do worker
morrer, a conexão cai e o Postgres libera o lock sozinho. Assim um worker
morto nunca deixa um job "travado", e dois workers vivos nunca convertem o
mesmo job ao mesmo tempo.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from mediaconv_common.contract import CONCLUIDO, ERRO, PENDENTE, PROCESSANDO


class JobSession:
    def __init__(self, conn):
        self.conn = conn

    def _one(self, sql, params):
        return self.conn.execute(sql, params).fetchone()

    def try_lock(self, job_id: str) -> bool:
        row = self._one("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok", (job_id,))
        return bool(row["ok"])

    def unlock(self, job_id: str) -> None:
        self.conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (job_id,))

    def get(self, job_id: str) -> Optional[dict]:
        return self._one(
            "SELECT id, status, original_filename, target_format, notify_email, "
            "source_bucket, source_key, public_url, error_message, worker_id FROM jobs WHERE id=%s",
            (job_id,),
        )

    def claim(self, job_id: str, worker_id: str) -> int:
        """Marca PROCESSANDO e conta a tentativa. Retorna o total de tentativas."""
        row = self._one(
            """
            UPDATE jobs SET status=%s, worker_id=%s, attempts=attempts+1, progress=0,
                            started_at=now(), updated_at=now()
            WHERE id=%s RETURNING attempts
            """,
            (PROCESSANDO, worker_id, job_id),
        )
        return row["attempts"]

    def progress(self, job_id: str, value: int) -> None:
        self.conn.execute(
            "UPDATE jobs SET progress=%s, updated_at=now() WHERE id=%s", (value, job_id)
        )

    def mark_done(self, job_id: str, result_key: str, public_url: str) -> None:
        self.conn.execute(
            """
            UPDATE jobs SET status=%s, progress=100, result_key=%s, public_url=%s,
                            error_message=NULL, finished_at=now(), updated_at=now()
            WHERE id=%s
            """,
            (CONCLUIDO, result_key, public_url, job_id),
        )

    def mark_retry(self, job_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status=%s, progress=0, error_message=%s, updated_at=now() WHERE id=%s",
            (PENDENTE, error, job_id),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status=%s, error_message=%s, finished_at=now(), updated_at=now() WHERE id=%s",
            (ERRO, error, job_id),
        )


class WorkerRepository:
    def __init__(self, pool):
        self.pool = pool   # pool com autocommit=True: cada UPDATE já é visível para a API

    @contextmanager
    def session(self):
        with self.pool.connection() as conn:
            yield JobSession(conn)
