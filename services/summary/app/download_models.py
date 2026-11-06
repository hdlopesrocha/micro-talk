"""Download the configured local summarization models."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import settings

ALLOW_PATTERNS = [
    "config.json",
    "generation_config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "spiece.model",
    "vocab.json",
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
]


def main() -> int:
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import snapshot_download

    targets = [
        ("en", settings.model_en_repo, settings.model_en_revision),
        ("pt", settings.model_pt_repo, settings.model_pt_revision),
    ]
    for language, repo, revision in targets:
        destination = Path(settings.models_dir) / language
        destination.mkdir(parents=True, exist_ok=True)
        print(f"[summary] Downloading {repo}@{revision} -> {destination}")
        snapshot_download(
            repo_id=repo,
            revision=revision,
            local_dir=str(destination),
            allow_patterns=ALLOW_PATTERNS,
            max_workers=2,
        )
        print(f"[summary] Ready: {language}")
    print("[summary] All configured models are installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
