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
    RunProgress,
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
                    completed_at TEXT,
                    progress_json TEXT
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
                CREATE TABLE IF NOT EXISTS harness_tool_calls (
                    call_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    safe_input_json TEXT NOT NULL,
                    output_json TEXT,
                    evidence_ref TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(run_id, tool_name, request_hash),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS harness_calls_run
                    ON harness_tool_calls(run_id, created_at);
                CREATE TABLE IF NOT EXISTS harness_state (
                    run_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS harness_tool_candidates (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, candidate_id),
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
            if "progress_json" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN progress_json TEXT")

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
                 query, input_json, created_at, progress_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    _dump(RunProgress(updated_at=created_at)),
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

    def list_recoverable_free_query_runs(self) -> list[RunRecord]:
        statuses = (
            RunStatus.queued.value,
            RunStatus.planning.value,
            RunStatus.validating.value,
            RunStatus.running.value,
        )
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM runs
                WHERE mode = ? AND status IN ({placeholders})
                ORDER BY created_at""",
                (RunMode.free_query.value, *statuses),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def update_run(self, run_id: str, **values: Any) -> RunRecord:
        allowed = {
            "status", "query", "input_json", "plan_json", "result_json", "thread_id",
            "error_json", "cancel_requested", "started_at", "completed_at", "progress_json",
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

    def set_progress(
        self,
        run_id: str,
        *,
        phase: str | None = None,
        state: str | None = None,
        current_step_id: str | None = None,
        current_node_id: str | None = None,
        current_tool: str | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        determinate: bool | None = None,
    ) -> RunEvent:
        """Persist the current activity and its SSE sequence atomically."""

        created_at = utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, progress_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = _load(row["progress_json"], None)
            progress = (
                RunProgress.model_validate(current)
                if isinstance(current, dict)
                else _legacy_progress(RunStatus(row["status"]), created_at)
            )
            updates: dict[str, Any] = {"updated_at": created_at}
            for key, value in {
                "phase": phase,
                "state": state,
                "completed_units": completed_units,
                "total_units": total_units,
                "determinate": determinate,
            }.items():
                if value is not None:
                    updates[key] = value
            # None is a meaningful reset for current activity identifiers.
            updates.update(
                {
                    "current_step_id": current_step_id,
                    "current_node_id": current_node_id,
                    "current_tool": current_tool,
                }
            )
            progress = RunProgress.model_validate(
                {**progress.model_dump(mode="json"), **updates}
            )
            cursor = connection.execute(
                "INSERT INTO events (run_id, event_type, data_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, "progress_changed", "{}", created_at),
            )
            sequence = int(cursor.lastrowid)
            progress = progress.model_copy(update={"event_sequence": sequence})
            payload = {"progress": progress.model_dump(mode="json")}
            connection.execute(
                "UPDATE events SET data_json = ? WHERE sequence = ?",
                (_dump(payload), sequence),
            )
            connection.execute(
                "UPDATE runs SET progress_json = ? WHERE run_id = ?",
                (_dump(progress), run_id),
            )
        return RunEvent(
            sequence=sequence,
            run_id=run_id,
            type="progress_changed",
            data=payload,
            created_at=created_at,
        )

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

    def save_harness_state(self, run_id: str, state: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO harness_state (run_id, state_json, updated_at)
                VALUES (?, ?, ?)""",
                (run_id, _dump(state), utc_now()),
            )

    def get_harness_state(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM harness_state WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _load(row["state_json"], {}) if row is not None else {}

    def save_harness_tool_candidates(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> None:
        with self._lock, self._connect() as connection:
            for candidate in candidates:
                candidate_id = str(candidate.get("candidate_id") or "")
                if not candidate_id:
                    continue
                connection.execute(
                    """INSERT OR REPLACE INTO harness_tool_candidates
                    (run_id, candidate_id, candidate_json, updated_at)
                    VALUES (?, ?, ?, ?)""",
                    (run_id, candidate_id, _dump(candidate), utc_now()),
                )

    def list_harness_tool_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT candidate_json FROM harness_tool_candidates
                WHERE run_id = ? ORDER BY candidate_id""",
                (run_id,),
            ).fetchall()
        return [
            value
            for row in rows
            if isinstance((value := _load(row["candidate_json"], None)), dict)
        ]

    def begin_harness_tool_call(
        self,
        *,
        call_id: str,
        run_id: str,
        tool_name: str,
        request_hash: str,
        safe_input: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Create a call record, returning a completed identical call for idempotency."""
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM harness_tool_calls
                WHERE run_id = ? AND tool_name = ? AND request_hash = ?""",
                (run_id, tool_name, request_hash),
            ).fetchone()
            if existing is not None:
                return _harness_call_from_row(existing)
            connection.execute(
                """INSERT INTO harness_tool_calls
                (call_id, run_id, tool_name, request_hash, status, safe_input_json, created_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)""",
                (call_id, run_id, tool_name, request_hash, _dump(safe_input), utc_now()),
            )
        return None

    def complete_harness_tool_call(
        self,
        call_id: str,
        *,
        status: str,
        output: dict[str, Any],
        evidence_ref: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE harness_tool_calls
                SET status = ?, output_json = ?, evidence_ref = ?, completed_at = ?
                WHERE call_id = ?""",
                (status, _dump(output), evidence_ref, utc_now(), call_id),
            )

    def list_harness_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM harness_tool_calls WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [_harness_call_from_row(row) for row in rows]

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
        progress=(
            RunProgress.model_validate(_load(row["progress_json"], {}))
            if "progress_json" in row.keys() and row["progress_json"]
            else _legacy_progress(RunStatus(row["status"]), row["completed_at"] or row["created_at"])
        ),
    )


def _legacy_progress(status: RunStatus, updated_at: str) -> RunProgress:
    if status == RunStatus.completed:
        return RunProgress(
            phase="preparing_result", state="completed", completed_units=1,
            total_units=1, determinate=True, updated_at=updated_at,
        )
    if status == RunStatus.inconclusive:
        return RunProgress(
            phase="preparing_result", state="inconclusive", completed_units=1,
            total_units=1, determinate=True, updated_at=updated_at,
        )
    if status in {RunStatus.failed, RunStatus.cancelled}:
        return RunProgress(
            phase="preparing", state=status.value, determinate=False, updated_at=updated_at,
        )
    if status == RunStatus.waiting_input:
        return RunProgress(phase="preparing", state="waiting_input", updated_at=updated_at)
    phase = "received" if status == RunStatus.queued else "preparing"
    return RunProgress(phase=phase, state="active", updated_at=updated_at)


def _harness_call_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "call_id": row["call_id"],
        "run_id": row["run_id"],
        "tool_name": row["tool_name"],
        "request_hash": row["request_hash"],
        "status": row["status"],
        "safe_input": _load(row["safe_input_json"], {}),
        "output": _load(row["output_json"], None),
        "evidence_ref": row["evidence_ref"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }
