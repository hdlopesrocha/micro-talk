"""Orchestration of PDF, translation, summary, TTS and media jobs."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from micro_common.storage import atomic_write_json, check_disk_space, ensure_dir
from micro_common.text import (
    clean_text,
    normalize_language,
    split_paragraphs,
    word_count,
)

from . import artifacts
from .clients import ServiceCallError, ServiceClients
from .config import Settings, settings
from .models import JobCreateRequest
from .store import JobStore

logger = logging.getLogger(__name__)

MAX_TRANSLATION_BATCH_CHARS = 45_000
MAX_TRANSLATION_BATCH_SEGMENTS = 400
TTS_POLL_SAVE_INTERVAL = 1.5


class JobCancelled(Exception):
    pass


class PipelineFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int = 500,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.http_status = http_status


def build_steps(config: JobCreateRequest) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []

    def add(step_id: str, label: str, weight: int, message: str) -> None:
        steps.append(
            {
                "id": step_id,
                "label": label,
                "weight": weight,
                "status": "pending",
                "progress": 0,
                "message": message,
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
        )

    if config.input.type == "pdf":
        add("extract", "Extract PDF text", 18, "Waiting to extract the selected pages.")
    else:
        add("prepare", "Prepare pasted text", 4, "Waiting to prepare the text.")
    if config.translation.enabled:
        add("translate", "Translate document", 26, "Waiting to translate the document.")
    if config.summary.enabled:
        add("summarize", "Summarize document", 18, "Waiting to build the summary.")
    if config.tts.enabled:
        add("synthesize", "Generate speech", 22, "Waiting to synthesize speech.")
        add("finalize", "Finalize audio and subtitles", 9, "Waiting to encode audio and subtitles.")
    add("archive", "Build download bundle", 3, "Waiting to build the ZIP bundle.")
    return steps


def build_initial_state(
    job_id: str,
    request: JobCreateRequest,
    *,
    title: str,
) -> dict[str, Any]:
    now = time.time()
    return {
        "id": job_id,
        "status": "queued",
        "title": title,
        "progress": 0,
        "message": "Queued for processing.",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "current_step": None,
        "config": request.public_config(),
        "steps": build_steps(request),
        "artifacts": {},
        "warnings": [],
        "source": {},
        "languages": {},
        "stats": {},
        "error": None,
        "cancel_requested": False,
    }


def _overall_progress(state: dict[str, Any]) -> int:
    steps = state.get("steps") or []
    total = sum(int(step.get("weight") or 0) for step in steps)
    if total <= 0:
        return 0
    earned = 0.0
    for step in steps:
        weight = int(step.get("weight") or 0)
        progress = int(step.get("progress") or 0)
        status = step.get("status")
        if status in {"completed", "skipped"}:
            progress = 100
        elif status == "failed":
            progress = min(progress, 99)
        earned += weight * max(0, min(100, progress)) / 100.0
    return max(0, min(99, int(round(earned / total * 100))))


def _add_warning(state: dict[str, Any], message: str) -> None:
    warnings = state.setdefault("warnings", [])
    if message and message not in warnings:
        warnings.append(message)


def _step(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in state.get("steps") or []:
        if step.get("id") == step_id:
            return step
    raise KeyError(step_id)


def _language_variant(canonical: str | None, target: str | None = None) -> str:
    value = (target or canonical or "en").lower().replace("_", "-")
    if value in {"pt", "pt-pt", "pt-br"}:
        return "pt-BR" if value == "pt-br" else "pt-PT"
    if value in {"fr", "fr-fr", "fr-ca"}:
        return "fr"
    return "en"


def _canonical(value: str | None) -> str:
    try:
        return normalize_language(value or "en")
    except ValueError:
        return "en"


class PipelineRunner:
    def __init__(
        self,
        job_id: str,
        *,
        store: JobStore,
        clients: ServiceClients,
        app_settings: Settings | None = None,
    ) -> None:
        self.job_id = job_id
        self.store = store
        self.clients = clients
        self.settings = app_settings or settings

    # -- state helpers -------------------------------------------------

    def _load(self) -> dict[str, Any]:
        job = self.store.get(self.job_id)
        if not job:
            raise PipelineFailure("job_not_found", "The job no longer exists.", http_status=404)
        return job

    def _save(self, state: dict[str, Any]) -> None:
        self.store.save_state(state)

    def _cancelled(self) -> bool:
        fields = self.store.get_raw_fields(self.job_id)
        return bool(fields and fields["cancel_requested"])

    def _check_cancel(self) -> None:
        if self._cancelled():
            raise JobCancelled()

    def _begin(
        self, state: dict[str, Any], step_id: str, message: str | None = None
    ) -> None:
        step = _step(state, step_id)
        step.update(
            {
                "status": "running",
                "progress": 1,
                "message": message or step.get("message"),
                "started_at": time.time(),
            }
        )
        state["current_step"] = step_id
        state["status"] = "running"
        state["message"] = message or step.get("label") or "Working..."
        if not state.get("started_at"):
            state["started_at"] = time.time()
        state["progress"] = _overall_progress(state)
        self._save(state)

    def _update(
        self,
        state: dict[str, Any],
        step_id: str,
        progress: int,
        message: str,
    ) -> None:
        step = _step(state, step_id)
        step["progress"] = max(int(step.get("progress") or 0), max(0, min(100, progress)))
        step["message"] = message
        state["message"] = message
        state["progress"] = _overall_progress(state)
        self._save(state)

    def _complete(
        self, state: dict[str, Any], step_id: str, message: str | None = None
    ) -> None:
        step = _step(state, step_id)
        step["status"] = "completed"
        step["progress"] = 100
        step["message"] = message or step.get("message") or "Done."
        step["finished_at"] = time.time()
        state["message"] = step["message"]
        state["progress"] = _overall_progress(state)
        self._save(state)

    # -- pipeline ------------------------------------------------------

    def run(self) -> None:
        state = self._load()
        if state.get("status") in {"completed", "failed", "cancelled"}:
            return
        if self._cancelled():
            self.store.mark_cancelled(self.job_id)
            return
        state["status"] = "running"
        state["started_at"] = time.time()
        state["message"] = "Starting the local processing pipeline."
        self._save(state)

        try:
            check_disk_space(self.settings.data_dir, self.settings.minimum_free_mb)
            config = JobCreateRequest.model_validate(state["config"])
            content = self._prepare_input(state, config)
            content = self._translate(state, config, content)
            summary = self._summarize(state, config, content)
            tts_result = self._synthesize(state, config, content, summary)
            if config.tts.enabled:
                self._finalize(state, config, tts_result)
            self._archive(state)
            self._check_cancel()
            state = self._load()
            state["status"] = "completed"
            state["progress"] = 100
            state["finished_at"] = time.time()
            state["message"] = "Workflow complete. All available results are ready."
            state["current_step"] = None
            self._save(state)
            logger.info("Job %s completed", self.job_id)
        except JobCancelled:
            logger.info("Job %s cancelled", self.job_id)
            self.store.mark_cancelled(self.job_id)
        except ServiceCallError as exc:
            self._fail(state, exc.code, exc.message, exc.details)
        except PipelineFailure as exc:
            self._fail(state, exc.code, exc.message, exc.details)
        except Exception:
            logger.exception("Unexpected failure in job %s", self.job_id)
            self._fail(
                state,
                "pipeline_internal_error",
                "An unexpected error stopped the workflow. Check the worker logs for details.",
                {},
            )

    def _fail(
        self,
        state: dict[str, Any],
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            state = self._load()
        except Exception:
            return
        current = state.get("current_step")
        for step in state.get("steps") or []:
            if step.get("id") == current and step.get("status") == "running":
                step["status"] = "failed"
                step["error"] = {"code": code, "message": message}
                step["finished_at"] = time.time()
                step["message"] = message
            elif step.get("status") == "pending":
                step["status"] = "skipped"
        state["status"] = "failed"
        state["message"] = message
        state["error"] = {
            "code": code,
            "message": message,
            "details": details or {},
        }
        state["finished_at"] = time.time()
        state["progress"] = min(99, int(state.get("progress") or 0))
        state["current_step"] = None
        self._save(state)
        logger.warning("Job %s failed (%s): %s", self.job_id, code, message)

    # -- input ---------------------------------------------------------

    def _prepare_input(
        self, state: dict[str, Any], config: JobCreateRequest
    ) -> dict[str, Any]:
        if config.input.type == "text":
            step_id = "prepare"
            self._begin(state, step_id, "Preparing pasted text.")
            text = clean_text(config.input.text or "")
            if not text:
                raise PipelineFailure("empty_text", "The pasted text is empty.", http_status=422)
            state["source"] = {
                "type": "text",
                "filename": "Pasted text",
                "page_count": 0,
                "selected_pages": [],
                "ocr_enabled": False,
            }
            artifact = artifacts.write_text_artifact(
                self.settings, self.job_id, "extracted", text + ("\n" if not text.endswith("\n") else "")
            )
            state.setdefault("artifacts", {})["extracted"] = artifact
            state.setdefault("stats", {})["extracted_words"] = word_count(text)
            state.setdefault("stats", {})["extracted_characters"] = len(text)
            self._complete(state, step_id, f"Prepared {word_count(text):,} words.")
            return {
                "text": text,
                "display_text": text,
                "pages": [{"page": None, "text": text}],
                "segments": [
                    {"id": f"t{index}", "page": None, "text": paragraph}
                    for index, paragraph in enumerate(split_paragraphs(text), start=1)
                ],
                "canonical_language": self._explicit_language(config.input.language),
                "variant_language": self._explicit_variant(config.input.language),
                "filename": "Pasted text",
                "page_count": 0,
                "warnings": [],
            }

        step_id = "extract"
        document_id = config.input.document_id or ""
        self._begin(state, step_id, "Reading the PDF document.")
        document = self.clients.get_document(document_id)
        page_count = int(document.get("page_count") or 0)
        try:
            selected = config.input.resolved_pages(page_count)
        except ValueError as exc:
            raise PipelineFailure("invalid_page_range", str(exc), http_status=422) from exc
        state["source"] = {
            "type": "pdf",
            "document_id": document_id,
            "filename": document.get("filename") or "document.pdf",
            "title": document.get("title") or "",
            "page_count": page_count,
            "selected_pages": selected,
            "ocr_enabled": config.input.ocr_enabled,
            "ignore_notes": config.input.ignore_notes,
            "ignore_figure_descriptions": config.input.ignore_figure_descriptions,
            "likely_scanned": bool(document.get("likely_scanned")),
            "size_bytes": document.get("size_bytes"),
        }
        state["message"] = f"Extracting {len(selected)} page(s) from the PDF."
        self._save(state)
        self._check_cancel()
        self._update(
            state,
            step_id,
            8,
            f"Extracting text from {len(selected)} selected page(s)."
            + (" OCR is enabled." if config.input.ocr_enabled else ""),
        )
        result = self.clients.extract(
            document_id,
            selected_pages=selected,
            ocr_enabled=config.input.ocr_enabled,
            ignore_notes=config.input.ignore_notes,
            ignore_figure_descriptions=config.input.ignore_figure_descriptions,
        )
        self._check_cancel()
        display_text = str(result.get("plain_text") or "")
        pages = result.get("pages") or []
        content_text = "\n\n".join(
            str(page.get("text") or "").strip()
            for page in pages
            if str(page.get("text") or "").strip()
        ).strip()
        if not content_text:
            raise PipelineFailure(
                "empty_extracted_text",
                "No readable text was extracted from the selected pages.",
                details={"selected_pages": selected},
                http_status=422,
            )
        artifact = artifacts.write_text_artifact(
            self.settings,
            self.job_id,
            "extracted",
            display_text + ("\n" if not display_text.endswith("\n") else ""),
        )
        state.setdefault("artifacts", {})["extracted"] = artifact
        stats = state.setdefault("stats", {})
        stats["extracted_words"] = word_count(content_text)
        stats["extracted_characters"] = len(content_text)
        stats["pages_with_text"] = len(
            [page for page in pages if str(page.get("text") or "").strip()]
        )
        for warning in result.get("warnings") or []:
            _add_warning(state, str(warning))
        if result.get("ocr_pages"):
            _add_warning(
                state,
                "OCR was used for page(s): "
                + ", ".join(str(page) for page in result["ocr_pages"])
                + ".",
            )
        self._complete(
            state,
            step_id,
            f"Extracted {word_count(content_text):,} words from "
            f"{stats['pages_with_text']} page(s).",
        )
        return {
            "text": content_text,
            "display_text": display_text,
            "pages": pages,
            "segments": self._segments_from_pages(pages),
            "canonical_language": self._explicit_language(config.input.language),
            "variant_language": self._explicit_variant(config.input.language),
            "filename": document.get("filename") or "document.pdf",
            "page_count": page_count,
            "warnings": list(result.get("warnings") or []),
        }

    @staticmethod
    def _explicit_language(value: str) -> str | None:
        if not value or value == "auto":
            return None
        try:
            return normalize_language(value)
        except ValueError:
            return None

    @staticmethod
    def _explicit_variant(value: str) -> str | None:
        normalized = (value or "").lower().replace("_", "-")
        if normalized == "pt-br":
            return "pt-BR"
        if normalized in {"pt", "pt-pt"}:
            return "pt-PT"
        if normalized == "en":
            return "en"
        if normalized in {"fr", "fr-fr", "fr-ca"}:
            return "fr"
        return None

    @staticmethod
    def _segments_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for page in pages:
            page_number = page.get("page")
            text = str(page.get("text") or "").strip()
            if not text:
                continue
            paragraphs = page.get("paragraphs") or split_paragraphs(text)
            for index, paragraph in enumerate(paragraphs, start=1):
                cleaned = clean_text(str(paragraph))
                if cleaned:
                    segments.append(
                        {
                            "id": f"p{page_number}-{index}",
                            "page": int(page_number) if page_number else None,
                            "text": cleaned,
                        }
                    )
        return segments

    def _detect_language(self, text: str) -> tuple[str | None, str | None]:
        try:
            payload = self.clients.detect_language(text)
            canonical = payload.get("canonical") or payload.get("language")
            if canonical in {"pt", "en", "fr"}:
                return canonical, _language_variant(canonical)
        except ServiceCallError as exc:
            logger.info("Language detection unavailable: %s", exc.message)
        return None, None

    @staticmethod
    def _voice_matches_language(speech_language: str, voice_language: str) -> bool:
        speech = (speech_language or "").lower().replace("_", "-")
        voice = (voice_language or "").lower().replace("_", "-")
        if speech.split("-")[0] != voice.split("-")[0]:
            return False
        # A qualified variant (pt-BR, pt-PT) must match exactly. An
        # unqualified target such as "en" accepts any regional voice.
        return speech == voice if "-" in speech else True

    # -- translation ---------------------------------------------------

    def _translate(
        self, state: dict[str, Any], config: JobCreateRequest, content: dict[str, Any]
    ) -> dict[str, Any]:
        canonical = content.get("canonical_language")
        variant = content.get("variant_language")
        if not canonical:
            detected, detected_variant = self._detect_language(content["text"][:20_000])
            canonical = detected
            variant = detected_variant
        state.setdefault("languages", {})["content"] = variant or canonical
        state.setdefault("languages", {})["content_canonical"] = canonical

        if not config.translation.enabled:
            self._save(state)
            return {
                **content,
                "content_text": content["text"],
                "content_segments": content["segments"],
                "content_canonical": canonical,
                "content_variant": variant,
                "translated": False,
            }

        step_id = "translate"
        self._begin(
            state,
            step_id,
            f"Translating into {config.translation.target_language}.",
        )
        segments = content["segments"]
        if not segments:
            raise PipelineFailure("empty_text", "There is no text to translate.", http_status=422)
        batches = self._translation_batches(segments)
        translated_segments: list[dict[str, Any]] = []
        source_language: str | None = None
        target_language = config.translation.target_language
        models: list[str] = []
        total_batches = len(batches)
        for index, batch in enumerate(batches):
            self._check_cancel()
            response = self.clients.translate(
                segments=batch,
                source_language=config.translation.source_language,
                target_language=target_language,
            )
            source_language = source_language or str(response.get("source_language") or "")
            target_language = str(response.get("target_language") or target_language)
            if response.get("model"):
                models.append(str(response["model"]))
            for translated in response.get("segments") or []:
                translated_segments.append(
                    {
                        "id": translated.get("id"),
                        "page": translated.get("page"),
                        "text": str(translated.get("text") or "").strip(),
                    }
                )
            progress = 4 + int((index + 1) / max(1, total_batches) * 92)
            self._update(
                state,
                step_id,
                progress,
                f"Translated batch {index + 1} of {total_batches}.",
            )
        translated_segments = [segment for segment in translated_segments if segment["text"]]
        if not translated_segments:
            raise PipelineFailure(
                "translation_empty",
                "The translation service returned no translated text.",
            )
        display_text = self._render_translated_text(translated_segments)
        content_text = "\n\n".join(segment["text"] for segment in translated_segments).strip()
        artifact = artifacts.write_text_artifact(
            self.settings, self.job_id, "translation", display_text
        )
        state.setdefault("artifacts", {})["translation"] = artifact
        canonical_target = _canonical(target_language)
        state.setdefault("languages", {}).update(
            {
                "translation_source": source_language,
                "translation_target": target_language,
                "translation_target_canonical": canonical_target,
                "content_machine_translated": True,
            }
        )
        state.setdefault("stats", {})["translated_words"] = word_count(content_text)
        state.setdefault("stats", {})["translated_characters"] = len(content_text)
        state.setdefault("stats", {})["translation_batches"] = total_batches
        if source_language == _canonical(target_language):
            _add_warning(
                state,
                "The source and target languages are the same; the translation is an identity copy.",
            )
        self._complete(
            state,
            step_id,
            f"Translated {word_count(content_text):,} words into {target_language}.",
        )
        return {
            **content,
            "content_text": content_text,
            "content_segments": translated_segments,
            "content_canonical": canonical_target,
            "content_variant": target_language,
            "translated": True,
            "display_text": display_text,
            "source_language": source_language,
        }

    @staticmethod
    def _translation_batches(
        segments: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        chars = 0
        for segment in segments:
            size = len(segment["text"])
            if current and (
                chars + size > MAX_TRANSLATION_BATCH_CHARS
                or len(current) >= MAX_TRANSLATION_BATCH_SEGMENTS
            ):
                batches.append(current)
                current = []
                chars = 0
            current.append(segment)
            chars += size
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _render_translated_text(segments: list[dict[str, Any]]) -> str:
        has_pages = any(segment.get("page") is not None for segment in segments)
        if not has_pages:
            return "\n\n".join(segment["text"] for segment in segments).strip() + "\n"
        groups: list[tuple[int, list[str]]] = []
        for segment in segments:
            page = int(segment.get("page") or 1)
            if not groups or groups[-1][0] != page:
                groups.append((page, []))
            groups[-1][1].append(segment["text"])
        return (
            "\n\n".join(
                f"--- Page {page} ---\n\n" + "\n\n".join(parts)
                for page, parts in groups
            ).strip()
            + "\n"
        )

    # -- summary -------------------------------------------------------

    def _summarize(
        self,
        state: dict[str, Any],
        config: JobCreateRequest,
        content: dict[str, Any],
    ) -> str | None:
        if not config.summary.enabled:
            return None
        step_id = "summarize"
        self._begin(state, step_id, "Building a local summary.")
        source_canonical = str(content.get("content_canonical") or "en")
        if source_canonical not in {"pt", "en"}:
            raise PipelineFailure(
                "summary_language_unsupported",
                "Summarization is available only for English and Portuguese text. "
                "Translate the document to English or Portuguese first, or disable summarization.",
                details={"content_language": source_canonical},
                http_status=422,
            )
        if config.summary.language:
            summary_language = config.summary.language
        else:
            summary_language = str(
                content.get("content_variant")
                or _language_variant(source_canonical)
            )
        source_language = source_canonical if source_canonical in {"pt", "en"} else "auto"
        text = str(content.get("content_text") or "")
        self._update(
            state,
            step_id,
            6,
            f"Summarizing {word_count(text):,} words ({config.summary.length}).",
        )
        result = self.clients.summarize(
            text,
            language=summary_language,
            source_language=source_language,
            length=config.summary.length,
        )
        self._check_cancel()
        summary = str(result.get("summary") or "").strip()
        if not summary:
            raise PipelineFailure(
                "summary_empty",
                "The summarization service returned an empty summary.",
            )
        artifact = artifacts.write_text_artifact(
            self.settings, self.job_id, "summary", summary + "\n"
        )
        state.setdefault("artifacts", {})["summary"] = artifact
        state.setdefault("languages", {})["summary"] = str(
            result.get("language") or summary_language
        )
        state.setdefault("stats", {})["summary_words"] = word_count(summary)
        state.setdefault("stats", {})["summary_input_words"] = result.get("input_words")
        state.setdefault("stats", {})["summary_chunks"] = result.get("chunk_count")
        state.setdefault("stats", {})["summary_levels"] = result.get("hierarchy_levels")
        for warning in result.get("warnings") or []:
            _add_warning(state, str(warning))
        if result.get("translated"):
            _add_warning(
                state,
                "The summary was generated in the source language and translated locally.",
            )
        self._complete(
            state,
            step_id,
            f"Summary ready ({word_count(summary):,} words).",
        )
        return summary

    # -- speech --------------------------------------------------------

    def _synthesize(
        self,
        state: dict[str, Any],
        config: JobCreateRequest,
        content: dict[str, Any],
        summary: str | None,
    ) -> dict[str, Any] | None:
        if not config.tts.enabled:
            return None
        step_id = "synthesize"
        self._begin(state, step_id, "Preparing speech synthesis.")
        if config.tts.text_source == "summary":
            speech_text = (summary or "").strip()
            speech_language = str(
                state.get("languages", {}).get("summary")
                or content.get("content_variant")
                or _language_variant(content.get("content_canonical"))
            )
        else:
            speech_text = str(content.get("content_text") or "").strip()
            speech_language = str(
                content.get("content_variant")
                or _language_variant(content.get("content_canonical"))
            )
        if not speech_text:
            raise PipelineFailure("empty_text", "There is no text to synthesize.", http_status=422)
        if len(speech_text) > 500_000:
            raise PipelineFailure(
                "tts_text_too_large",
                "The speech text exceeds the 500,000-character TTS limit. Select fewer pages.",
                details={"characters": len(speech_text), "limit": 500_000},
                http_status=413,
            )
        voices = self.clients.tts_voices().get("voices") or []
        voice = next(
            (item for item in voices if item.get("id") == config.tts.voice), None
        )
        if not voice:
            raise PipelineFailure(
                "tts_voice_unknown",
                f"The selected voice '{config.tts.voice}' is not available in the TTS service.",
                details={"voices": [item.get("id") for item in voices]},
                http_status=422,
            )
        voice_language = str(voice.get("language") or speech_language)
        if not voice.get("available"):
            raise PipelineFailure(
                "tts_voice_missing",
                f"The Piper voice {config.tts.voice} has not been installed.",
                details={"install_command": "docker compose run --rm tts-service download-models"},
                http_status=503,
            )
        if not self._voice_matches_language(speech_language, voice_language):
            raise PipelineFailure(
                "tts_voice_language_mismatch",
                f"The text language ({speech_language}) does not match the selected voice "
                f"({voice_language}). Choose a matching voice or change the translation target.",
                details={"text_language": speech_language, "voice_language": voice_language},
                http_status=422,
            )
        state.setdefault("languages", {})["speech"] = voice_language
        self._update(state, step_id, 3, f"Starting Piper voice {config.tts.voice}.")
        created = self.clients.tts_create(
            {
                "text": speech_text,
                "language": speech_language,
                "voice": config.tts.voice,
                "speed": config.tts.speed,
                "pitch": 0.0,
                "format": config.tts.format,
                "normalize_audio": False,
                "preserve_phrases": config.tts.preserve_phrases,
                "ignore_leading_numbers": config.tts.ignore_leading_numbers,
            }
        )
        tts_job_id = str(created.get("job_id") or "")
        if not tts_job_id:
            raise PipelineFailure("tts_invalid_response", "The TTS service did not return a job id.")
        state.setdefault("tts", {})["job_id"] = tts_job_id
        state.setdefault("tts", {})["voice"] = config.tts.voice
        state.setdefault("tts", {})["voice_name"] = voice.get("name")
        self._save(state)
        total_sentences = int(created.get("sentence_count") or 0)
        last_save = 0.0
        try:
            while True:
                self._check_cancel()
                job = self.clients.tts_get(tts_job_id)
                status = job.get("status")
                service_progress = int(job.get("progress") or 0)
                progress = 5 + int(service_progress * 0.83)
                message = str(job.get("message") or "Synthesizing speech.")
                if total_sentences:
                    message = (
                        f"{message} {job.get('completed_sentences', 0)}/{total_sentences} sentences."
                    )
                if status in {"queued", "processing"}:
                    now = time.monotonic()
                    if now - last_save >= TTS_POLL_SAVE_INTERVAL or progress > int(
                        _step(state, step_id).get("progress") or 0
                    ):
                        self._update(state, step_id, progress, message)
                        last_save = now
                    time.sleep(self.settings.tts_poll_interval_seconds)
                    continue
                if status == "cancelled":
                    raise JobCancelled()
                if status == "failed":
                    error = job.get("error") or {}
                    raise PipelineFailure(
                        str(error.get("code") or "tts_failed"),
                        str(error.get("message") or "Speech synthesis failed."),
                    )
                if status != "completed":
                    raise PipelineFailure(
                        "tts_unexpected_status",
                        f"The TTS service returned an unexpected status: {status}",
                    )
                timeline = self.clients.tts_timeline(tts_job_id)
                duration = float(
                    job.get("duration_seconds") or timeline.get("duration_seconds") or 0.0
                )
                state.setdefault("tts", {}).update(
                    {
                        "status": "completed",
                        "duration_seconds": round(duration, 3),
                        "sentences": int(job.get("completed_sentences") or total_sentences),
                        "audio_path": job.get("audio_path"),
                        "wav_path": job.get("wav_path"),
                        "format": job.get("format"),
                        "timeline": timeline.get("segments") or [],
                        "timeline_method": timeline.get("method"),
                    }
                )
                state.setdefault("stats", {})["speech_seconds"] = round(duration, 3)
                state.setdefault("stats", {})["speech_sentences"] = len(
                    timeline.get("segments") or []
                )
                self._complete(
                    state,
                    step_id,
                    f"Generated {duration:.1f}s of speech ({len(timeline.get('segments') or [])} sentences).",
                )
                return {
                    "job_id": tts_job_id,
                    "audio_path": job.get("audio_path"),
                    "wav_path": job.get("wav_path"),
                    "duration_seconds": duration,
                    "timeline": timeline.get("segments") or [],
                    "language": voice_language,
                    "voice": voice,
                    "summary_source": config.tts.text_source == "summary",
                }
        except JobCancelled:
            try:
                self.clients.tts_cancel(tts_job_id)
            except Exception:
                logger.info("Could not cancel TTS job %s", tts_job_id)
            raise

    # -- finalize ------------------------------------------------------

    def _finalize(
        self,
        state: dict[str, Any],
        config: JobCreateRequest,
        tts_result: dict[str, Any] | None,
    ) -> None:
        if not tts_result:
            return
        step_id = "finalize"
        self._begin(state, step_id, "Converting audio with FFmpeg.")
        source_path = str(tts_result.get("wav_path") or tts_result.get("audio_path") or "")
        if not source_path:
            raise PipelineFailure("tts_audio_missing", "The generated audio file is missing.")
        title = str(state.get("title") or "microtalk")
        self._update(state, step_id, 8, "Encoding the final audio format.")
        media = self.clients.media_finalize(
            source_path=source_path,
            output_format=config.tts.format,
            normalize_audio=config.tts.normalize_audio,
            title=title,
        )
        self._check_cancel()
        audio_artifact = {
            "kind": "audio",
            "media_id": media.get("id"),
            "filename": media.get("filename"),
            "format": media.get("format") or config.tts.format,
            "content_type": media.get("content_type"),
            "path": media.get("relative_path"),
            "storage": "media",
            "size_bytes": media.get("size_bytes"),
            "duration_seconds": media.get("duration_seconds"),
            "normalized": bool(config.tts.normalize_audio),
            "available": True,
        }
        state.setdefault("artifacts", {})["audio"] = audio_artifact
        wav_source = str(tts_result.get("wav_path") or "")
        if config.tts.format == "wav":
            state.setdefault("artifacts", {})["wav"] = {
                **audio_artifact,
                "kind": "wav",
                "format": "wav",
            }
        elif wav_source:
            state.setdefault("artifacts", {})["wav"] = {
                "kind": "wav",
                "filename": "master.wav",
                "format": "wav",
                "content_type": "audio/wav",
                "path": wav_source,
                "storage": "tts",
                "size_bytes": None,
                "duration_seconds": tts_result.get("duration_seconds"),
                "normalized": False,
                "available": True,
            }
        state.setdefault("stats", {})["audio_seconds"] = media.get("duration_seconds")
        state.setdefault("stats", {})["audio_format"] = media.get("format")

        timeline = tts_result.get("timeline") or []
        if not timeline:
            raise PipelineFailure(
                "tts_timeline_missing",
                "The speech timeline is missing, so synchronized subtitles cannot be created.",
            )
        self._update(state, step_id, 56, "Aligning subtitle timings to the final audio.")
        subtitles = self.clients.media_subtitles(
            segments=timeline,
            audio_path=str(media.get("relative_path") or ""),
            language=tts_result.get("language") or "en",
            title=title,
        )
        self._check_cancel()
        srt = str(subtitles.get("srt") or "")
        if not srt.strip():
            raise PipelineFailure(
                "subtitles_empty",
                "The media service returned empty subtitles.",
            )
        artifact = artifacts.write_subtitles(self.settings, self.job_id, srt)
        state.setdefault("artifacts", {})["subtitles"] = {
            **artifact,
            "timing_method": subtitles.get("timing_method"),
            "segments": subtitles.get("segment_count"),
            "media_id": subtitles.get("id"),
        }
        segments = subtitles.get("segments") or []
        captions = artifacts.write_captions(self.settings, self.job_id, segments)
        state.setdefault("artifacts", {})["captions"] = {
            **captions,
            "segments": subtitles.get("segment_count"),
        }
        cues_path = artifacts.artifact_file(self.settings, self.job_id, "cues.json")
        atomic_write_json(cues_path, {"segments": segments})
        for warning in subtitles.get("warnings") or []:
            _add_warning(state, str(warning))
        if subtitles.get("timing_method"):
            state.setdefault("stats", {})["subtitle_timing_method"] = subtitles.get(
                "timing_method"
            )
        state.setdefault("stats", {})["subtitle_segments"] = len(segments)
        self._complete(
            state,
            step_id,
            f"Audio ({media.get('duration_seconds')}s) and {len(segments)} subtitle cues ready.",
        )

    # -- archive -------------------------------------------------------

    def _archive(self, state: dict[str, Any]) -> None:
        step_id = "archive"
        self._begin(state, step_id, "Creating the ZIP download bundle.")
        try:
            bundle = artifacts.build_zip(self.settings, self.job_id, state)
            state.setdefault("artifacts", {})["zip"] = bundle
            self._complete(
                state,
                step_id,
                f"Bundle ready ({bundle['size_bytes'] // 1024:,} KB, {len(bundle['files'])} files).",
            )
        except Exception:
            logger.exception("ZIP bundle creation failed for %s", self.job_id)
            _add_warning(
                state,
                "The ZIP bundle could not be created. Individual files are still available.",
            )
            step = _step(state, step_id)
            step["status"] = "completed"
            step["progress"] = 100
            step["message"] = "ZIP bundle skipped; individual results are available."
            step["finished_at"] = time.time()
            state["progress"] = _overall_progress(state)
            self._save(state)


def run_job(job_id: str) -> None:
    """RQ entry point. Creates its own store and HTTP client."""
    from micro_common.logging_config import configure_logging

    from .store import JobStore

    configure_logging("gateway-worker")
    store = JobStore()
    clients = ServiceClients(settings)
    try:
        PipelineRunner(job_id, store=store, clients=clients).run()
    finally:
        clients.close()
