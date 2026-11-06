FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    OMP_NUM_THREADS=4 \
    TOKENIZERS_PARALLELISM=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/models/.hf-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/summary/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared/python/micro_common /app/micro_common
COPY services/summary/app /app/app
COPY shared/docker/entrypoint.sh /usr/local/bin/microtalk-entrypoint

RUN groupadd --gid 10001 microtalk \
    && useradd --uid 10001 --gid 10001 --create-home --no-log-init microtalk \
    && mkdir -p /models/summary /models/.hf-cache \
    && printf '#!/bin/sh\nexec python -m app.download_models "$@"\n' > /usr/local/bin/download-models \
    && chmod 0755 /usr/local/bin/download-models /usr/local/bin/microtalk-entrypoint \
    && chown -R microtalk:microtalk /app /models

# Named volumes can arrive root-owned. The entrypoint fixes the writable mounts
# and then drops to UID/GID 10001 before starting the service or downloads.
ENV MICROTALK_WRITABLE_DIRS="/models/summary /models/.hf-cache"
ENTRYPOINT ["/usr/local/bin/microtalk-entrypoint"]
EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8004/health', timeout=5)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "1", "--no-access-log"]
