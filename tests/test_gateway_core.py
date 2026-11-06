"""Document, translation-batch and archive helpers from the gateway."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from micro_common.errors import ServiceError


def test_text_update_rejects_blank_edits(service_module):
    models = service_module("gateway", "models")
    with pytest.raises(ValidationError):
        models.TextUpdateRequest(text="   \n\t ")
    accepted = models.TextUpdateRequest(text="Edited paragraph.\n")
    assert accepted.text.startswith("Edited")


def test_job_store_lifecycle(tmp_path: Path, service_module):
    store_module = service_module("gateway", "store")
    store = store_module.JobStore(str(tmp_path / "jobs.sqlite3"))
    job_id = str(uuid4())
    state = {
        "id": job_id,
        "status": "queued",
        "title": "Test workflow",
        "config": {"input": {"type": "text"}},
        "steps": [{"id": "prepare", "status": "pending"}],
        "artifacts": {},
        "created_at": 1234.0,
        "updated_at": 1234.0,
    }
    store.create(job_id, state)

    loaded = store.get(job_id)
    assert loaded and loaded["title"] == "Test workflow"
    assert loaded["status"] == "queued"

    store.save_state({**loaded, "status": "running", "updated_at": 0})
    assert store.get(job_id)["status"] == "running"
    assert store.count_active() == 1

    store.request_cancel(job_id)
    assert store.get_raw_fields(job_id)["cancel_requested"] is True

    store.mark_cancelled(job_id, "Stopped for the test.")
    cancelled = store.get(job_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"]
    assert store.count_active() == 0

    listing, total = store.list_jobs(limit=10, offset=0)
    assert total == 1
    assert listing[0]["id"] == job_id
    assert store.delete(job_id) is None
    assert store.get(job_id) is None


def test_job_store_rejects_invalid_ids(tmp_path: Path, service_module):
    store_module = service_module("gateway", "store")
    store = store_module.JobStore(str(tmp_path / "jobs.sqlite3"))
    assert store.get("../../etc/passwd") is None
    with pytest.raises(ValueError):
        store.create("not-a-uuid", {"title": "x"})


def test_translation_batches_respect_char_and_segment_limits(service_module):
    pipeline = service_module("gateway", "pipeline")
    segments = [
        {"id": f"s{i}", "page": None, "text": "x" * 1000} for i in range(120)
    ]
    batches = pipeline.PipelineRunner._translation_batches(segments)
    assert len(batches) > 1
    assert all(len(batch) <= pipeline.MAX_TRANSLATION_BATCH_SEGMENTS for batch in batches)
    assert sum(len(batch) for batch in batches) == len(segments)


def test_voice_language_matching_requires_exact_regional_variant(service_module):
    pipeline = service_module("gateway", "pipeline")
    assert pipeline.PipelineRunner._voice_matches_language("pt", "pt-BR")
    assert pipeline.PipelineRunner._voice_matches_language("pt-BR", "pt-BR")
    assert not pipeline.PipelineRunner._voice_matches_language("pt-BR", "pt-PT")
    assert not pipeline.PipelineRunner._voice_matches_language("pt-PT", "pt-BR")
    assert pipeline.PipelineRunner._voice_matches_language("en", "en-US")


def test_render_translated_text_groups_pages(service_module):
    pipeline = service_module("gateway", "pipeline")
    rendered = pipeline.PipelineRunner._render_translated_text(
        [
            {"page": 1, "text": "First page."},
            {"page": 1, "text": "Still first page."},
            {"page": 2, "text": "Second page."},
        ]
    )
    assert rendered.startswith("--- Page 1 ---")
    assert "Still first page." in rendered
    assert "--- Page 2 ---" in rendered
    assert rendered.endswith("\n")


def test_write_artifacts_and_build_zip(tmp_path: Path, service_module):
    artifacts = service_module("gateway", "artifacts")
    config = service_module("gateway", "config")
    job_id = str(uuid4())
    settings = config.Settings(
        data_dir=str(tmp_path / "gateway"),
        media_data_dir=str(tmp_path / "media"),
        tts_data_dir=str(tmp_path / "tts"),
    )

    extracted = artifacts.write_text_artifact(
        settings, job_id, "extracted", "Original text.\n"
    )
    summary = artifacts.write_text_artifact(settings, job_id, "summary", "Short summary.\n")
    state = {
        "id": job_id,
        "title": "A / dangerous: title",
        "created_at": 1.0,
        "finished_at": 2.0,
        "config": {"input": {"type": "text"}},
        "artifacts": {"extracted": extracted, "summary": summary},
        "warnings": ["example"],
    }
    bundle = artifacts.build_zip(settings, job_id, state)

    archive_path = artifacts.artifact_file(settings, job_id, "output.zip")
    assert bundle["available"] is True
    assert bundle["size_bytes"] == archive_path.stat().st_size
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "job.json" in names
        assert any(name.endswith("-extracted.txt") for name in names)
        assert any(name.endswith("-summary.txt") for name in names)
        manifest = json.loads(archive.read("job.json"))
    assert manifest["id"] == job_id
    assert manifest["warnings"] == ["example"]
    assert "path" not in manifest["artifacts"]["extracted"]


def test_text_artifact_word_count_ignores_page_markers(tmp_path: Path, service_module):
    artifacts = service_module("gateway", "artifacts")
    config = service_module("gateway", "config")
    settings = config.Settings(data_dir=str(tmp_path / "gateway"))
    artifact = artifacts.write_text_artifact(
        settings,
        str(uuid4()),
        "extracted",
        "--- Page 1 ---\n\nOriginal text.\n\n--- Page 2 ---\n\nSecond page.\n",
    )
    assert artifact["words"] == 4


def test_artifact_paths_reject_traversal(tmp_path: Path, service_module):
    artifacts = service_module("gateway", "artifacts")
    config = service_module("gateway", "config")
    settings = config.Settings(data_dir=str(tmp_path / "gateway"))
    with pytest.raises(ServiceError):
        artifacts.artifact_file(settings, "../../escape", "extracted.txt")
