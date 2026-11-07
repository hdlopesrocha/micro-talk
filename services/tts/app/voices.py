"""Explicit catalog of supported Piper voices.

Only voices listed here can be loaded. User input never becomes a filesystem
path. All paths are constructed from the fixed catalog and a validated voice id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .config import settings


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    language_name: str
    engine: str
    quality: str
    sample_rate: int
    repo_id: str
    repo_model_path: str
    repo_config_path: str
    license: str
    description: str
    gender: str = "unknown"
    pitch_supported: bool = False

    @property
    def onnx_path(self) -> Path:
        return Path(settings.voices_dir) / self.id / f"{self.id}.onnx"

    @property
    def config_path(self) -> Path:
        return Path(settings.voices_dir) / self.id / f"{self.id}.onnx.json"

    @property
    def available(self) -> bool:
        return self.onnx_path.is_file() and self.config_path.is_file()

    def public(self) -> dict:
        payload = asdict(self)
        payload.pop("repo_id", None)
        payload.pop("repo_model_path", None)
        payload.pop("repo_config_path", None)
        payload["available"] = self.available
        payload["download_hint"] = (
            None
            if self.available
            else "run: docker compose run --rm tts-service download-models"
        )
        return payload


VOICES: tuple[Voice, ...] = (
    Voice(
        id="pt-PT-tugao",
        name="Tugão",
        language="pt-PT",
        language_name="Portuguese (Portugal)",
        engine="piper",
        quality="medium",
        sample_rate=22050,
        repo_id="rhasspy/piper-voices",
        repo_model_path="pt/pt_PT/tugão/medium/pt_PT-tugão-medium.onnx",
        repo_config_path="pt/pt_PT/tugão/medium/pt_PT-tugão-medium.onnx.json",
        license="CC0 (voice dataset)",
        description="European Portuguese neural voice, single speaker, 22.05 kHz.",
    ),
    Voice(
        id="pt-PT-dii",
        name="Dii",
        language="pt-PT",
        language_name="Portuguese (Portugal)",
        engine="piper",
        quality="high",
        sample_rate=22050,
        repo_id="OpenVoiceOS/pipertts_pt-PT_dii",
        repo_model_path="dii_pt-PT.onnx",
        repo_config_path="dii_pt-PT.onnx.json",
        license="CC BY-NC-ND 4.0 (voice model)",
        description="European Portuguese female neural voice, single speaker, 22.05 kHz.",
        gender="female",
    ),
    Voice(
        id="pt-BR-faber",
        name="Faber",
        language="pt-BR",
        language_name="Portuguese (Brazil)",
        engine="piper",
        quality="medium",
        sample_rate=22050,
        repo_id="rhasspy/piper-voices",
        repo_model_path="pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx",
        repo_config_path="pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json",
        license="CC0 (voice dataset)",
        description="Brazilian Portuguese neural voice, single speaker, 22.05 kHz.",
    ),
    Voice(
        id="pt-BR-dii",
        name="Dii",
        language="pt-BR",
        language_name="Portuguese (Brazil)",
        engine="piper",
        quality="high",
        sample_rate=22050,
        repo_id="OpenVoiceOS/pipertts_pt-BR_dii",
        repo_model_path="dii_pt-BR.onnx",
        repo_config_path="dii_pt-BR.onnx.json",
        license="CC BY-NC-ND 4.0 (voice model)",
        description="Brazilian Portuguese female neural voice, single speaker, 22.05 kHz.",
        gender="female",
    ),
    Voice(
        id="fr-FR-siwis",
        name="Siwis",
        language="fr-FR",
        language_name="French (France)",
        engine="piper",
        quality="medium",
        sample_rate=22050,
        repo_id="rhasspy/piper-voices",
        repo_model_path="fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx",
        repo_config_path="fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json",
        license="CC-BY 4.0 (voice dataset)",
        description="French female neural voice, single speaker, 22.05 kHz.",
        gender="female",
    ),
    Voice(
        id="en-US-lessac",
        name="Lessac",
        language="en-US",
        language_name="English (United States)",
        engine="piper",
        quality="medium",
        sample_rate=22050,
        repo_id="rhasspy/piper-voices",
        repo_model_path="en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        repo_config_path="en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        license="Lessac Blizzard 2013 license",
        description="US English neural voice, single speaker, 22.05 kHz.",
    ),
)

VOICE_BY_ID = {voice.id: voice for voice in VOICES}


def get_voice(voice_id: str | None) -> Voice:
    voice = VOICE_BY_ID.get(voice_id or settings.default_voice)
    if not voice:
        from micro_common.errors import ServiceError

        raise ServiceError(
            "tts_voice_unknown",
            f"Unknown TTS voice: {voice_id}. Available voices: {', '.join(VOICE_BY_ID)}",
            status_code=422,
            details={"voices": list(VOICE_BY_ID)},
        )
    return voice


def language_matches(requested: str, voice_language: str) -> bool:
    req = (requested or "").lower().replace("_", "-")
    lang = (voice_language or "").lower().replace("_", "-")
    req_base = req.split("-", 1)[0]
    lang_base = lang.split("-", 1)[0]
    if req_base != lang_base:
        return False
    # An unqualified "pt" accepts either Portuguese variant. A qualified variant
    # must match exactly so the UI never claims the wrong regional accent.
    if "-" in req and req != lang:
        return False
    return True


def voices_for_language(language: str) -> list[Voice]:
    return [voice for voice in VOICES if language_matches(language, voice.language)]
