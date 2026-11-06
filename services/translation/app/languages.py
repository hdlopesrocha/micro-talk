"""Language validation and automatic source-language detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect_langs
from micro_common.errors import ServiceError
from micro_common.text import normalize_language

DetectorFactory.seed = 0

SUPPORTED_OUTPUT_LANGUAGES = {"en": "English", "pt-PT": "Portuguese (Portugal)", "pt-BR": "Portuguese (Brazil)"}

_PORTUGUESE_HINTS = {
    "não", "uma", "para", "com", "isso", "este", "esta", "como", "mais", "dos", "das",
    "pelo", "pela", "você", "voce", "também", "tambem", "porque", "quando", "muito",
    "documento", "texto", "é", "são", "está", "estão", "ser", "foi", "há",
}
_ENGLISH_HINTS = {
    "the", "and", "this", "that", "with", "from", "have", "has", "are", "was", "were",
    "for", "not", "you", "your", "document", "text", "will", "would", "there", "their",
    "is", "it", "of", "to", "in", "on", "at", "be", "by",
}


@dataclass(frozen=True)
class ResolvedLanguages:
    source: str  # canonical en|pt
    target: str  # canonical en|pt
    target_variant: str  # en|pt-PT|pt-BR
    detected: bool = False


def detect_language(text: str) -> str:
    sample = re.sub(r"\s+", " ", text or "").strip()
    if not sample:
        raise ServiceError("empty_text", "There is no text to detect a language from.", status_code=422)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", sample.lower())
    pt_hits = sum(1 for word in words if word in _PORTUGUESE_HINTS)
    en_hits = sum(1 for word in words if word in _ENGLISH_HINTS)
    if len(words) <= 8:
        if pt_hits > en_hits:
            return "pt"
        if en_hits > pt_hits:
            return "en"
    try:
        candidates = detect_langs(sample[:5000])
    except LangDetectException as exc:
        raise ServiceError(
            "language_detection_failed",
            "The source language could not be detected. Select it explicitly.",
            status_code=422,
        ) from exc
    for candidate in candidates:
        language = candidate.lang.lower()
        if language in {"pt", "en"} and candidate.prob > 0.15:
            return language
    if pt_hits > en_hits:
        return "pt"
    if en_hits > pt_hits:
        return "en"
    raise ServiceError(
        "unsupported_language",
        "Only Portuguese and English are supported by the installed translation models.",
        status_code=422,
        details={"detected": [c.lang for c in candidates]},
    )


def resolve_languages(source_language: str | None, target_language: str | None, text: str) -> ResolvedLanguages:
    source_raw = (source_language or "auto").strip()
    target_raw = (target_language or "en").strip()
    if source_raw.lower() == "auto":
        source = detect_language(text)
        detected = True
    else:
        try:
            source = normalize_language(source_raw)
        except ValueError as exc:
            raise ServiceError("unsupported_language", f"Unsupported source language: {source_raw}", status_code=422) from exc
        detected = False
    if source not in {"pt", "en"}:
        raise ServiceError(
            "unsupported_language",
            "Only Portuguese and English source languages are supported.",
            status_code=422,
        )
    try:
        target = normalize_language(target_raw)
    except ValueError as exc:
        raise ServiceError("unsupported_language", f"Unsupported target language: {target_raw}", status_code=422) from exc
    if target not in {"pt", "en"}:
        raise ServiceError("unsupported_language", "Only Portuguese and English targets are supported.", status_code=422)
    variant = target
    if target == "pt":
        lowered = target_raw.lower().replace("_", "-")
        variant = "pt-BR" if lowered in {"pt-br", "pt_br"} else "pt-PT"
    return ResolvedLanguages(
        source=source, target=target, target_variant=variant, detected=detected
    )
