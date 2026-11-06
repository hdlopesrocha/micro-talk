"""Optional bearer-token protection for the local gateway API."""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings

PUBLIC_PATHS = {
    "/health",
    "/api/health",
    "/api/v1/auth/status",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _provided_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header_token = request.headers.get("x-api-token", "").strip()
    if header_token:
        return header_token
    return request.query_params.get("token", "").strip()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            return await call_next(request)
        expected = settings.auth_token
        if not expected:
            return await call_next(request)
        provided = _provided_token(request)
        if not provided or not hmac.compare_digest(provided, expected):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": "A valid access token is required.",
                        "details": {},
                    }
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
