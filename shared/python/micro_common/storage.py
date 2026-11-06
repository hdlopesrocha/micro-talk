"""Filesystem helpers with conservative path containment checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import ServiceError

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def new_id() -> str:
    return str(uuid.uuid4())


def is_uuid(value: str) -> bool:
    return bool(_UUID_RE.fullmatch(value or ""))


def require_uuid(value: str, field: str = "id") -> str:
    if not is_uuid(value):
        raise ServiceError("invalid_identifier", f"The {field} is not valid.", status_code=400)
    return value.lower()


def safe_component(value: str, field: str = "name") -> str:
    if not value or not _SAFE_COMPONENT_RE.fullmatch(value) or value in {".", ".."}:
        raise ServiceError("invalid_path", f"The {field} is invalid.", status_code=400)
    return value


def safe_join(root: str | Path, *parts: str) -> Path:
    """Join inside root and reject traversal or symlink escapes."""
    root_path = Path(root).resolve()
    candidate = root_path.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - unusual filesystem error
        raise ServiceError("invalid_path", "The requested path is invalid.", status_code=400) from exc
    if resolved != root_path and root_path not in resolved.parents:
        raise ServiceError("invalid_path", "The requested path is outside the storage area.", status_code=400)
    return resolved


def relative_to_root(root: str | Path, path: str | Path) -> str:
    root_path = Path(root).resolve()
    resolved = Path(path).resolve()
    if resolved != root_path and root_path not in resolved.parents:
        raise ServiceError("invalid_path", "The requested path is outside the storage area.", status_code=400)
    return str(resolved.relative_to(root_path))


def resolve_relative(root: str | Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ServiceError("invalid_path", "The requested path is invalid.", status_code=400)
    return safe_join(root, *[part for part in Path(relative).parts if part not in {"", "."}])


def atomic_write_text(path: str | Path, content: str) -> None:
    target = Path(path)
    ensure_dir(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        raise ServiceError("storage_corrupt", "A stored data file could not be read.", status_code=500) from exc


def check_disk_space(path: str | Path, minimum_mb: int) -> None:
    ensure_dir(path)
    usage = shutil.disk_usage(str(path))
    if usage.free < minimum_mb * 1024 * 1024:
        raise ServiceError(
            "insufficient_disk_space",
            "There is not enough free disk space to complete this operation.",
            status_code=507,
            details={"free_mb": usage.free // (1024 * 1024)},
        )


def remove_tree(path: str | Path) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target, ignore_errors=False)


def storage_bytes(path: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total
