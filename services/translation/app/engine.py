"""Lazy local Marian/OPUS-MT model manager and translation engine."""

from __future__ import annotations

import gc
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from micro_common.errors import ServiceError
from micro_common.text import chunk_paragraph
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

from .config import Settings
from .languages import ResolvedLanguages

logger = logging.getLogger(__name__)


@dataclass
class LoadedModel:
    pair: str
    model: Any
    tokenizer: Any
    loaded_at: float
    last_used: float


class TranslationEngine:
    """A thread-safe, deliberately single-model cache.

    Marian models for both directions are never loaded at the same time. This
    keeps peak memory well below the 16 GB target. An optional idle unloader is
    driven by the service lifespan.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._current: LoadedModel | None = None
        torch.set_num_threads(settings.cpu_threads)
        try:
            torch.set_num_interop_threads(max(1, min(settings.cpu_threads, 4)))
        except RuntimeError:
            pass

    def _pair_name(self, languages: ResolvedLanguages) -> str:
        if languages.source == languages.target:
            return ""
        return f"{languages.source}-{languages.target}"

    def _model_dir(self, pair: str) -> Path:
        return Path(self.settings.models_dir) / pair

    def model_available(self, pair: str) -> bool:
        folder = self._model_dir(pair)
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
            for name in ("tokenizer_config.json", "source.spm", "vocab.json", "spiece.model")
        )
        return has_config and has_weights and has_tokenizer

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "loaded_pair": self._current.pair if self._current else None,
                "loaded_since": self._current.loaded_at if self._current else None,
                "supported_pairs": {
                    pair: self.model_available(pair) for pair in ("pt-en", "en-pt")
                },
                "model_ids": {
                    "pt-en": self.settings.model_pt_en_repo,
                    "en-pt": self.settings.model_en_pt_repo,
                },
                "cpu_threads": self.settings.cpu_threads,
                "device": "cpu",
            }

    def _load(self, pair: str) -> LoadedModel:
        if self._current and self._current.pair == pair:
            self._current.last_used = time.time()
            return self._current
        if self._current:
            logger.info("Unloading translation model %s before loading %s", self._current.pair, pair)
            self._current.model = None
            self._current.tokenizer = None
            self._current = None
            gc.collect()
            if hasattr(torch, "mps"):
                pass
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        folder = self._model_dir(pair)
        if not self.model_available(pair):
            repo = self.settings.model_pt_en_repo if pair == "pt-en" else self.settings.model_en_pt_repo
            raise ServiceError(
                "translation_model_missing",
                "The required translation model has not been installed.",
                status_code=503,
                details={
                    "pair": pair,
                    "model": repo,
                    "install_command": "docker compose run --rm translation-service download-models",
                },
            )
        logger.info("Loading translation model %s from %s", pair, folder)
        started = time.time()
        try:
            tokenizer = AutoTokenizer.from_pretrained(str(folder), local_files_only=True)
            model = AutoModelForSeq2SeqLM.from_pretrained(
                str(folder),
                local_files_only=True,
            )
            model.to("cpu")
            model.eval()
        except Exception as exc:
            logger.exception("Failed to load translation model %s", pair)
            raise ServiceError(
                "translation_model_failed",
                "The translation model could not be loaded. Reinstall the local models.",
                status_code=503,
            ) from exc
        loaded = LoadedModel(
            pair=pair,
            model=model,
            tokenizer=tokenizer,
            loaded_at=started,
            last_used=time.time(),
        )
        self._current = loaded
        logger.info("Loaded translation model %s in %.1fs", pair, time.time() - started)
        return loaded

    def _prefix(self, languages: ResolvedLanguages) -> str:
        if languages.source == "en" and languages.target == "pt":
            # The model is multilingual on the target side. >>por<< selects
            # Portuguese as used in Portugal; >>pob<< selects Brazilian usage.
            return ">>pob<< " if languages.target_variant == "pt-BR" else ">>por<< "
        return ""

    def _translate_paragraph(
        self, paragraph: str, loaded: LoadedModel, languages: ResolvedLanguages
    ) -> str:
        if not paragraph.strip():
            return ""
        chunks = chunk_paragraph(paragraph, max_chars=self.settings.max_chunk_chars, language=languages.source)
        translated: list[str] = []
        prefix = self._prefix(languages)
        for chunk in chunks:
            encoded = loaded.tokenizer(
                [prefix + chunk],
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=False,
            )
            with torch.inference_mode():
                generated = loaded.model.generate(
                    **encoded,
                    max_new_tokens=self.settings.max_new_tokens,
                    num_beams=self.settings.num_beams,
                    early_stopping=True,
                    no_repeat_ngram_size=3,
                )
            result = loaded.tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
            if result:
                translated.append(result)
        return " ".join(translated).strip()

    def translate_segments(
        self,
        segments: list[dict[str, Any]],
        languages: ResolvedLanguages,
    ) -> list[dict[str, Any]]:
        if languages.source == languages.target:
            return [
                {
                    "id": segment.get("id"),
                    "page": segment.get("page"),
                    "text": segment["text"],
                }
                for segment in segments
            ]
        pair = self._pair_name(languages)
        with self._lock:
            loaded = self._load(pair)
            output: list[dict[str, Any]] = []
            for segment in segments:
                if not segment["text"].strip():
                    translated = ""
                else:
                    translated = self._translate_paragraph(segment["text"], loaded, languages)
                output.append(
                    {
                        "id": segment.get("id"),
                        "page": segment.get("page"),
                        "text": translated,
                    }
                )
            loaded.last_used = time.time()
            return output

    def maybe_unload_idle(self) -> None:
        if not self.settings.idle_unload_seconds:
            return
        with self._lock:
            if self._current and time.time() - self._current.last_used > self.settings.idle_unload_seconds:
                logger.info("Unloading idle translation model %s", self._current.pair)
                self._current.model = None
                self._current.tokenizer = None
                self._current = None
                gc.collect()

    def unload(self) -> None:
        with self._lock:
            if self._current:
                self._current = None
                gc.collect()
