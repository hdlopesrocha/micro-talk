"""Configuration for the local translation service."""

from __future__ import annotations

import os
from dataclasses import dataclass

from micro_common.config import env_float, env_int


@dataclass(frozen=True)
class Settings:
    models_dir: str = os.getenv("TRANSLATION_MODELS_DIR", "/models/translation")
    model_pt_en_repo: str = os.getenv("TRANSLATION_MODEL_PT_EN", "Helsinki-NLP/opus-mt-roa-en")
    model_en_pt_repo: str = os.getenv("TRANSLATION_MODEL_EN_PT", "Helsinki-NLP/opus-mt-tc-big-en-pt")
    model_en_fr_repo: str = os.getenv("TRANSLATION_MODEL_EN_FR", "Helsinki-NLP/opus-mt-en-fr")
    model_fr_en_repo: str = os.getenv("TRANSLATION_MODEL_FR_EN", "Helsinki-NLP/opus-mt-fr-en")
    default_target_language: str = os.getenv("DEFAULT_TARGET_LANGUAGE", "en")
    default_source_language: str = os.getenv("DEFAULT_SOURCE_LANGUAGE", "pt")
    idle_unload_seconds: int = env_int("MODEL_IDLE_UNLOAD_SECONDS", 900, minimum=0)
    cpu_threads: int = env_int("CPU_THREADS", 4, minimum=1)
    max_input_chars: int = env_int("MAX_TRANSLATION_CHARS", 500_000, minimum=100)
    max_chunk_chars: int = env_int("TRANSLATION_CHUNK_CHARS", 850, minimum=200)
    num_beams: int = env_int("TRANSLATION_NUM_BEAMS", 2, minimum=1)
    max_new_tokens: int = env_int("TRANSLATION_MAX_TOKENS", 512, minimum=64)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def model_repos(self) -> dict[str, str]:
        return {
            "pt-en": self.model_pt_en_repo,
            "en-pt": self.model_en_pt_repo,
            "en-fr": self.model_en_fr_repo,
            "fr-en": self.model_fr_en_repo,
        }

    @property
    def pair_directories(self) -> dict[str, str]:
        return {pair: f"{self.models_dir}/{pair}" for pair in self.model_repos}


settings = Settings()
