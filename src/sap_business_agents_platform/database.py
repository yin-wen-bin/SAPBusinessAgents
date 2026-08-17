from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from .models import (
    DraftRecord,
    RunCreate,
    RunEvent,
    RunMode,
    RunRecord,
    RunResult,
    RunStatus,
    WorkflowDraftRecord,
    utc_now,
)


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_id TEXT,
                    query TEXT,
                    input_json TEXT NOT NULL,
                    plan_json TEXT,
                    result_json TEXT,
                    thread_id TEXT,
                    error_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    path TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_drafts (
                    draft_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    workflow_json TEXT NOT NULL,
                    path TEXT NOT NULL,
                    thread_id TEXT,
                    validation_run_id TEXT,
                    validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workflow_revisions (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    workflow_json TEXT NOT NULL,
                    diff_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS workflow_run_snapshots (
                    run_id TEXT PRIMARY KEY,
                    workflow_json TEXT NOT NULL,
                    validation_draft_id TEXT,
                    validation_revision INTEGER,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            for name in ("workflow_id", "parent_run_id", "node_id"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {name} TEXT")

    def create_run(
        self,
        run_id: str,
        request: RunCreate,
        *,
        parent_run_id: str | None = None,
        node_id: str | None = None,
    ) -> RunRecord:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runs
                (run_id, mode, status, agent_id, workflow_id, parent_run_id, node_id,
                 query, input_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    request.mode.value,
                    RunStatus.queued.value,
                    request.agent_id,
                    request.workflow_id,
                    parent_run_id,
                    node_id,
                    request.query,
                    _dump(request.input),
                    created_at,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row)

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def update_run(self, run_id: str, **values: Any) -> RunRecord:
        allowed = {
            "status", "query", "input_json", "plan_json", "result_json", "thread_id",
            "error_json", "cancel_requested", "started_at", "completed_at",
        }
        encoded: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"Unsupported run field: {key}")
            if key.endswith("_json") and value is not None and not isinstance(value, str):
                value = _dump(value)
            if isinstance(value, RunStatus):
                value = value.value
            if key == "cancel_requested":
                value = int(bool(value))
            encoded[key] = value
        if not encoded:
            return self.get_run(run_id)
        assignments = ", ".join(f"{name} = ?" for name in encoded)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", [*encoded.values(), run_id]
            )
            if cursor.rowcount == 0:
                raise KeyError(run_id)
        return self.get_run(run_id)

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any] | None = None) -> RunEvent:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO events (run_id, event_type, data_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event_type, _dump(data or {}), created_at),
            )
            sequence = int(cursor.lastrowid)
        return RunEvent(sequence=sequence, run_id=run_id, type=event_type, data=data or {}, created_at=created_at)

    def events_after(self, run_id: str, sequence: int = 0) -> list[RunEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence",
                (run_id, max(0, sequence)),
            ).fetchall()
        return [
            RunEvent(
                sequence=int(row["sequence"]),
                run_id=row["run_id"],
                type=row["event_type"],
                data=_load(row["data_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def save_draft(self, draft: DraftRecord) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO drafts
                (draft_id, run_id, status, path, validation_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    draft.draft_id,
                    draft.run_id,
                    draft.status,
                    draft.path,
                    _dump(draft.validation),
                    draft.created_at,
                ),
            )

    def save_workflow_snapshot(
        self,
        run_id: str,
        workflow: dict[str, Any],
        *,
        draft_id: str | None = None,
        revision: int | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO workflow_run_snapshots
                (run_id, workflow_json, validation_draft_id, validation_revision)
                VALUES (?, ?, ?, ?)""",
                (run_id, _dump(workflow), draft_id, revision),
            )

    def get_workflow_snapshot(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT workflow_json FROM workflow_run_snapshots WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _load(row["workflow_json"], {})

    def save_workflow_draft(
        self,
        draft: WorkflowDraftRecord,
        *,
        diff: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO workflow_drafts
                (draft_id, status, revision, workflow_json, path, thread_id,
                 validation_run_id, validation_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft.draft_id,
                    draft.status,
                    draft.revision,
                    _dump(draft.workflow),
                    draft.path,
                    draft.thread_id,
                    draft.validation_run_id,
                    _dump(draft.validation),
                    draft.created_at,
                    draft.updated_at,
                ),
            )
            if diff is not None:
                connection.execute(
                    """INSERT OR REPLACE INTO workflow_revisions
                    (draft_id, revision, workflow_json, diff_json, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        draft.draft_id,
                        draft.revision,
                        _dump(draft.workflow),
                        _dump(diff),
                        draft.updated_at,
                    ),
                )

    def get_workflow_draft(self, draft_id: str) -> WorkflowDraftRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return WorkflowDraftRecord(
            draft_id=row["draft_id"],
            status=row["status"],
            revision=int(row["revision"]),
            workflow=_load(row["workflow_json"], {}),
            path=row["path"],
            thread_id=row["thread_id"],
            validation_run_id=row["validation_run_id"],
            validation=_load(row["validation_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_workflow_revisions(self, draft_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT revision, diff_json, created_at FROM workflow_revisions
                WHERE draft_id = ? ORDER BY revision""",
                (draft_id,),
            ).fetchall()
        return [
            {
                "revision": int(row["revision"]),
                "diff": _load(row["diff_json"], []),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_draft(self, draft_id: str) -> DraftRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return DraftRecord(
            draft_id=row["draft_id"],
            run_id=row["run_id"],
            status=row["status"],
            path=row["path"],
            validation=_load(row["validation_json"], {}),
            created_at=row["created_at"],
        )


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    result_data = _load(row["result_json"], None)
    return RunRecord(
        run_id=row["run_id"],
        mode=RunMode(row["mode"]),
        status=RunStatus(row["status"]),
        agent_id=row["agent_id"],
        workflow_id=row["workflow_id"],
        parent_run_id=row["parent_run_id"],
        node_id=row["node_id"],
        query=row["query"],
        input=_load(row["input_json"], {}),
        plan=_load(row["plan_json"], None),
        result=RunResult.model_validate(result_data) if result_data else None,
        thread_id=row["thread_id"],
        error=_load(row["error_json"], None),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )
