"""MicroTalk local translation microservice."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from micro_common.errors import ServiceError, install_error_handlers
from micro_common.logging_config import configure_logging
from micro_common.text import normalize_language

from .config import settings
from .engine import TranslationEngine
from .segments import segments_from_text
from .languages import (
    ResolvedLanguages,
    detect_language,
    resolve_languages,
    translation_route,
)

configure_logging("translation-service")
logger = logging.getLogger(__name__)
engine = TranslationEngine(settings)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=200)
    page: int | None = Field(default=None, ge=1, le=100_000)
    text: str = Field(min_length=1)


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=500_000)
    segments: list[Segment] | None = Field(default=None, max_length=20_000)
    source_language: str = "auto"
    target_language: str = "en"

    @model_validator(mode="after")
    def require_content(self) -> "TranslateRequest":
        if self.text is not None and self.segments is not None:
            raise ValueError("Provide either text or segments, not both")
        if self.text is None and not self.segments:
            raise ValueError("Provide text or at least one segment")
        total = len(self.text or "")
        total += sum(len(segment.text) for segment in self.segments or [])
        if total > 500_000:
            raise ValueError("The text exceeds the 500,000 character limit")
        return self


class TranslatedSegment(BaseModel):
    id: str | None
    page: int | None
    text: str


class TranslateResponse(BaseModel):
    source_language: str
    source_language_name: str
    target_language: str
    target_language_name: str
    detected_source_language: bool
    model: str
    translated_text: str
    segments: list[TranslatedSegment]
    pages: list[dict[str, Any]]
    stats: dict[str, Any]
    duration_seconds: float


def _language_label(code: str) -> str:
    return {"pt": "Portuguese", "en": "English", "fr": "French"}.get(code, code)


def _build_plain_text(segments: list[dict[str, Any]], pages_present: bool) -> str:
    if not pages_present:
        return "\n\n".join(segment["text"] for segment in segments if segment["text"]).strip() + "\n"
    groups: list[tuple[int, list[str]]] = []
    for segment in segments:
        if not segment["text"].strip():
            continue
        page = int(segment["page"] or 1)
        if not groups or groups[-1][0] != page:
            groups.append((page, []))
        groups[-1][1].append(segment["text"])
    return "\n\n".join(
        f"--- Page {page} ---\n\n" + "\n\n".join(paragraphs) for page, paragraphs in groups
    ).strip() + "\n"


def _translate(request: TranslateRequest) -> dict[str, Any]:
    started = time.time()
    if request.segments is not None:
        source_segments = [segment.model_dump() for segment in request.segments]
        joined = "\n\n".join(segment["text"] for segment in source_segments)
    else:
        source_segments = []
        joined = request.text or ""
    if not joined.strip():
        raise ServiceError("empty_text", "There is no text to translate.", status_code=422)
    if len(joined) > settings.max_input_chars:
        raise ServiceError(
            "text_too_large",
            f"The text exceeds the {settings.max_input_chars}-character limit.",
            status_code=413,
        )
    languages = resolve_languages(request.source_language, request.target_language, joined)
    if not source_segments:
        source_segments = segments_from_text(joined, settings.max_chunk_chars, languages.source)
    translated_segments = engine.translate_segments(source_segments, languages)
    pages_present = any(segment.get("page") is not None for segment in translated_segments)
    plain = _build_plain_text(translated_segments, pages_present)
    pages: list[dict[str, Any]] = []
    if pages_present:
        page_map: dict[int, list[str]] = {}
        for segment in translated_segments:
            page = int(segment.get("page") or 1)
            if segment["text"]:
                page_map.setdefault(page, []).append(segment["text"])
        pages = [{"page": page, "text": "\n\n".join(parts)} for page, parts in sorted(page_map.items())]
    pair = f"{languages.source}-{languages.target}"
    route = translation_route(languages.source, languages.target)
    if not route:
        model = "identity"
    else:
        model = "+".join(settings.model_repos[pair_name] for pair_name in route)
    duration = round(time.time() - started, 3)
    logger.info(
        "Translated %s chars %s->%s in %.2fs",
        len(joined),
        languages.source,
        languages.target,
        duration,
    )
    return {
        "source_language": languages.source,
        "source_language_name": _language_label(languages.source),
        "target_language": languages.target_variant,
        "target_language_name": _language_label(languages.target),
        "detected_source_language": languages.detected,
        "model": f"{pair}:{model}",
        "translated_text": plain,
        "segments": translated_segments,
        "pages": pages,
        "stats": {
            "input_characters": len(joined),
            "output_characters": len(plain),
            "paragraphs": len(translated_segments),
            "pages": len(pages),
            "chunk_chars": settings.max_chunk_chars,
            "device": "cpu",
        },
        "duration_seconds": duration,
    }


async def _idle_unload_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await run_in_threadpool(engine.maybe_unload_idle)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Idle model unload failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    idle_task = asyncio.create_task(_idle_unload_loop()) if settings.idle_unload_seconds else None
    logger.info(
        "Translation service ready: cache TTL=%ss, CPU threads=%s, model dir=%s",
        settings.idle_unload_seconds,
        settings.cpu_threads,
        settings.models_dir,
    )
    try:
        yield
    finally:
        if idle_task:
            idle_task.cancel()
            try:
                await idle_task
            except (asyncio.CancelledError, Exception):
                pass
        engine.unload()


app = FastAPI(
    title="MicroTalk Translation Service",
    version="1.0.0",
    description=(
        "Local Portuguese, English and French neural machine translation using "
        "OPUS-MT / Marian models. Portuguese↔French is routed through English. "
        "Runs on CPU, loads only one model at a time, and never downloads models "
        "during inference."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    status = engine.status()
    return {
        "status": "ok",
        "service": "translation-service",
        "models_loaded": status["loaded_pair"],
        "models_available": status["supported_pairs"],
    }


@app.get("/api/v1/translation/status")
async def translation_status() -> dict[str, Any]:
    return engine.status()


@app.get("/api/v1/translation/languages")
async def translation_languages() -> dict[str, Any]:
    return {
        "source": ["auto", "pt", "pt-PT", "pt-BR", "en", "fr"],
        "target": ["en", "pt-PT", "pt-BR", "fr"],
        "pairs": [
            {"source": "pt", "target": "en"},
            {"source": "en", "target": "pt-PT"},
            {"source": "en", "target": "fr"},
            {"source": "fr", "target": "en"},
            {"source": "pt", "target": "fr"},
            {"source": "fr", "target": "pt-PT"},
        ],
    }


@app.post("/api/v1/translate", response_model=TranslateResponse)
async def translate(request: TranslateRequest) -> TranslateResponse:
    result = await run_in_threadpool(_translate, request)
    return TranslateResponse(**result)


@app.post("/api/v1/detect")
async def detect(request: dict[str, str]) -> dict[str, Any]:
    text = request.get("text", "")
    if not text.strip():
        raise ServiceError("empty_text", "There is no text to detect a language from.", status_code=422)
    returning = {"language": detect_language(text)}
    try:
        returning["canonical"] = normalize_language(returning["language"])
    except ValueError:
        pass
    return returning
