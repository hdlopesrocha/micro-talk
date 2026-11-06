"""FFmpeg wrappers and media record helpers."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
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
from micro_common.text import (
    build_srt,
    distribute_duration,
    estimate_speech_duration,
    sentence_split,
)

from .config import Settings

logger = logging.getLogger(__name__)


def run_ffmpeg(args: list[str], timeout: int, code: str = "audio_conversion_failed") -> None:
    try:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ServiceError("ffmpeg_unavailable", "FFmpeg is not available in the media service.", status_code=503) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("ffmpeg_timeout", "Audio processing took too long.", status_code=504) from exc
    if completed.returncode != 0:
        logger.error("FFmpeg failed: %s", completed.stderr[-3000:])
        raise ServiceError(
            code,
            "The audio file could not be processed. It may be corrupt or use an unsupported codec.",
            status_code=500,
        )


def probe_audio(path: Path, settings: Settings) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,sample_rate,channels",
                "-show_entries",
                "format=duration,size",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=settings.ffprobe_timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ServiceError("ffprobe_unavailable", "FFprobe is not available.", status_code=503) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("ffprobe_timeout", "Inspecting the audio file took too long.", status_code=504) from exc
    if completed.returncode != 0:
        raise ServiceError(
            "invalid_audio",
            "The file is not a readable audio file.",
            status_code=422,
        )
    import json

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ServiceError("invalid_audio", "The audio metadata could not be read.", status_code=422) from exc
    streams = payload.get("streams") or []
    if not streams:
        raise ServiceError("invalid_audio", "The file contains no audio stream.", status_code=422)
    try:
        duration = float((payload.get("format") or {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise ServiceError("invalid_audio", "The audio file has no measurable duration.", status_code=422)
    return {
        "duration_seconds": duration,
        "codec": streams[0].get("codec_name", ""),
        "sample_rate": int(streams[0].get("sample_rate", 0) or 0),
        "channels": int(streams[0].get("channels", 0) or 0),
        "size_bytes": int((payload.get("format") or {}).get("size", 0) or 0),
    }


def encode_audio(
    source: Path,
    destination: Path,
    output_format: str,
    normalize: bool,
    settings: Settings,
) -> None:
    ensure_dir(destination.parent)
    check_disk_space(destination.parent, settings.minimum_free_mb)
    temporary = destination.with_name(f".{destination.stem}.part{destination.suffix}")
    args = ["-i", str(source), "-map", "0:a:0", "-vn"]
    if normalize:
        args += ["-af", "dynaudnorm=f=200:g=15:p=0.90"]
    if output_format == "mp3":
        args += ["-codec:a", "libmp3lame", "-b:a", settings.default_mp3_bitrate, "-ar", "44100"]
    elif output_format == "wav":
        args += ["-codec:a", "pcm_s16le", "-ar", "44100"]
    else:
        raise ServiceError("invalid_audio_format", "Audio format must be mp3 or wav.", status_code=422)
    args.append(str(temporary))
    run_ffmpeg(args, settings.ffmpeg_timeout_seconds)
    if not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise ServiceError("audio_conversion_failed", "The audio conversion produced no output.", status_code=500)
    temporary.replace(destination)


def media_file_path(media_dir: str, relative_path: str) -> Path:
    return resolve_relative(media_dir, relative_path)


class MediaStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.media_dir)
        self.files_root = ensure_dir(self.root / "files")
        ensure_dir(self.root / "tmp")

    def _record_dir(self, media_id: str) -> Path:
        if not is_uuid(media_id):
            raise ServiceError("invalid_media_id", "The media id is invalid.", status_code=400)
        return self.files_root / media_id

    def create_record(self, record: dict[str, Any]) -> dict[str, Any]:
        media_id = record["id"]
        folder = ensure_dir(self._record_dir(media_id))
        record["metadata_path"] = relative_to_root(self.root, folder / "record.json")
        record.setdefault("created_at", time.time())
        atomic_write_json(folder / "record.json", record)
        return self.public(record)

    def load_record(self, media_id: str) -> dict[str, Any]:
        folder = self._record_dir(media_id)
        record = read_json(folder / "record.json", default=None)
        if not record:
            raise ServiceError("media_not_found", "The media file could not be found.", status_code=404)
        return record

    def public(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = dict(record)
        payload["download_url"] = f"/api/v1/media/{payload['id']}/download"
        payload["inline_url"] = f"/api/v1/media/{payload['id']}/inline"
        return payload

    def file_for(self, media_id: str) -> tuple[Path, dict[str, Any]]:
        record = self.load_record(media_id)
        path = media_file_path(str(self.root), record["relative_path"])
        if not path.is_file():
            raise ServiceError("media_file_missing", "The stored media file is missing.", status_code=404)
        return path, record

    def delete(self, media_id: str) -> None:
        folder = self._record_dir(media_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=False)

    def cleanup(self, active_ids: set[str]) -> None:
        cutoff = time.time() - self.settings.retention_hours * 3600
        for folder in self.files_root.iterdir() if self.files_root.exists() else []:
            if not folder.is_dir() or not is_uuid(folder.name) or folder.name in active_ids:
                continue
            try:
                if folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    logger.info("Removed expired media %s", folder.name)
            except OSError:
                continue
        tmp = self.root / "tmp"
        if tmp.exists():
            for child in tmp.iterdir():
                try:
                    if child.stat().st_mtime < cutoff:
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink(missing_ok=True)
                except OSError:
                    continue

    def new_temp_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="media-", dir=str(self.root / "tmp")))


def normalize_timings(
    segments: list[dict[str, Any]],
    audio_duration: float,
    allow_scale: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    cleaned: list[dict[str, Any]] = []
    previous_end = 0.0
    max_end = 0.0
    for index, item in enumerate(segments):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(item.get("start", previous_end))
            end = float(item.get("end", start + 1.0))
        except (TypeError, ValueError):
            start = previous_end
            end = start + 1.0
        start = max(0.0, start)
        end = max(start + 0.2, end)
        if start < previous_end:
            start = previous_end
            end = max(end, start + 0.2)
        cleaned.append(
            {
                "index": len(cleaned) + 1,
                "text": text,
                "start": start,
                "end": end,
            }
        )
        previous_end = end
        max_end = max(max_end, end)
    if not cleaned:
        raise ServiceError("no_subtitle_segments", "There are no subtitle segments to write.", status_code=422)
    if max_end > audio_duration + 0.05:
        if allow_scale:
            factor = audio_duration / max_end if max_end else 1.0
            for segment in cleaned:
                segment["start"] = round(min(audio_duration, segment["start"] * factor), 3)
                segment["end"] = round(min(audio_duration, segment["end"] * factor), 3)
                if segment["end"] <= segment["start"]:
                    segment["end"] = min(audio_duration, segment["start"] + 0.2)
            warnings.append(
                "Subtitle timings were scaled proportionally to the final encoded audio duration."
            )
        else:
            for segment in cleaned:
                segment["start"] = min(segment["start"], audio_duration)
                segment["end"] = min(segment["end"], audio_duration)
            warnings.append("Subtitle timings were clamped to the final audio duration.")
    return cleaned, warnings


def segments_from_text(text: str, language: str) -> list[str]:
    sentences: list[str] = []
    from micro_common.text import clean_text, split_long_piece, split_paragraphs

    for paragraph in split_paragraphs(text):
        for sentence in sentence_split(paragraph, language):
            sentences.extend(split_long_piece(sentence, 160))
    return [clean_text(sentence) for sentence in sentences if clean_text(sentence)]


def build_subtitle_payload(
    *,
    text: str | None,
    segments: list[dict[str, Any]] | None,
    language: str,
    audio_duration: float | None,
    words_per_minute: float,
) -> dict[str, Any]:
    warnings: list[str] = []
    timing_method = "provided_segment_timings"
    if segments:
        if audio_duration is None:
            raise ServiceError(
                "audio_required_for_timings",
                "Segment timings are tied to generated audio, but no final audio was provided.",
                status_code=422,
            )
        final_segments, warnings = normalize_timings(segments, audio_duration)
        timing_method = "generated_audio_segment_timings"
    else:
        if not text or not text.strip():
            raise ServiceError("no_subtitle_segments", "Provide text or timed segments for subtitles.", status_code=422)
        sentences = segments_from_text(text, language)
        if not sentences:
            raise ServiceError("no_subtitle_segments", "No sentences were found for subtitles.", status_code=422)
        if audio_duration is not None:
            # The minimum cue length must never force the sum of subtitle
            # timings past the real audio duration.
            minimum = min(0.6, audio_duration / max(1, len(sentences)))
            timings = distribute_duration(audio_duration, sentences, minimum=minimum)
            timing_method = "proportional_to_actual_audio_duration"
            warnings.append(
                "Sentence timings were distributed proportionally over the actual audio duration; no word-level alignment is available."
            )
        else:
            duration = sum(estimate_speech_duration(sentence, words_per_minute) for sentence in sentences)
            timings = distribute_duration(duration, sentences)
            timing_method = "estimated_from_text_word_rate"
            warnings.append(
                "No audio was provided. Subtitle timings are estimates based on the configured speaking rate."
            )
        final_segments = [
            {"index": index + 1, "text": sentence, "start": round(start, 3), "end": round(end, 3)}
            for index, (sentence, (start, end)) in enumerate(zip(sentences, timings, strict=False))
        ]
    srt = build_srt(final_segments, total_duration=audio_duration)
    return {
        "segments": final_segments,
        "srt": srt,
        "timing_method": timing_method,
        "warnings": warnings,
        "segment_count": len(final_segments),
    }
