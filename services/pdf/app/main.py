"""MicroTalk PDF extraction microservice."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
import pytesseract
from micro_common.errors import ServiceError, install_error_handlers
from micro_common.logging_config import configure_logging
from micro_common.security import content_disposition, looks_like_pdf_header, sanitize_original_filename
from micro_common.storage import (
    atomic_write_json,
    check_disk_space,
    ensure_dir,
    is_uuid,
    new_id,
    read_json,
    safe_join,
)

from .config import Settings, settings
from .extractor import extract_document, inspect_pdf, render_page_preview

configure_logging("pdf-service")
logger = logging.getLogger(__name__)

DOCS_DIR = Path(settings.storage_dir)
TMP_DIR = Path(settings.tmp_dir)


class PageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=64)
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)
    selected_pages: list[int] | None = None
    ocr_enabled: bool = False
    ignore_notes: bool = False
    ignore_figure_descriptions: bool = False

    @model_validator(mode="after")
    def validate_range(self) -> "PageSelection":
        if self.start_page is not None and self.end_page is not None and self.end_page < self.start_page:
            raise ValueError("end_page must be greater than or equal to start_page")
        if self.selected_pages is not None:
            if not self.selected_pages:
                raise ValueError("selected_pages cannot be empty")
            if len(self.selected_pages) > 2000:
                raise ValueError("Too many selected pages")
            if any(page < 1 for page in self.selected_pages):
                raise ValueError("selected_pages must use 1-based page numbers")
        if self.start_page is not None and self.end_page is not None:
            if self.end_page - self.start_page > 2000:
                raise ValueError("The page range is too large")
        return self


class DocumentMetadata(BaseModel):
    id: str
    filename: str
    size_bytes: int
    page_count: int
    title: str = ""
    author: str = ""
    created_at: float
    likely_scanned: bool = False
    sampled_pages: int = 0


def _doc_dir(document_id: str) -> Path:
    if not is_uuid(document_id):
        raise ServiceError("invalid_identifier", "The document id is invalid.", status_code=400)
    return safe_join(DOCS_DIR, document_id.lower())


def _pdf_path(document_id: str) -> Path:
    return safe_join(str(_doc_dir(document_id)), "source.pdf")


def _meta_path(document_id: str) -> Path:
    return safe_join(str(_doc_dir(document_id)), "metadata.json")


def _load_metadata(document_id: str) -> dict[str, Any]:
    folder = _doc_dir(document_id)
    meta = read_json(folder / "metadata.json", default=None)
    if not meta:
        raise ServiceError("document_not_found", "The document could not be found.", status_code=404)
    return meta


def _safe_delete_document(document_id: str) -> bool:
    try:
        folder = _doc_dir(document_id)
    except ServiceError:
        return False
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
        return True
    return False


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            cutoff = time.time() - settings.retention_hours * 3600
            if not DOCS_DIR.exists():
                continue
            for child in DOCS_DIR.iterdir():
                if not child.is_dir() or not is_uuid(child.name):
                    continue
                meta = read_json(child / "metadata.json", default={}) or {}
                created = float(meta.get("created_at", child.stat().st_mtime))
                if created < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    logger.info("Removed expired PDF document %s", child.name)
            for child in TMP_DIR.glob("*"):
                try:
                    if child.stat().st_mtime < cutoff:
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink(missing_ok=True)
                except OSError:
                    continue
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("PDF cleanup loop failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dir(DOCS_DIR)
    ensure_dir(TMP_DIR)
    cleanup_task = asyncio.create_task(_cleanup_loop()) if settings.cleanup_enabled else None
    try:
        yield
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="MicroTalk PDF Service",
    version="1.0.0",
    description=(
        "PDF validation, page selection, layout-aware text extraction and optional "
        "Tesseract OCR. This service is internal to the MicroTalk Docker network."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        pytesseract_version = pytesseract.get_tesseract_version()
        ocr_available = True
        ocr_version = str(pytesseract_version)
    except Exception:
        ocr_available = False
        ocr_version = ""
    return {
        "status": "ok",
        "service": "pdf-service",
        "ocr_available": ocr_available,
        "ocr_version": ocr_version,
        "storage_writable": os.access(DOCS_DIR, os.W_OK),
    }


@app.post("/api/v1/pdf/documents", response_model=DocumentMetadata, status_code=201)
async def upload_document(file: UploadFile = File(...)) -> DocumentMetadata:
    if not file.filename:
        raise ServiceError("missing_file", "A PDF file is required.", status_code=422)
    original_name = sanitize_original_filename(file.filename, fallback="document.pdf")
    if not original_name.lower().endswith(".pdf"):
        raise ServiceError("invalid_file_type", "Only PDF files are accepted.", status_code=415)
    check_disk_space(DOCS_DIR, settings.minimum_free_mb)
    ensure_dir(DOCS_DIR)
    ensure_dir(TMP_DIR)
    document_id = new_id()
    destination_dir = ensure_dir(_doc_dir(document_id))
    final_path = destination_dir / "source.pdf"
    fd, temp_name = tempfile.mkstemp(
        prefix=".upload-", suffix=".part", dir=str(destination_dir)
    )
    total = 0
    header = b""
    limit_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit_bytes:
                    raise ServiceError(
                        "file_too_large",
                        f"The PDF exceeds the {settings.max_upload_mb} MB upload limit.",
                        status_code=413,
                    )
                if len(header) < 8:
                    header += chunk[: 8 - len(header)]
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if total == 0:
            raise ServiceError("empty_file", "The uploaded file is empty.", status_code=422)
        if not looks_like_pdf_header(header):
            raise ServiceError("invalid_pdf", "The uploaded file is not a PDF.", status_code=422)
        os.replace(temp_name, final_path)
        info = await run_in_threadpool(inspect_pdf, final_path, settings.ocr_min_chars)
        if info["page_count"] > settings.max_pages:
            raise ServiceError(
                "too_many_pages",
                f"The PDF has more than the {settings.max_pages}-page limit.",
                status_code=422,
            )
        metadata = DocumentMetadata(
            id=document_id,
            filename=original_name,
            size_bytes=total,
            page_count=info["page_count"],
            title=info.get("title", ""),
            author=info.get("author", ""),
            created_at=time.time(),
            likely_scanned=bool(info.get("likely_scanned", False)),
            sampled_pages=int(info.get("sampled_pages", 0)),
        )
        atomic_write_json(_meta_path(document_id), metadata.model_dump())
        logger.info("Uploaded PDF %s (%s bytes, %s pages)", document_id, total, info["page_count"])
        return metadata
    except Exception:
        shutil.rmtree(destination_dir, ignore_errors=True)
        raise
    finally:
        Path(temp_name).unlink(missing_ok=True)
        await file.close()


@app.get("/api/v1/pdf/documents/{document_id}", response_model=DocumentMetadata)
async def get_document(document_id: str) -> DocumentMetadata:
    meta = await run_in_threadpool(_load_metadata, document_id)
    return DocumentMetadata(**meta)


@app.delete("/api/v1/pdf/documents/{document_id}", status_code=204)
async def delete_document(document_id: str) -> Response:
    _load_metadata(document_id)
    await run_in_threadpool(_safe_delete_document, document_id)
    return Response(status_code=204)


@app.get("/api/v1/pdf/documents/{document_id}/download")
async def download_pdf(document_id: str) -> FileResponse:
    meta = await run_in_threadpool(_load_metadata, document_id)
    path = _pdf_path(document_id)
    if not path.is_file():
        raise ServiceError("document_not_found", "The PDF document could not be found.", status_code=404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=meta["filename"],
        headers={"Content-Disposition": content_disposition(meta["filename"], inline=False)},
    )


@app.get("/api/v1/pdf/documents/{document_id}/pages/{page_number}/preview.png")
async def page_preview(
    document_id: str,
    page_number: int,
    width: int = Query(default=480, ge=120, le=1200),
) -> Response:
    meta = await run_in_threadpool(_load_metadata, document_id)
    if page_number < 1 or page_number > meta["page_count"]:
        raise ServiceError("invalid_page_number", "The requested page does not exist.", status_code=404)
    content = await run_in_threadpool(
        render_page_preview, _pdf_path(document_id), page_number, width
    )
    return Response(
        content=content,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _resolve_pages(request: PageSelection, page_count: int) -> list[int]:
    if request.selected_pages:
        pages = sorted(set(request.selected_pages))
    elif request.start_page is not None and request.end_page is not None:
        pages = list(range(request.start_page, request.end_page + 1))
    elif request.start_page is not None:
        pages = [request.start_page]
    else:
        pages = list(range(1, page_count + 1))
    if any(page > page_count for page in pages):
        raise ServiceError(
            "invalid_page_range",
            f"Page numbers must be between 1 and {page_count}.",
            status_code=422,
            details={"page_count": page_count},
        )
    if not pages:
        raise ServiceError("no_pages_selected", "At least one page must be selected.", status_code=422)
    return pages


@app.post("/api/v1/pdf/extract")
async def extract(request: PageSelection) -> dict[str, Any]:
    meta = await run_in_threadpool(_load_metadata, request.document_id)
    pages = _resolve_pages(request, meta["page_count"])
    result = await run_in_threadpool(
        extract_document,
        _pdf_path(request.document_id),
        selected_pages=pages,
        ocr_enabled=request.ocr_enabled,
        settings=settings,
        ignore_notes=request.ignore_notes,
        ignore_figure_descriptions=request.ignore_figure_descriptions,
    )
    result = {
        "document_id": request.document_id,
        "filename": meta["filename"],
        **result,
    }
    return result


@app.post("/api/v1/pdf/extract/download")
async def extract_as_txt(request: PageSelection) -> PlainTextResponse:
    meta = await run_in_threadpool(_load_metadata, request.document_id)
    pages = _resolve_pages(request, meta["page_count"])
    result = await run_in_threadpool(
        extract_document,
        _pdf_path(request.document_id),
        selected_pages=pages,
        ocr_enabled=request.ocr_enabled,
        settings=settings,
        ignore_notes=request.ignore_notes,
        ignore_figure_descriptions=request.ignore_figure_descriptions,
    )
    stem = Path(meta["filename"]).stem
    filename = f"{stem}-extracted.txt"
    return PlainTextResponse(
        result["plain_text"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@app.get("/api/v1/pdf/languages")
async def ocr_languages() -> dict[str, Any]:
    try:
        languages = sorted(pytesseract.get_languages(config=""))
    except Exception:
        languages = []
    return {"configured": settings.ocr_languages, "installed": languages}

