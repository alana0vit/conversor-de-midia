"""Acesso à tabela de jobs pela API (criação, consulta e métricas)."""
from __future__ import annotations

from typing import Optional

from mediaconv_common.contract import ERRO, PENDENTE

_COLUMNS = """
    id, original_filename, source_format, target_format, notify_email, status,
    progress, attempts, worker_id, public_url, error_message,
    created_at, started_at, finished_at
"""


class JobRepository:
    def __init__(self, pool):
        self.pool = pool

    def create(self, *, job_id, original_filename, source_bucket, source_key, source_type,
               source_format, target_format, notify_email) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, original_filename, source_bucket, source_key, source_type,
                                  source_format, target_format, notify_email, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (job_id, original_filename, source_bucket, source_key, source_type,
                 source_format, target_format, notify_email, PENDENTE),
            )

    def mark_enqueue_failed(self, job_id, error: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status=%s, error_message=%s, finished_at=now(), updated_at=now() WHERE id=%s",
                (ERRO, error, job_id),
            )

    def get(self, job_id) -> Optional[dict]:
        with self.pool.connection() as conn:
            return conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE id=%s", (job_id,)).fetchone()

    def list_recent(self, limit: int) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(
                f"SELECT {_COLUMNS} FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()

    def changed_since(self, since) -> list[dict]:
        """Jobs alterados desde 'since' (alimenta o stream de eventos da interface)."""
        with self.pool.connection() as conn:
            return conn.execute(
                f"SELECT {_COLUMNS}, updated_at FROM jobs WHERE updated_at >= %s ORDER BY updated_at",
                (since,),
            ).fetchall()

    def metrics(self) -> dict:
        with self.pool.connection() as conn:
            by_status = {
                r["status"]: r["total"]
                for r in conn.execute("SELECT status, count(*) AS total FROM jobs GROUP BY status")
            }
            avg = conn.execute(
                """
                SELECT avg(extract(epoch FROM finished_at - started_at)) AS avg_s
                FROM jobs WHERE status='CONCLUIDO' AND started_at IS NOT NULL
                """
            ).fetchone()["avg_s"]
            per_worker = {
                r["worker_id"]: r["total"]
                for r in conn.execute(
                    """
                    SELECT worker_id, count(*) AS total FROM jobs
                    WHERE status='CONCLUIDO' AND worker_id IS NOT NULL
                    GROUP BY worker_id ORDER BY worker_id
                    """
                )
            }
        done = by_status.get("CONCLUIDO", 0)
        failed = by_status.get("ERRO", 0)
        return {
            "jobsByStatus": by_status,
            "avgConversionSeconds": round(float(avg), 2) if avg is not None else None,
            "errorRate": round(failed / (done + failed), 4) if (done + failed) else 0.0,
            "completedPerWorker": per_worker,
        }
