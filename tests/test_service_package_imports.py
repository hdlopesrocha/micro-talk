"""Regression checks for service package import timing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared" / "python"


def test_model_download_packages_do_not_eagerly_import_the_app() -> None:
    """``download-models`` must unset offline flags before transformers loads.

    Importing ``app.download_models`` executes ``app/__init__.py`` first. If that
    package eagerly imports ``app.main``, huggingface_hub reads HF_HUB_OFFLINE=1
    before the download command can clear it and every model download fails.
    """

    for service in ("translation", "summary"):
        service_path = ROOT / "services" / service
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(SHARED), str(service_path)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, app; assert 'app.main' not in sys.modules, "
                "'app.main was imported eagerly'",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{service}: {result.stderr}"
