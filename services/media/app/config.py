"""Media service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from micro_common.config import env_bool, env_float, env_int


@dataclass(frozen=True)
class Settings:
    media_dir: str = os.getenv("MEDIA_STORAGE_DIR", "/data/media")
    tts_source_dir: str = os.getenv("TTS_SOURCE_DIR", "/data/tts")
    tmp_dir: str = os.getenv("MEDIA_TMP_DIR", "/data/media/tmp")
    max_upload_mb: int = env_int("MEDIA_MAX_UPLOAD_MB", 500, minimum=1)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)
    ffmpeg_timeout_seconds: int = env_int("FFMPEG_TIMEOUT_SECONDS", 900, minimum=30)
    ffprobe_timeout_seconds: int = env_int("FFPROBE_TIMEOUT_SECONDS", 60, minimum=5)
    default_mp3_bitrate: str = os.getenv("MEDIA_MP3_BITRATE", "128k")
    subtitle_words_per_minute: float = env_float("SUBTITLE_WORDS_PER_MINUTE", 150.0, minimum=60.0)
    retention_hours: int = env_int("MEDIA_RETENTION_HOURS", 168, minimum=1)
    cleanup_interval_seconds: int = env_int("CLEANUP_INTERVAL_SECONDS", 3600, minimum=60)
    cleanup_enabled: bool = env_bool("CLEANUP_ENABLED", True)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
