"""Small Redis-backed fixed-window rate limiter for expensive endpoints."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Request
from micro_common.errors import ServiceError

from .config import settings

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64] or "unknown"
    return (request.client.host if request.client else "unknown")[:64]


async def enforce_rate_limit(
    request: Request, bucket: str, limit: int | None = None
) -> None:
    if not settings.rate_limit_enabled:
        return
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    limit = limit or settings.rate_limit_per_minute
    window = int(time.time() // 60)
    key = f"microtalk:rate:{bucket}:{_client_ip(request)}:{window}"

    def consume() -> int:
        value = int(redis.incr(key))
        if value == 1:
            redis.expire(key, 70)
        return value

    try:
        count = await asyncio.to_thread(consume)
    except Exception:
        logger.warning("Rate limiter unavailable; allowing request", exc_info=True)
        return
    if count > limit:
        raise ServiceError(
            "rate_limited",
            "Too many requests. Wait a minute and try again.",
            status_code=429,
            details={"limit": limit, "window_seconds": 60},
        )
