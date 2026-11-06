"""Subtitle timing and transcript segmentation in the media service."""

from __future__ import annotations

import pytest

from micro_common.errors import ServiceError


def test_provided_segment_timings_are_normalized(service_module):
    media = service_module("media", "media")
    payload = media.build_subtitle_payload(
        text=None,
        segments=[
            {"text": "First sentence.", "start": 0.0, "end": 1.4},
            {"text": "Second sentence.", "start": 1.3, "end": 2.0},
        ],
        language="en",
        audio_duration=5.0,
        words_per_minute=150,
    )
    assert payload["timing_method"] == "generated_audio_segment_timings"
    assert payload["segment_count"] == 2
    assert payload["segments"][1]["start"] == 1.4
    assert "00:00:00,000 --> 00:00:01,400" in payload["srt"]
    assert payload["warnings"] == []


def test_timings_are_scaled_when_they_exceed_audio(service_module):
    media = service_module("media", "media")
    payload = media.build_subtitle_payload(
        text=None,
        segments=[
            {"text": "One.", "start": 0.0, "end": 10.0},
            {"text": "Two.", "start": 10.0, "end": 20.0},
        ],
        language="en",
        audio_duration=10.0,
        words_per_minute=150,
    )
    assert payload["segments"][-1]["end"] <= 10.0
    assert any("scaled proportionally" in warning for warning in payload["warnings"])


def test_text_only_subtitles_use_actual_audio_duration(service_module):
    media = service_module("media", "media")
    payload = media.build_subtitle_payload(
        text="First sentence. Second sentence. Third sentence.",
        segments=None,
        language="en",
        audio_duration=9.0,
        words_per_minute=150,
    )
    assert payload["timing_method"] == "proportional_to_actual_audio_duration"
    assert payload["segments"][-1]["end"] == pytest.approx(9.0, abs=0.01)
    assert any("no word-level alignment" in warning for warning in payload["warnings"])
    assert "First sentence." in payload["srt"]


def test_text_only_cues_never_exceed_short_audio(service_module):
    media = service_module("media", "media")
    payload = media.build_subtitle_payload(
        text="One. Two. Three. Four. Five.",
        segments=None,
        language="en",
        audio_duration=1.5,
        words_per_minute=150,
    )
    assert payload["segments"][-1]["end"] <= 1.5
    assert all(
        segment["end"] <= payload["segments"][-1]["end"]
        for segment in payload["segments"]
    )


def test_text_only_without_audio_is_marked_as_estimated(service_module):
    media = service_module("media", "media")
    payload = media.build_subtitle_payload(
        text="A short text for an estimated subtitle.",
        segments=None,
        language="en",
        audio_duration=None,
        words_per_minute=150,
    )
    assert payload["timing_method"] == "estimated_from_text_word_rate"
    assert any("estimates" in warning for warning in payload["warnings"])


def test_subtitles_require_content(service_module):
    media = service_module("media", "media")
    with pytest.raises(ServiceError):
        media.build_subtitle_payload(
            text=None,
            segments=None,
            language="en",
            audio_duration=1.0,
            words_per_minute=150,
        )


def test_segments_from_text_keeps_paragraph_boundaries(service_module):
    media = service_module("media", "media")
    segments = media.segments_from_text(
        "Primeira frase. Segunda frase.\n\nOutro parágrafo. Última frase.",
        "pt",
    )
    assert len(segments) == 4
    assert segments[0] == "Primeira frase."
    assert segments[-1] == "Última frase."
