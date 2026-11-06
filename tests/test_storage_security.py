"""Path containment, HTTP ranges and content-disposition helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from micro_common.errors import ServiceError
from micro_common.security import (
    content_disposition,
    looks_like_pdf_header,
    parse_byte_range,
    sanitize_original_filename,
)
from micro_common.storage import (
    atomic_write_json,
    atomic_write_text,
    read_json,
    relative_to_root,
    resolve_relative,
    safe_component,
    safe_join,
)


def test_safe_join_accepts_inside_and_rejects_traversal(tmp_path: Path):
    target = safe_join(tmp_path, "job", "file.txt")
    assert target.parent.parent == tmp_path
    with pytest.raises(ServiceError):
        safe_join(tmp_path, "..", "escape.txt")
    with pytest.raises(ServiceError):
        safe_join(tmp_path, "/etc/passwd")


def test_resolve_relative_and_relative_to_root(tmp_path: Path):
    nested = tmp_path / "a" / "b.txt"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert relative_to_root(tmp_path, nested) == "a/b.txt"
    assert resolve_relative(tmp_path, "a/b.txt") == nested
    with pytest.raises(ServiceError):
        resolve_relative(tmp_path, "../b.txt")
    with pytest.raises(ServiceError):
        resolve_relative(tmp_path, "/tmp/b.txt")


def test_safe_component_rejects_dot_and_separators():
    assert safe_component("voice-1.onnx") == "voice-1.onnx"
    for value in ("..", ".", "a/b", "a\\b", ""):
        with pytest.raises(ServiceError):
            safe_component(value)


def test_atomic_text_and_json_round_trip(tmp_path: Path):
    text_path = tmp_path / "nested" / "file.txt"
    atomic_write_text(text_path, "olá\n")
    assert text_path.read_text(encoding="utf-8") == "olá\n"
    json_path = tmp_path / "data.json"
    atomic_write_json(json_path, {"ok": True, "n": 1})
    assert read_json(json_path) == {"ok": True, "n": 1}
    assert read_json(tmp_path / "missing.json", default=[]) == []


def test_content_disposition_is_rfc5987_safe():
    header = content_disposition('relatório "final".txt', inline=True)
    assert header.startswith("inline;")
    assert 'filename="relatrio final.txt"' in header
    assert "filename*=UTF-8''relat%C3%B3rio%20%22final%22.txt" in header
    assert content_disposition("a.pdf").startswith("attachment;")


def test_parse_byte_range_variants():
    assert parse_byte_range("bytes=0-99", 1000) == (0, 99)
    assert parse_byte_range("bytes=900-", 1000) == (900, 999)
    assert parse_byte_range("bytes=-100", 1000) == (900, 999)
    assert parse_byte_range("bytes=0-2000", 1000) == (0, 999)
    assert parse_byte_range("bytes=1000-", 1000) is None
    assert parse_byte_range("bytes=10-5", 1000) is None
    assert parse_byte_range("items=0-5", 1000) is None
    assert parse_byte_range("bytes=0-1,5-6", 1000) is None


def test_sanitize_filename_and_pdf_header():
    assert sanitize_original_filename("C:\\temp\\meu.pdf") == "meu.pdf"
    assert sanitize_original_filename("") == "document.pdf"
    assert looks_like_pdf_header(b"%PDF-1.7\n")
    assert not looks_like_pdf_header(b"GIF89a")
