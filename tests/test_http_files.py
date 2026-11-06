"""Tests for ranged artifact responses shared by gateway, TTS, and media."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import Request
from fastapi.responses import Response

from micro_common.http_files import CHUNK_SIZE, ranged_file_response


def _request(range_header: str | None = None) -> Request:
    headers = [(b"range", range_header.encode("ascii"))] if range_header else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _collect(response: Response) -> bytes:
    async def consume() -> bytes:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    async def consume_with_timeout() -> bytes:
        # The StopIteration-in-a-Future bug used to leave the stream pending
        # forever, so fail fast instead of hanging the suite.
        return await asyncio.wait_for(consume(), timeout=5)

    return asyncio.run(consume_with_timeout())


@pytest.fixture()
def artifact(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(bytes(range(256)) * (CHUNK_SIZE // 128 + 2))
    return path


def test_full_download_ends_without_future_error(artifact, caplog):
    response = ranged_file_response(
        _request(), artifact, media_type="application/octet-stream", filename="artifact.bin"
    )
    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == artifact.stat().st_size

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        body = _collect(response)

    assert body == artifact.read_bytes()
    assert "StopIteration" not in caplog.text
    assert "Exception in callback" not in caplog.text


def test_range_download_returns_exact_slice(artifact, caplog):
    response = ranged_file_response(
        _request("bytes=100-299"),
        artifact,
        media_type="application/octet-stream",
        filename="artifact.bin",
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-299/{artifact.stat().st_size}"
    assert response.headers["content-length"] == "200"

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        body = _collect(response)

    assert body == artifact.read_bytes()[100:300]
    assert "StopIteration" not in caplog.text


def test_suffix_range_and_invalid_range(artifact):
    suffix = ranged_file_response(
        _request("bytes=-16"), artifact, media_type="application/octet-stream", filename="a.bin"
    )
    size = artifact.stat().st_size
    assert suffix.status_code == 206
    assert suffix.headers["content-range"] == f"bytes {size - 16}-{size - 1}/{size}"
    assert _collect(suffix) == artifact.read_bytes()[-16:]

    invalid = ranged_file_response(
        _request("bytes=999999999-"), artifact, media_type="application/octet-stream", filename="a.bin"
    )
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"bytes */{artifact.stat().st_size}"


def test_missing_file_returns_404(tmp_path):
    response = ranged_file_response(
        _request(), tmp_path / "missing.bin", media_type="application/octet-stream", filename="missing.bin"
    )
    assert response.status_code == 404


def test_activity_hooks_run_once(artifact):
    started: list[str] = []
    ended: list[str] = []

    response = ranged_file_response(
        _request("bytes=0-15"),
        artifact,
        media_type="application/octet-stream",
        filename="artifact.bin",
        on_start=lambda: started.append("start"),
        on_end=lambda: ended.append("end"),
    )
    assert _collect(response) == artifact.read_bytes()[:16]
    assert started == ["start"]
    assert ended == ["end"]
