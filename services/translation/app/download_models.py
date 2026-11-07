"""Download configured translation models into the persistent model volume."""

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
    "vocab.json",
    "source.spm",
    "target.spm",
    "spiece.model",
    "model.safetensors",
    "model.safetensors.index.json",
    "pytorch_model.bin",
    "pytorch_model.bin.index.json",
]


def main() -> int:
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import snapshot_download

    targets = list(settings.model_repos.items())
    for pair, repo in targets:
        destination = Path(settings.models_dir) / pair
        destination.mkdir(parents=True, exist_ok=True)
        print(f"[translation] Downloading {repo} -> {destination}")
        snapshot_download(
            repo_id=repo,
            local_dir=str(destination),
            allow_patterns=ALLOW_PATTERNS,
            max_workers=2,
        )
        print(f"[translation] Ready: {pair}")
    print("[translation] All configured models are installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
