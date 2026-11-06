"""Gateway configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from micro_common.config import env_bool, env_float, env_int


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("GATEWAY_DATA_DIR", "/data/gateway")
    database_path: str = os.getenv("GATEWAY_DATABASE_PATH", "")
    pdf_data_dir: str = os.getenv("PDF_DATA_DIR", "/data/pdf")
    tts_data_dir: str = os.getenv("TTS_DATA_DIR", "/data/tts")
    media_data_dir: str = os.getenv("MEDIA_DATA_DIR", "/data/media")

    pdf_service_url: str = os.getenv("PDF_SERVICE_URL", "http://pdf-service:8001")
    translation_service_url: str = os.getenv("TRANSLATION_SERVICE_URL", "http://translation-service:8002")
    tts_service_url: str = os.getenv("TTS_SERVICE_URL", "http://tts-service:8003")
    summary_service_url: str = os.getenv("SUMMARY_SERVICE_URL", "http://summary-service:8004")
    media_service_url: str = os.getenv("MEDIA_SERVICE_URL", "http://media-service:8005")

    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    queue_name: str = os.getenv("QUEUE_NAME", "microtalk")
    worker_concurrency: int = env_int("WORKER_CONCURRENCY", 1, minimum=1)
    job_timeout_seconds: int = env_int("JOB_TIMEOUT_SECONDS", 21600, minimum=300)
    job_retention_hours: int = env_int("GATEWAY_RETENTION_HOURS", 168, minimum=1)
    cleanup_interval_seconds: int = env_int("CLEANUP_INTERVAL_SECONDS", 1800, minimum=60)
    cleanup_enabled: bool = env_bool("CLEANUP_ENABLED", True)
    max_active_jobs: int = env_int("GATEWAY_MAX_ACTIVE_JOBS", 12, minimum=1)

    max_upload_mb: int = env_int("MAX_UPLOAD_MB", 100, minimum=1)
    max_text_chars: int = env_int("GATEWAY_MAX_TEXT_CHARS", 500_000, minimum=1000)
    minimum_free_mb: int = env_int("MINIMUM_FREE_DISK_MB", 500, minimum=1)

    auth_token: str = os.getenv("GATEWAY_AUTH_TOKEN", "").strip()
    cors_origins: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
    )
    rate_limit_enabled: bool = env_bool("RATE_LIMIT_ENABLED", True)
    rate_limit_per_minute: int = env_int("RATE_LIMIT_PER_MINUTE", 180, minimum=10)
    upload_rate_limit_per_minute: int = env_int("UPLOAD_RATE_LIMIT_PER_MINUTE", 30, minimum=1)
    job_rate_limit_per_minute: int = env_int("JOB_RATE_LIMIT_PER_MINUTE", 12, minimum=1)

    request_timeout_seconds: int = env_int("SERVICE_REQUEST_TIMEOUT_SECONDS", 1800, minimum=30)
    tts_poll_interval_seconds: float = env_float("TTS_POLL_INTERVAL_SECONDS", 1.5, minimum=0.25)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def resolved_database_path(self) -> str:
        if self.database_path:
            return self.database_path
        return str(Path(self.data_dir) / "microtalk.sqlite3")

    @property
    def jobs_dir(self) -> str:
        return str(Path(self.data_dir) / "jobs")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
