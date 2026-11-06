"""MicroTalk local Piper text-to-speech microservice."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from micro_common.errors import ServiceError, install_error_handlers
from micro_common.http_files import ranged_file_response
from micro_common.logging_config import configure_logging

from .config import settings
from .jobs import TTSJobManager, split_speech_segments
from .voices import VOICES, get_voice

configure_logging("tts-service")
logger = logging.getLogger(__name__)
manager = TTSJobManager(settings)


class TTSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500_000)
    language: str = Field(default=settings.default_language, max_length=20)
    voice: str = Field(default=settings.default_voice, max_length=80)
    speed: float = Field(default=settings.default_speed, ge=0.5, le=2.0)
    pitch: float = Field(default=0.0, ge=-24.0, le=24.0)
    format: Literal["mp3", "wav"] = "mp3"
    normalize_audio: bool = False


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            await run_in_threadpool(manager.cleanup_expired)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("TTS cleanup loop failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_loop()) if settings.cleanup_enabled else None
    logger.info(
        "TTS service ready: voices dir=%s, default voice=%s, data=%s",
        settings.voices_dir,
        settings.default_voice,
        settings.data_dir,
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
        manager.shutdown()


app = FastAPI(
    title="MicroTalk TTS Service",
    version="1.0.0",
    description=(
        "Local neural text-to-speech with Piper. Audio is synthesized sentence by "
        "sentence, written to disk, and merged using actual WAV durations. Pitch is "
        "reported as unsupported for Piper voices rather than being silently ignored."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        import piper  # noqa: F401

        piper_available = True
        try:
            from importlib.metadata import version

            piper_version = version("piper-tts")
        except Exception:
            piper_version = "unknown"
    except Exception:
        piper_available = False
        piper_version = ""
    available = sum(1 for voice in VOICES if voice.available)
    return {
        "status": "ok",
        "service": "tts-service",
        "piper_available": piper_available,
        "piper_version": piper_version,
        "voices_total": len(VOICES),
        "voices_available": available,
        "data_writable": Path(settings.data_dir).is_dir(),
    }


@app.get("/api/v1/tts/voices")
async def list_voices(language: str | None = Query(default=None, max_length=20)) -> dict[str, Any]:
    voices = [
        voice.public()
        for voice in VOICES
        if not language or language.lower().split("-")[0] == voice.language.lower().split("-")[0]
    ]
    return {
        "voices": voices,
        "engines": [
            {
                "id": "piper",
                "name": "Piper",
                "pitch_supported": False,
                "device": "cpu",
                "description": "Fast local ONNX neural speech synthesis.",
            }
        ],
        "default_voice": settings.default_voice,
        "pitch_supported": False,
        "formats": ["mp3", "wav"],
    }


@app.get("/api/v1/tts/defaults")
async def defaults() -> dict[str, Any]:
    voice = get_voice(settings.default_voice)
    return {
        "voice": voice.public(),
        "language": settings.default_language,
        "speed": settings.default_speed,
        "format": "mp3",
        "normalize_audio": False,
    }


@app.post("/api/v1/tts/generate", status_code=202)
async def generate(request: TTSRequest) -> dict[str, Any]:
    payload = request.model_dump()
    public = await run_in_threadpool(manager.create, payload)
    return public


@app.get("/api/v1/tts/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    return manager.public_job(job_id)


@app.delete("/api/v1/tts/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict[str, Any]:
    return manager.cancel(job_id)


@app.get("/api/v1/tts/jobs/{job_id}/audio")
async def download_audio(job_id: str, request: Request) -> Response:
    path, media_type, filename, _relative = manager.audio_file(job_id, want_wav=False)
    return ranged_file_response(
        request,
        path,
        media_type=media_type,
        filename=filename,
        inline=True,
    )


@app.get("/api/v1/tts/jobs/{job_id}/wav")
async def download_wav(job_id: str, request: Request) -> Response:
    path, _media_type, filename, _relative = manager.audio_file(job_id, want_wav=True)
    return ranged_file_response(
        request,
        path,
        media_type="audio/wav",
        filename=filename,
        inline=True,
    )


@app.get("/api/v1/tts/jobs/{job_id}/timeline")
async def get_timeline(job_id: str) -> dict[str, Any]:
    return manager.timeline(job_id)


@app.get("/api/v1/tts/jobs")
async def recent_jobs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    # Listing states requires job ids; expose only a bounded recent view.
    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    jobs = []
    jobs_root = Path(settings.data_dir) / "jobs"
    children = jobs_root.glob("*") if jobs_root.exists() else []
    for child in sorted(children, key=modified, reverse=True)[:limit]:
        try:
            jobs.append(manager.public_job(child.name))
        except (ServiceError, OSError):
            continue
    return {"jobs": jobs}


@app.post("/api/v1/tts/preview-chunks")
async def preview_chunks(request: dict[str, Any]) -> dict[str, Any]:
    text = str(request.get("text", ""))
    language = str(request.get("language") or settings.default_language)
    sentences = split_speech_segments(text, language, settings.max_segment_chars)
    return {"count": len(sentences), "segments": sentences[:200]}


@app.delete("/api/v1/tts/jobs/{job_id}/purge", status_code=204)
async def purge_job(job_id: str) -> Response:
    # Only terminal jobs can be removed.
    state = manager.public_job(job_id)
    if state.get("status") in {"queued", "processing"}:
        raise ServiceError("tts_job_active", "Cancel the job before purging it.", status_code=409)
    import shutil

    shutil.rmtree(Path(settings.data_dir) / "jobs" / job_id, ignore_errors=True)
    with manager._lock:  # internal maintenance endpoint
        manager._jobs.pop(job_id, None)
    return Response(status_code=204)
