"""MicroTalk audio and subtitle processing microservice."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from micro_common.errors import ServiceError, install_error_handlers
from micro_common.http_files import ranged_file_response
from micro_common.logging_config import configure_logging
from micro_common.security import sanitize_original_filename
from micro_common.storage import (
    check_disk_space,
    ensure_dir,
    new_id,
    relative_to_root,
    resolve_relative,
)
from micro_common.text import clean_text

from .config import settings
from .media import (
    MediaStore,
    build_subtitle_payload,
    encode_audio,
    normalize_timings,
    probe_audio,
    run_ffmpeg,
)

configure_logging("media-service")
logger = logging.getLogger(__name__)
store = MediaStore(settings)
_active_downloads: set[str] = set()
_active_lock = threading.Lock()


class FinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1, max_length=500)
    format: Literal["mp3", "wav"] = "mp3"
    normalize_audio: bool = False
    title: str | None = Field(default=None, max_length=200)


class TimingSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # TTS timelines include their own one-based index. Subtitle generation
    # renumbers cues after filtering empty segments, so the value is optional
    # and never used directly.
    index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class SubtitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    segments: list[TimingSegment] | None = None
    audio_path: str | None = Field(default=None, max_length=500)
    language: str = "en"
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_content(self) -> "SubtitleRequest":
        if not self.text and not self.segments:
            raise ValueError("Provide either text or segments")
        if self.audio_path:
            # The path is resolved against the media storage root with traversal
            # protection at request handling time.
            if Path(self.audio_path).is_absolute():
                raise ValueError("audio_path must be relative")
        return self


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            with _active_lock:
                active = set(_active_downloads)
            await asyncio.to_thread(store.cleanup, active)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Media cleanup loop failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_loop()) if settings.cleanup_enabled else None
    logger.info("Media service ready: storage=%s, TTS source=%s", settings.media_dir, settings.tts_source_dir)
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
    title="MicroTalk Media Service",
    version="1.0.0",
    description=(
        "FFmpeg-based audio conversion, peak normalization, chunk merging, and "
        "sentence-level SRT generation. Subtitle durations can use actual TTS WAV "
        "timestamps or the actual final audio duration."
    ),
    lifespan=lifespan,
)
install_error_handlers(app)


@app.get("/health")
async def health() -> dict[str, Any]:
    def tool_version(tool: str) -> str:
        try:
            import subprocess

            result = subprocess.run(
                [tool, "-version"], capture_output=True, text=True, timeout=5, check=False
            )
            return result.stdout.splitlines()[0] if result.stdout else ""
        except Exception:
            return ""

    ffmpeg_version = tool_version("ffmpeg")
    ffprobe_version = tool_version("ffprobe")
    return {
        "status": "ok",
        "service": "media-service",
        "ffmpeg_available": bool(ffmpeg_version),
        "ffprobe_available": bool(ffprobe_version),
        "ffmpeg_version": ffmpeg_version,
        "ffprobe_version": ffprobe_version,
    }


def _audio_record(
    media_id: str,
    output: Path,
    *,
    filename: str,
    output_format: str,
    source: str,
    normalize_audio: bool,
) -> dict[str, Any]:
    probed = probe_audio(output, settings)
    return {
        "id": media_id,
        "kind": "audio",
        "format": output_format,
        "content_type": "audio/mpeg" if output_format == "mp3" else "audio/wav",
        "filename": filename,
        "relative_path": relative_to_root(store.root, output),
        "size_bytes": output.stat().st_size,
        "duration_seconds": round(probed["duration_seconds"], 3),
        "sample_rate": probed["sample_rate"],
        "channels": probed["channels"],
        "codec": probed["codec"],
        "normalized": normalize_audio,
        "source": source,
    }


@app.post("/api/v1/media/finalize", status_code=201)
async def finalize_audio(request: FinalizeRequest) -> dict[str, Any]:
    check_disk_space(store.root, settings.minimum_free_mb)
    source = resolve_relative(settings.tts_source_dir, request.source_path)
    if not source.is_file():
        raise ServiceError(
            "media_source_not_found",
            "The generated audio source could not be found.",
            status_code=404,
        )
    probe_audio(source, settings)
    media_id = new_id()
    folder = ensure_dir(store.files_root / media_id)
    extension = request.format
    output = folder / f"audio.{extension}"
    await asyncio.to_thread(
        encode_audio,
        source,
        output,
        request.format,
        request.normalize_audio,
        settings,
    )
    title = sanitize_original_filename(request.title or "microtalk-audio", fallback="microtalk-audio")
    filename = f"{Path(title).stem}-audio.{extension}"
    record = _audio_record(
        media_id,
        output,
        filename=filename,
        output_format=request.format,
        source=f"tts:{request.source_path}",
        normalize_audio=request.normalize_audio,
    )
    record["title"] = Path(title).stem
    return store.create_record(record)


@app.post("/api/v1/media/transcode", status_code=201)
async def transcode_upload(
    file: UploadFile = File(...),
    format: Literal["mp3", "wav"] = Form("mp3"),
    normalize_audio: bool = Form(False),
) -> dict[str, Any]:
    check_disk_space(store.root, settings.minimum_free_mb)
    temp_dir = store.new_temp_dir()
    try:
        original = sanitize_original_filename(file.filename, fallback="audio")
        temp_input = temp_dir / "input"
        total = 0
        limit = settings.max_upload_mb * 1024 * 1024
        with open(temp_input, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ServiceError(
                        "file_too_large",
                        f"The audio exceeds the {settings.max_upload_mb} MB limit.",
                        status_code=413,
                    )
                handle.write(chunk)
        await file.close()
        if not total:
            raise ServiceError("empty_file", "The uploaded audio file is empty.", status_code=422)
        probe_audio(temp_input, settings)
        media_id = new_id()
        folder = ensure_dir(store.files_root / media_id)
        output = folder / f"audio.{format}"
        await asyncio.to_thread(
            encode_audio, temp_input, output, format, normalize_audio, settings
        )
        filename = f"{Path(original).stem}-converted.{format}"
        record = _audio_record(
            media_id,
            output,
            filename=filename,
            output_format=format,
            source="upload",
            normalize_audio=normalize_audio,
        )
        return store.create_record(record)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/v1/media/merge", status_code=201)
async def merge_chunks(
    files: list[UploadFile] = File(...),
    format: Literal["mp3", "wav"] = Form("mp3"),
    normalize_audio: bool = Form(False),
    gap_ms: int = Form(120),
) -> dict[str, Any]:
    if not files:
        raise ServiceError("missing_file", "At least one audio chunk is required.", status_code=422)
    if len(files) > 200:
        raise ServiceError("too_many_chunks", "At most 200 chunks can be merged at once.", status_code=422)
    gap_ms = max(0, min(2000, gap_ms))
    check_disk_space(store.root, settings.minimum_free_mb)
    temp_dir = store.new_temp_dir()
    try:
        paths: list[Path] = []
        total = 0
        limit = settings.max_upload_mb * 1024 * 1024
        for index, upload in enumerate(files):
            path = temp_dir / f"chunk-{index:05d}"
            with open(path, "wb") as handle:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise ServiceError(
                            "file_too_large",
                            f"The merged audio exceeds the {settings.max_upload_mb} MB limit.",
                            status_code=413,
                        )
                    handle.write(chunk)
            await upload.close()
            probe_audio(path, settings)
            paths.append(path)
        merged = temp_dir / "merged.wav"
        args: list[str] = []
        for path in paths:
            args += ["-i", str(path)]
        filters: list[str] = []
        concat_inputs: list[str] = []
        for index in range(len(paths)):
            base_label = f"base{index}"
            filters.append(
                f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono,"
                f"aresample=async=1:first_pts=0[{base_label}]"
            )
            if index < len(paths) - 1 and gap_ms:
                filters.append(f"[{base_label}]apad=pad_dur={gap_ms / 1000.0}[chunk{index}]")
                concat_inputs.append(f"[chunk{index}]")
            else:
                concat_inputs.append(f"[{base_label}]")
        filters.append(
            "".join(concat_inputs) + f"concat=n={len(paths)}:v=0:a=1[merged]"
        )
        args += ["-filter_complex", ";".join(filters), "-map", "[merged]", "-c:a", "pcm_s16le", str(merged)]
        await asyncio.to_thread(run_ffmpeg, args, settings.ffmpeg_timeout_seconds)
        media_id = new_id()
        folder = ensure_dir(store.files_root / media_id)
        output = folder / f"merged.{format}"
        await asyncio.to_thread(
            encode_audio, merged, output, format, normalize_audio, settings
        )
        record = _audio_record(
            media_id,
            output,
            filename=f"merged-audio.{format}",
            output_format=format,
            source=f"merge:{len(paths)}",
            normalize_audio=normalize_audio,
        )
        record["chunk_count"] = len(paths)
        return store.create_record(record)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/v1/media/subtitles", status_code=201)
async def create_subtitles(request: SubtitleRequest) -> dict[str, Any]:
    check_disk_space(store.root, settings.minimum_free_mb)
    audio_duration: float | None = None
    audio_relative: str | None = None
    if request.audio_path:
        audio_file = resolve_relative(store.root, request.audio_path)
        if not audio_file.is_file():
            raise ServiceError("invalid_audio", "The referenced audio file could not be found.", status_code=404)
        probed = probe_audio(audio_file, settings)
        audio_duration = probed["duration_seconds"]
        audio_relative = relative_to_root(store.root, audio_file)
    payload = build_subtitle_payload(
        text=request.text,
        segments=[segment.model_dump() for segment in request.segments] if request.segments else None,
        language=request.language,
        audio_duration=audio_duration,
        words_per_minute=settings.subtitle_words_per_minute,
    )
    media_id = new_id()
    folder = ensure_dir(store.files_root / media_id)
    output = folder / "subtitles.srt"
    with open(output, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload["srt"])
        handle.flush()
        os.fsync(handle.fileno())
    title = sanitize_original_filename(request.title or "microtalk", fallback="microtalk")
    record = {
        "id": media_id,
        "kind": "subtitles",
        "format": "srt",
        "content_type": "application/x-subrip; charset=utf-8",
        "filename": f"{Path(title).stem}-subtitles.srt",
        "relative_path": relative_to_root(store.root, output),
        "size_bytes": output.stat().st_size,
        "duration_seconds": round(audio_duration, 3) if audio_duration is not None else None,
        "audio_path": audio_relative,
        "timing_method": payload["timing_method"],
        "segment_count": payload["segment_count"],
        "language": request.language,
        "warnings": payload["warnings"],
        "segments": payload["segments"],
    }
    public = store.create_record(record)
    public["srt"] = payload["srt"]
    return public


@app.get("/api/v1/media/{media_id}")
async def get_media(media_id: str) -> dict[str, Any]:
    record = store.load_record(media_id)
    return store.public(record)


@app.get("/api/v1/media/{media_id}/download")
async def download_media(media_id: str, request: Request, inline: bool = Query(default=False)) -> Response:
    path, record = store.file_for(media_id)
    # Touch the enclosing directory so automatic cleanup treats recent access as
    # activity. The gateway also tracks active streams and skips those files.
    try:
        os.utime(path.parent, None)
    except OSError:
        pass

    def mark_start() -> None:
        with _active_lock:
            _active_downloads.add(media_id)

    def mark_end() -> None:
        with _active_lock:
            _active_downloads.discard(media_id)

    return ranged_file_response(
        request,
        path,
        media_type=record["content_type"],
        filename=record["filename"],
        inline=inline,
        on_start=mark_start,
        on_end=mark_end,
    )


@app.get("/api/v1/media/{media_id}/inline")
async def inline_media(media_id: str, request: Request) -> Response:
    return await download_media(media_id, request, inline=True)


@app.delete("/api/v1/media/{media_id}", status_code=204)
async def delete_media(media_id: str) -> Response:
    store.load_record(media_id)
    with _active_lock:
        if media_id in _active_downloads:
            raise ServiceError("media_in_use", "This media file is currently being downloaded.", status_code=409)
    store.delete(media_id)
    return Response(status_code=204)


@app.get("/api/v1/media-capabilities")
async def capabilities() -> dict[str, Any]:
    return {
        "input_formats": ["wav", "mp3"],
        "output_formats": ["mp3", "wav"],
        "subtitle_formats": ["srt"],
        "normalization": "dynaudnorm",
        "max_upload_mb": settings.max_upload_mb,
        "srt_alignment_methods": [
            "generated_audio_segment_timings",
            "proportional_to_actual_audio_duration",
            "estimated_from_text_word_rate",
        ],
    }
