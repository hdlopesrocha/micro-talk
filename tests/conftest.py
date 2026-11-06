"""Shared test configuration and safe service-module loading.

Every service uses the Python package name ``app``. Importing several services
in one pytest process would therefore collide, so each service directory is
loaded under a unique synthetic package name (``microtalk_pdf`` and so on).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared" / "python"
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))


def _import_service_module(service: str, module: str):
    package_name = f"microtalk_{service}"
    package = sys.modules.get(package_name)
    if package is None:
        package_dir = ROOT / "services" / service / "app"
        # Create a synthetic namespace package instead of executing the real
        # ``app/__init__.py``: those files eagerly import FastAPI applications
        # which create storage directories and load model runtimes. The helper
        # only needs the package search path so relative imports work.
        package = types.ModuleType(package_name)
        package.__path__ = [str(package_dir)]  # type: ignore[attr-defined]
        package.__package__ = package_name
        package.__file__ = str(package_dir / "__init__.py")
        sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.{module}")


@pytest.fixture(scope="session")
def service_module():
    cache: dict[tuple[str, str], object] = {}

    def load(service: str, module: str):
        key = (service, module)
        if key not in cache:
            cache[key] = _import_service_module(service, module)
        return cache[key]

    return load
