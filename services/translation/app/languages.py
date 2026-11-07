"""Language validation and automatic source-language detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langdetect import DetectorFactory, LangDetectException, detect_langs
from micro_common.errors import ServiceError
from micro_common.text import normalize_language

DetectorFactory.seed = 0

SUPPORTED_LANGUAGES = frozenset({"pt", "en", "fr"})
SUPPORTED_OUTPUT_LANGUAGES = {
    "en": "English",
    "pt-PT": "Portuguese (Portugal)",
    "pt-BR": "Portuguese (Brazil)",
    "fr": "French",
}
# Direct OPUS-MT models. Portuguese↔French uses English as a pivot because no
# direct PT↔FR model is part of the pinned local catalog.
DIRECT_PAIRS = frozenset(
    {
        ("pt", "en"),
        ("en", "pt"),
        ("en", "fr"),
        ("fr", "en"),
    }
)

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
_FRENCH_HINTS = {
    "le", "la", "les", "des", "une", "un", "est", "sont", "dans", "pour", "avec",
    "pas", "plus", "sur", "au", "aux", "ce", "cette", "ces", "qui", "que", "quoi",
    "être", "etre", "été", "etait", "était", "document", "texte", "français",
    "francais", "nous", "vous", "ils", "elles", "du", "de", "en", "il", "elle",
}


@dataclass(frozen=True)
class ResolvedLanguages:
    source: str  # canonical pt|en|fr
    target: str  # canonical pt|en|fr
    target_variant: str  # en|pt-PT|pt-BR|fr
    detected: bool = False


def translation_route(source: str, target: str) -> list[str]:
    """Return the ordered model pairs needed for a language direction.

    An empty list means that source and target are identical. Direct pairs use
    one model; Portuguese↔French is routed through English in two passes.
    """
    source = (source or "").split("-", 1)[0].lower()
    target = (target or "").split("-", 1)[0].lower()
    if source not in SUPPORTED_LANGUAGES or target not in SUPPORTED_LANGUAGES:
        raise ServiceError(
            "unsupported_language",
            "Only Portuguese, English and French are supported by the translation service.",
            status_code=422,
        )
    if source == target:
        return []
    if (source, target) in DIRECT_PAIRS:
        return [f"{source}-{target}"]
    if source in {"pt", "fr"} and target in {"pt", "fr"}:
        return [f"{source}-en", f"en-{target}"]
    raise ServiceError(
        "translation_route_unsupported",
        f"No translation route is available for {source} to {target}.",
        status_code=422,
    )


def detect_language(text: str) -> str:
    sample = re.sub(r"\s+", " ", text or "").strip()
    if not sample:
        raise ServiceError("empty_text", "There is no text to detect a language from.", status_code=422)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", sample.lower())
    hits = {
        "pt": sum(1 for word in words if word in _PORTUGUESE_HINTS),
        "en": sum(1 for word in words if word in _ENGLISH_HINTS),
        "fr": sum(1 for word in words if word in _FRENCH_HINTS),
    }
    ranking = sorted(hits.items(), key=lambda item: item[1], reverse=True)
    if len(words) <= 8 and ranking[0][1] > 0 and ranking[0][1] > ranking[1][1]:
        return ranking[0][0]
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
        if language in SUPPORTED_LANGUAGES and candidate.prob > 0.15:
            return language
    if ranking[0][1] > 0:
        return ranking[0][0]
    raise ServiceError(
        "unsupported_language",
        "Only Portuguese, English and French are supported by the installed translation models.",
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
    if source not in SUPPORTED_LANGUAGES:
        raise ServiceError(
            "unsupported_language",
            "Only Portuguese, English and French source languages are supported.",
            status_code=422,
        )
    try:
        target = normalize_language(target_raw)
    except ValueError as exc:
        raise ServiceError("unsupported_language", f"Unsupported target language: {target_raw}", status_code=422) from exc
    if target not in SUPPORTED_LANGUAGES:
        raise ServiceError(
            "unsupported_language",
            "Only Portuguese, English and French targets are supported.",
            status_code=422,
        )
    variant = target
    if target == "pt":
        lowered = target_raw.lower().replace("_", "-")
        variant = "pt-BR" if lowered in {"pt-br", "pt_br"} else "pt-PT"
    elif target == "fr":
        variant = "fr"
    return ResolvedLanguages(
        source=source, target=target, target_variant=variant, detected=detected
    )
