"""Download configured Piper voices into the persistent model volume."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import settings
from .voices import VOICES


def main() -> int:
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    from huggingface_hub import hf_hub_download

    root = Path(settings.voices_dir)
    root.mkdir(parents=True, exist_ok=True)
    for voice in VOICES:
        destination = root / voice.id
        destination.mkdir(parents=True, exist_ok=True)
        if voice.available:
            print(f"[tts] Already installed: {voice.id}")
            continue
        print(f"[tts] Downloading {voice.id} from {voice.repo_id}")
        with tempfile.TemporaryDirectory(prefix=".download-", dir=str(root)) as temporary:
            model_path = hf_hub_download(
                repo_id=voice.repo_id,
                filename=voice.repo_model_path,
                local_dir=temporary,
            )
            config_path = hf_hub_download(
                repo_id=voice.repo_id,
                filename=voice.repo_config_path,
                local_dir=temporary,
            )
            shutil.copyfile(model_path, destination / f"{voice.id}.onnx")
            shutil.copyfile(config_path, destination / f"{voice.id}.onnx.json")
        print(f"[tts] Ready: {voice.id}")
    print("[tts] All configured voices are installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
