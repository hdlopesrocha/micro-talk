"""HTTP file responses with real byte-range support and optional activity hooks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Callable

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from .security import content_disposition, parse_byte_range

CHUNK_SIZE = 256 * 1024
_END = object()


def _next_chunk(iterator):
    """Advance a blocking iterator without letting StopIteration cross a Future.

    ``asyncio.to_thread`` sets whatever the worker raises as the Future's
    exception. ``StopIteration`` cannot be stored in a Future (PEP 479), so the
    sentinel keeps normal end-of-stream from surfacing as a background error.
    """
    try:
        return next(iterator)
    except StopIteration:
        return _END


def ranged_file_response(
    request: Request,
    path: str | Path,
    *,
    media_type: str,
    filename: str,
    inline: bool = True,
    on_start: Callable[[], None] | None = None,
    on_end: Callable[[], None] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    file_path = Path(path)
    if not file_path.is_file():
        return Response(status_code=404)
    file_size = file_path.stat().st_size
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition(filename, inline=inline),
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    if extra_headers:
        headers.update(extra_headers)
    range_header = request.headers.get("range")
    if range_header:
        parsed = parse_byte_range(range_header, file_size)
        if parsed is None:
            return Response(
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{file_size}"},
            )
        start, end = parsed
        length = end - start + 1
        headers.update(
            {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
            }
        )
        status_code = 206
    else:
        start, end = 0, file_size - 1
        length = file_size
        headers["Content-Length"] = str(length)
        status_code = 200

    started = False
    ended = False

    def finish() -> None:
        nonlocal ended
        if ended:
            return
        ended = True
        if on_end:
            on_end()

    def stream():
        nonlocal started
        started = True
        if on_start:
            on_start()
        try:
            with open(file_path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    data = handle.read(min(CHUNK_SIZE, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        finally:
            finish()

    async def async_stream() -> AsyncIterator[bytes]:
        import asyncio

        iterator = stream()
        try:
            while True:
                chunk = await asyncio.to_thread(_next_chunk, iterator)
                if chunk is _END:
                    break
                yield chunk
        finally:
            # Closing the generator runs its finally block exactly once. If the
            # response was never consumed, there is no start hook to balance.
            try:
                await asyncio.to_thread(iterator.close)
            except Exception:  # pragma: no cover - defensive cleanup
                pass
            if not started:
                finish()

    return StreamingResponse(
        async_stream(),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )
