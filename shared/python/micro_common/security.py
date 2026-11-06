"""Security helpers shared by public-facing and internal services."""

from __future__ import annotations

import io
import os
import re

PDF_MAGIC = b"%PDF-"
_UNSAFE_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f\\]")


def looks_like_pdf_header(header: bytes) -> bool:
    return header.startswith(PDF_MAGIC)


def sanitize_original_filename(filename: str | None, fallback: str = "document.pdf") -> str:
    name = (filename or fallback).replace("\x00", "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_PATH_CHARS.sub("_", name)
    if not name or name in {".", ".."}:
        name = fallback
    return name[:240]


def content_disposition(filename: str, inline: bool = False) -> str:
    """Build an RFC 5987 safe Content-Disposition header."""
    safe = sanitize_original_filename(filename, fallback="download")
    ascii_name = safe.encode("ascii", "ignore").decode("ascii") or "download"
    ascii_name = ascii_name.replace('"', "")
    encoded = quote(safe)
    mode = "inline" if inline else "attachment"
    return f'{mode}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def quote(value: str) -> str:
    from urllib.parse import quote as urlquote

    return urlquote(value, safe="")


def parse_byte_range(range_header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse a single HTTP Range header. Returns inclusive start/end."""
    if not range_header or not range_header.startswith("bytes="):
        return None
    ranges = range_header[6:].split(",")
    if len(ranges) != 1:
        return None
    raw = ranges[0].strip()
    if "-" not in raw:
        return None
    start_text, end_text = raw.split("-", 1)
    try:
        if start_text == "":
            suffix = int(end_text)
            if suffix <= 0:
                return None
            suffix = min(suffix, file_size)
            return file_size - suffix, file_size - 1
        start = int(start_text)
        if start < 0 or start >= file_size:
            return None
        if end_text == "":
            end = file_size - 1
        else:
            end = min(int(end_text), file_size - 1)
        if end < start:
            return None
        return start, end
    except ValueError:
        return None


def safe_zip_arcname(filename: str) -> str:
    clean = os.path.basename(sanitize_original_filename(filename, fallback="artifact"))
    clean = clean.replace("/", "_").replace("\\", "_")
    return clean or "artifact"
