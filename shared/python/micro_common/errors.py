"""Consistent public error shapes for all MicroTalk services."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("microtalk.errors")


class ServiceError(Exception):
    """An error that is safe to expose to a local API consumer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }


def _safe_validation_details(exc: RequestValidationError) -> dict[str, Any]:
    """Keep validation context JSON-serializable.

    Pydantic v2 puts the original ``ValueError`` object in ``ctx.error`` for
    model validators. Feeding that directly to JSONResponse turns an expected
    422 into an internal 500.
    """
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        safe: dict[str, Any] = {}
        for key, value in error.items():
            if key == "ctx" and isinstance(value, dict):
                safe[key] = {str(ctx_key): str(ctx_value) for ctx_key, ctx_value in value.items()}
            elif key == "input":
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    safe[key] = str(value)
                else:
                    safe[key] = value
            else:
                safe[key] = value
        fields.append(safe)
    return {"fields": fields}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("Service error %s", exc.code)
        else:
            logger.warning("Request rejected (%s): %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = _safe_validation_details(exc)
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "The request contains invalid fields.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = str(exc.detail) if exc.detail else "The request could not be completed."
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "An internal error occurred. Check the service logs for details.",
            ),
        )
