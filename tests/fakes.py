"""Dublês (fakes) em memória para testar sem broker, banco ou MinIO."""
from __future__ import annotations

import shutil
from contextlib import contextmanager


class FakeStorage:
    """Imita o ObjectStorage guardando objetos num dicionário."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.download_names: dict[tuple[str, str], str] = {}
        self.fail_download = 0          # quantas vezes o download deve falhar

    def object_url(self, bucket, key):
        return f"http://minio:9000/{bucket}/{key}"

    def put_bytes(self, bucket, key, data, content_type):
        self.objects[(bucket, key)] = data
        return self.object_url(bucket, key)

    def download_url(self, url, dest):
        if self.fail_download:
            self.fail_download -= 1
            raise ConnectionError("MinIO fora do ar")
        bucket, key = url.split("/", 3)[3].split("/", 1)
        with open(dest, "wb") as fh:
            fh.write(self.objects[(bucket, key)])

    def upload_file(self, bucket, key, path, content_type, download_name=None):
        with open(path, "rb") as fh:
            self.objects[(bucket, key)] = fh.read()
        self.download_names[(bucket, key)] = download_name

    def exists(self, bucket, key):
        return (bucket, key) in self.objects

    def remove(self, bucket, key):
        self.objects.pop((bucket, key), None)


class FakeJobSession:
    def __init__(self, db):
        self.db = db

    def try_lock(self, job_id):
        if job_id in self.db.locks:
            return False
        self.db.locks.add(job_id)
        return True

    def unlock(self, job_id):
        self.db.locks.discard(job_id)

    def get(self, job_id):
        job = self.db.jobs.get(job_id)
        return dict(job) if job else None

    def claim(self, job_id, worker_id):
        job = self.db.jobs[job_id]
        job.update(status="PROCESSANDO", worker_id=worker_id, attempts=job["attempts"] + 1)
        return job["attempts"]

    def progress(self, job_id, value):
        self.db.jobs[job_id]["progress"] = value

    def mark_done(self, job_id, result_key, public_url):
        self.db.jobs[job_id].update(status="CONCLUIDO", progress=100, public_url=public_url,
                                    result_key=result_key, error_message=None)

    def mark_retry(self, job_id, error):
        self.db.jobs[job_id].update(status="PENDENTE", error_message=error)

    def mark_failed(self, job_id, error):
        self.db.jobs[job_id].update(status="ERRO", error_message=error)


class FakeWorkerRepo:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.locks: set[str] = set()

    def add(self, job_id, **extra):
        self.jobs[job_id] = {
            "id": job_id, "status": "PENDENTE", "attempts": 0, "progress": 0,
            "original_filename": "foto.png", "target_format": "jpg",
            "notify_email": "aluno@exemplo.com", "source_bucket": "uploads",
            "source_key": f"{job_id}.png", "public_url": None, "error_message": None,
            "worker_id": None, **extra,
        }

    @contextmanager
    def session(self):
        yield FakeJobSession(self)


class FakeEvents:
    def __init__(self):
        self.published = []

    def publish(self, event):
        self.published.append(event)
