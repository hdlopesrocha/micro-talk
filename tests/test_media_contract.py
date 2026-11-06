"""Media HTTP request schemas that are shared with the gateway.

The TTS timeline contains a one-based ``index`` for each sentence. Subtitle
normalization renumbers cues, so the media request schema must accept the field
without tripping ``extra="forbid"``.
"""

from __future__ import annotations

from dataclasses import replace


def test_timing_segment_accepts_tts_timeline_index(tmp_path, service_module, monkeypatch):
    # Importing the FastAPI module constructs the media store, so redirect it
    # away from the production /data path before the module is loaded.
    config = service_module("media", "config")
    settings = replace(
        config.settings,
        media_dir=str(tmp_path / "media"),
        tmp_dir=str(tmp_path / "media" / "tmp"),
    )
    monkeypatch.setattr(config, "settings", settings)

    main = service_module("media", "main")
    segment = main.TimingSegment(
        index=7, text="A real TTS sentence.", start=1.25, end=2.5
    )
    assert segment.index == 7

    request = main.SubtitleRequest(
        segments=[
            {"index": 1, "text": "First.", "start": 0.0, "end": 1.0},
            {"index": 2, "text": "Second.", "start": 1.0, "end": 2.0},
        ],
        audio_path="files/00000000-0000-0000-0000-000000000000/audio.mp3",
        language="en",
    )
    assert [item.index for item in request.segments or []] == [1, 2]
