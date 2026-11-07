"""Internal HTTP clients for the MicroTalk microservices."""

from __future__ import annotations

import logging
import time
from typing import Any, BinaryIO

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class ServiceCallError(Exception):
    def __init__(
        self,
        service: str,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _extract_error(service: str, response: httpx.Response) -> ServiceCallError:
    message = f"The {service} service could not complete the request."
    code = "service_error"
    details: dict[str, Any] = {}
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
            details = error.get("details") if isinstance(error.get("details"), dict) else {}
    except Exception:
        pass
    status = response.status_code
    if status in {401, 403}:
        public_status = 502
    elif status == 404:
        public_status = 404
    elif status == 409:
        public_status = 409
    elif status == 413:
        public_status = 413
    elif status == 422:
        public_status = 422
    elif status == 429:
        public_status = 429
    elif status == 503:
        public_status = 503
    elif status >= 500:
        public_status = 502
    else:
        public_status = status if 400 <= status < 500 else 502
    return ServiceCallError(service, code, message, status_code=public_status, details=details)


class ServiceClients:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        timeout = httpx.Timeout(settings.request_timeout_seconds, connect=10.0)
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)
        self.pdf = settings.pdf_service_url.rstrip("/")
        self.translation = settings.translation_service_url.rstrip("/")
        self.tts = settings.tts_service_url.rstrip("/")
        self.summary = settings.summary_service_url.rstrip("/")
        self.media = settings.media_service_url.rstrip("/")

    def close(self) -> None:
        self._http.close()

    # -- low level -----------------------------------------------------

    def _request(
        self,
        service: str,
        method: str,
        url: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 0,
    ) -> httpx.Response:
        last_error: Exception | None = None
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            try:
                response = self._http.request(
                    method, url, json=json, params=params, headers=headers
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise ServiceCallError(
                    service,
                    f"{service}_timeout",
                    f"The {service} service did not respond in time.",
                    status_code=504,
                ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise ServiceCallError(
                    service,
                    f"{service}_unavailable",
                    f"The {service} service is unavailable.",
                    status_code=503,
                ) from exc
            if response.status_code >= 400:
                raise _extract_error(service, response)
            return response
        raise ServiceCallError(
            service,
            f"{service}_unavailable",
            f"The {service} service is unavailable.",
            status_code=503,
        ) from last_error

    def _json(
        self,
        service: str,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = self._request(service, method, url, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ServiceCallError(
                service,
                f"{service}_invalid_response",
                f"The {service} service returned an invalid response.",
                status_code=502,
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceCallError(
                service,
                f"{service}_invalid_response",
                f"The {service} service returned an unexpected response.",
                status_code=502,
            )
        return payload

    def _health(self, service: str, base: str) -> dict[str, Any]:
        try:
            return self._json(service, "GET", f"{base}/health")
        except ServiceCallError as exc:
            return {
                "status": "unavailable",
                "service": service,
                "error": {"code": exc.code, "message": exc.message},
            }

    # -- PDF -----------------------------------------------------------

    def upload_pdf(self, filename: str, fileobj: BinaryIO) -> dict[str, Any]:
        url = f"{self.pdf}/api/v1/pdf/documents"
        try:
            response = self._http.post(
                url,
                files={"file": (filename, fileobj, "application/pdf")},
            )
        except httpx.TimeoutException as exc:
            raise ServiceCallError(
                "pdf", "pdf_timeout", "Uploading the PDF timed out.", status_code=504
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceCallError(
                "pdf", "pdf_unavailable", "The PDF service is unavailable.", status_code=503
            ) from exc
        if response.status_code >= 400:
            raise _extract_error("pdf", response)
        return response.json()

    def get_document(self, document_id: str) -> dict[str, Any]:
        return self._json(
            "pdf",
            "GET",
            f"{self.pdf}/api/v1/pdf/documents/{document_id}",
            retries=1,
        )

    def delete_document(self, document_id: str) -> None:
        self._request(
            "pdf", "DELETE", f"{self.pdf}/api/v1/pdf/documents/{document_id}", retries=0
        )

    def document_preview(self, document_id: str, page: int, width: int) -> tuple[bytes, str]:
        response = self._request(
            "pdf",
            "GET",
            f"{self.pdf}/api/v1/pdf/documents/{document_id}/pages/{page}/preview.png",
            params={"width": width},
            retries=1,
        )
        return response.content, response.headers.get("content-type", "image/png")

    def extract(
        self,
        document_id: str,
        *,
        selected_pages: list[int],
        ocr_enabled: bool,
        ignore_notes: bool = False,
        ignore_figure_descriptions: bool = False,
    ) -> dict[str, Any]:
        return self._json(
            "pdf",
            "POST",
            f"{self.pdf}/api/v1/pdf/extract",
            json={
                "document_id": document_id,
                "selected_pages": selected_pages,
                "ocr_enabled": ocr_enabled,
                "ignore_notes": ignore_notes,
                "ignore_figure_descriptions": ignore_figure_descriptions,
            },
        )

    # -- translation ---------------------------------------------------

    def translate(
        self,
        *,
        text: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        source_language: str = "auto",
        target_language: str = "en",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_language": source_language,
            "target_language": target_language,
        }
        if segments is not None:
            payload["segments"] = segments
        else:
            payload["text"] = text or ""
        return self._json(
            "translation",
            "POST",
            f"{self.translation}/api/v1/translate",
            json=payload,
            retries=0,
        )

    def detect_language(self, text: str) -> dict[str, Any]:
        return self._json(
            "translation",
            "POST",
            f"{self.translation}/api/v1/detect",
            json={"text": text[:20_000]},
            retries=1,
        )

    # -- summary -------------------------------------------------------

    def summarize(
        self,
        text: str,
        *,
        language: str,
        source_language: str = "auto",
        length: str = "medium",
    ) -> dict[str, Any]:
        return self._json(
            "summary",
            "POST",
            f"{self.summary}/api/v1/summary",
            json={
                "text": text,
                "language": language,
                "source_language": source_language,
                "length": length,
            },
            retries=0,
        )

    # -- TTS -----------------------------------------------------------

    def tts_voices(self) -> dict[str, Any]:
        return self._json("tts", "GET", f"{self.tts}/api/v1/tts/voices", retries=1)

    def tts_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json(
            "tts", "POST", f"{self.tts}/api/v1/tts/generate", json=payload, retries=0
        )

    def tts_get(self, job_id: str) -> dict[str, Any]:
        return self._json(
            "tts", "GET", f"{self.tts}/api/v1/tts/jobs/{job_id}", retries=2
        )

    def tts_cancel(self, job_id: str) -> dict[str, Any]:
        return self._json(
            "tts", "DELETE", f"{self.tts}/api/v1/tts/jobs/{job_id}", retries=0
        )

    def tts_timeline(self, job_id: str) -> dict[str, Any]:
        return self._json(
            "tts", "GET", f"{self.tts}/api/v1/tts/jobs/{job_id}/timeline", retries=1
        )

    def tts_purge(self, job_id: str) -> None:
        try:
            self._request("tts", "DELETE", f"{self.tts}/api/v1/tts/jobs/{job_id}/purge")
        except ServiceCallError as exc:
            logger.info("Could not purge TTS job %s: %s", job_id, exc.message)

    # -- media ---------------------------------------------------------

    def media_finalize(
        self,
        *,
        source_path: str,
        output_format: str,
        normalize_audio: bool,
        title: str,
    ) -> dict[str, Any]:
        return self._json(
            "media",
            "POST",
            f"{self.media}/api/v1/media/finalize",
            json={
                "source_path": source_path,
                "format": output_format,
                "normalize_audio": normalize_audio,
                "title": title,
            },
            retries=0,
        )

    def media_subtitles(
        self,
        *,
        segments: list[dict[str, Any]],
        audio_path: str,
        language: str,
        title: str,
    ) -> dict[str, Any]:
        return self._json(
            "media",
            "POST",
            f"{self.media}/api/v1/media/subtitles",
            json={
                "segments": segments,
                "audio_path": audio_path,
                "language": language,
                "title": title,
            },
            retries=0,
        )

    def media_delete(self, media_id: str) -> None:
        try:
            self._request("media", "DELETE", f"{self.media}/api/v1/media/{media_id}")
        except ServiceCallError as exc:
            logger.info("Could not delete media %s: %s", media_id, exc.message)

    # -- aggregate -----------------------------------------------------

    def catalog(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        calls = {
            "translation_languages": (
                "translation",
                f"{self.translation}/api/v1/translation/languages",
            ),
            "translation_status": (
                "translation",
                f"{self.translation}/api/v1/translation/status",
            ),
            "summary_lengths": ("summary", f"{self.summary}/api/v1/summary/lengths"),
            "summary_status": ("summary", f"{self.summary}/api/v1/summary/status"),
            "tts": ("tts", f"{self.tts}/api/v1/tts/voices"),
            "pdf_health": ("pdf", f"{self.pdf}/health"),
            "media_health": ("media", f"{self.media}/health"),
            "translation_health": ("translation", f"{self.translation}/health"),
            "summary_health": ("summary", f"{self.summary}/health"),
            "tts_health": ("tts", f"{self.tts}/health"),
        }
        health = {
            "pdf": None,
            "translation": None,
            "summary": None,
            "tts": None,
            "media": None,
        }
        for key, (service, url) in calls.items():
            if key.endswith("_health"):
                health[service] = self._health(service, url.rsplit("/health", 1)[0])
                continue
            try:
                result[key] = self._json(service, "GET", url, retries=1)
            except ServiceCallError as exc:
                result[key] = {
                    "unavailable": True,
                    "error": {"code": exc.code, "message": exc.message},
                }
        result["health"] = health
        return result
