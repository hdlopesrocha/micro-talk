FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY services/gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared/python/micro_common /app/micro_common
COPY services/gateway/app /app/app
COPY shared/docker/entrypoint.sh /usr/local/bin/microtalk-entrypoint

RUN groupadd --gid 10001 microtalk \
    && useradd --uid 10001 --gid 10001 --create-home --no-log-init microtalk \
    && mkdir -p /data/gateway /data/pdf /data/tts /data/media \
    && chown -R microtalk:microtalk /app /data/gateway \
    && chmod -R a+rX /data/pdf /data/tts /data/media \
    && chmod 0755 /usr/local/bin/microtalk-entrypoint

# Named volumes can arrive root-owned. The entrypoint fixes the writable mount
# and then drops to UID/GID 10001 before starting the service.
ENV MICROTALK_WRITABLE_DIRS="/data/gateway"
ENTRYPOINT ["/usr/local/bin/microtalk-entrypoint"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
