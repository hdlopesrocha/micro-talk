"""Text segmentation helpers for the translation service."""

from __future__ import annotations

import re
from typing import Any

from micro_common.errors import ServiceError
from micro_common.text import split_paragraphs

_PAGE_MARKER = re.compile(r"(?m)^[ \t]*---[ \t]*Page[ \t]+(\d+)[ \t]*---[ \t]*$")


def split_page_blocks(text: str) -> list[tuple[int | None, str]]:
    """Split text on ``--- Page N ---`` markers without translating the markers."""

    matches = list(_PAGE_MARKER.finditer(text))
    if not matches:
        return [(None, text)]
    blocks: list[tuple[int | None, str]] = []
    prefix = text[: matches[0].start()]
    if prefix.strip():
        blocks.append((None, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((int(match.group(1)), text[match.end() : end]))
    return blocks


def segments_from_text(text: str, max_chars: int, language: str) -> list[dict[str, Any]]:
    """Create paragraph segments, preserving explicit PDF page boundaries."""

    del max_chars, language
    blocks = split_page_blocks(text)
    segments: list[dict[str, Any]] = []
    for page, block in blocks:
        for paragraph in split_paragraphs(block):
            segments.append(
                {
                    "id": f"p{len(segments) + 1}",
                    "page": page,
                    "text": paragraph,
                }
            )
    if not segments:
        raise ServiceError("empty_text", "There is no text to translate.", status_code=422)
    return segments
