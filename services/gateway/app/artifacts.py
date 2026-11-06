"""Artifact files, ZIP bundles and WebVTT captions for gateway jobs."""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from micro_common.storage import atomic_write_text, ensure_dir, safe_join
from micro_common.text import format_vtt_timestamp, safe_filename, word_count

from .config import Settings

TEXT_ARTIFACTS = {
    "extracted": ("extracted.txt", "Extracted text"),
    "translation": ("translation.txt", "Translation"),
    "summary": ("summary.txt", "Summary"),
}
OUTPUT_FILES = ("extracted.txt", "translation.txt", "summary.txt", "subtitles.srt", "captions.vtt")
_PAGE_MARKER_LINE = re.compile(r"(?m)^[ \t]*---[ \t]*Page[ \t]+\d+[ \t]*---[ \t]*$")


def _artifact_word_count(text: str) -> int:
    """Count document words, not structural PDF page markers."""

    return word_count(_PAGE_MARKER_LINE.sub(" ", text))


def job_directory(settings: Settings, job_id: str) -> Path:
    return ensure_dir(safe_join(settings.jobs_dir, job_id.lower()))


def artifact_file(settings: Settings, job_id: str, filename: str) -> Path:
    return safe_join(str(job_directory(settings, job_id)), filename)


def write_text_artifact(
    settings: Settings, job_id: str, kind: str, text: str
) -> dict[str, Any]:
    if kind not in TEXT_ARTIFACTS:
        raise ValueError(f"Unknown text artifact: {kind}")
    filename, label = TEXT_ARTIFACTS[kind]
    path = artifact_file(settings, job_id, filename)
    atomic_write_text(path, text)
    return {
        "kind": kind,
        "label": label,
        "filename": filename,
        "size_bytes": path.stat().st_size,
        "characters": len(text),
        "words": _artifact_word_count(text),
        "edited_at": time.time(),
        "available": True,
    }


def read_text_artifact(settings: Settings, job_id: str, kind: str) -> str:
    if kind not in TEXT_ARTIFACTS:
        raise KeyError(kind)
    path = artifact_file(settings, job_id, TEXT_ARTIFACTS[kind][0])
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def write_subtitles(settings: Settings, job_id: str, srt: str) -> dict[str, Any]:
    path = artifact_file(settings, job_id, "subtitles.srt")
    atomic_write_text(path, srt)
    return {
        "filename": "subtitles.srt",
        "size_bytes": path.stat().st_size,
        "characters": len(srt),
        "available": True,
    }


def build_vtt(segments: list[dict[str, Any]]) -> str:
    lines = ["WEBVTT", ""]
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        start = max(0.0, float(segment.get("start") or 0.0))
        end = max(start + 0.2, float(segment.get("end") or start + 1.0))
        lines.append(str(index))
        lines.append(f"{format_vtt_timestamp(start)} --> {format_vtt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def write_captions(
    settings: Settings, job_id: str, segments: list[dict[str, Any]]
) -> dict[str, Any]:
    vtt = build_vtt(segments)
    path = artifact_file(settings, job_id, "captions.vtt")
    atomic_write_text(path, vtt)
    return {
        "filename": "captions.vtt",
        "size_bytes": path.stat().st_size,
        "cues": len([segment for segment in segments if str(segment.get("text") or "").strip()]),
        "available": True,
    }


def _slug(state: dict[str, Any]) -> str:
    title = str(state.get("title") or "microtalk")
    return safe_filename(title, "", fallback="microtalk").rstrip("._-") or "microtalk"


def build_zip(settings: Settings, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Bundle every available result. Missing optional files are skipped."""
    dir_path = job_directory(settings, job_id)
    archive_path = dir_path / "output.zip"
    archive_tmp = dir_path / ".output.zip.part"
    slug = _slug(state)
    artifacts = state.get("artifacts") or {}
    included: list[str] = []

    def add_file(source: Path | None, arcname: str) -> None:
        if source and source.is_file():
            archive.write(source, arcname)
            included.append(arcname)

    with zipfile.ZipFile(archive_tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for kind, (filename, label) in TEXT_ARTIFACTS.items():
            if artifacts.get(kind):
                add_file(dir_path / filename, f"{slug}-{kind}.txt")
        for kind in ("subtitles", "captions"):
            filename = "subtitles.srt" if kind == "subtitles" else "captions.vtt"
            if artifacts.get(kind):
                add_file(dir_path / filename, f"{slug}-{filename}")
        audio = artifacts.get("audio") or {}
        audio_rel = str(audio.get("path") or "")
        if audio_rel:
            try:
                audio_path = safe_join(settings.media_data_dir, *Path(audio_rel).parts)
            except Exception:
                audio_path = None
            add_file(audio_path, f"{slug}-audio.{audio.get('format') or 'mp3'}")
        wav = artifacts.get("wav") or {}
        wav_rel = str(wav.get("path") or "")
        if wav_rel and wav_rel != audio_rel:
            try:
                wav_path = safe_join(settings.tts_data_dir, *Path(wav_rel).parts)
            except Exception:
                wav_path = None
            add_file(wav_path, f"{slug}-audio.wav")
        manifest = {
            "id": job_id,
            "title": state.get("title"),
            "created_at": state.get("created_at"),
            "completed_at": state.get("finished_at"),
            "config": state.get("config"),
            "artifacts": {
                key: value
                for key, value in artifacts.items()
                if isinstance(value, dict) and "path" not in value
            },
            "warnings": state.get("warnings") or [],
            "generator": "MicroTalk local document studio",
        }
        archive.writestr(
            "job.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        included.append("job.json")
    archive_tmp.replace(archive_path)
    return {
        "filename": f"{slug}-microtalk.zip",
        "size_bytes": archive_path.stat().st_size,
        "files": included,
        "available": True,
    }


def remove_job_files(settings: Settings, job_id: str) -> None:
    """Remove gateway-owned files for one job. Callers handle media/TTS cleanup."""
    import shutil

    try:
        folder = safe_join(settings.jobs_dir, job_id.lower())
    except Exception:
        return
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def artifact_download_name(state: dict[str, Any], kind: str) -> str:
    slug = _slug(state)
    mapping = {
        "extracted": "extracted.txt",
        "translation": "translation.txt",
        "summary": "summary.txt",
        "srt": "subtitles.srt",
        "vtt": "captions.vtt",
        "zip": "microtalk.zip",
    }
    if kind == "audio":
        audio = (state.get("artifacts") or {}).get("audio") or {}
        extension = audio.get("format") or "mp3"
        return f"{slug}-audio.{extension}"
    if kind == "wav":
        return f"{slug}-audio.wav"
    return f"{slug}-{mapping.get(kind, kind)}"
