-- Executado automaticamente pelo Postgres apenas na PRIMEIRA subida
-- (quando o volume ainda está vazio).

CREATE TABLE IF NOT EXISTS jobs (
    id                UUID PRIMARY KEY,               -- jobId (chave de idempotência)
    original_filename TEXT        NOT NULL,
    source_bucket     TEXT        NOT NULL,
    source_key        TEXT        NOT NULL,
    source_type       TEXT        NOT NULL,           -- 'image'
    source_format     TEXT        NOT NULL,           -- 'png'
    target_format     TEXT        NOT NULL,           -- 'jpg'
    notify_email      TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'PENDENTE'
                      CHECK (status IN ('PENDENTE', 'PROCESSANDO', 'CONCLUIDO', 'ERRO')),
    progress          SMALLINT    NOT NULL DEFAULT 0, -- 0..100 (barra de progresso)
    attempts          INT         NOT NULL DEFAULT 0,
    worker_id         TEXT,                           -- qual réplica processou (evidência)
    result_key        TEXT,
    public_url        TEXT,
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

-- Idempotência do e-mail: a PRIMARY KEY em job_id impede
-- registrar (e portanto enviar) duas notificações para o mesmo job.
CREATE TABLE IF NOT EXISTS notifications (
    job_id   UUID        PRIMARY KEY REFERENCES jobs (id),
    email    TEXT        NOT NULL,
    kind     TEXT        NOT NULL,                    -- 'CONCLUIDO' ou 'ERRO'
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
