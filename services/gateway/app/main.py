"""MicroTalk public gateway API.

The gateway is the only application service exposed to the browser. It validates
requests, stores job metadata, enqueues RQ work, streams real progress over SSE,
and serves the resulting text, audio, subtitle and ZIP artifacts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import redis as redis_lib
from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from rq import Queue
from rq.job import Job as RQJob
from micro_common.errors import ServiceError, error_body, install_error_handlers
from micro_common.http_files import ranged_file_response
from micro_common.logging_config import configure_logging
from micro_common.security import sanitize_original_filename
from micro_common.storage import check_disk_space, ensure_dir, new_id, safe_join

from . import artifacts
from .auth import AuthMiddleware
from .clients import ServiceCallError, ServiceClients
from .config import settings
from .models import (
    DirectSummaryRequest,
    DirectTTSRequest,
    DirectTranslateRequest,
    JobCreateRequest,
    TextUpdateRequest,
)
from .pipeline import build_initial_state, build_steps
from .rate_limit import enforce_rate_limit
from .store import TERMINAL_STATUSES, JobStore

configure_logging("gateway")
logger = logging.getLogger(__name__)

VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dir(settings.data_dir)
    ensure_dir(settings.jobs_dir)
    store = JobStore()
    clients = ServiceClients(settings)
    rq_connection = redis_lib.Redis.from_url(settings.redis_url, decode_responses=False)
    text_redis = redis_lib.Redis.from_url(
        settings.redis_url, decode_responses=True, socket_timeout=3, socket_connect_timeout=3
    )
    queue = Queue(
        settings.queue_name,
        connection=rq_connection,
        default_timeout=settings.job_timeout_seconds,
    )
    app.state.store = store
    app.state.clients = clients
    app.state.rq_connection = rq_connection
    app.state.redis = text_redis
    app.state.queue = queue
    app.state.catalog_cache = {"at": 0.0, "data": None}
    try:
        await asyncio.to_thread(text_redis.ping)
        logger.info("Connected to Redis at %s", settings.redis_url)
    except Exception:
        logger.warning("Redis is not reachable at startup; job creation will fail until it returns.")
    cleanup_task = asyncio.create_task(_maintenance_loop(app)) if settings.cleanup_enabled else None
    logger.info(
        "Gateway ready: data=%s, queue=%s, auth=%s",
        settings.data_dir,
        settings.queue_name,
        "enabled" if settings.auth_token else "disabled",
    )
    try:
        yield
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
        clients.close()
        try:
            rq_connection.close()
            text_redis.close()
        except Exception:
            pass


app = FastAPI(
    title="MicroTalk Gateway",
    version=VERSION,
    description=(
        "Local-first document extraction, translation, summarization, speech and "
        "subtitle workflows. Only this gateway and the reverse proxy are reachable "
        "from outside the Docker network."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)

app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or new_id()
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# Add CORS last so it is the outermost middleware and browser preflight
# requests receive headers even when the token middleware would reject them.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Range",
        "Accept-Ranges",
        "Content-Disposition",
        "X-Request-ID",
    ],
)


@app.exception_handler(ServiceCallError)
async def service_call_error_handler(_request: Request, exc: ServiceCallError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, {"service": exc.service, **exc.details}),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _store(request: Request) -> JobStore:
    return request.app.state.store


def _clients(request: Request) -> ServiceClients:
    return request.app.state.clients


async def _call(func, *args, **kwargs):
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except ServiceCallError as exc:
        raise ServiceError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details={"service": exc.service, **exc.details},
        ) from exc


def _get_job_or_404(store: JobStore, job_id: str) -> dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise ServiceError("job_not_found", "The workflow could not be found.", status_code=404)
    return job


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Strip large internal fields before sending state to the browser or SSE."""
    public = dict(job)
    config = public.get("config") or {}
    if isinstance(config, dict):
        config = dict(config)
        input_config = config.get("input")
        if isinstance(input_config, dict):
            input_config = dict(input_config)
            text = input_config.get("text")
            if text:
                input_config["text_characters"] = len(str(text))
                input_config["text"] = None
            config["input"] = input_config
        public["config"] = config
    tts = public.get("tts")
    if isinstance(tts, dict):
        tts = {key: value for key, value in tts.items() if key != "timeline"}
        if isinstance(job.get("tts"), dict) and "timeline" in job["tts"]:
            tts["timeline_segments"] = len(job["tts"].get("timeline") or [])
        public["tts"] = tts
    return public


def _job_directory(store: JobStore, job_id: str) -> Path:
    job = _get_job_or_404(store, job_id)
    del job
    return artifacts.job_directory(settings, job_id)


async def _cleanup_job_resources(clients: ServiceClients, job: dict[str, Any]) -> None:
    artifacts_map = job.get("artifacts") or {}
    audio = artifacts_map.get("audio") or {}
    subtitles = artifacts_map.get("subtitles") or {}
    wav = artifacts_map.get("wav") or {}
    media_ids = {
        value
        for value in (
            audio.get("media_id"),
            subtitles.get("media_id"),
            wav.get("media_id") if wav.get("storage") == "media" else None,
        )
        if value
    }
    for media_id in media_ids:
        try:
            await run_in_threadpool(clients.media_delete, str(media_id))
        except Exception:
            logger.warning("Could not delete media %s during job cleanup", media_id, exc_info=True)
    tts_job_id = (job.get("tts") or {}).get("job_id")
    if tts_job_id:
        try:
            await run_in_threadpool(clients.tts_purge, str(tts_job_id))
        except Exception:
            logger.warning("Could not purge TTS job %s during job cleanup", tts_job_id, exc_info=True)


async def _maintenance_loop(app: FastAPI) -> None:
    store: JobStore = app.state.store
    clients: ServiceClients = app.state.clients
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            expired = await run_in_threadpool(
                store.expired_jobs, settings.job_retention_hours
            )
            for job in expired:
                try:
                    await _cleanup_job_resources(clients, job)
                    artifacts.remove_job_files(settings, str(job["id"]))
                    store.delete(str(job["id"]))
                    logger.info("Removed expired gateway job %s", job["id"])
                except Exception:
                    logger.exception("Failed to clean up expired job %s", job.get("id"))
            stale = await run_in_threadpool(
                store.stale_active_jobs, max(settings.job_timeout_seconds + 300, 3600)
            )
            for job in stale:
                def mark_stale(state: dict[str, Any]) -> None:
                    state["status"] = "failed"
                    state["message"] = (
                        "The workflow stopped reporting progress and was marked as failed."
                    )
                    state["error"] = {
                        "code": "job_stalled",
                        "message": state["message"],
                        "details": {},
                    }
                    state["finished_at"] = time.time()
                    for step in state.get("steps") or []:
                        if step.get("status") == "running":
                            step["status"] = "failed"
                            step["message"] = state["message"]
                        elif step.get("status") == "pending":
                            step["status"] = "skipped"

                await run_in_threadpool(store.mutate_state, str(job["id"]), mark_stale)
                logger.warning("Marked stale job %s as failed", job["id"])
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Gateway maintenance loop failed")


def _fallback_title(payload: JobCreateRequest, document: dict[str, Any] | None) -> str:
    if payload.title.strip():
        return " ".join(payload.title.split())[:200]
    if document and document.get("filename"):
        return Path(str(document["filename"])).stem[:200] or "PDF document"
    text = " ".join((payload.input.text or "").split())
    if text:
        return text[:80] + ("…" if len(text) > 80 else "")
    return "Text workflow"


def _artifact_kind_path(job: dict[str, Any], kind: str) -> tuple[Path, str, str, bool]:
    """Return (path, media type, filename, inline_capable)."""
    state_artifacts = job.get("artifacts") or {}
    job_id = str(job["id"])
    if kind in {"extracted", "translation", "summary"}:
        artifact = state_artifacts.get(kind)
        if not artifact:
            raise ServiceError("artifact_not_available", "That result is not available.", status_code=404)
        filename = {"extracted": "extracted.txt", "translation": "translation.txt", "summary": "summary.txt"}[kind]
        return (
            artifacts.artifact_file(settings, job_id, filename),
            "text/plain; charset=utf-8",
            artifacts.artifact_download_name(job, kind),
            False,
        )
    if kind == "srt":
        if not state_artifacts.get("subtitles"):
            raise ServiceError("artifact_not_available", "Subtitles are not available.", status_code=404)
        return (
            artifacts.artifact_file(settings, job_id, "subtitles.srt"),
            "application/x-subrip; charset=utf-8",
            artifacts.artifact_download_name(job, "srt"),
            False,
        )
    if kind == "vtt":
        if not state_artifacts.get("captions"):
            raise ServiceError("artifact_not_available", "Captions are not available.", status_code=404)
        return (
            artifacts.artifact_file(settings, job_id, "captions.vtt"),
            "text/vtt; charset=utf-8",
            artifacts.artifact_download_name(job, "vtt"),
            False,
        )
    if kind == "zip":
        if not state_artifacts.get("zip"):
            raise ServiceError("artifact_not_available", "The ZIP bundle is not available.", status_code=404)
        return (
            artifacts.artifact_file(settings, job_id, "output.zip"),
            "application/zip",
            str(state_artifacts["zip"].get("filename") or artifacts.artifact_download_name(job, "zip")),
            False,
        )
    if kind in {"audio", "wav"}:
        artifact = state_artifacts.get(kind)
        if not artifact:
            raise ServiceError("artifact_not_available", "Audio is not available.", status_code=404)
        storage = str(artifact.get("storage") or "media")
        relative = str(artifact.get("path") or "")
        root = settings.media_data_dir if storage == "media" else settings.tts_data_dir
        try:
            path = safe_join(root, *Path(relative).parts)
        except ServiceError as exc:
            raise ServiceError("artifact_path_invalid", "The stored audio path is invalid.", status_code=500) from exc
        media_type = "audio/wav" if kind == "wav" else str(
            artifact.get("content_type") or "audio/mpeg"
        )
        return path, media_type, artifacts.artifact_download_name(job, kind), True
    raise ServiceError("artifact_unknown", "Unknown download type.", status_code=404)


def _decorate_direct_tts(state: dict[str, Any]) -> dict[str, Any]:
    job_id = state.get("job_id")
    if job_id:
        state["gateway_audio_url"] = f"/api/v1/direct/tts/{job_id}/audio"
        state["gateway_wav_url"] = f"/api/v1/direct/tts/{job_id}/wav"
        state["gateway_timeline_url"] = f"/api/v1/direct/tts/{job_id}/timeline"
    return state


# ---------------------------------------------------------------------------
# health and metadata
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    redis_ok = False
    try:
        redis_ok = bool(await asyncio.to_thread(request.app.state.redis.ping))
    except Exception:
        redis_ok = False
    return {
        "status": "ok" if redis_ok else "degraded",
        "service": "gateway",
        "version": VERSION,
        "redis": redis_ok,
        "auth_required": bool(settings.auth_token),
    }


@app.get("/api/health")
async def api_health(request: Request) -> dict[str, Any]:
    return await health(request)


@app.get("/api/v1/auth/status")
async def auth_status() -> dict[str, Any]:
    return {"auth_required": bool(settings.auth_token), "version": VERSION}


@app.get("/api/v1/auth/check")
async def auth_check() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/v1/catalog")
async def catalog(request: Request) -> dict[str, Any]:
    cache = request.app.state.catalog_cache
    now = time.time()
    if cache["data"] is None or now - float(cache["at"]) > 15:
        data = await _call(_clients(request).catalog)
        cache["data"] = data
        cache["at"] = now
    result = dict(cache["data"])
    result["limits"] = {
        "max_upload_mb": settings.max_upload_mb,
        "max_text_chars": settings.max_text_chars,
        "max_active_jobs": settings.max_active_jobs,
    }
    result["defaults"] = {
        "translation_target": "en",
        "summary_length": "medium",
        "tts_voice": "pt-PT-tugao",
        "tts_speed": 1.0,
        "tts_format": "mp3",
    }
    result["auth_required"] = bool(settings.auth_token)
    return result


@app.get("/api/v1/health/services")
async def service_health(request: Request) -> dict[str, Any]:
    data = await _call(_clients(request).catalog)
    return {"health": data.get("health", {}), "translation": data.get("translation_status"), "summary": data.get("summary_status"), "tts": data.get("tts")}


# ---------------------------------------------------------------------------
# PDF documents
# ---------------------------------------------------------------------------


@app.post("/api/v1/documents", status_code=201)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    await enforce_rate_limit(request, "upload", settings.upload_rate_limit_per_minute)
    if not file.filename:
        raise ServiceError("missing_file", "A PDF file is required.", status_code=422)
    filename = sanitize_original_filename(file.filename, fallback="document.pdf")
    if not filename.lower().endswith(".pdf"):
        raise ServiceError("invalid_file_type", "Only PDF files are accepted.", status_code=415)
    check_disk_space(settings.jobs_dir, settings.minimum_free_mb)
    try:
        result = await _call(_clients(request).upload_pdf, filename, file.file)
    finally:
        await file.close()
    return result


@app.get("/api/v1/documents/{document_id}")
async def get_document(document_id: str, request: Request) -> dict[str, Any]:
    return await _call(_clients(request).get_document, document_id)


@app.get("/api/v1/documents/{document_id}/pages/{page_number}/preview.png")
async def document_preview(
    document_id: str,
    page_number: int,
    request: Request,
    width: int = Query(default=480, ge=120, le=1200),
) -> Response:
    content, media_type = await _call(
        _clients(request).document_preview, document_id, page_number, width
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.delete("/api/v1/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, request: Request) -> Response:
    await _call(_clients(request).delete_document, document_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------


@app.post("/api/v1/jobs", status_code=202)
async def create_job(payload: JobCreateRequest, request: Request) -> dict[str, Any]:
    await enforce_rate_limit(request, "jobs", settings.job_rate_limit_per_minute)
    store = _store(request)
    clients = _clients(request)
    if await run_in_threadpool(store.count_active) >= settings.max_active_jobs:
        raise ServiceError(
            "queue_full",
            "The workflow queue is full. Wait for current jobs to finish.",
            status_code=429,
            details={"max_active_jobs": settings.max_active_jobs},
        )
    document: dict[str, Any] | None = None
    if payload.input.type == "pdf":
        document = await _call(clients.get_document, payload.input.document_id or "")
        page_count = int(document.get("page_count") or 0)
        try:
            selected = payload.input.resolved_pages(page_count)
        except ValueError as exc:
            raise ServiceError("invalid_page_range", str(exc), status_code=422) from exc
        payload.input.selected_pages = selected
        payload.input.start_page = None
        payload.input.end_page = None
    if payload.tts.enabled:
        voices = (await _call(clients.tts_voices)).get("voices") or []
        voice = next((item for item in voices if item.get("id") == payload.tts.voice), None)
        if not voice:
            raise ServiceError(
                "tts_voice_unknown",
                f"The selected voice '{payload.tts.voice}' is not available.",
                status_code=422,
                details={"voices": [item.get("id") for item in voices]},
            )
        if not voice.get("available"):
            raise ServiceError(
                "tts_voice_missing",
                f"The Piper voice {payload.tts.voice} has not been installed.",
                status_code=503,
                details={
                    "install_command": "docker compose run --rm tts-service download-models"
                },
            )
    title = _fallback_title(payload, document)
    job_id = new_id()
    state = build_initial_state(job_id, payload, title=title)
    if document:
        state["source"] = {
            "type": "pdf",
            "document_id": payload.input.document_id,
            "filename": document.get("filename"),
            "page_count": document.get("page_count"),
            "selected_pages": payload.input.selected_pages,
            "ocr_enabled": payload.input.ocr_enabled,
            "likely_scanned": document.get("likely_scanned"),
        }
    await run_in_threadpool(store.create, job_id, state)
    try:
        rq_job = await asyncio.to_thread(
            request.app.state.queue.enqueue,
            "app.pipeline.run_job",
            job_id,
            job_timeout=settings.job_timeout_seconds,
            result_ttl=86400,
            failure_ttl=86400,
        )
    except Exception as exc:
        logger.exception("Could not enqueue job %s", job_id)
        await run_in_threadpool(store.delete, job_id)
        raise ServiceError(
            "queue_unavailable",
            "The job queue is unavailable. Check that Redis is running.",
            status_code=503,
        ) from exc
    await run_in_threadpool(store.set_rq_job, job_id, rq_job.id)
    job = await run_in_threadpool(store.get, job_id)
    return _public_job(job or state)


@app.get("/api/v1/jobs")
async def list_jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"] | None = None,
) -> dict[str, Any]:
    jobs, total = await run_in_threadpool(
        _store(request).list_jobs, limit=limit, offset=offset, status=status
    )
    return {
        "jobs": [_public_job(job) for job in jobs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    job = await run_in_threadpool(_get_job_or_404, _store(request), job_id)
    return _public_job(job)


@app.get("/api/v1/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    store = _store(request)
    await run_in_threadpool(_get_job_or_404, store, job_id)

    async def event_stream():
        last_stamp: float | None = None
        last_emit = time.monotonic()
        while True:
            if await request.is_disconnected():
                break
            job = await run_in_threadpool(store.get, job_id)
            if not job:
                payload = error_body("job_not_found", "The workflow no longer exists.")
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                break
            public = _public_job(job)
            stamp = float(public.get("updated_at") or 0.0)
            if last_stamp is None or stamp != last_stamp:
                yield f"event: job\ndata: {json.dumps(public, ensure_ascii=False)}\n\n"
                last_stamp = stamp
                last_emit = time.monotonic()
            elif time.monotonic() - last_emit > 15:
                yield ": keep-alive\n\n"
                last_emit = time.monotonic()
            if public.get("status") in TERMINAL_STATUSES:
                yield "event: done\ndata: {}\n\n"
                break
            await asyncio.sleep(0.9)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    if job.get("status") in TERMINAL_STATUSES:
        return _public_job(job)
    await run_in_threadpool(store.request_cancel, job_id)
    fields = await run_in_threadpool(store.get_raw_fields, job_id)
    if job.get("status") == "queued":
        rq_job_id = (fields or {}).get("rq_job_id")
        cancelled = False
        if rq_job_id:
            try:
                rq_job = await asyncio.to_thread(
                    RQJob.fetch, rq_job_id, connection=request.app.state.rq_connection
                )
                if rq_job.get_status() == "queued":
                    await asyncio.to_thread(rq_job.cancel)
                    cancelled = True
            except Exception:
                logger.info("Could not remove queued RQ job %s", rq_job_id)
        if cancelled:
            await run_in_threadpool(
                store.mark_cancelled, job_id, "Workflow cancelled before it started."
            )
            updated = await run_in_threadpool(store.get, job_id)
            return _public_job(updated or job)
    await run_in_threadpool(
        store.mutate_state,
        job_id,
        lambda state: state.update(
            {
                "status": "cancelling",
                "message": "Cancellation requested. Stopping at the next safe point.",
            }
        ),
    )
    updated = await run_in_threadpool(store.get, job_id)
    return _public_job(updated or job)


@app.delete("/api/v1/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    request: Request,
    delete_source: bool = Query(default=False),
) -> Response:
    store = _store(request)
    clients = _clients(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    if job.get("status") not in TERMINAL_STATUSES:
        raise ServiceError(
            "job_active",
            "Cancel the workflow before deleting it.",
            status_code=409,
        )
    await _cleanup_job_resources(clients, job)
    if delete_source:
        document_id = (job.get("source") or {}).get("document_id")
        if document_id:
            try:
                await run_in_threadpool(clients.delete_document, str(document_id))
            except (ServiceError, ServiceCallError):
                logger.info("Could not delete source PDF %s", document_id)
    artifacts.remove_job_files(settings, job_id)
    await run_in_threadpool(store.delete, job_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# editable text results
# ---------------------------------------------------------------------------


@app.get("/api/v1/jobs/{job_id}/text/{kind}")
async def get_job_text(job_id: str, kind: str, request: Request) -> PlainTextResponse:
    store = _store(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    if kind not in artifacts.TEXT_ARTIFACTS or not (job.get("artifacts") or {}).get(kind):
        raise ServiceError("artifact_not_available", "That text result is not available.", status_code=404)
    try:
        text = await run_in_threadpool(artifacts.read_text_artifact, settings, job_id, kind)
    except FileNotFoundError as exc:
        raise ServiceError("artifact_missing", "The text result file is missing.", status_code=404) from exc
    except KeyError as exc:
        raise ServiceError("artifact_unknown", "Unknown text result.", status_code=404) from exc
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@app.patch("/api/v1/jobs/{job_id}/text/{kind}")
async def update_job_text(
    job_id: str, kind: str, payload: TextUpdateRequest, request: Request
) -> dict[str, Any]:
    store = _store(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    if kind not in artifacts.TEXT_ARTIFACTS or not (job.get("artifacts") or {}).get(kind):
        raise ServiceError("artifact_not_available", "That text result is not available.", status_code=404)
    artifact = await run_in_threadpool(
        artifacts.write_text_artifact, settings, job_id, kind, payload.text
    )
    note = None
    artifacts_map = job.get("artifacts") or {}
    if artifacts_map.get("audio"):
        note = (
            "This edit changes the text download only. Existing audio and subtitles were "
            "generated from the previous version; start a new workflow to regenerate them."
        )

    def mutate(state: dict[str, Any]) -> None:
        state.setdefault("artifacts", {})[kind] = artifact
        if note and note not in (state.get("warnings") or []):
            state.setdefault("warnings", []).append(note)
        state["updated_at"] = time.time()

    await run_in_threadpool(store.mutate_state, job_id, mutate)
    return {"artifact": artifact, "warning": note}


@app.get("/api/v1/jobs/{job_id}/cues")
async def get_job_cues(job_id: str, request: Request) -> dict[str, Any]:
    store = _store(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    if not (job.get("artifacts") or {}).get("subtitles"):
        raise ServiceError("artifact_not_available", "Subtitles are not available.", status_code=404)
    path = artifacts.artifact_file(settings, job_id, "cues.json")
    if not path.is_file():
        raise ServiceError("artifact_missing", "Subtitle cues are missing.", status_code=404)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError("artifact_corrupt", "Subtitle cues could not be read.", status_code=500) from exc


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------


@app.get("/api/v1/jobs/{job_id}/downloads/{kind}")
async def download_job_artifact(
    job_id: str,
    kind: str,
    request: Request,
    inline: bool = Query(default=False),
) -> Response:
    store = _store(request)
    job = await run_in_threadpool(_get_job_or_404, store, job_id)
    path, media_type, filename, inline_capable = _artifact_kind_path(job, kind)
    if not path.is_file():
        raise ServiceError("artifact_missing", "The result file is missing.", status_code=404)
    return ranged_file_response(
        request,
        path,
        media_type=media_type,
        filename=filename,
        inline=bool(inline and inline_capable),
    )


# ---------------------------------------------------------------------------
# direct service tools
# ---------------------------------------------------------------------------


@app.post("/api/v1/direct/translate")
async def direct_translate(payload: DirectTranslateRequest, request: Request) -> dict[str, Any]:
    await enforce_rate_limit(request, "direct-translate")
    return await _call(
        _clients(request).translate,
        text=payload.text,
        source_language=payload.source_language,
        target_language=payload.target_language,
    )


@app.post("/api/v1/direct/summarize")
async def direct_summarize(payload: DirectSummaryRequest, request: Request) -> dict[str, Any]:
    await enforce_rate_limit(request, "direct-summary")
    return await _call(
        _clients(request).summarize,
        payload.text,
        language=payload.language,
        source_language=payload.source_language,
        length=payload.length,
    )


@app.post("/api/v1/direct/tts", status_code=202)
async def direct_tts(payload: DirectTTSRequest, request: Request) -> dict[str, Any]:
    await enforce_rate_limit(request, "direct-tts")
    clients = _clients(request)
    voices = (await _call(clients.tts_voices)).get("voices") or []
    voice = next((item for item in voices if item.get("id") == payload.voice), None)
    if not voice:
        raise ServiceError("tts_voice_unknown", "The selected voice is not available.", status_code=422)
    if not voice.get("available"):
        raise ServiceError("tts_voice_missing", "The selected voice is not installed.", status_code=503)
    language = payload.language or str(voice.get("language") or "")
    created = await _call(
        clients.tts_create,
        {
            "text": payload.text,
            "voice": payload.voice,
            "language": language,
            "speed": payload.speed,
            "pitch": 0.0,
            "format": payload.format,
            "normalize_audio": payload.normalize_audio,
            "preserve_phrases": payload.preserve_phrases,
            "ignore_leading_numbers": payload.ignore_leading_numbers,
        },
    )
    return _decorate_direct_tts(created)


@app.get("/api/v1/direct/tts/{tts_job_id}")
async def direct_tts_status(tts_job_id: str, request: Request) -> dict[str, Any]:
    return _decorate_direct_tts(await _call(_clients(request).tts_get, tts_job_id))


@app.delete("/api/v1/direct/tts/{tts_job_id}")
async def direct_tts_cancel(tts_job_id: str, request: Request) -> dict[str, Any]:
    return _decorate_direct_tts(await _call(_clients(request).tts_cancel, tts_job_id))


@app.get("/api/v1/direct/tts/{tts_job_id}/timeline")
async def direct_tts_timeline(tts_job_id: str, request: Request) -> dict[str, Any]:
    return await _call(_clients(request).tts_timeline, tts_job_id)


@app.get("/api/v1/direct/tts/{tts_job_id}/audio")
async def direct_tts_audio(
    tts_job_id: str,
    request: Request,
    inline: bool = Query(default=True),
    wav: bool = Query(default=False),
) -> Response:
    state = await _call(_clients(request).tts_get, tts_job_id)
    if state.get("status") != "completed":
        raise ServiceError("tts_job_not_ready", "The TTS audio is not ready yet.", status_code=409)
    relative = state.get("wav_path" if wav else "audio_path")
    if not relative:
        raise ServiceError("tts_audio_missing", "The audio file is not available.", status_code=404)
    try:
        path = safe_join(settings.tts_data_dir, *Path(str(relative)).parts)
    except ServiceError as exc:
        raise ServiceError("artifact_path_invalid", "The stored audio path is invalid.", status_code=500) from exc
    if not path.is_file():
        raise ServiceError("tts_audio_missing", "The audio file is missing.", status_code=404)
    filename = state.get("wav_filename" if wav else "audio_filename") or path.name
    # The TTS service may be configured for WAV or MP3 output. Trust the
    # stored artifact extension instead of assuming the selected option.
    media_type = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"
    return ranged_file_response(
        request, path, media_type=media_type, filename=filename, inline=inline
    )


@app.get("/api/v1/direct/tts/{tts_job_id}/wav")
async def direct_tts_wav(tts_job_id: str, request: Request) -> Response:
    return await direct_tts_audio(tts_job_id, request, inline=True, wav=True)
