"""RQ worker process for MicroTalk workflows."""

from __future__ import annotations

import logging

import redis
from rq import Worker
from micro_common.logging_config import configure_logging

from .config import settings

configure_logging("gateway-worker")
logger = logging.getLogger(__name__)


def main() -> None:
    connection = redis.Redis.from_url(settings.redis_url, decode_responses=False)
    worker = Worker([settings.queue_name], connection=connection)
    logger.info(
        "Starting RQ worker for queue '%s' (concurrency is controlled by the container count)",
        settings.queue_name,
    )
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
