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


def test_split_speech_segments_can_ignore_bible_verse_numbers(service_module):
    jobs = service_module("tts", "jobs")
    text = (
        "1 No princípio criou Deus os céus e a terra. "
        "2 E a terra era sem forma e vazia."
    )
    numbered = jobs.split_speech_segments(text, "pt-PT", 400)
    assert numbered[0].startswith("1 ")
    assert numbered[1].startswith("2 ")

    cleaned = jobs.split_speech_segments(
        text, "pt-PT", 400, ignore_leading_numbers=True
    )
    assert cleaned[0].startswith("No princípio")
    assert cleaned[1].startswith("E a terra")
    assert all(not segment.startswith(("1 ", "2 ")) for segment in cleaned)


def test_split_speech_segments_can_keep_long_sentences_whole(service_module):
    jobs = service_module("tts", "jobs")
    text = ("Esta é uma frase deliberadamente longa que continua, " * 8).strip() + "."
    split = jobs.split_speech_segments(text, "pt-PT", max_chars=80)
    assert len(split) > 1

    preserved = jobs.split_speech_segments(
        text,
        "pt-PT",
        max_chars=80,
        preserve_phrases=True,
        phrase_max_chars=2000,
    )
    assert preserved == [text]


def test_voice_language_matching_requires_exact_regional_variant(service_module):
    voices = service_module("tts", "voices")
    assert voices.language_matches("pt", "pt-BR")
    assert voices.language_matches("pt-BR", "pt-BR")
    assert not voices.language_matches("pt-BR", "pt-PT")
    assert not voices.language_matches("pt-PT", "pt-BR")
    assert voices.language_matches("en", "en-US")


def test_portuguese_and_french_female_voices_are_catalogued(service_module):
    voices = service_module("tts", "voices")
    female_portuguese = {
        voice.id
        for voice in voices.VOICES
        if voice.gender == "female" and voice.language.startswith("pt")
    }
    assert {"pt-PT-dii", "pt-BR-dii"} <= female_portuguese
    french = next(voice for voice in voices.VOICES if voice.id == "fr-FR-siwis")
    assert french.gender == "female"
    assert french.language == "fr-FR"


def test_translation_language_resolution_and_detection(service_module):
    languages = service_module("translation", "languages")
    assert languages.detect_language("This is an English document about local software.") == "en"
    assert languages.detect_language("Este é um documento em português sobre software local.") == "pt"
    assert (
        languages.detect_language("Ceci est un document français sur les logiciels locaux.")
        == "fr"
    )

    resolved = languages.resolve_languages("auto", "pt-BR", "Este é um texto em português.")
    assert resolved.source == "pt"
    assert resolved.target == "pt"
    assert resolved.target_variant == "pt-BR"
    assert resolved.detected is True

    resolved_fr = languages.resolve_languages("auto", "en", "Bonjour tout le monde.")
    assert resolved_fr.source == "fr"
    assert resolved_fr.target == "en"
    assert resolved_fr.detected is True

    resolved_target = languages.resolve_languages("fr", "pt-PT", "Bonjour tout le monde.")
    assert resolved_target.source == "fr"
    assert resolved_target.target == "pt"
    assert resolved_target.target_variant == "pt-PT"

    with pytest.raises(ServiceError):
        languages.resolve_languages("de", "en", "Hallo")


def test_translation_routes_use_english_pivot_for_portuguese_french(service_module):
    languages = service_module("translation", "languages")
    assert languages.translation_route("pt", "en") == ["pt-en"]
    assert languages.translation_route("en", "fr") == ["en-fr"]
    assert languages.translation_route("pt", "fr") == ["pt-en", "en-fr"]
    assert languages.translation_route("fr", "pt") == ["fr-en", "en-pt"]
    assert languages.translation_route("fr", "fr") == []


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
