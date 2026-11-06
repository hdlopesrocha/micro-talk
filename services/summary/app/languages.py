"""Source-language detection and target validation for summarization."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect_langs
from micro_common.errors import ServiceError
from micro_common.text import normalize_language

DetectorFactory.seed = 0

_PT_HINTS = {"não", "uma", "para", "com", "isso", "como", "mais", "dos", "das", "pelo", "pela", "é", "são", "está"}
_EN_HINTS = {"the", "and", "this", "that", "with", "from", "have", "are", "was", "were", "for", "not", "of", "to", "in"}


@dataclass(frozen=True)
class SummaryLanguages:
    source: str
    target: str
    target_variant: str


def detect_language(text: str) -> str:
    sample = re.sub(r"\s+", " ", text or "").strip()
    if not sample:
        raise ServiceError("empty_text", "There is no text to summarize.", status_code=422)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", sample.lower())
    pt_hits = sum(word in _PT_HINTS for word in words)
    en_hits = sum(word in _EN_HINTS for word in words)
    if len(words) <= 8 and pt_hits != en_hits:
        return "pt" if pt_hits > en_hits else "en"
    try:
        candidates = detect_langs(sample[:5000])
    except LangDetectException as exc:
        raise ServiceError(
            "language_detection_failed",
            "The summary source language could not be detected.",
            status_code=422,
        ) from exc
    for candidate in candidates:
        if candidate.lang in {"pt", "en"} and candidate.prob > 0.15:
            return candidate.lang
    if pt_hits > en_hits:
        return "pt"
    if en_hits > pt_hits:
        return "en"
    raise ServiceError(
        "unsupported_language",
        "The summarization models support Portuguese and English text.",
        status_code=422,
        details={"detected": [candidate.lang for candidate in candidates]},
    )


def resolve_languages(source_language: str | None, summary_language: str, text: str) -> SummaryLanguages:
    if not (source_language or "auto").lower() == "auto":
        try:
            source = normalize_language(source_language)
        except ValueError as exc:
            raise ServiceError(
                "unsupported_language", f"Unsupported source language: {source_language}", status_code=422
            ) from exc
    else:
        source = detect_language(text)
    if source not in {"en", "pt"}:
        raise ServiceError("unsupported_language", "Only Portuguese and English are supported.", status_code=422)
    raw_target = (summary_language or source).strip()
    try:
        target = normalize_language(raw_target)
    except ValueError as exc:
        raise ServiceError(
            "unsupported_language", f"Unsupported summary language: {summary_language}", status_code=422
        ) from exc
    if target not in {"en", "pt"}:
        raise ServiceError("unsupported_language", "Only Portuguese and English summaries are supported.", status_code=422)
    variant = target
    if target == "pt":
        variant = "pt-BR" if raw_target.lower().replace("_", "-") in {"pt-br"} else "pt-PT"
    return SummaryLanguages(source=source, target=target, target_variant=variant)
