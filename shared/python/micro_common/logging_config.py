"""Compact logging setup that keeps tracebacks in container logs only."""

from __future__ import annotations

import logging
import os


def configure_logging(service_name: str) -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=f"%(asctime)s %(levelname)s {service_name} %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
