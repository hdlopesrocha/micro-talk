"""Disk-backed Piper synthesis jobs with real chunk progress."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from micro_common.errors import ServiceError
from micro_common.storage import (
    atomic_write_json,
    check_disk_space,
    ensure_dir,
    is_uuid,
    new_id,
    read_json,
    relative_to_root,
    resolve_relative,
)
from micro_common.text import clean_text, sentence_split, split_long_piece, split_paragraphs

from .config import Settings
from .voices import Voice, get_voice, language_matches

logger = logging.getLogger(__name__)


class PiperSynthesizer:
    """Reuse one Piper voice at a time to keep memory bounded."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._voice: Any = None
        self._voice_id: str | None = None

    def _load(self, voice: Voice) -> Any:
        with self._lock:
            if self._voice is not None and self._voice_id == voice.id:
                return self._voice
            self._voice = None
            self._voice_id = None
            if not voice.available:
                raise ServiceError(
                    "tts_voice_missing",
                    f"The Piper voice {voice.id} is not installed.",
                    status_code=503,
                    details={
                        "voice": voice.id,
                        "install_command": "docker compose run --rm tts-service download-models",
                    },
                )
            try:
                from piper import PiperVoice

                loaded = PiperVoice.load(str(voice.onnx_path), config_path=str(voice.config_path))
            except Exception as exc:
                logger.exception("Failed to load Piper voice %s", voice.id)
                raise ServiceError(
                    "tts_initialization_failed",
                    "The Piper voice could not be initialized. Reinstall the local voice files.",
                    status_code=503,
                ) from exc
            self._voice = loaded
            self._voice_id = voice.id
            logger.info("Loaded Piper voice %s", voice.id)
            return loaded

    def synthesize(self, voice: Voice, text: str, speed: float, destination: Path) -> None:
        loaded = self._load(voice)
        try:
            from piper import SynthesisConfig
        except Exception as exc:  # pragma: no cover
            raise ServiceError("tts_engine_unavailable", "Piper TTS is not available.", status_code=503) from exc
        config = SynthesisConfig(
            length_scale=1.0 / speed,
            noise_scale=0.667,
            noise_w_scale=0.8,
            normalize_audio=True,
            volume=1.0,
        )
        ensure_dir(destination.parent)
        try:
            with wave.open(str(destination), "wb") as wav_file:
                loaded.synthesize_wav(text, wav_file, syn_config=config)
        except ServiceError:
            raise
        except Exception as exc:
            logger.exception("Piper synthesis failed")
            raise ServiceError(
                "tts_synthesis_failed",
                "Speech synthesis failed for part of the document.",
                status_code=500,
            ) from exc
        if not destination.is_file() or destination.stat().st_size <= 44:
            raise ServiceError(
                "tts_empty_audio",
                "The TTS engine did not produce audio for a sentence.",
                status_code=500,
            )

    def unload(self) -> None:
        with self._lock:
            self._voice = None
            self._voice_id = None


def split_speech_segments(text: str, language: str, max_chars: int) -> list[str]:
    segments: list[str] = []
    for paragraph in split_paragraphs(text):
        for sentence in sentence_split(paragraph, language):
            for piece in split_long_piece(sentence, max_chars):
                cleaned = clean_text(piece)
                if cleaned and any(char.isalnum() for char in cleaned):
                    segments.append(cleaned)
    return segments


def _wave_duration(path: Path) -> tuple[float, int, int, int]:
    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        frames = reader.getnframes()
        return (frames / float(rate) if rate else 0.0, rate, reader.getsampwidth(), reader.getnchannels())


def _probe_duration(path: Path, timeout: int) -> float | None:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=max(10, min(timeout, 60)),
            check=False,
        )
        if completed.returncode == 0:
            return max(0.0, float(completed.stdout.strip()))
    except Exception:
        return None
    return None


class TTSJobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.data_dir)
        ensure_dir(self.root)
        ensure_dir(self.root / "jobs")
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancelled: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper-tts")
        self._synthesizer = PiperSynthesizer()
        self._load_existing()

    def _job_dir(self, job_id: str) -> Path:
        if not is_uuid(job_id):
            raise ServiceError("invalid_job_id", "The TTS job id is invalid.", status_code=400)
        return self.root / "jobs" / job_id

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.json"

    def _persist(self, state: dict[str, Any]) -> None:
        atomic_write_json(self._job_dir(state["job_id"]) / "state.json", state)

    def _load_existing(self) -> None:
        jobs_root = self.root / "jobs"
        for child in jobs_root.iterdir() if jobs_root.exists() else []:
            if not child.is_dir() or not is_uuid(child.name):
                continue
            state = read_json(child / "state.json", default=None)
            if not isinstance(state, dict) or state.get("job_id") != child.name:
                continue
            if state.get("status") in {"queued", "processing"}:
                state.update(
                    {
                        "status": "failed",
                        "progress": state.get("progress", 0),
                        "message": "The TTS service restarted before this job completed.",
                        "updated_at": time.time(),
                        "completed_at": time.time(),
                        "error": {
                            "code": "tts_worker_restarted",
                            "message": "The TTS service restarted before this job completed. Start a new generation job.",
                        },
                    }
                )
                self._persist(state)
            self._jobs[child.name] = state
        logger.info("Loaded %s persisted TTS jobs", len(self._jobs))

    def _active_count(self) -> int:
        return sum(
            1 for state in self._jobs.values() if state.get("status") in {"queued", "processing"}
        )

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        voice = get_voice(request.get("voice"))
        language = request.get("language") or voice.language
        if not language_matches(language, voice.language):
            raise ServiceError(
                "tts_language_mismatch",
                f"The selected voice {voice.id} speaks {voice.language}, not {language}.",
                status_code=422,
            )
        pitch = float(request.get("pitch") or 0.0)
        if pitch != 0 and not voice.pitch_supported:
            raise ServiceError(
                "tts_pitch_not_supported",
                "The selected Piper voice does not support pitch adjustment.",
                status_code=422,
                details={"voice": voice.id, "pitch_supported": False},
            )
        text = clean_text(request.get("text") or "")
        if not text:
            raise ServiceError("empty_text", "There is no text to synthesize.", status_code=422)
        if len(text) > self.settings.max_text_chars:
            raise ServiceError(
                "text_too_large",
                f"The text exceeds the {self.settings.max_text_chars}-character TTS limit.",
                status_code=413,
            )
        speed = float(request.get("speed") or self.settings.default_speed)
        if not 0.5 <= speed <= 2.0:
            raise ServiceError("invalid_speed", "Speech speed must be between 0.5 and 2.0.", status_code=422)
        output_format = (request.get("format") or "mp3").lower()
        if output_format not in {"mp3", "wav"}:
            raise ServiceError("invalid_audio_format", "Audio format must be mp3 or wav.", status_code=422)
        if output_format == "wav" and not self.settings.enable_wav:
            raise ServiceError("invalid_audio_format", "WAV output is disabled.", status_code=422)
        if not voice.available:
            raise ServiceError(
                "tts_voice_missing",
                f"The Piper voice {voice.id} is not installed.",
                status_code=503,
                details={
                    "voice": voice.id,
                    "install_command": "docker compose run --rm tts-service download-models",
                },
            )
        check_disk_space(self.root, self.settings.minimum_free_mb)
        segments = split_speech_segments(text, voice.language, self.settings.max_segment_chars)
        if not segments:
            raise ServiceError("empty_text", "No speakable sentences were found in the text.", status_code=422)
        if len(segments) > self.settings.max_sentences:
            raise ServiceError(
                "text_too_large",
                f"The text contains more than {self.settings.max_sentences} sentences.",
                status_code=413,
                details={"sentences": len(segments), "max": self.settings.max_sentences},
            )
        with self._lock:
            if self._active_count() >= self.settings.max_queue:
                raise ServiceError(
                    "tts_queue_full",
                    "The TTS queue is full. Try again when current jobs finish.",
                    status_code=429,
                )
            job_id = new_id()
            ensure_dir(self._job_dir(job_id) / "chunks")
            now = time.time()
            state: dict[str, Any] = {
                "job_id": job_id,
                "status": "queued",
                "progress": 0,
                "message": "Queued for speech synthesis.",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
                "voice": voice.id,
                "language": voice.language,
                "speed": speed,
                "pitch": 0.0,
                "pitch_supported": voice.pitch_supported,
                "format": output_format,
                "normalize_audio": bool(request.get("normalize_audio", False)),
                "sentence_count": len(segments),
                "completed_sentences": 0,
                "characters": len(text),
                "duration_seconds": None,
                "audio_path": None,
                "wav_path": None,
                "timeline_path": None,
                "audio_filename": None,
                "wav_filename": None,
                "error": None,
                "warnings": [],
                "cancel_requested": False,
            }
            self._persist(state)
            self._jobs[job_id] = state
        self._executor.submit(self._run_job, job_id, text, segments, voice.id)
        return self.public_job(job_id)

    def public_job(self, job_id: str, include_timeline: bool = False) -> dict[str, Any]:
        with self._lock:
            if not is_uuid(job_id) or job_id not in self._jobs:
                raise ServiceError("tts_job_not_found", "The TTS job could not be found.", status_code=404)
            state = dict(self._jobs[job_id])
        payload = dict(state)
        payload.pop("cancel_requested", None)
        if payload.get("status") == "completed":
            payload["audio_url"] = f"/api/v1/tts/jobs/{job_id}/audio"
            payload["wav_url"] = f"/api/v1/tts/jobs/{job_id}/wav"
            payload["timeline_url"] = f"/api/v1/tts/jobs/{job_id}/timeline"
            if include_timeline and state.get("timeline_path"):
                timeline_path = resolve_relative(self.root, state["timeline_path"])
                payload["timeline"] = read_json(timeline_path, default=None)
        return payload

    def timeline(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._jobs.get(job_id)
            if not state:
                raise ServiceError("tts_job_not_found", "The TTS job could not be found.", status_code=404)
            if state.get("status") != "completed" or not state.get("timeline_path"):
                raise ServiceError("tts_job_not_ready", "The TTS audio is not ready yet.", status_code=409)
            path = resolve_relative(self.root, state["timeline_path"])
        data = read_json(path, default=None)
        if not data:
            raise ServiceError("tts_timeline_missing", "The TTS timeline file is missing.", status_code=500)
        return data

    def audio_file(self, job_id: str, want_wav: bool = False) -> tuple[Path, str, str, str]:
        with self._lock:
            state = self._jobs.get(job_id)
            if not state:
                raise ServiceError("tts_job_not_found", "The TTS job could not be found.", status_code=404)
            if state.get("status") == "cancelled":
                raise ServiceError("tts_job_cancelled", "This TTS job was cancelled.", status_code=409)
            if state.get("status") != "completed":
                raise ServiceError("tts_job_not_ready", "The TTS audio is not ready yet.", status_code=409)
            relative = state.get("wav_path" if want_wav else "audio_path")
            filename = state.get("wav_filename" if want_wav else "audio_filename")
            if not relative:
                raise ServiceError("tts_audio_missing", "The TTS audio file is not available.", status_code=404)
            path = resolve_relative(self.root, relative)
            media_type = "audio/wav" if want_wav or str(path).endswith(".wav") else "audio/mpeg"
        if not path.is_file():
            raise ServiceError("tts_audio_missing", "The TTS audio file is missing.", status_code=404)
        return path, media_type, filename or path.name, (state.get("audio_path") if not want_wav else state.get("wav_path")) or ""

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._jobs.get(job_id)
            if not state:
                raise ServiceError("tts_job_not_found", "The TTS job could not be found.", status_code=404)
            if state.get("status") in {"completed", "failed"}:
                raise ServiceError(
                    "tts_job_not_cancellable",
                    "This TTS job has already finished and cannot be cancelled.",
                    status_code=409,
                )
            if state.get("status") == "cancelled":
                return self.public_job(job_id)
            state["cancel_requested"] = True
            if state.get("status") == "queued":
                state["status"] = "cancelled"
                state["message"] = "TTS job cancelled."
                state["updated_at"] = time.time()
                state["completed_at"] = time.time()
            else:
                state["message"] = "Cancelling after the current sentence..."
            self._persist(state)
            self._cancelled.add(job_id)
            return self.public_job(job_id)

    def _update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self._jobs[job_id]
            state.update(changes)
            state["updated_at"] = time.time()
            self._persist(state)
            return dict(state)

    def _check_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled or bool(self._jobs[job_id].get("cancel_requested"))

    def _run_job(self, job_id: str, text: str, segments: list[str], voice_id: str) -> None:
        try:
            if self._check_cancelled(job_id):
                self._mark_cancelled(job_id)
                return
            voice = get_voice(voice_id)
            state = self.public_job(job_id)
            check_disk_space(self.root, self.settings.minimum_free_mb)
            self._update(
                job_id,
                status="processing",
                started_at=time.time(),
                progress=1,
                message=f"Preparing {len(segments)} sentence(s).",
            )
            job_dir = self._job_dir(job_id)
            chunks_dir = ensure_dir(job_dir / "chunks")
            gap_seconds = self.settings.gap_ms / 1000.0
            chunk_paths: list[Path] = []
            for index, sentence in enumerate(segments):
                if self._check_cancelled(job_id):
                    self._mark_cancelled(job_id)
                    return
                if index % 20 == 0:
                    check_disk_space(self.root, self.settings.minimum_free_mb)
                chunk_path = chunks_dir / f"{index:05d}.wav"
                self._synthesizer.synthesize(voice, sentence, float(state["speed"]), chunk_path)
                chunk_paths.append(chunk_path)
                progress = 2 + int((index + 1) / max(1, len(segments)) * 82)
                self._update(
                    job_id,
                    progress=min(84, progress),
                    completed_sentences=index + 1,
                    message=f"Synthesized sentence {index + 1} of {len(segments)}.",
                )
            if self._check_cancelled(job_id):
                self._mark_cancelled(job_id)
                return
            self._update(job_id, progress=88, message="Merging sentence audio with real durations.")
            master_path = job_dir / "master.wav"
            timeline_segments = self._merge_chunks(
                chunk_paths, segments, master_path, gap_seconds, job_id
            )
            total_duration = timeline_segments[-1]["end"] if timeline_segments else 0.0
            gap_frames = int(round(gap_seconds * 22050))
            del gap_frames
            timeline = {
                "job_id": job_id,
                "duration_seconds": round(total_duration, 3),
                "method": "actual_sentence_wav_durations",
                "gap_seconds": gap_seconds,
                "segments": timeline_segments,
            }
            timeline_path = job_dir / "timeline.json"
            atomic_write_json(timeline_path, timeline)
            if self._check_cancelled(job_id):
                self._mark_cancelled(job_id)
                return
            self._update(job_id, progress=93, message="Encoding the requested audio format.")
            output_format = str(state["format"])
            normalize = bool(state["normalize_audio"])
            audio_path = master_path
            if output_format == "mp3" or normalize:
                if output_format == "mp3":
                    audio_path = job_dir / "audio.mp3"
                else:
                    audio_path = job_dir / "audio.wav"
                self._encode_audio(master_path, audio_path, output_format, normalize)
            final_duration = _probe_duration(audio_path, self.settings.ffmpeg_timeout_seconds) or total_duration
            extension = "mp3" if output_format == "mp3" else "wav"
            base = f"microtalk-{voice.id}-{job_id[:8]}"
            audio_filename = f"{base}.{extension}"
            wav_filename = f"{base}.wav"
            self._update(
                job_id,
                status="completed",
                progress=100,
                message="Speech generation complete.",
                completed_at=time.time(),
                duration_seconds=round(final_duration, 3),
                audio_path=relative_to_root(self.root, audio_path),
                wav_path=relative_to_root(self.root, master_path),
                timeline_path=relative_to_root(self.root, timeline_path),
                audio_filename=audio_filename,
                wav_filename=wav_filename,
            )
            self._cancelled.discard(job_id)
            shutil.rmtree(chunks_dir, ignore_errors=True)
            logger.info(
                "TTS job %s complete: %.1fs, %s sentences, %s",
                job_id,
                final_duration,
                len(segments),
                output_format,
            )
        except ServiceError as exc:
            logger.warning("TTS job %s failed (%s): %s", job_id, exc.code, exc.message)
            self._update(
                job_id,
                status="failed",
                message="Speech generation failed.",
                completed_at=time.time(),
                error={"code": exc.code, "message": exc.message},
            )
            self._cancelled.discard(job_id)
        except Exception:
            logger.exception("Unexpected TTS job failure %s", job_id)
            self._update(
                job_id,
                status="failed",
                message="Speech generation failed.",
                completed_at=time.time(),
                error={
                    "code": "tts_internal_error",
                    "message": "An internal error occurred during speech generation.",
                },
            )
            self._cancelled.discard(job_id)

    def _mark_cancelled(self, job_id: str) -> None:
        self._update(
            job_id,
            status="cancelled",
            message="TTS job cancelled.",
            completed_at=time.time(),
        )
        self._cancelled.discard(job_id)

    def _merge_chunks(
        self,
        paths: list[Path],
        texts: list[str],
        destination: Path,
        gap_seconds: float,
        job_id: str,
    ) -> list[dict[str, Any]]:
        ensure_dir(destination.parent)
        source_params: tuple[int, int, int] | None = None
        frames_written = 0
        timeline: list[dict[str, Any]] = []
        with wave.open(str(destination), "wb") as output:
            for index, (path, sentence) in enumerate(zip(paths, texts, strict=False)):
                if self._check_cancelled(job_id):
                    raise ServiceError("tts_job_cancelled", "TTS job cancelled.", status_code=409)
                with wave.open(str(path), "rb") as source:
                    params = (source.getframerate(), source.getsampwidth(), source.getnchannels())
                    if source_params is None:
                        source_params = params
                        output.setframerate(params[0])
                        output.setsampwidth(params[1])
                        output.setnchannels(params[2])
                    elif params != source_params:
                        raise ServiceError(
                            "tts_audio_format_mismatch",
                            "Generated sentence chunks have inconsistent audio formats.",
                            status_code=500,
                        )
                    rate = params[0]
                    if index > 0 and gap_seconds > 0:
                        gap_frames = int(rate * gap_seconds)
                        output.writeframes(b"\x00" * gap_frames * params[1] * params[2])
                        frames_written += gap_frames
                    start = frames_written / float(rate)
                    frame_count = source.getnframes()
                    while True:
                        frames = source.readframes(min(65536, max(1, frame_count)))
                        if not frames:
                            break
                        output.writeframes(frames)
                        written = len(frames) // (params[1] * params[2])
                        frames_written += written
                        frame_count -= written
                    end = frames_written / float(rate)
                    if end > start:
                        timeline.append(
                            {
                                "index": len(timeline) + 1,
                                "text": sentence,
                                "start": round(start, 3),
                                "end": round(end, 3),
                            }
                        )
        return timeline

    def _encode_audio(self, source: Path, destination: Path, output_format: str, normalize: bool) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}.tmp{destination.suffix}")
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:a:0",
        ]
        if normalize:
            args += ["-af", "dynaudnorm=f=200:g=15:p=0.90"]
        if output_format == "mp3":
            args += ["-codec:a", "libmp3lame", "-b:a", "128k", "-ar", "22050"]
        else:
            args += ["-codec:a", "pcm_s16le"]
        args.append(str(temporary))
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.settings.ffmpeg_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ServiceError(
                "ffmpeg_unavailable",
                "FFmpeg is not available in the TTS service.",
                status_code=503,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ServiceError(
                "audio_conversion_timeout",
                "Audio encoding took too long.",
                status_code=504,
            ) from exc
        if completed.returncode != 0 or not temporary.is_file():
            logger.error("FFmpeg audio conversion failed: %s", completed.stderr[-2000:])
            temporary.unlink(missing_ok=True)
            raise ServiceError(
                "audio_conversion_failed",
                "The generated speech could not be converted to the requested audio format.",
                status_code=500,
            )
        temporary.replace(destination)

    def cleanup_expired(self) -> None:
        cutoff = time.time() - self.settings.retention_hours * 3600
        with self._lock:
            active = {
                job_id
                for job_id, state in self._jobs.items()
                if state.get("status") in {"queued", "processing"}
            }
            for job_id, state in list(self._jobs.items()):
                if job_id in active:
                    continue
                if float(state.get("updated_at") or state.get("created_at") or 0) >= cutoff:
                    continue
                try:
                    shutil.rmtree(self._job_dir(job_id), ignore_errors=True)
                    self._jobs.pop(job_id, None)
                    self._cancelled.discard(job_id)
                    logger.info("Removed expired TTS job %s", job_id)
                except Exception:
                    logger.exception("Failed to remove TTS job %s", job_id)
        jobs_root = self.root / "jobs"
        if jobs_root.exists():
            for child in jobs_root.iterdir():
                if child.is_dir() and is_uuid(child.name) and child.name not in self._jobs:
                    try:
                        if child.stat().st_mtime < cutoff:
                            shutil.rmtree(child, ignore_errors=True)
                    except OSError:
                        continue

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._synthesizer.unload()
