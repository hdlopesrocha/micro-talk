"""PDF extraction service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from micro_common.config import env_bool, env_int


@dataclass(frozen=True)
class Settings:
    storage_dir: str = os.getenv("PDF_STORAGE_DIR", "/data/documents")
    tmp_dir: str = os.getenv("PDF_TMP_DIR", "/data/tmp")
    max_upload_mb: int = env_int("MAX_UPLOAD_MB", 100, minimum=1)
    max_pages: int = env_int("MAX_PDF_PAGES", 2000, minimum=1)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)
    ocr_enabled_default: bool = env_bool("OCR_ENABLED_DEFAULT", False)
    ocr_languages: str = os.getenv("OCR_LANGUAGES", "por+eng+fra")
    ocr_dpi: int = env_int("OCR_DPI", 200, minimum=100)
    ocr_timeout_seconds: int = env_int("OCR_TIMEOUT_SECONDS", 180, minimum=10)
    ocr_min_chars: int = env_int("OCR_MIN_CHARS", 40, minimum=1)
    retention_hours: int = env_int("PDF_RETENTION_HOURS", 168, minimum=1)
    cleanup_interval_seconds: int = env_int("CLEANUP_INTERVAL_SECONDS", 3600, minimum=60)
    cleanup_enabled: bool = env_bool("CLEANUP_ENABLED", True)
    cpu_threads: int = env_int("CPU_THREADS", 4, minimum=1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
