"""Configuration for the local summarization service."""

from __future__ import annotations

import os
from dataclasses import dataclass

from micro_common.config import env_int


@dataclass(frozen=True)
class Settings:
    models_dir: str = os.getenv("SUMMARY_MODELS_DIR", "/models/summary")
    model_en_repo: str = os.getenv(
        "SUMMARY_MODEL_EN", "farleyknight/cnn_dailymail-summarization-t5-small-2022-09-08"
    )
    model_pt_repo: str = os.getenv("SUMMARY_MODEL_PT", "rhaymison/t5-portuguese-small-summarization")
    model_en_revision: str = os.getenv("SUMMARY_MODEL_EN_REVISION", "main")
    model_pt_revision: str = os.getenv("SUMMARY_MODEL_PT_REVISION", "main")
    prompt_en: str = os.getenv("SUMMARY_PROMPT_EN", "summarize: ")
    prompt_pt: str = os.getenv("SUMMARY_PROMPT_PT", "sumarize: ")
    idle_unload_seconds: int = env_int("MODEL_IDLE_UNLOAD_SECONDS", 900, minimum=0)
    cpu_threads: int = env_int("CPU_THREADS", 4, minimum=1)
    max_input_chars: int = env_int("SUMMARY_MAX_INPUT_CHARS", 500_000, minimum=100)
    max_input_tokens: int = env_int("SUMMARY_MAX_INPUT_TOKENS", 480, minimum=128)
    max_chunks: int = env_int("SUMMARY_MAX_CHUNKS", 300, minimum=1)
    num_beams: int = env_int("SUMMARY_NUM_BEAMS", 3, minimum=1)
    short_tokens: int = env_int("SUMMARY_SHORT_TOKENS", 120, minimum=24)
    medium_tokens: int = env_int("SUMMARY_MEDIUM_TOKENS", 240, minimum=48)
    long_tokens: int = env_int("SUMMARY_LONG_TOKENS", 420, minimum=96)
    translation_service_url: str = os.getenv(
        "TRANSLATION_SERVICE_URL", "http://translation-service:8002"
    )
    translation_timeout_seconds: int = env_int("SUMMARY_TRANSLATION_TIMEOUT", 900, minimum=30)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def model_directories(self) -> dict[str, str]:
        return {"en": f"{self.models_dir}/en", "pt": f"{self.models_dir}/pt"}

    @property
    def token_budgets(self) -> dict[str, int]:
        return {
            "short": self.short_tokens,
            "medium": self.medium_tokens,
            "long": self.long_tokens,
        }


settings = Settings()
