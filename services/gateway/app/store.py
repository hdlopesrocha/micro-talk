"""SQLite-backed job metadata store.

Large text, audio and subtitle payloads live on disk. SQLite stores only the
public job state (step progress, artifact metadata, warnings) so the SSE stream
and job list stay small and fast.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from micro_common.storage import ensure_dir, is_uuid

from .config import settings

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}


class JobStore:
    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path or settings.resolved_database_path
        ensure_dir(Path(self.database_path).parent)
        self._init_lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._init_lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    rq_job_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        try:
            state = json.loads(row["state_json"])
            config = json.loads(row["config_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("A stored gateway job is corrupt.") from exc
        state.setdefault("id", row["id"])
        state.setdefault("status", row["status"])
        state.setdefault("title", row["title"])
        state["cancel_requested"] = bool(row["cancel_requested"])
        state["rq_job_id"] = row["rq_job_id"]
        state["created_at"] = row["created_at"]
        state["updated_at"] = row["updated_at"]
        state["config"] = config
        return state

    def create(self, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
        if not is_uuid(job_id):
            raise ValueError("Invalid job id")
        title = str(state.get("title") or "Untitled workflow")[:200]
        config = state.get("config") or {}
        now = float(state.get("created_at") or time.time())
        state["created_at"] = now
        state["updated_at"] = now
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs
                    (id, status, title, config_json, state_json, cancel_requested,
                     rq_job_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)
                """,
                (
                    job_id,
                    str(state.get("status") or "queued"),
                    title,
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(state, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get(job_id) or state

    def get(self, job_id: str) -> dict[str, Any] | None:
        if not is_uuid(job_id):
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def get_raw_fields(self, job_id: str) -> dict[str, Any] | None:
        if not is_uuid(job_id):
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, cancel_requested, rq_job_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "rq_job_id": row["rq_job_id"],
        }

    def save_state(self, state: dict[str, Any], *, rq_job_id: str | None = None) -> None:
        job_id = str(state.get("id") or "")
        if not is_uuid(job_id):
            raise ValueError("Invalid job id")
        now = time.time()
        state["updated_at"] = now
        status = str(state.get("status") or "running")
        with self._connect() as connection:
            if rq_job_id is not None:
                connection.execute(
                    """
                    UPDATE jobs SET status = ?, state_json = ?, updated_at = ?, rq_job_id = ?
                    WHERE id = ?
                    """,
                    (status, json.dumps(state, ensure_ascii=False), now, rq_job_id, job_id),
                )
            else:
                connection.execute(
                    "UPDATE jobs SET status = ?, state_json = ?, updated_at = ? WHERE id = ?",
                    (status, json.dumps(state, ensure_ascii=False), now, job_id),
                )

    def mutate_state(
        self, job_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any] | None]
    ) -> dict[str, Any] | None:
        """Atomically read-modify-write a public state document."""
        if not is_uuid(job_id):
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            state = json.loads(row["state_json"])
            updated = mutator(state)
            if updated is not None:
                state = updated
            now = time.time()
            state["updated_at"] = now
            status = str(state.get("status") or row["status"])
            connection.execute(
                "UPDATE jobs SET status = ?, state_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(state, ensure_ascii=False), now, job_id),
            )
            state = self._decode(
                connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )
            return state

    def set_rq_job(self, job_id: str, rq_job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET rq_job_id = ?, updated_at = ? WHERE id = ?",
                (rq_job_id, time.time(), job_id),
            )

    def request_cancel(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                (time.time(), job_id),
            )

    def mark_cancelled(self, job_id: str, message: str = "Workflow cancelled by the user.") -> None:
        def mutate(state: dict[str, Any]) -> None:
            state["status"] = "cancelled"
            state["message"] = message
            state["finished_at"] = time.time()
            state["progress"] = min(99, int(state.get("progress") or 0))
            for step in state.get("steps") or []:
                if step.get("status") == "running":
                    step["status"] = "cancelled"
                    step["message"] = message
                    step["finished_at"] = time.time()
                elif step.get("status") == "pending":
                    step["status"] = "skipped"
            state["cancel_requested"] = True

        self.mutate_state(job_id, mutate)

    def list_jobs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM jobs {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._decode(row) for row in rows], int(total)

    def count_active(self) -> int:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM jobs WHERE status IN ({placeholders})",
                tuple(ACTIVE_STATUSES),
            ).fetchone()
        return int(row[0]) if row else 0

    def delete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def expired_jobs(self, retention_hours: int) -> list[dict[str, Any]]:
        cutoff = time.time() - retention_hours * 3600
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) AND updated_at < ?",
                (*TERMINAL_STATUSES, cutoff),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def stale_active_jobs(self, older_than_seconds: int) -> list[dict[str, Any]]:
        cutoff = time.time() - older_than_seconds
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE status IN ({placeholders}) AND updated_at < ?",
                (*ACTIVE_STATUSES, cutoff),
            ).fetchall()
        return [self._decode(row) for row in rows]
