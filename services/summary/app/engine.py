"""Hierarchical local summarization with compact T5 models."""

from __future__ import annotations

import gc
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import torch
from micro_common.errors import ServiceError
from micro_common.text import (
    clean_text,
    sentence_split,
    split_long_piece,
    split_paragraphs,
    word_count,
)
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from .config import Settings
from .languages import SummaryLanguages

logger = logging.getLogger(__name__)


@dataclass
class LoadedSummaryModel:
    language: str
    model: Any
    tokenizer: Any
    loaded_at: float
    last_used: float


class SummarizationEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._current: LoadedSummaryModel | None = None
        torch.set_num_threads(settings.cpu_threads)
        try:
            torch.set_num_interop_threads(max(1, min(settings.cpu_threads, 4)))
        except RuntimeError:
            pass

    def model_dir(self, language: str) -> Path:
        return Path(self.settings.model_directories[language])

    def model_available(self, language: str) -> bool:
        folder = self.model_dir(language)
        if not folder.is_dir():
            return False
        has_config = (folder / "config.json").is_file()
        has_weights = any(
            (folder / name).is_file()
            for name in (
                "model.safetensors",
                "pytorch_model.bin",
                "model.safetensors.index.json",
                "pytorch_model.bin.index.json",
            )
        )
        has_tokenizer = any(
            (folder / name).is_file()
            for name in ("tokenizer.json", "spiece.model", "tokenizer_config.json")
        )
        return has_config and has_weights and has_tokenizer

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "loaded_language": self._current.language if self._current else None,
                "available": {language: self.model_available(language) for language in ("en", "pt")},
                "model_ids": {"en": self.settings.model_en_repo, "pt": self.settings.model_pt_repo},
                "device": "cpu",
                "cpu_threads": self.settings.cpu_threads,
                "max_input_tokens": self.settings.max_input_tokens,
            }

    def _load(self, language: str) -> LoadedSummaryModel:
        if self._current and self._current.language == language:
            self._current.last_used = time.time()
            return self._current
        if self._current:
            self._current.model = None
            self._current.tokenizer = None
            self._current = None
            gc.collect()
        folder = self.model_dir(language)
        repo = self.settings.model_en_repo if language == "en" else self.settings.model_pt_repo
        if not self.model_available(language):
            raise ServiceError(
                "summary_model_missing",
                "The required summarization model has not been installed.",
                status_code=503,
                details={
                    "language": language,
                    "model": repo,
                    "install_command": "docker compose run --rm summary-service download-models",
                },
            )
        logger.info("Loading summarization model %s from %s", language, folder)
        started = time.time()
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(folder), local_files_only=True)
            model = AutoModelForSeq2SeqLM.from_pretrained(
                str(folder), local_files_only=True
            )
            model.to("cpu")
            model.eval()
        except Exception as exc:
            logger.exception("Failed to load summary model %s", language)
            raise ServiceError(
                "summary_model_failed",
                "The summarization model could not be loaded. Reinstall the local models.",
                status_code=503,
            ) from exc
        loaded = LoadedSummaryModel(
            language=language,
            model=model,
            tokenizer=tokenizer,
            loaded_at=started,
            last_used=time.time(),
        )
        self._current = loaded
        logger.info("Loaded summary model %s in %.1fs", language, time.time() - started)
        return loaded

    def _token_length(self, tokenizer: Any, text: str) -> int:
        return len(tokenizer.encode(text, add_special_tokens=False))

    def _split_sentence_to_fit(self, tokenizer: Any, sentence: str, max_tokens: int) -> list[str]:
        pieces: list[str] = []
        words = sentence.split()
        current: list[str] = []
        for word in words:
            candidate = (" ".join(current + [word])).strip()
            if current and self._token_length(tokenizer, candidate) > max_tokens:
                pieces.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            pieces.append(" ".join(current))
        # A single gigantic token/word can still exceed the limit. Cut by
        # characters so it can be handled without silently dropping data.
        safe: list[str] = []
        for piece in pieces:
            if self._token_length(tokenizer, piece) <= max_tokens:
                safe.append(piece)
                continue
            step = max(200, int(len(piece) * max_tokens / max(1, self._token_length(tokenizer, piece))) // 2)
            for start in range(0, len(piece), step):
                safe.append(piece[start : start + step])
        return safe

    def _chunk_text(self, tokenizer: Any, text: str) -> list[str]:
        max_tokens = self.settings.max_input_tokens - 12
        sentences: list[str] = []
        for paragraph in split_paragraphs(text):
            for sentence in sentence_split(paragraph):
                for piece in split_long_piece(sentence, max(400, self.settings.max_input_tokens * 3)):
                    if self._token_length(tokenizer, piece) > max_tokens:
                        sentences.extend(self._split_sentence_to_fit(tokenizer, piece, max_tokens))
                    else:
                        sentences.append(piece)
        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for sentence in sentences:
            tokens = self._token_length(tokenizer, sentence) + 1
            if current and current_tokens + tokens > max_tokens:
                chunks.append(" ".join(current))
                current = [sentence]
                current_tokens = tokens
            else:
                current.append(sentence)
                current_tokens += tokens
        if current:
            chunks.append(" ".join(current))
        return [chunk for chunk in chunks if chunk.strip()]

    def _generate(self, loaded: LoadedSummaryModel, text: str, max_new_tokens: int) -> str:
        prefix = self.settings.prompt_en if loaded.language == "en" else self.settings.prompt_pt
        prompt = prefix + text.strip()
        encoded = loaded.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.settings.max_input_tokens,
        )
        if int(encoded["input_ids"].shape[-1]) < 4:
            return ""
        try:
            with torch.inference_mode():
                generated = loaded.model.generate(
                    **encoded,
                    max_new_tokens=max_new_tokens,
                    num_beams=self.settings.num_beams,
                    early_stopping=True,
                    no_repeat_ngram_size=3,
                    length_penalty=1.0,
                )
        except Exception as exc:
            logger.exception("Summary generation failed")
            raise ServiceError(
                "summary_generation_failed",
                "The local summarization model failed to generate a summary.",
                status_code=500,
            ) from exc
        result = loaded.tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        result = clean_text(result)
        result = re.sub(r"^(?:summari[sz]e|sumarize|resuma|resumo)\s*:\s*", "", result, flags=re.I)
        return result.strip()

    def _group_summaries(self, tokenizer: Any, summaries: list[str]) -> list[str]:
        max_tokens = self.settings.max_input_tokens - 20
        groups: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for summary in summaries:
            tokens = self._token_length(tokenizer, summary) + 1
            if current and current_tokens + tokens > max_tokens:
                groups.append("\n\n".join(current))
                current = [summary]
                current_tokens = tokens
            else:
                current.append(summary)
                current_tokens += tokens
        if current:
            groups.append("\n\n".join(current))
        return groups

    def _translate_summary(self, summary: str, languages: SummaryLanguages) -> str:
        target = languages.target_variant
        try:
            with httpx.Client(timeout=self.settings.translation_timeout_seconds) as client:
                response = client.post(
                    f"{self.settings.translation_service_url.rstrip('/')}/api/v1/translate",
                    json={
                        "text": summary,
                        "source_language": languages.source,
                        "target_language": target,
                    },
                )
        except httpx.TimeoutException as exc:
            raise ServiceError(
                "summary_translation_timeout",
                "The summary was generated but could not be translated in time.",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Translation service unavailable: %s", exc)
            raise ServiceError(
                "translation_service_unavailable",
                "The translation service is unavailable, so the summary could not be produced in the requested language.",
                status_code=502,
            ) from exc
        if response.status_code >= 400:
            detail = "The translation service rejected the summary translation request."
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message", detail)
            except Exception:
                pass
            raise ServiceError(
                "summary_translation_failed",
                detail,
                status_code=502,
            )
        payload = response.json()
        translated = clean_text(payload.get("translated_text", ""))
        if not translated:
            raise ServiceError(
                "summary_translation_failed",
                "The translation service returned an empty summary.",
                status_code=502,
            )
        return translated

    def summarize(
        self,
        text: str,
        languages: SummaryLanguages,
        length: str,
    ) -> dict[str, Any]:
        started = time.time()
        if not text.strip():
            raise ServiceError("empty_text", "There is no text to summarize.", status_code=422)
        if len(text) > self.settings.max_input_chars:
            raise ServiceError(
                "text_too_large",
                f"The text exceeds the {self.settings.max_input_chars}-character limit.",
                status_code=413,
            )
        budget = self.settings.token_budgets[length]
        with self._lock:
            loaded = self._load(languages.source)
            chunks = self._chunk_text(loaded.tokenizer, text)
            if not chunks:
                raise ServiceError("empty_text", "There is no text to summarize.", status_code=422)
            if len(chunks) > self.settings.max_chunks:
                raise ServiceError(
                    "document_too_large",
                    "The document requires too many model passes. Select fewer pages or a shorter text.",
                    status_code=413,
                    details={"chunks": len(chunks), "max_chunks": self.settings.max_chunks},
                )
            warnings: list[str] = []
            chunk_count = len(chunks)
            if len(chunks) == 1:
                hierarchy_levels = 0
                summary = self._generate(loaded, chunks[0], budget)
            else:
                map_budget = max(80, min(budget, budget // 3 + 60))
                current = [self._generate(loaded, chunk, map_budget) for chunk in chunks]
                current = [item for item in current if item]
                if not current:
                    raise ServiceError(
                        "summary_generation_failed",
                        "The local summarization model returned no usable summaries.",
                        status_code=500,
                    )
                hierarchy_levels = 1
                while len(current) > 1:
                    combined_tokens = sum(self._token_length(loaded.tokenizer, item) + 1 for item in current)
                    if combined_tokens <= self.settings.max_input_tokens - 32:
                        break
                    groups = self._group_summaries(loaded.tokenizer, current)
                    if len(groups) >= len(current):
                        # Prevent unbounded loops if the model returns text longer
                        # than its context. Hard split the summaries for the next
                        # reduction rather than truncating the whole document.
                        groups = self._group_summaries(
                            loaded.tokenizer,
                            ["\n\n".join(current[: len(current) // 2]), "\n\n".join(current[len(current) // 2 :])],
                        )
                    next_current = [self._generate(loaded, group, map_budget) for group in groups]
                    next_current = [item for item in next_current if item]
                    if not next_current:
                        raise ServiceError(
                            "summary_generation_failed",
                            "The local model returned no usable intermediate summaries.",
                            status_code=500,
                        )
                    current = next_current
                    hierarchy_levels += 1
                    if hierarchy_levels > 12:
                        raise ServiceError(
                            "summary_generation_failed",
                            "The document could not be reduced to a summary.",
                            status_code=500,
                        )
                if len(current) == 1 and hierarchy_levels > 1:
                    summary = current[0]
                    # A final pass shapes the length for the requested setting.
                    summary = self._generate(loaded, current[0], budget)
                else:
                    summary = self._generate(loaded, "\n\n".join(current), budget)
                    hierarchy_levels += 1
            loaded.last_used = time.time()
        if not summary.strip():
            raise ServiceError(
                "summary_generation_failed",
                "The local model returned an empty summary. Try a different page range or less text.",
                status_code=500,
            )
        translated = False
        final_summary = summary
        if languages.source != languages.target:
            final_summary = self._translate_summary(summary, languages)
            translated = True
            warnings.append(
                "The summary was generated in the source language and then machine-translated to the requested language."
            )
        source_numbers = set(re.findall(r"\b\d[\d.,%:/-]*\b", text))
        summary_numbers = set(re.findall(r"\b\d[\d.,%:/-]*\b", final_summary))
        missing = source_numbers - summary_numbers
        if source_numbers and len(missing) > max(1, len(source_numbers) // 2):
            warnings.append(
                "Some numerical values from the source are not present in the compact summary. Review the source for critical figures."
            )
        warnings.append(
            "Summaries are generated by a compact local model. Verify names, dates and figures before relying on them."
        )
        duration = round(time.time() - started, 2)
        logger.info(
            "Summarized %s words (%s chunks, %s levels) in %.1fs",
            word_count(text),
            chunk_count,
            hierarchy_levels,
            duration,
        )
        return {
            "summary": final_summary.strip() + "\n",
            "source_language": languages.source,
            "language": languages.target_variant,
            "length": length,
            "translated": translated,
            "model": self.settings.model_en_repo if languages.source == "en" else self.settings.model_pt_repo,
            "input_words": word_count(text),
            "output_words": word_count(final_summary),
            "chunk_count": chunk_count,
            "hierarchy_levels": hierarchy_levels,
            "duration_seconds": duration,
            "warnings": warnings,
        }

    def maybe_unload_idle(self) -> None:
        if not self.settings.idle_unload_seconds:
            return
        with self._lock:
            if self._current and time.time() - self._current.last_used > self.settings.idle_unload_seconds:
                logger.info("Unloading idle summarization model %s", self._current.language)
                self._current.model = None
                self._current.tokenizer = None
                self._current = None
                gc.collect()

    def unload(self) -> None:
        with self._lock:
            if self._current:
                self._current = None
                gc.collect()
