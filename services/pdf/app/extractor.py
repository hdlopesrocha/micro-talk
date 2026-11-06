"""PDF parsing, OCR and layout-aware text extraction."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from micro_common.errors import ServiceError
from micro_common.text import clean_text, sentence_split

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class Block:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0


def inspect_pdf(path: Path, scanned_threshold: int = 40) -> dict[str, Any]:
    if not path.is_file():
        raise ServiceError("document_not_found", "The PDF document could not be found.", status_code=404)
    try:
        document = fitz.open(path)
    except Exception as exc:
        logger.info("Invalid PDF %s: %s", path.name, exc)
        raise ServiceError(
            "invalid_pdf",
            "The uploaded file is not a valid or readable PDF.",
            status_code=422,
        ) from exc
    try:
        if document.needs_pass:
            raise ServiceError(
                "pdf_password_protected",
                "Password-protected PDFs are not supported. Remove the password first.",
                status_code=422,
            )
        page_count = document.page_count
        if page_count < 1:
            raise ServiceError("empty_pdf", "The PDF contains no pages.", status_code=422)
        metadata = document.metadata or {}
        sample_count = min(5, page_count)
        text_pages = 0
        for page_number in range(sample_count):
            try:
                if len(document.load_page(page_number).get_text("text").strip()) >= scanned_threshold:
                    text_pages += 1
            except Exception:
                continue
        return {
            "page_count": page_count,
            "title": (metadata.get("title") or "").strip()[:500],
            "author": (metadata.get("author") or "").strip()[:500],
            "encrypted": False,
            "likely_scanned": sample_count > 0 and text_pages == 0,
            "sampled_pages": sample_count,
        }
    finally:
        document.close()


def render_page_preview(path: Path, page_number: int, max_width: int = 480) -> bytes:
    """Render one page to a compact PNG for the page-selection UI."""
    if not path.is_file():
        raise ServiceError("document_not_found", "The PDF document could not be found.", status_code=404)
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ServiceError("invalid_pdf", "The PDF document could not be opened.", status_code=422) from exc
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ServiceError("invalid_page_number", "The requested page does not exist.", status_code=404)
        page = document.load_page(page_number - 1)
        width = max(1.0, float(page.rect.width))
        zoom = max(0.15, min(2.0, max_width / width))
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


def _line_text(block: dict[str, Any]) -> str:
    lines: list[str] = []
    for line in block.get("lines", []):
        parts = [span.get("text", "") for span in line.get("spans", [])]
        text = "".join(parts).strip()
        if text:
            lines.append(text)
    raw = "\n".join(lines)
    # Rejoin words hyphenated across lines, then normalise wraps within a block.
    raw = re.sub(r"(?<=[A-Za-zÀ-ÿ])-\n(?=[a-zà-ÿ])", "", raw)
    raw = re.sub(r"\s*\n\s*", " ", raw)
    return clean_text(raw)


def _blocks_from_page(page: fitz.Page) -> list[Block]:
    try:
        data = page.get_text("dict", sort=False)
    except Exception as exc:  # pragma: no cover - corrupt PDFs
        raise ServiceError(
            "pdf_extraction_failed", "Text could not be extracted from a page.", status_code=422
        ) from exc
    blocks: list[Block] = []
    page_width = float(page.rect.width) or 1.0
    for raw_block in data.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        text = _line_text(raw_block)
        if not text:
            continue
        x0, y0, x1, y1 = [float(v) for v in raw_block.get("bbox", (0, 0, 0, 0))]
        # Drop obvious page furniture only when it is extremely short; otherwise
        # preserve all extracted content. Page numbers are repeated in each page
        # result anyway, and silently dropping body text is worse than keeping it.
        if text.isdigit() and len(text) <= 4 and (y1 < 50 or y0 > page.rect.height - 50):
            continue
        if (x1 - x0) > page_width * 2.5:
            continue
        blocks.append(Block(x0, y0, x1, y1, text))
    return blocks


def _order_blocks(blocks: list[Block], page_width: float) -> list[Block]:
    """Best-effort reading order for one, two or more text columns.

    Full-width blocks split a page into bands. Within each band, narrow blocks in
    the left column are read before the right column. If a page does not look like
    a multi-column layout, conventional top-to-bottom, left-to-right order is used.
    """
    if len(blocks) < 2:
        return blocks
    narrow = [block for block in blocks if block.width < page_width * 0.72]
    left = [block for block in narrow if block.center_x < page_width * 0.48]
    right = [block for block in narrow if block.center_x > page_width * 0.52]
    is_multi_column = len(left) >= 2 and len(right) >= 2
    if is_multi_column:
        # Require a credible gutter between the columns.
        left_edge = max(block.x1 for block in left)
        right_edge = min(block.x0 for block in right)
        if right_edge - left_edge < page_width * 0.02:
            is_multi_column = False
    if not is_multi_column:
        return sorted(blocks, key=lambda b: (round(b.y0, 1), round(b.x0, 1)))

    spanning = sorted(
        [block for block in blocks if block.width >= page_width * 0.78],
        key=lambda b: (b.y0, b.x0),
    )
    narrow_blocks = [block for block in blocks if block.width < page_width * 0.78]
    band_map: dict[int, list[Block]] = {}
    for block in narrow_blocks:
        band = sum(1 for sep in spanning if sep.center_y <= block.center_y)
        band_map.setdefault(band, []).append(block)

    ordered: list[Block] = []
    total_bands = len(spanning) + 1
    for band in range(total_bands):
        band_blocks = band_map.get(band, [])
        left_col = sorted(
            [b for b in band_blocks if b.center_x < page_width / 2],
            key=lambda b: (round(b.y0, 1), round(b.x0, 1)),
        )
        right_col = sorted(
            [b for b in band_blocks if b.center_x >= page_width / 2],
            key=lambda b: (round(b.y0, 1), round(b.x0, 1)),
        )
        ordered.extend(left_col)
        ordered.extend(right_col)
        if band < len(spanning):
            ordered.append(spanning[band])
    # A spanning separator may be interleaved differently when multiple exist.
    # If that happened, fall back to a stable y/x order rather than losing order.
    ordered = [b for b in ordered if b is not None]
    return ordered


def _paragraphs_from_text(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    # Tesseract commonly emits line wraps. Treat blank lines as hard paragraph
    # boundaries, and group wrapped non-empty lines into paragraphs.
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if buffer:
                paragraphs.append(" ".join(buffer).strip())
                buffer = []
            continue
        if buffer and re.search(r"[.!?:;]$", buffer[-1]) and stripped[:1].isupper():
            # A new sentence on a new line in OCR output is still a new paragraph
            # only when the previous line is finished. This keeps prose readable.
            paragraphs.append(" ".join(buffer).strip())
            buffer = [stripped]
        else:
            buffer.append(stripped)
    if buffer:
        paragraphs.append(" ".join(buffer).strip())
    cleaned = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if paragraph:
            cleaned.append(paragraph)
    return cleaned


def _ocr_page(page: fitz.Page, settings: Settings) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    try:
        matrix = fitz.Matrix(settings.ocr_dpi / 72.0, settings.ocr_dpi / 72.0)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.open(__import__("io").BytesIO(pixmap.tobytes("png")))
        text = pytesseract.image_to_string(
            image,
            lang=settings.ocr_languages,
            config="--psm 3",
            timeout=settings.ocr_timeout_seconds,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise ServiceError(
            "ocr_unavailable",
            "OCR was requested but Tesseract is not available in the PDF service.",
            status_code=503,
        ) from exc
    except RuntimeError as exc:
        raise ServiceError(
            "ocr_timeout",
            "OCR took too long for a page. Try a smaller page selection.",
            status_code=504,
        ) from exc
    except pytesseract.TesseractError as exc:
        logger.warning("Tesseract failed: %s", exc)
        raise ServiceError(
            "ocr_failed",
            "OCR could not process one of the selected pages.",
            status_code=500,
        ) from exc
    if not text.strip():
        warnings.append("OCR found no text on this page.")
    return _paragraphs_from_text(text), warnings


def _page_text(page: fitz.Page, settings: Settings) -> tuple[list[str], str, list[str]]:
    try:
        blocks = _blocks_from_page(page)
    except ServiceError:
        raise
    except Exception as exc:
        logger.warning("Unexpected text block error: %s", exc)
        blocks = []
    ordered = _order_blocks(blocks, float(page.rect.width))
    paragraphs = [block.text for block in ordered if block.text.strip()]
    text = "\n\n".join(paragraphs).strip()
    warnings: list[str] = []
    method = "text"
    if len(text) < settings.ocr_min_chars:
        warnings.append(
            "Little or no extractable text was found; this page may be scanned or image-based."
        )
    return paragraphs, method, warnings


def extract_document(
    path: Path,
    *,
    selected_pages: list[int],
    ocr_enabled: bool,
    settings: Settings,
) -> dict[str, Any]:
    if not path.is_file():
        raise ServiceError("document_not_found", "The PDF document could not be found.", status_code=404)
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ServiceError("invalid_pdf", "The uploaded PDF could not be opened.", status_code=422) from exc
    try:
        if document.needs_pass:
            raise ServiceError(
                "pdf_password_protected",
                "Password-protected PDFs are not supported. Remove the password first.",
                status_code=422,
            )
        total_pages = document.page_count
        if not selected_pages:
            raise ServiceError("no_pages_selected", "At least one page must be selected.", status_code=422)
        if any(page < 1 or page > total_pages for page in selected_pages):
            raise ServiceError(
                "invalid_page_range",
                f"Page numbers must be between 1 and {total_pages}.",
                status_code=422,
                details={"page_count": total_pages},
            )
        selected_unique = sorted(set(selected_pages))
        pages_payload: list[dict[str, Any]] = []
        scanned_pages: list[int] = []
        empty_pages: list[int] = []
        ocr_pages: list[int] = []
        for page_number in selected_unique:
            page = document.load_page(page_number - 1)
            paragraphs, method, warnings = _page_text(page, settings)
            text = "\n\n".join(paragraphs).strip()
            scanned = len(text) < settings.ocr_min_chars
            if scanned:
                scanned_pages.append(page_number)
            if scanned and ocr_enabled:
                paragraphs, ocr_warnings = _ocr_page(page, settings)
                warnings.extend(ocr_warnings)
                text = "\n\n".join(paragraphs).strip()
                method = "ocr"
                if text:
                    ocr_pages.append(page_number)
                scanned = len(text) < settings.ocr_min_chars
            if not text:
                empty_pages.append(page_number)
                if not warnings:
                    warnings.append("This page contains no extractable text.")
            pages_payload.append(
                {
                    "page": page_number,
                    "text": text,
                    "paragraphs": paragraphs,
                    "char_count": len(text),
                    "word_count": len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text)),
                    "extraction_method": method,
                    "needs_ocr": scanned,
                    "warnings": sorted(set(warnings)),
                }
            )
        extracted_pages = sum(1 for page in pages_payload if page["text"])
        if extracted_pages == 0:
            hint = (
                "Enable OCR to process scanned or image-based pages."
                if not ocr_enabled
                else "OCR did not find readable text in the selected pages."
            )
            raise ServiceError(
                "empty_extracted_text",
                f"No readable text was found in the selected pages. {hint}",
                status_code=422,
                details={
                    "page_count": total_pages,
                    "selected_pages": selected_unique,
                    "scanned_pages": scanned_pages,
                    "ocr_enabled": ocr_enabled,
                },
            )
        full_text_parts: list[str] = []
        for page in pages_payload:
            full_text_parts.append(f"--- Page {page['page']} ---\n\n{page['text']}")
        plain_text = "\n\n".join(full_text_parts).strip() + "\n"
        warnings: list[str] = []
        if empty_pages:
            warnings.append(
                "Some selected pages contain no extractable text: "
                + ", ".join(str(p) for p in empty_pages)
                + "."
            )
        if scanned_pages and not ocr_enabled:
            warnings.append(
                "Some selected pages look scanned. Enable OCR to attempt text recognition."
            )
        if ocr_pages:
            warnings.append(
                "OCR was used for page(s): " + ", ".join(str(p) for p in ocr_pages) + "."
            )
        return {
            "page_count": total_pages,
            "selected_pages": selected_unique,
            "pages": pages_payload,
            "plain_text": plain_text,
            "ocr_enabled": ocr_enabled,
            "scanned_pages": scanned_pages,
            "empty_pages": empty_pages,
            "ocr_pages": ocr_pages,
            "warnings": warnings,
            "stats": {
                "selected_count": len(selected_unique),
                "pages_with_text": extracted_pages,
                "pages_without_text": len(empty_pages),
                "characters": len(plain_text),
            },
        }
    finally:
        try:
            document.close()
        except Exception:
            pass


def make_srt_from_pages(pages: list[dict[str, Any]], characters_per_second: float = 14.0) -> str:
    """Not part of PDF extraction; retained for server-side previews/tests."""
    from micro_common.text import build_srt

    entries = []
    cursor = 0.0
    for page in pages:
        for paragraph in page.get("paragraphs", []):
            for sentence in sentence_split(paragraph):
                duration = max(0.8, len(sentence) / characters_per_second)
                entries.append({"start": cursor, "end": cursor + duration, "text": sentence})
                cursor += duration
    return build_srt(entries)
