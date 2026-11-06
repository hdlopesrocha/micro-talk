"""Speech segmentation and language helpers that do not need model weights."""

from __future__ import annotations

import pytest

from micro_common.errors import ServiceError


def test_split_speech_segments_handles_sentences_and_long_text(service_module):
    jobs = service_module("tts", "jobs")
    text = (
        "A primeira frase é curta. "
        "Esta segunda frase é deliberadamente mais longa, com várias orações, "
        "para confirmar que o limite de caracteres é respeitado. "
        "Fim."
    )
    segments = jobs.split_speech_segments(text, "pt-PT", max_chars=60)
    assert len(segments) >= 3
    assert all(0 < len(segment) <= 60 for segment in segments)
    assert segments[-1] == "Fim."


def test_split_speech_segments_skips_punctuation_only(service_module):
    jobs = service_module("tts", "jobs")
    assert jobs.split_speech_segments("... !!! ---", "en", 100) == []


def test_voice_language_matching_requires_exact_regional_variant(service_module):
    voices = service_module("tts", "voices")
    assert voices.language_matches("pt", "pt-BR")
    assert voices.language_matches("pt-BR", "pt-BR")
    assert not voices.language_matches("pt-BR", "pt-PT")
    assert not voices.language_matches("pt-PT", "pt-BR")
    assert voices.language_matches("en", "en-US")


def test_translation_language_resolution_and_detection(service_module):
    languages = service_module("translation", "languages")
    assert languages.detect_language("This is an English document about local software.") == "en"
    assert languages.detect_language("Este é um documento em português sobre software local.") == "pt"

    resolved = languages.resolve_languages("auto", "pt-BR", "Este é um texto em português.")
    assert resolved.source == "pt"
    assert resolved.target == "pt"
    assert resolved.target_variant == "pt-BR"
    assert resolved.detected is True

    with pytest.raises(ServiceError):
        languages.resolve_languages("fr", "en", "Bonjour")


def test_translation_segments_preserve_page_markers(service_module):
    segments = service_module("translation", "segments")
    result = segments.segments_from_text(
        "--- Page 1 ---\nFirst paragraph.\n\nSecond paragraph.\n\n"
        "--- Page 2 ---\nThird paragraph.",
        850,
        "en",
    )
    assert [(item["page"], item["text"]) for item in result] == [
        (1, "First paragraph."),
        (1, "Second paragraph."),
        (2, "Third paragraph."),
    ]
    assert [item["id"] for item in result] == ["p1", "p2", "p3"]


def test_summary_language_resolution(service_module):
    languages = service_module("summary", "languages")
    resolved = languages.resolve_languages("en", "pt-PT", "This is an English document.")
    assert resolved.source == "en"
    assert resolved.target == "pt"
    assert resolved.target_variant == "pt-PT"

    resolved_br = languages.resolve_languages("auto", "pt-BR", "This is an English document.")
    assert resolved_br.source == "en"
    assert resolved_br.target_variant == "pt-BR"

    with pytest.raises(ServiceError):
        languages.resolve_languages("de", "en", "Hallo")
