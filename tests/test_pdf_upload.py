"""Upload endpoint tests for the PDF service."""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
from fastapi.testclient import TestClient


def make_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 96), "MicroTalk upload test", fontsize=18)
    page.insert_text(
        (72, 140),
        "A real PDF upload should survive the staging and inspection steps.",
        fontsize=11,
    )
    payload = document.tobytes()
    document.close()
    return payload


def test_upload_stages_the_file_on_the_destination_filesystem(
    tmp_path: Path, service_module, monkeypatch
):
    pdf_main = service_module("pdf", "main")
    documents = tmp_path / "documents"
    staging = tmp_path / "staging"
    documents.mkdir()
    staging.mkdir()
    monkeypatch.setattr(pdf_main, "DOCS_DIR", documents)
    monkeypatch.setattr(pdf_main, "TMP_DIR", staging)

    temp_dirs: list[Path] = []
    real_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        temp_dirs.append(Path(kwargs["dir"]))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(pdf_main.tempfile, "mkstemp", recording_mkstemp)

    with TestClient(pdf_main.app) as client:
        response = client.post(
            "/api/v1/pdf/documents",
            files={"file": ("upload.pdf", make_pdf_bytes(), "application/pdf")},
        )

    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    assert (documents / document_id / "source.pdf").is_file()
    # The temporary file must live beside the final path so os.replace never
    # crosses the boundary between the documents and tmp volumes.
    assert temp_dirs
    assert temp_dirs[0].name == document_id
    assert temp_dirs[0].parent == documents
    assert list(staging.iterdir()) == []
