"""Text, subtitle and filename helpers shared by every service."""

from __future__ import annotations

import pytest

from micro_common.text import (
    build_srt,
    chunk_paragraph,
    clean_text,
    distribute_duration,
    format_srt_timestamp,
    format_vtt_timestamp,
    normalize_language,
    safe_filename,
    sentence_split,
    split_long_piece,
    word_count,
)


def test_clean_text_normalizes_whitespace_and_nulls():
    assert clean_text("  Hello\r\nworld\x00 \t again  ") == "Hello\nworld again"
    assert clean_text("a\n\n\n\nb") == "a\n\nb"


def test_sentence_split_preserves_abbreviations_and_decimals():
    text = "Dr. Silva arrived at 3.14 p.m. with the report. It was ready!"
    sentences = sentence_split(text)
    assert sentences[0].startswith("Dr. Silva")
    assert "3.14" in sentences[0]
    assert any(sentence.endswith("!") for sentence in sentences)


def test_sentence_split_splits_paragraph_lines():
    sentences = sentence_split("First line.\nSecond line. Third line.")
    assert len(sentences) == 3
    assert sentences[1] == "Second line."


def test_split_long_piece_respects_limit_and_keeps_content():
    text = "word " * 120
    pieces = split_long_piece(text.strip(), 100)
    assert len(pieces) > 1
    assert all(len(piece) <= 100 for piece in pieces)
    assert " ".join(pieces).split() == text.split()


def test_chunk_paragraph_packs_sentences_below_budget():
    paragraph = "One short sentence. " * 30
    chunks = chunk_paragraph(paragraph.strip(), max_chars=120)
    assert chunks
    assert all(len(chunk) <= 120 for chunk in chunks)
    with pytest.raises(ValueError):
        chunk_paragraph("text", max_chars=20)


def test_word_count_handles_portuguese_accents():
    assert word_count("O rato roeu a rolha, não é?") == 7


def test_srt_timestamps_round_and_format():
    assert format_srt_timestamp(61.2344) == "00:01:01,234"
    assert format_srt_timestamp(3661.5) == "01:01:01,500"
    assert format_vtt_timestamp(1.5) == "00:00:01.500"


def test_build_srt_clamps_to_audio_duration_and_skips_empty():
    srt = build_srt(
        [
            {"text": "Hello", "start": 0, "end": 1},
            {"text": "", "start": 1, "end": 2},
            {"text": "World", "start": 2, "end": 9},
        ],
        total_duration=3.0,
    )
    assert "00:00:00,000 --> 00:00:01,000" in srt
    assert "00:00:02,000 --> 00:00:03,000" in srt
    assert "World" in srt
    assert srt.count("-->") == 2


def test_distribute_duration_is_monotonic_and_ends_at_total():
    timings = distribute_duration(12.0, ["one", "two", "three"])
    assert timings[0][0] == 0
    assert all(start < end for start, end in timings)
    assert timings[-1][1] == pytest.approx(12.0)


def test_normalize_language_aliases():
    assert normalize_language("PT_br") == "pt"
    assert normalize_language("en-US") == "en"
    with pytest.raises(ValueError):
        normalize_language("fr")


def test_safe_filename_strips_unsafe_characters():
    assert safe_filename("../../meu documento: final", ".txt") == "meu documento_ final.txt"
    assert safe_filename("", ".zip", fallback="output") == "output.zip"
