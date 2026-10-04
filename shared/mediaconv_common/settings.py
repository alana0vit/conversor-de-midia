"""Leitura de variáveis de ambiente e utilitários comuns aos serviços."""
from __future__ import annotations

import logging
import os
import time


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"variável de ambiente obrigatória ausente: {name}")
    return value


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def setup_logging(service: str) -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format=f"%(asctime)s [{service}] %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pika").setLevel(logging.WARNING)


def make_pool(dsn: str, autocommit: bool, max_size: int = 5):
    """Pool de conexões do Postgres, esperando o banco ficar disponível."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    log = logging.getLogger("db")
    for i in range(1, 31):
        try:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=max_size,
                kwargs={"autocommit": autocommit, "row_factory": dict_row},
                open=True,
                timeout=10,
            )
            pool.wait(timeout=10)
            return pool
        except Exception as exc:  # noqa: BLE001
            log.warning("Postgres indisponível (tentativa %d/30): %s", i, exc)
            time.sleep(2)
    raise RuntimeError("não foi possível conectar ao Postgres")
