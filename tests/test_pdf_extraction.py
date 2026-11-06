"""Real PDF inspection, extraction and preview tests using PyMuPDF."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from micro_common.errors import ServiceError


def make_pdf(path: Path, pages: int = 2) -> None:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=595, height=842)
        page.insert_text(
            (72, 96),
            f"MicroTalk test page {number}",
            fontsize=18,
        )
        page.insert_text(
            (72, 140),
            "This paragraph contains enough readable text to be extracted. "
            "The local PDF service should preserve the page boundary.",
            fontsize=11,
        )
    document.set_metadata({"title": "MicroTalk extraction fixture", "author": "Tests"})
    document.save(path)
    document.close()


@pytest.fixture()
def pdf_modules(service_module):
    extractor = service_module("pdf", "extractor")
    config = service_module("pdf", "config")
    return extractor, config


def test_inspect_pdf_reports_pages_and_metadata(tmp_path: Path, pdf_modules):
    extractor, _config = pdf_modules
    path = tmp_path / "sample.pdf"
    make_pdf(path, pages=3)
    info = extractor.inspect_pdf(path)
    assert info["page_count"] == 3
    assert info["title"] == "MicroTalk extraction fixture"
    assert info["author"] == "Tests"
    assert info["likely_scanned"] is False
    assert info["sampled_pages"] == 3


def test_inspect_rejects_a_non_pdf(tmp_path: Path, pdf_modules):
    extractor, _config = pdf_modules
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(ServiceError) as exc:
        extractor.inspect_pdf(path)
    assert exc.value.code == "invalid_pdf"


def test_extract_selected_pages_preserves_page_markers(tmp_path: Path, pdf_modules):
    extractor, config = pdf_modules
    path = tmp_path / "sample.pdf"
    make_pdf(path, pages=3)
    settings = config.Settings(ocr_min_chars=10)

    result = extractor.extract_document(
        path,
        selected_pages=[3, 1],
        ocr_enabled=False,
        settings=settings,
    )

    assert result["selected_pages"] == [1, 3]
    assert result["stats"]["selected_count"] == 2
    assert "--- Page 1 ---" in result["plain_text"]
    assert "--- Page 3 ---" in result["plain_text"]
    assert "--- Page 2 ---" not in result["plain_text"]
    assert "MicroTalk test page 1" in result["plain_text"]
    assert all(page["extraction_method"] == "text" for page in result["pages"])


def test_extract_rejects_out_of_range_pages(tmp_path: Path, pdf_modules):
    extractor, config = pdf_modules
    path = tmp_path / "sample.pdf"
    make_pdf(path, pages=1)
    with pytest.raises(ServiceError) as exc:
        extractor.extract_document(
            path,
            selected_pages=[2],
            ocr_enabled=False,
            settings=config.Settings(),
        )
    assert exc.value.code == "invalid_page_range"


def test_render_page_preview_returns_png(tmp_path: Path, pdf_modules):
    extractor, _config = pdf_modules
    path = tmp_path / "sample.pdf"
    make_pdf(path, pages=2)
    png = extractor.render_page_preview(path, 2, max_width=220)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 500
    with pytest.raises(ServiceError):
        extractor.render_page_preview(path, 3, max_width=220)
