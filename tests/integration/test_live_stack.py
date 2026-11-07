"""Optional live Docker Compose integration tests.

Run against a started stack:

    MICROTALK_URL=http://localhost:8080 \
    GATEWAY_AUTH_TOKEN=... \
    pytest tests/integration -v

These tests never mock a service. Workflow tests skip automatically when the
required local models have not been downloaded.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("MICROTALK_URL", "").rstrip("/")
TOKEN = os.getenv("GATEWAY_AUTH_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="set MICROTALK_URL to run live Docker integration tests",
)


def client() -> httpx.Client:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=30.0)


def test_proxy_and_gateway_health():
    with client() as http:
        proxy = http.get(f"{BASE_URL.replace('/api', '')}/healthz")
        assert proxy.status_code == 200
        health = http.get("/api/health")
        assert health.status_code == 200
        assert health.json()["service"] == "gateway"
        assert health.json()["redis"] is True


def test_catalog_reports_every_service():
    with client() as http:
        response = http.get("/api/v1/catalog")
        assert response.status_code == 200
        catalog = response.json()
        health = catalog.get("health") or {}
        for service in ("pdf", "translation", "summary", "tts", "media"):
            assert service in health
            assert (health[service] or {}).get("status") in {"ok", "degraded"}
        assert "voices" in (catalog.get("tts") or {})


def test_french_translation_when_models_are_installed():
    with client() as http:
        catalog = http.get("/api/v1/catalog").json()
        routes = (catalog.get("translation_status") or {}).get("supported_routes") or {}
        if not routes.get("pt-fr") or not routes.get("fr-pt"):
            pytest.skip("French translation models are not installed")

        for source, target, text in (
            ("pt", "fr", "Este é um teste de tradução para francês."),
            ("fr", "pt-PT", "Ceci est un test de traduction vers le portugais."),
        ):
            response = http.post(
                "/api/v1/direct/translate",
                json={
                    "text": text,
                    "source_language": source,
                    "target_language": target,
                },
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["translated_text"].strip()
            assert payload["target_language"] == target


def test_translation_workflow_when_models_are_installed():
    with client() as http:
        catalog = http.get("/api/v1/catalog").json()
        pairs = (catalog.get("translation_status") or {}).get("supported_pairs") or {}
        if not any(pairs.values()):
            pytest.skip("translation models are not installed")

        response = http.post(
            "/api/v1/jobs",
            json={
                "title": "Live integration test",
                "input": {
                    "type": "text",
                    "text": (
                        "Este é um teste de integração do MicroTalk. "
                        "O serviço deve extrair, traduzir e empacotar este texto."
                    ),
                    "language": "auto",
                },
                "translation": {
                    "enabled": True,
                    "source_language": "auto",
                    "target_language": "en",
                },
                "summary": {"enabled": False},
                "tts": {"enabled": False},
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]

        deadline = time.time() + 300
        state = {}
        while time.time() < deadline:
            state = http.get(f"/api/v1/jobs/{job_id}").json()
            if state["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1.5)
        assert state.get("status") == "completed", state
        assert state["artifacts"].get("translation", {}).get("available") is True

        text = http.get(f"/api/v1/jobs/{job_id}/text/translation")
        assert text.status_code == 200
        assert text.text.strip()
        download = http.get(f"/api/v1/jobs/{job_id}/downloads/zip")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"

        http.delete(f"/api/v1/jobs/{job_id}")
