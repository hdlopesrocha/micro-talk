"""Local translation service package.

The FastAPI application is imported lazily. Model-download commands must be
able to clear the offline Hugging Face flags before transformers is imported;
an eager ``from .main import app`` would load it too early.
"""

from __future__ import annotations

from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from .main import app as application

        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
