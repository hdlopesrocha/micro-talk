"""Configuration for the local Piper TTS service."""

from __future__ import annotations

import os
from dataclasses import dataclass

from micro_common.config import env_bool, env_float, env_int


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("TTS_DATA_DIR", "/data/tts")
    voices_dir: str = os.getenv("PIPER_VOICES_DIR", "/models/piper")
    default_voice: str = os.getenv("DEFAULT_TTS_VOICE", "pt-PT-tugao")
    default_language: str = os.getenv("DEFAULT_TTS_LANGUAGE", "pt-PT")
    max_text_chars: int = env_int("TTS_MAX_TEXT_CHARS", 500_000, minimum=100)
    max_sentences: int = env_int("TTS_MAX_SENTENCES", 5000, minimum=1)
    max_segment_chars: int = env_int("TTS_MAX_SEGMENT_CHARS", 400, minimum=80)
    phrase_max_chars: int = env_int("TTS_PHRASE_MAX_CHARS", 2000, minimum=80)
    gap_ms: int = env_int("TTS_SENTENCE_GAP_MS", 120, minimum=0)
    max_queue: int = env_int("TTS_MAX_QUEUE", 20, minimum=1)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)
    retention_hours: int = env_int("TTS_RETENTION_HOURS", 72, minimum=1)
    cleanup_interval_seconds: int = env_int("CLEANUP_INTERVAL_SECONDS", 3600, minimum=60)
    cleanup_enabled: bool = env_bool("CLEANUP_ENABLED", True)
    ffmpeg_timeout_seconds: int = env_int("FFMPEG_TIMEOUT_SECONDS", 900, minimum=30)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    default_speed: float = env_float("DEFAULT_TTS_SPEED", 1.0, minimum=0.5)
    enable_wav: bool = env_bool("TTS_ENABLE_WAV", True)


settings = Settings()
