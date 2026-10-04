"""Testes do CONSUMER: regras de idempotência, retry e DLQ do worker."""
import uuid

import pytest

from converter_worker.processor import JobProcessor, Outcome, WorkerSettings
from fakes import FakeEvents, FakeStorage, FakeWorkerRepo
from helpers import png_bytes
from mediaconv_common.contract import ConversionRequest


@pytest.fixture
def env():
    repo, storage, events = FakeWorkerRepo(), FakeStorage(), FakeEvents()
    settings = WorkerSettings(max_attempts=3, conversion_delay_seconds=0,
                              public_base_url="http://localhost:9000")
    proc = JobProcessor(repo, storage, events, settings, worker_id="worker-A", sleep=lambda s: None)
    return proc, repo, storage, events


def new_job(repo, storage, data=None):
    job_id = str(uuid.uuid4())
    repo.add(job_id)
    storage.put_bytes("uploads", f"{job_id}.png", data or png_bytes(), "image/png")
    req = ConversionRequest(
        jobId=job_id, sourceUrl=f"http://minio:9000/uploads/{job_id}.png", sourceType="image",
        sourceFormat="png", targetFormat="jpg", notifyEmail="aluno@exemplo.com",
        requestedAt="2026-01-01T00:00:00Z",
    )
    return job_id, req


def test_success_converts_cleans_and_notifies(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)

    result = proc.handle(req)

    assert result.outcome is Outcome.ACK
    assert storage.exists("converted", f"{job_id}.jpg")
    assert not storage.exists("uploads", f"{job_id}.png")          # origem removida
    job = repo.jobs[job_id]
    assert job["status"] == "CONCLUIDO"
    assert job["public_url"] == f"http://localhost:9000/converted/{job_id}.jpg"
    assert [e.status for e in events.published] == ["CONCLUIDO"]
    assert job_id not in repo.locks                                   # lock liberado


def test_same_message_twice_converts_only_once(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    proc.handle(req)
    attempts_before = repo.jobs[job_id]["attempts"]

    again = proc.handle(req)

    assert again.outcome is Outcome.ACK
    assert "duplicada" in again.reason
    assert repo.jobs[job_id]["attempts"] == attempts_before           # não reprocessou
    assert [k for k in storage.objects if k[0] == "converted"] == [("converted", f"{job_id}.jpg")]


def test_duplicate_while_other_worker_holds_lock_is_discarded(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    repo.locks.add(job_id)                                            # outro worker vivo

    result = proc.handle(req)

    assert result.outcome is Outcome.ACK
    assert repo.jobs[job_id]["status"] == "PENDENTE"
    assert not events.published


def test_redelivery_after_crash_reuses_existing_result(env):
    """Worker anterior gravou o JPG e morreu antes do ack: não converte de novo."""
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    repo.jobs[job_id].update(status="PROCESSANDO", attempts=1)
    storage.put_bytes("converted", f"{job_id}.jpg", b"jpg-anterior", "image/jpeg")

    result = proc.handle(req, redelivered=True)

    assert result.outcome is Outcome.ACK
    assert storage.objects[("converted", f"{job_id}.jpg")] == b"jpg-anterior"
    assert repo.jobs[job_id]["status"] == "CONCLUIDO"
    assert not storage.exists("uploads", f"{job_id}.png")


def test_transient_failure_asks_for_retry_with_next_attempt(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    storage.fail_download = 1

    result = proc.handle(req)

    assert result.outcome is Outcome.RETRY
    assert result.retry_message.attempt == 2
    assert repo.jobs[job_id]["status"] == "PENDENTE"
    assert storage.exists("uploads", f"{job_id}.png")
    assert not events.published


def test_gives_up_after_max_attempts_and_keeps_source(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    storage.fail_download = 99

    outcomes = []
    msg = req
    for _ in range(3):
        r = proc.handle(msg)
        outcomes.append(r.outcome)
        msg = r.retry_message or msg

    assert outcomes == [Outcome.RETRY, Outcome.RETRY, Outcome.DEAD]
    assert repo.jobs[job_id]["status"] == "ERRO"
    assert storage.exists("uploads", f"{job_id}.png")                 # preservada p/ diagnóstico
    assert [e.status for e in events.published] == ["ERRO"]


def test_worker_crash_loop_is_bounded(env):
    """Se o worker morre sempre (attempts já no limite), a mensagem vai para a DLQ."""
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    repo.jobs[job_id].update(status="PROCESSANDO", attempts=3)

    assert proc.handle(req, redelivered=True).outcome is Outcome.DEAD
    assert repo.jobs[job_id]["status"] == "ERRO"


def test_corrupted_image_fails_without_retries(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage, data=b"\x89PNG\r\n\x1a\nquebrado")

    result = proc.handle(req)

    assert result.outcome is Outcome.DEAD
    assert repo.jobs[job_id]["attempts"] == 1


def test_progress_reaches_100(env):
    proc, repo, storage, events = env
    proc.s.conversion_delay_seconds = 3
    job_id, req = new_job(repo, storage)
    proc.handle(req)
    assert repo.jobs[job_id]["progress"] == 100


def test_result_is_saved_as_download_with_friendly_name(env):
    proc, repo, storage, events = env
    job_id, req = new_job(repo, storage)
    repo.jobs[job_id]["original_filename"] = "férias 2026.png"
    proc.handle(req)
    assert storage.download_names[("converted", f"{job_id}.jpg")] == "férias 2026.jpg"


def test_attachment_header_keeps_accents_and_is_ascii_safe():
    from mediaconv_common.storage import attachment_header
    h = attachment_header('férias "1".jpg')
    h.encode("latin-1")                                  # cabeçalho HTTP válido
    assert h.startswith("attachment;")
    assert "filename*=UTF-8''f%C3%A9rias%20%221%22.jpg" in h
