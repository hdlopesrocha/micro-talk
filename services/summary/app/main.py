"""MicroTalk local document summarization microservice."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from micro_common.errors import ServiceError, install_error_handlers
from micro_common.logging_config import configure_logging

from .config import settings
from .engine import SummarizationEngine
from .languages import resolve_languages

configure_logging("summary-service")
logger = logging.getLogger(__name__)
engine = SummarizationEngine(settings)


class SummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    language: str = "en"
    length: Literal["short", "medium", "long"] = "medium"
    source_language: str = "auto"


class SummaryResponse(BaseModel):
    summary: str
    source_language: str
    language: str
    length: str
    translated: bool
    model: str
    input_words: int
    output_words: int
    chunk_count: int
    hierarchy_levels: int
    duration_seconds: float
    warnings: list[str]


def _summarize(request: SummaryRequest) -> dict[str, Any]:
    if not request.text.strip():
        raise ServiceError("empty_text", "There is no text to summarize.", status_code=422)
    languages = resolve_languages(request.source_language, request.language, request.text)
    return engine.summarize(request.text, languages, request.length)


async def _idle_unload_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await run_in_threadpool(engine.maybe_unload_idle)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Idle summary model unload failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    idle_task = asyncio.create_task(_idle_unload_loop()) if settings.idle_unload_seconds else None
    logger.info(
        "Summary service ready: CPU threads=%s, model dir=%s, max chunks=%s",
        settings.cpu_threads,
        settings.models_dir,
        settings.max_chunks,
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
    title="MicroTalk Summarization Service",
    version="1.0.0",
    description=(
        "Local hierarchical summarization with compact T5 models. Long inputs are "
        "chunked and reduced recursively; cross-language summaries are translated "
        "through the local translation service."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    status = engine.status()
    return {
        "status": "ok",
        "service": "summary-service",
        "models_loaded": status["loaded_language"],
        "models_available": status["available"],
    }


@app.get("/api/v1/summary/status")
async def summary_status() -> dict[str, Any]:
    return engine.status()


@app.get("/api/v1/summary/lengths")
async def summary_lengths() -> dict[str, Any]:
    return {
        "lengths": [
            {"id": "short", "label": "Short", "max_tokens": settings.short_tokens},
            {"id": "medium", "label": "Medium", "max_tokens": settings.medium_tokens},
            {"id": "long", "label": "Long", "max_tokens": settings.long_tokens},
        ],
        "languages": [
            {"id": "en", "label": "English"},
            {"id": "pt-PT", "label": "Portuguese (Portugal)"},
            {"id": "pt-BR", "label": "Portuguese (Brazil)"},
        ],
    }


@app.post("/api/v1/summary", response_model=SummaryResponse)
async def summarize(request: SummaryRequest) -> SummaryResponse:
    result = await run_in_threadpool(_summarize, request)
    return SummaryResponse(**result)
