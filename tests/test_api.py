"""Testes do PUBLISHER: a API valida, grava a origem, publica e responde 202."""
import json

import pytest
from fastapi.testclient import TestClient

from fakes import FakeStorage
from helpers import png_bytes
from ingest_api.main import Settings, create_app
from mediaconv_common.contract import ConversionRequest


class FakeApiRepo:
    def __init__(self):
        self.jobs = {}

    def create(self, **kw):
        self.jobs[kw["job_id"]] = {**kw, "status": "PENDENTE"}

    def mark_enqueue_failed(self, job_id, error):
        self.jobs[job_id].update(status="ERRO", error=error)


class FakePublisher:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def publish(self, message, priority):
        if self.fail:
            raise RuntimeError("broker fora do ar")
        self.messages.append((message, priority))


def make_client(fail_publish=False):
    repo, storage, pub = FakeApiRepo(), FakeStorage(), FakePublisher(fail_publish)
    app = create_app(repo=repo, storage=storage, publisher=pub, settings=Settings(max_upload_mb=1))
    return TestClient(app), repo, storage, pub


def post(client, data=None, name="foto.png", target="jpg", email="aluno@exemplo.com"):
    return client.post(
        "/api/jobs",
        files={"file": (name, data if data is not None else png_bytes(), "image/png")},
        data={"targetFormat": target, "email": email},
    )


def test_accepts_and_publishes_contract_message():
    client, repo, storage, pub = make_client()
    res = post(client)

    assert res.status_code == 202
    job_id = res.json()["jobId"]
    assert repo.jobs[job_id]["status"] == "PENDENTE"
    assert ("uploads", f"{job_id}.png") in storage.objects

    [(msg, priority)] = pub.messages
    assert isinstance(msg, ConversionRequest)
    body = json.loads(msg.to_json())
    assert body["jobId"] == job_id
    assert body["sourceUrl"] == f"http://minio:9000/uploads/{job_id}.png"
    assert body["targetFormat"] == "jpg" and body["attempt"] == 1
    assert len(msg.to_json()) < 1024            # só metadados, nunca o binário
    assert priority == 9


@pytest.mark.parametrize("kwargs,status", [
    ({"name": "foto.gif"}, 415),
    ({"data": b"GIF89a nao sou png"}, 415),
    ({"target": "webp"}, 422),
    ({"email": "sem-arroba"}, 422),
    ({"data": b""}, 422),
])
def test_rejects_invalid_requests(kwargs, status):
    client, repo, storage, pub = make_client()
    assert post(client, **kwargs).status_code == status
    assert not pub.messages and not storage.objects


def test_rejects_too_large_file():
    client, *_ = make_client()
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024 + 10)
    assert post(client, data=big).status_code == 413


def test_broker_down_returns_503_and_cleans_up():
    client, repo, storage, pub = make_client(fail_publish=True)
    res = post(client)
    assert res.status_code == 503
    [job] = repo.jobs.values()
    assert job["status"] == "ERRO"
    assert not storage.objects


def test_formats_endpoint():
    client, *_ = make_client()
    assert client.get("/api/formats").json() == {"png": ["jpg"]}
