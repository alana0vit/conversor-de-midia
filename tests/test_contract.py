import json

import pytest

from mediaconv_common.contract import (
    ContractError,
    ConversionRequest,
    JobFinishedEvent,
    priority_for_size,
)

VALID = {
    "jobId": "9f2c1a84-3b77-4e5a-9d21-6c0a8f4b1e30",
    "sourceUrl": "http://minio:9000/uploads/9f2c1a84-3b77-4e5a-9d21-6c0a8f4b1e30.png",
    "sourceType": "image",
    "sourceFormat": "png",
    "targetFormat": "jpg",
    "notifyEmail": "aluno@exemplo.com",
    "requestedAt": "2026-03-14T18:22:05Z",
    "attempt": 1,
}


def test_roundtrip_keeps_every_field():
    msg = ConversionRequest.from_json(json.dumps(VALID))
    assert json.loads(msg.to_json()) == VALID


def test_message_is_small_and_has_no_binary():
    msg = ConversionRequest.from_json(json.dumps(VALID))
    assert len(msg.to_json()) < 1024


@pytest.mark.parametrize("change", [
    {"jobId": "nao-e-uuid"},
    {"targetFormat": "gif"},
    {"sourceUrl": "ftp://x/y"},
    {"attempt": 0},
])
def test_invalid_messages_are_rejected(change):
    with pytest.raises(ContractError):
        ConversionRequest.from_json(json.dumps({**VALID, **change}))


def test_missing_field_is_rejected():
    data = dict(VALID)
    del data["notifyEmail"]
    with pytest.raises(ContractError):
        ConversionRequest.from_json(json.dumps(data))


def test_garbage_is_rejected():
    with pytest.raises(ContractError):
        ConversionRequest.from_json(b"\x89PNG isto nao e json")


def test_completed_event_requires_link():
    with pytest.raises(ContractError):
        JobFinishedEvent.from_json(json.dumps({
            "jobId": VALID["jobId"], "status": "CONCLUIDO", "notifyEmail": "a@b.c",
            "originalFilename": "x.png", "targetFormat": "jpg", "publicUrl": None,
            "error": None, "workerId": "w1", "finishedAt": "2026-01-01T00:00:00Z",
        }))


def test_jpeg_alias_is_normalized():
    msg = ConversionRequest.from_json(json.dumps({**VALID, "sourceFormat": "JPEG", "targetFormat": "png"}))
    assert (msg.sourceFormat, msg.targetFormat) == ("jpg", "png")


def test_smaller_files_get_higher_priority():
    assert priority_for_size(100_000) > priority_for_size(3_000_000) > priority_for_size(50_000_000)
