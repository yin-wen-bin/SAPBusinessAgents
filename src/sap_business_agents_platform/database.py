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
    RuntimeSnapshot,
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
                    progress_json TEXT,
                    runtime_json TEXT
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
                CREATE TABLE IF NOT EXISTS free_query_sessions (
                    session_id TEXT PRIMARY KEY,
                    original_query TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    thread_id TEXT,
                    status TEXT NOT NULL,
                    current_iteration INTEGER NOT NULL,
                    accepted_iteration INTEGER,
                    accepted_result_digest TEXT,
                    accepted_at TEXT,
                    draft_id TEXT,
                    pending_feedback_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS free_query_feedback_requests (
                    feedback_request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    base_iteration INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    feedback TEXT NOT NULL,
                    feedback_type_hint TEXT,
                    locale TEXT NOT NULL,
                    supplemental_input TEXT,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    run_id TEXT,
                    event_sequence INTEGER NOT NULL DEFAULT 0,
                    error_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES free_query_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS free_query_feedback_session
                    ON free_query_feedback_requests(session_id, created_at);
                CREATE TABLE IF NOT EXISTS free_query_feedback_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    feedback_request_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(feedback_request_id)
                        REFERENCES free_query_feedback_requests(feedback_request_id)
                );
                CREATE INDEX IF NOT EXISTS free_query_feedback_event_sequence
                    ON free_query_feedback_events(feedback_request_id, sequence);
                CREATE TABLE IF NOT EXISTS free_query_iterations (
                    session_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    parent_iteration INTEGER,
                    feedback TEXT NOT NULL DEFAULT '',
                    feedback_type TEXT,
                    execution_action TEXT NOT NULL,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    plan_digest TEXT,
                    result_digest TEXT,
                    source_run_id TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(session_id, iteration),
                    FOREIGN KEY(session_id) REFERENCES free_query_sessions(session_id),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS free_query_iterations_run
                    ON free_query_iterations(run_id);
                CREATE TABLE IF NOT EXISTS free_query_evidence_links (
                    run_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    source_evidence_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, evidence_ref),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    path TEXT NOT NULL,
                    origin_json TEXT NOT NULL DEFAULT '{}',
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
                    composition_json TEXT NOT NULL DEFAULT '{}',
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
                CREATE TABLE IF NOT EXISTS workflow_conversation_turns (
                    draft_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    parent_turn INTEGER,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    user_message TEXT,
                    feedback_type TEXT,
                    action TEXT,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    base_revision INTEGER,
                    result_revision INTEGER,
                    proposal_digest TEXT,
                    workflow_hash TEXT,
                    diff_json TEXT NOT NULL DEFAULT '[]',
                    validation_run_id TEXT,
                    validation_report_digest TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(draft_id, turn),
                    FOREIGN KEY(draft_id) REFERENCES workflow_drafts(draft_id)
                );
                CREATE INDEX IF NOT EXISTS workflow_conversation_validation
                    ON workflow_conversation_turns(validation_run_id);
                CREATE TABLE IF NOT EXISTS workflow_management_events (
                    event_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_version TEXT,
                    to_version TEXT,
                    workflow_hash TEXT,
                    branch TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS workflow_management_events_workflow
                    ON workflow_management_events(workflow_id, created_at);
                CREATE TABLE IF NOT EXISTS role_matching_sessions (
                    session_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    paths_json TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    thread_id TEXT,
                    consent_to_runtime INTEGER NOT NULL,
                    current_revision INTEGER NOT NULL DEFAULT 0,
                    active_job_id TEXT,
                    event_sequence INTEGER NOT NULL DEFAULT 0,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS role_matching_documents (
                    session_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    modified_at TEXT NOT NULL,
                    paths_json TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issue_json TEXT,
                    chunk_count INTEGER NOT NULL,
                    cache_path TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    excluded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(session_id, document_id),
                    FOREIGN KEY(session_id) REFERENCES role_matching_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS role_matching_documents_hash
                    ON role_matching_documents(session_id, sha256);
                CREATE TABLE IF NOT EXISTS role_matching_chunks (
                    session_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    locator_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    cache_path TEXT NOT NULL,
                    PRIMARY KEY(session_id, document_id, chunk_id)
                );
                CREATE TABLE IF NOT EXISTS role_matching_revisions (
                    session_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    parent_revision INTEGER,
                    mode TEXT NOT NULL,
                    catalog_digest TEXT NOT NULL,
                    document_ids_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, revision)
                );
                CREATE TABLE IF NOT EXISTS role_matching_turns (
                    session_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    base_revision INTEGER,
                    result_revision INTEGER,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    rematch_mode TEXT,
                    added_paths_json TEXT NOT NULL DEFAULT '[]',
                    excluded_document_ids_json TEXT NOT NULL DEFAULT '[]',
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(session_id, turn)
                );
                CREATE TABLE IF NOT EXISTS role_matching_jobs (
                    session_id TEXT NOT NULL,
                    job_id TEXT PRIMARY KEY,
                    turn INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS role_matching_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS role_matching_events_sequence
                    ON role_matching_events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS execution_jobs (
                    job_id TEXT PRIMARY KEY,
                    workload_class TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS execution_jobs_claim
                    ON execution_jobs(workload_class, status, priority, created_at);
                CREATE INDEX IF NOT EXISTS execution_jobs_subject
                    ON execution_jobs(subject_id, created_at);
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
            if "runtime_json" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN runtime_json TEXT")
            draft_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(drafts)").fetchall()
            }
            if "origin_json" not in draft_columns:
                connection.execute("ALTER TABLE drafts ADD COLUMN origin_json TEXT NOT NULL DEFAULT '{}'")
            workflow_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(workflow_drafts)").fetchall()
            }
            if "composition_json" not in workflow_columns:
                connection.execute(
                    "ALTER TABLE workflow_drafts ADD COLUMN composition_json TEXT NOT NULL DEFAULT '{}'"
                )
            feedback_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(free_query_feedback_requests)"
                ).fetchall()
            }
            if "event_sequence" not in feedback_columns:
                connection.execute(
                    "ALTER TABLE free_query_feedback_requests "
                    "ADD COLUMN event_sequence INTEGER NOT NULL DEFAULT 0"
                )

    def create_run(
        self,
        run_id: str,
        request: RunCreate,
        *,
        parent_run_id: str | None = None,
        node_id: str | None = None,
        runtime: RuntimeSnapshot | dict[str, Any] | None = None,
    ) -> RunRecord:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runs
                (run_id, mode, status, agent_id, workflow_id, parent_run_id, node_id,
                 query, input_json, created_at, progress_json, runtime_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    _dump(runtime) if runtime is not None else None,
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

    def workflow_business_run_count(self, workflow_id: str) -> int:
        """Count user-started workflow runs, excluding authoring validation snapshots."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count
                FROM runs
                LEFT JOIN workflow_run_snapshots snapshots ON snapshots.run_id = runs.run_id
                WHERE runs.mode = ? AND runs.workflow_id = ?
                  AND snapshots.validation_draft_id IS NULL""",
                (RunMode.workflow.value, workflow_id),
            ).fetchone()
        return int(row["count"] if row else 0)

    def list_recoverable_runs(self) -> list[RunRecord]:
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
                WHERE status IN ({placeholders})
                ORDER BY created_at""",
                statuses,
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def list_recoverable_free_query_runs(self) -> list[RunRecord]:
        return [
            record
            for record in self.list_recoverable_runs()
            if record.mode == RunMode.free_query
        ]

    def create_free_query_session(
        self,
        *,
        session_id: str,
        run_id: str,
        original_query: str,
        runtime: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO free_query_sessions
                (session_id, original_query, runtime_json, thread_id, status,
                 current_iteration, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', 1, ?, ?)""",
                (session_id, original_query, _dump(runtime), thread_id, created_at, created_at),
            )
            connection.execute(
                """INSERT INTO free_query_iterations
                (session_id, iteration, run_id, parent_iteration, feedback,
                 execution_action, decision_json, created_at)
                VALUES (?, 1, ?, NULL, '', 'initial', '{}', ?)""",
                (session_id, run_id, created_at),
            )
        return self.get_free_query_session(session_id)

    def create_free_query_iteration(
        self,
        *,
        session_id: str,
        iteration: int,
        run_id: str,
        parent_iteration: int,
        feedback: str,
        feedback_type: str,
        execution_action: str,
        decision: dict[str, Any],
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO free_query_iterations
                (session_id, iteration, run_id, parent_iteration, feedback,
                 feedback_type, execution_action, decision_json, source_run_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, iteration, run_id, parent_iteration, feedback,
                    feedback_type, execution_action, _dump(decision), source_run_id, created_at,
                ),
            )
            connection.execute(
                """UPDATE free_query_sessions
                SET status = 'running', current_iteration = ?, pending_feedback_json = NULL,
                    updated_at = ? WHERE session_id = ?""",
                (iteration, created_at, session_id),
            )
        return self.get_free_query_iteration(session_id, iteration)

    def get_free_query_session(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM free_query_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return _free_query_session_from_row(row)

    def get_free_query_session_by_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT sessions.* FROM free_query_sessions sessions
                JOIN free_query_iterations iterations
                  ON iterations.session_id = sessions.session_id
                WHERE iterations.run_id = ?""",
                (run_id,),
            ).fetchone()
        return _free_query_session_from_row(row) if row is not None else None

    def get_free_query_iteration(self, session_id: str, iteration: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM free_query_iterations
                WHERE session_id = ? AND iteration = ?""",
                (session_id, iteration),
            ).fetchone()
        if row is None:
            raise KeyError((session_id, iteration))
        return _free_query_iteration_from_row(row)

    def get_free_query_iteration_by_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM free_query_iterations WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _free_query_iteration_from_row(row) if row is not None else None

    def list_free_query_iterations(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM free_query_iterations
                WHERE session_id = ? ORDER BY iteration""",
                (session_id,),
            ).fetchall()
        return [_free_query_iteration_from_row(row) for row in rows]

    def update_free_query_session(self, session_id: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "thread_id", "status", "current_iteration", "accepted_iteration",
            "accepted_result_digest", "accepted_at", "draft_id", "pending_feedback_json",
        }
        encoded: dict[str, Any] = {}
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"Unsupported free-query session field: {key}")
            if key == "pending_feedback_json" and value is not None and not isinstance(value, str):
                value = _dump(value)
            encoded[key] = value
        encoded["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in encoded)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE free_query_sessions SET {assignments} WHERE session_id = ?",
                [*encoded.values(), session_id],
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
        return self.get_free_query_session(session_id)

    def create_free_query_feedback_request(
        self,
        *,
        feedback_request_id: str,
        session_id: str,
        base_iteration: int,
        feedback: str,
        feedback_type_hint: str | None,
        locale: str,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO free_query_feedback_requests
                (feedback_request_id, session_id, base_iteration, status, phase,
                 feedback, feedback_type_hint, locale, decision_json,
                 created_at, updated_at)
                VALUES (?, ?, ?, 'queued', 'received', ?, ?, ?, '{}', ?, ?)""",
                (
                    feedback_request_id,
                    session_id,
                    base_iteration,
                    feedback,
                    feedback_type_hint,
                    locale,
                    created_at,
                    created_at,
                ),
            )
            event_cursor = connection.execute(
                """INSERT INTO free_query_feedback_events
                (feedback_request_id, event_type, data_json, created_at)
                VALUES (?, 'feedback_received', ?, ?)""",
                (
                    feedback_request_id,
                    _dump(
                        {
                            "session_id": session_id,
                            "base_iteration": base_iteration,
                        }
                    ),
                    created_at,
                ),
            )
            connection.execute(
                """UPDATE free_query_feedback_requests SET event_sequence = ?
                WHERE feedback_request_id = ?""",
                (int(event_cursor.lastrowid), feedback_request_id),
            )
            connection.execute(
                """UPDATE free_query_sessions
                SET status = 'reviewing_feedback',
                    pending_feedback_json = ?, updated_at = ?
                WHERE session_id = ?""",
                (
                    _dump(
                        {
                            "feedback_request_id": feedback_request_id,
                            "base_iteration": base_iteration,
                            "status": "queued",
                            "phase": "received",
                        }
                    ),
                    created_at,
                    session_id,
                ),
            )
        return self.get_free_query_feedback_request(feedback_request_id)

    def get_free_query_feedback_request(self, feedback_request_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM free_query_feedback_requests WHERE feedback_request_id = ?",
                (feedback_request_id,),
            ).fetchone()
        if row is None:
            raise KeyError(feedback_request_id)
        return _free_query_feedback_request_from_row(row)

    def active_free_query_feedback_request(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM free_query_feedback_requests
                WHERE session_id = ? AND status IN ('queued', 'reviewing', 'waiting_input')
                ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        return _free_query_feedback_request_from_row(row) if row is not None else None

    def list_recoverable_free_query_feedback_requests(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM free_query_feedback_requests
                WHERE status IN ('queued', 'reviewing') ORDER BY created_at"""
            ).fetchall()
        return [_free_query_feedback_request_from_row(row) for row in rows]

    def update_free_query_feedback_request(
        self,
        feedback_request_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        decision: dict[str, Any] | None = None,
        run_id: str | None = None,
        error: dict[str, Any] | None = None,
        supplemental_input: str | None = None,
        cancel_requested: bool | None = None,
        completed: bool = False,
        event_type: str | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated_at = utc_now()
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [updated_at]
        for name, value in (
            ("status", status),
            ("phase", phase),
            ("run_id", run_id),
            ("supplemental_input", supplemental_input),
        ):
            if value is not None:
                assignments.append(f"{name} = ?")
                values.append(value)
        if decision is not None:
            assignments.append("decision_json = ?")
            values.append(_dump(decision))
        if error is not None:
            assignments.append("error_json = ?")
            values.append(_dump(error))
        if cancel_requested is not None:
            assignments.append("cancel_requested = ?")
            values.append(1 if cancel_requested else 0)
        if completed:
            assignments.append("completed_at = ?")
            values.append(updated_at)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE free_query_feedback_requests SET {', '.join(assignments)} "
                "WHERE feedback_request_id = ?",
                [*values, feedback_request_id],
            )
            if cursor.rowcount == 0:
                raise KeyError(feedback_request_id)
            if event_type:
                event_cursor = connection.execute(
                    """INSERT INTO free_query_feedback_events
                    (feedback_request_id, event_type, data_json, created_at)
                    VALUES (?, ?, ?, ?)""",
                    (feedback_request_id, event_type, _dump(event_data or {}), updated_at),
                )
                connection.execute(
                    """UPDATE free_query_feedback_requests SET event_sequence = ?
                    WHERE feedback_request_id = ?""",
                    (int(event_cursor.lastrowid), feedback_request_id),
                )
            request = connection.execute(
                "SELECT * FROM free_query_feedback_requests WHERE feedback_request_id = ?",
                (feedback_request_id,),
            ).fetchone()
            session_id = str(request["session_id"])
            connection.execute(
                """UPDATE free_query_sessions
                SET pending_feedback_json = ?, updated_at = ? WHERE session_id = ?""",
                (
                    _dump(
                        {
                            "feedback_request_id": feedback_request_id,
                            "base_iteration": int(request["base_iteration"]),
                            "status": str(request["status"]),
                            "phase": str(request["phase"]),
                            "run_id": request["run_id"],
                            "decision": _load(request["decision_json"], {}),
                            "error": _load(request["error_json"], None),
                        }
                    ),
                    updated_at,
                    session_id,
                ),
            )
        return self.get_free_query_feedback_request(feedback_request_id)

    def free_query_feedback_events_after(
        self, feedback_request_id: str, sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM free_query_feedback_events
                WHERE feedback_request_id = ? AND sequence > ? ORDER BY sequence""",
                (feedback_request_id, max(0, sequence)),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "feedback_request_id": str(row["feedback_request_id"]),
                "type": str(row["event_type"]),
                "data": _load(row["data_json"], {}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def list_execution_jobs(self, *, statuses: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        query = "SELECT * FROM execution_jobs"
        values: list[Any] = []
        if statuses:
            query += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            values.extend(statuses)
        query += " ORDER BY priority, created_at"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_execution_job_from_row(row) for row in rows]

    def latest_execution_job_for_subject(
        self,
        subject_id: str,
        *,
        statuses: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM execution_jobs WHERE subject_id = ?"
        values: list[Any] = [subject_id]
        if statuses:
            query += f" AND status IN ({','.join('?' for _ in statuses)})"
            values.extend(statuses)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, values).fetchone()
        return _execution_job_from_row(row) if row is not None else None

    def create_execution_job(
        self,
        *,
        job_id: str,
        workload_class: str,
        subject_id: str,
        priority: int = 100,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO execution_jobs
                (job_id, workload_class, subject_id, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
                (job_id, workload_class, subject_id, priority, created_at, created_at),
            )
        return self.get_execution_job(job_id)

    def get_execution_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _execution_job_from_row(row)

    def claim_execution_job(
        self, job_id: str, *, lease_owner: str, lease_expires_at: str
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE execution_jobs
                SET status = 'running', attempt = attempt + 1, lease_owner = ?,
                    lease_expires_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'queued'""",
                (lease_owner, lease_expires_at, now, now, job_id),
            )
        return self.get_execution_job(job_id) if cursor.rowcount else None

    def heartbeat_execution_job(
        self, job_id: str, *, lease_owner: str, lease_expires_at: str
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE execution_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'running' AND lease_owner = ?""",
                (now, lease_expires_at, now, job_id, lease_owner),
            )

    def finish_execution_job(
        self, job_id: str, *, status: str, error: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE execution_jobs
                SET status = ?, error_json = ?, lease_owner = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE job_id = ?""",
                (status, _dump(error) if error is not None else None, now, now, job_id),
            )
        return self.get_execution_job(job_id)

    def requeue_execution_job(self, job_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE execution_jobs SET status = 'queued', lease_owner = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE job_id = ?""",
                (now, job_id),
            )
        return self.get_execution_job(job_id)

    def execution_queue_position(self, job_id: str) -> int | None:
        job = self.get_execution_job(job_id)
        if job["status"] != "queued":
            return 0 if job["status"] == "running" else None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM execution_jobs
                WHERE workload_class = ? AND status = 'queued'
                  AND (priority < ? OR (priority = ? AND created_at <= ?))""",
                (
                    job["workload_class"],
                    job["priority"],
                    job["priority"],
                    job["created_at"],
                ),
            ).fetchone()
        return int(row["count"])

    def create_role_matching_session(self, item: dict[str, Any]) -> dict[str, Any]:
        now = item.get("created_at") or utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO role_matching_sessions
                (session_id, status, phase, locale, paths_json, runtime_json,
                 thread_id, consent_to_runtime, current_revision, active_job_id,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, ?, ?)""",
                (
                    item["session_id"], item.get("status", "queued"),
                    item.get("phase", "queued"), item.get("locale", "zh"),
                    _dump(item.get("paths") or []), _dump(item.get("runtime") or {}),
                    item.get("thread_id"), now, now,
                ),
            )
        return self.get_role_matching_session(str(item["session_id"]))

    def get_role_matching_session(self, session_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM role_matching_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        result = _role_matching_session_from_row(row)
        result["documents"] = len(self.list_role_matching_documents(session_id))
        result["revisions"] = len(self.list_role_matching_revisions(session_id))
        return result

    def update_role_matching_session(self, session_id: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "status": "status", "phase": "phase", "paths": "paths_json",
            "runtime": "runtime_json", "thread_id": "thread_id",
            "current_revision": "current_revision", "active_job_id": "active_job_id",
            "error": "error_json", "completed_at": "completed_at",
        }
        assignments: list[str] = []
        parameters: list[Any] = []
        for name, value in values.items():
            column = allowed.get(name)
            if not column:
                continue
            assignments.append(f"{column} = ?")
            parameters.append(_dump(value) if name in {"paths", "runtime", "error"} and value is not None else value)
        assignments.append("updated_at = ?")
        parameters.append(utc_now())
        parameters.append(session_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE role_matching_sessions SET {', '.join(assignments)} WHERE session_id = ?",
                parameters,
            )
        return self.get_role_matching_session(session_id)

    def save_role_matching_documents(
        self, session_id: str, documents: list[dict[str, Any]], *, excluded: set[str] | None = None
    ) -> None:
        excluded = excluded or set()
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE role_matching_documents SET active = 0 WHERE session_id = ?", (session_id,))
            for document in documents:
                connection.execute(
                    """INSERT OR REPLACE INTO role_matching_documents
                    (session_id, document_id, sha256, name, extension, size_bytes,
                     modified_at, paths_json, parser_version, status, issue_json,
                     chunk_count, cache_path, active, excluded)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        session_id, document["document_id"], document["sha256"], document["name"],
                        document["extension"], int(document["size_bytes"]),
                        str(document.get("modified_ns") or ""), _dump(document.get("paths") or []),
                        document["parser_version"], document["status"],
                        _dump(document.get("issue")) if document.get("issue") else None,
                        int(document.get("chunk_count") or 0), document["cache_path"],
                        1 if document["document_id"] in excluded else 0,
                    ),
                )
                connection.execute(
                    "DELETE FROM role_matching_chunks WHERE session_id = ? AND document_id = ?",
                    (session_id, document["document_id"]),
                )
                cache = Path(str(document["cache_path"]))
                if cache.exists():
                    payload = _load(cache.read_text(encoding="utf-8"), {})
                    for chunk in payload.get("chunks") or []:
                        connection.execute(
                            """INSERT INTO role_matching_chunks
                            (session_id, document_id, chunk_id, locator_json, content_hash, cache_path)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (session_id, document["document_id"], chunk["chunk_id"],
                             _dump(chunk.get("locator") or {}), chunk.get("sha256") or "", str(cache)),
                        )

    def list_role_matching_documents(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM role_matching_documents WHERE session_id = ? AND active = 1 ORDER BY name, document_id",
                (session_id,),
            ).fetchall()
        return [_role_matching_document_from_row(row) for row in rows]

    def save_role_matching_revision(self, item: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO role_matching_revisions
                (session_id, revision, parent_revision, mode, catalog_digest,
                 document_ids_json, result_json, result_digest, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item["session_id"], int(item["revision"]), item.get("parent_revision"),
                 item["mode"], item["catalog_digest"], _dump(item.get("document_ids") or []),
                 _dump(item["result"]), item["result_digest"], item.get("created_at") or utc_now(),
                 item.get("completed_at") or utc_now()),
            )

    def list_role_matching_revisions(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM role_matching_revisions WHERE session_id = ? ORDER BY revision", (session_id,)
            ).fetchall()
        return [_role_matching_revision_from_row(row) for row in rows]

    def get_role_matching_revision(self, session_id: str, revision: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM role_matching_revisions WHERE session_id = ? AND revision = ?",
                (session_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError((session_id, revision))
        return _role_matching_revision_from_row(row)

    def save_role_matching_turn(self, item: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO role_matching_turns
                (session_id, turn, base_revision, result_revision, kind, status,
                 message, rematch_mode, added_paths_json, excluded_document_ids_json,
                 decision_json, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (item["session_id"], int(item["turn"]), item.get("base_revision"),
                 item.get("result_revision"), item["kind"], item["status"], item.get("message"),
                 item.get("rematch_mode"), _dump(item.get("added_paths") or []),
                 _dump(item.get("excluded_document_ids") or []), _dump(item.get("decision") or {}),
                 item.get("created_at") or utc_now(), item.get("completed_at")),
            )

    def list_role_matching_turns(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM role_matching_turns WHERE session_id = ? ORDER BY turn", (session_id,)
            ).fetchall()
        return [_role_matching_turn_from_row(row) for row in rows]

    def append_role_matching_event(self, session_id: str, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO role_matching_events (session_id, event_type, data_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, event_type, _dump(data), created_at),
            )
            sequence = int(cursor.lastrowid)
            connection.execute(
                "UPDATE role_matching_sessions SET event_sequence = ?, updated_at = ? WHERE session_id = ?",
                (sequence, created_at, session_id),
            )
        return {"sequence": sequence, "session_id": session_id, "type": event_type, "data": data, "created_at": created_at}

    def role_matching_events_after(self, session_id: str, sequence: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM role_matching_events WHERE session_id = ? AND sequence > ? ORDER BY sequence",
                (session_id, max(0, sequence)),
            ).fetchall()
        return [{"sequence": int(row["sequence"]), "session_id": session_id,
                 "type": row["event_type"], "data": _load(row["data_json"], {}),
                 "created_at": row["created_at"]} for row in rows]

    def save_role_matching_job(self, *, session_id: str, job_id: str, turn: int, mode: str, status: str) -> None:
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO role_matching_jobs
                (session_id, job_id, turn, mode, status, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM role_matching_jobs WHERE job_id = ?), ?), ?)""",
                (session_id, job_id, turn, mode, status, job_id, now,
                 now if status in {"completed", "failed", "cancelled"} else None),
            )

    def delete_role_matching_session(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM execution_jobs WHERE workload_class = 'role_matching' AND subject_id = ?",
                (session_id,),
            )
            connection.execute("DELETE FROM role_matching_events WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_jobs WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_turns WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_revisions WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_chunks WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_documents WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM role_matching_sessions WHERE session_id = ?", (session_id,))

    def complete_free_query_iteration(
        self,
        run_id: str,
        *,
        thread_id: str | None,
        plan_digest: str | None,
        result_digest: str | None,
        status: str = "reviewing",
    ) -> None:
        item = self.get_free_query_iteration_by_run(run_id)
        if item is None:
            return
        completed_at = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE free_query_iterations
                SET plan_digest = ?, result_digest = ?, completed_at = ?
                WHERE run_id = ?""",
                (plan_digest, result_digest, completed_at, run_id),
            )
            connection.execute(
                """UPDATE free_query_sessions
                SET thread_id = COALESCE(?, thread_id), status = ?, updated_at = ?
                WHERE session_id = ?""",
                (thread_id, status, completed_at, item["session_id"]),
            )

    def save_free_query_evidence_links(
        self, run_id: str, source_run_id: str, links: dict[str, str]
    ) -> None:
        with self._lock, self._connect() as connection:
            for alias, source_ref in links.items():
                connection.execute(
                    """INSERT INTO free_query_evidence_links
                    (run_id, evidence_ref, source_run_id, source_evidence_ref, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (run_id, alias, source_run_id, source_ref, utc_now()),
                )

    def list_free_query_evidence_links(self, run_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT evidence_ref, source_run_id, source_evidence_ref
                FROM free_query_evidence_links WHERE run_id = ? ORDER BY evidence_ref""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_run(self, run_id: str, **values: Any) -> RunRecord:
        allowed = {
            "status", "query", "input_json", "plan_json", "result_json", "thread_id",
            "error_json", "cancel_requested", "started_at", "completed_at", "progress_json",
            "runtime_json",
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
        elapsed_seconds: int | None = None,
        hard_limit_seconds: int | None = None,
        deadline_phase: str | None = None,
        next_deadline_at: str | None = None,
        extension_count: int | None = None,
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
                "elapsed_seconds": elapsed_seconds,
                "hard_limit_seconds": hard_limit_seconds,
                "deadline_phase": deadline_phase,
                "next_deadline_at": next_deadline_at,
                "extension_count": extension_count,
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

    def update_harness_state(self, run_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM harness_state WHERE run_id = ?", (run_id,)
            ).fetchone()
            state = _load(row["state_json"], {}) if row is not None else {}
            state.update(values)
            connection.execute(
                """INSERT OR REPLACE INTO harness_state (run_id, state_json, updated_at)
                VALUES (?, ?, ?)""",
                (run_id, _dump(state), utc_now()),
            )
        return state

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
                (draft_id, run_id, status, path, origin_json, validation_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft.draft_id,
                    draft.run_id,
                    draft.status,
                    draft.path,
                    _dump(draft.origin),
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
                 validation_run_id, composition_json, validation_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft.draft_id,
                    draft.status,
                    draft.revision,
                    _dump(draft.workflow),
                    draft.path,
                    draft.thread_id,
                    draft.validation_run_id,
                    _dump(draft.composition),
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
            composition=_load(row["composition_json"], {}),
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

    def save_workflow_conversation_turn(self, item: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO workflow_conversation_turns
                (draft_id, turn, parent_turn, kind, status, user_message,
                 feedback_type, action, decision_json, base_revision,
                 result_revision, proposal_digest, workflow_hash, diff_json,
                 validation_run_id, validation_report_digest, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["draft_id"], int(item["turn"]), item.get("parent_turn"),
                    item["kind"], item["status"], item.get("user_message"),
                    item.get("feedback_type"), item.get("action"),
                    _dump(item.get("decision") or {}), item.get("base_revision"),
                    item.get("result_revision"), item.get("proposal_digest"),
                    item.get("workflow_hash"), _dump(item.get("diff") or []),
                    item.get("validation_run_id"), item.get("validation_report_digest"),
                    item.get("created_at") or utc_now(), item.get("completed_at"),
                ),
            )

    def fail_running_harness_tool_calls(
        self, run_id: str, *, code: str, message: str
    ) -> int:
        completed_at = utc_now()
        output = _dump({"ok": False, "code": code, "message": message})
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE harness_tool_calls
                SET status = 'failed', output_json = ?, completed_at = ?
                WHERE run_id = ? AND status = 'running'""",
                (output, completed_at, run_id),
            )
        return int(cursor.rowcount)

    def list_workflow_conversation_turns(self, draft_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workflow_conversation_turns
                WHERE draft_id = ? ORDER BY turn""",
                (draft_id,),
            ).fetchall()
        return [
            {
                "draft_id": row["draft_id"], "turn": int(row["turn"]),
                "parent_turn": row["parent_turn"], "kind": row["kind"],
                "status": row["status"], "user_message": row["user_message"],
                "feedback_type": row["feedback_type"], "action": row["action"],
                "decision": _load(row["decision_json"], {}),
                "base_revision": row["base_revision"],
                "result_revision": row["result_revision"],
                "proposal_digest": row["proposal_digest"],
                "workflow_hash": row["workflow_hash"],
                "diff": _load(row["diff_json"], []),
                "validation_run_id": row["validation_run_id"],
                "validation_report_digest": row["validation_report_digest"],
                "created_at": row["created_at"], "completed_at": row["completed_at"],
            }
            for row in rows
        ]

    def get_workflow_revision(self, draft_id: str, revision: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT workflow_json, diff_json, created_at FROM workflow_revisions
                WHERE draft_id = ? AND revision = ?""",
                (draft_id, revision),
            ).fetchone()
        if row is None:
            raise KeyError((draft_id, revision))
        return {
            "revision": revision,
            "workflow": _load(row["workflow_json"], {}),
            "diff": _load(row["diff_json"], []),
            "created_at": row["created_at"],
        }

    def list_workflow_drafts(self) -> list[WorkflowDraftRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_drafts ORDER BY updated_at DESC"
            ).fetchall()
        return [
            WorkflowDraftRecord(
                draft_id=row["draft_id"],
                status=row["status"],
                revision=int(row["revision"]),
                workflow=_load(row["workflow_json"], {}),
                path=row["path"],
                thread_id=row["thread_id"],
                validation_run_id=row["validation_run_id"],
                composition=_load(row["composition_json"], {}),
                validation=_load(row["validation_json"], {}),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def append_workflow_management_event(
        self,
        *,
        event_id: str,
        workflow_id: str,
        action: str,
        from_version: str | None,
        to_version: str | None,
        workflow_hash: str | None,
        branch: str | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO workflow_management_events
                (event_id, workflow_id, action, from_version, to_version,
                 workflow_hash, branch, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    workflow_id,
                    action,
                    from_version,
                    to_version,
                    workflow_hash,
                    branch,
                    _dump(detail or {}),
                    utc_now(),
                ),
            )

    def list_workflow_management_events(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM workflow_management_events
                WHERE workflow_id = ? ORDER BY created_at DESC""",
                (workflow_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "workflow_id": row["workflow_id"],
                "action": row["action"],
                "from_version": row["from_version"],
                "to_version": row["to_version"],
                "workflow_hash": row["workflow_hash"],
                "branch": row["branch"],
                "detail": _load(row["detail_json"], {}),
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
            origin=_load(row["origin_json"], {}),
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


def _free_query_session_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "original_query": row["original_query"],
        "runtime": _load(row["runtime_json"], {}),
        "thread_id": row["thread_id"],
        "status": row["status"],
        "current_iteration": int(row["current_iteration"]),
        "accepted_iteration": row["accepted_iteration"],
        "accepted_result_digest": row["accepted_result_digest"],
        "accepted_at": row["accepted_at"],
        "draft_id": row["draft_id"],
        "pending_feedback": _load(row["pending_feedback_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _free_query_iteration_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "iteration": int(row["iteration"]),
        "run_id": row["run_id"],
        "parent_iteration": row["parent_iteration"],
        "feedback": row["feedback"],
        "feedback_type": row["feedback_type"],
        "execution_action": row["execution_action"],
        "decision": _load(row["decision_json"], {}),
        "plan_digest": row["plan_digest"],
        "result_digest": row["result_digest"],
        "source_run_id": row["source_run_id"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


def _free_query_feedback_request_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "feedback_request_id": row["feedback_request_id"],
        "session_id": row["session_id"],
        "base_iteration": int(row["base_iteration"]),
        "status": row["status"],
        "phase": row["phase"],
        "feedback": row["feedback"],
        "feedback_type_hint": row["feedback_type_hint"],
        "locale": row["locale"],
        "supplemental_input": row["supplemental_input"],
        "decision": _load(row["decision_json"], {}),
        "run_id": row["run_id"],
        "event_sequence": int(row["event_sequence"]),
        "error": _load(row["error_json"], None),
        "cancel_requested": bool(row["cancel_requested"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _execution_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "workload_class": row["workload_class"],
        "subject_id": row["subject_id"],
        "status": row["status"],
        "priority": int(row["priority"]),
        "attempt": int(row["attempt"]),
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "heartbeat_at": row["heartbeat_at"],
        "error": _load(row["error_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _role_matching_session_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "status": row["status"], "phase": row["phase"],
        "locale": row["locale"], "paths": _load(row["paths_json"], []),
        "runtime": _load(row["runtime_json"], {}), "thread_id": row["thread_id"],
        "consent_to_runtime": bool(row["consent_to_runtime"]),
        "current_revision": int(row["current_revision"]), "active_job_id": row["active_job_id"],
        "event_sequence": int(row["event_sequence"]), "error": _load(row["error_json"], None),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _role_matching_document_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "document_id": row["document_id"], "sha256": row["sha256"], "name": row["name"],
        "extension": row["extension"], "size_bytes": int(row["size_bytes"]),
        "modified_ns": row["modified_at"], "paths": _load(row["paths_json"], []),
        "parser_version": row["parser_version"], "status": row["status"],
        "issue": _load(row["issue_json"], None), "chunk_count": int(row["chunk_count"]),
        "cache_path": row["cache_path"], "active": bool(row["active"]),
        "excluded": bool(row["excluded"]),
    }


def _role_matching_revision_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "revision": int(row["revision"]),
        "parent_revision": row["parent_revision"], "mode": row["mode"],
        "catalog_digest": row["catalog_digest"],
        "document_ids": _load(row["document_ids_json"], []),
        "result": _load(row["result_json"], {}), "result_digest": row["result_digest"],
        "created_at": row["created_at"], "completed_at": row["completed_at"],
    }


def _role_matching_turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "turn": int(row["turn"]),
        "base_revision": row["base_revision"], "result_revision": row["result_revision"],
        "kind": row["kind"], "status": row["status"], "message": row["message"],
        "rematch_mode": row["rematch_mode"], "added_paths": _load(row["added_paths_json"], []),
        "excluded_document_ids": _load(row["excluded_document_ids_json"], []),
        "decision": _load(row["decision_json"], {}), "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


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
        runtime=(
            RuntimeSnapshot.model_validate(_load(row["runtime_json"], {}))
            if "runtime_json" in row.keys() and row["runtime_json"]
            else None
        ),
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
