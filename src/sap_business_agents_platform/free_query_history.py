"""Local free-query history, retention, and resumable cleanup.

Draft packages are independent objects and are never removed by this service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .database import RunStore

LOGGER = logging.getLogger(__name__)

# Only query/feedback activity contributes to the retention clock. In particular,
# sessions.updated_at also changes when accepting a result or generating a draft.
_HISTORY_SQL = """
WITH latest_feedback AS (
 SELECT f.* FROM free_query_feedback_requests f
 WHERE f.feedback_request_id = (
   SELECT f2.feedback_request_id FROM free_query_feedback_requests f2
   WHERE f2.session_id = f.session_id
   ORDER BY julianday(f2.created_at) DESC, f2.feedback_request_id DESC LIMIT 1)
), history AS (
 SELECT s.session_id AS history_id, s.session_id, r.run_id,
   s.original_query AS query, s.current_iteration AS iteration,
   s.status AS session_status, s.created_at,
   COALESCE(s.draft_id, (SELECT d.draft_id FROM drafts d WHERE d.run_id=r.run_id
     ORDER BY d.created_at DESC LIMIT 1)) AS draft_id,
   CASE WHEN f.base_iteration = s.current_iteration THEN
     MAX(COALESCE(r.completed_at,r.created_at),COALESCE(f.completed_at,f.created_at))
     ELSE COALESCE(r.completed_at,r.created_at) END AS activity_at,
   CASE WHEN f.base_iteration = s.current_iteration THEN
     MAX(COALESCE(r.completed_at,r.created_at),COALESCE(f.completed_at,f.created_at))
     ELSE COALESCE(r.completed_at,r.created_at) END AS anchor_at,
   CASE WHEN f.base_iteration = s.current_iteration AND f.status NOT IN ('completed','iteration_created','new_session_required')
     THEN CASE WHEN f.status IN ('failed','cancelled','waiting_input') THEN f.status
       ELSE 'reviewing' END ELSE r.status END AS status,
   (EXISTS(SELECT 1 FROM free_query_iterations ai JOIN runs ar ON ar.run_id=ai.run_id
     WHERE ai.session_id=s.session_id AND ar.status IN ('queued','planning','validating','running'))
    OR EXISTS(SELECT 1 FROM free_query_feedback_requests af WHERE af.session_id=s.session_id
     AND af.status NOT IN ('completed','failed','cancelled','waiting_input','iteration_created','new_session_required'))) AS active
 FROM free_query_sessions s
 JOIN free_query_iterations i ON i.session_id=s.session_id AND i.iteration=s.current_iteration
 JOIN runs r ON r.run_id=i.run_id
 LEFT JOIN latest_feedback f ON f.session_id=s.session_id
 WHERE r.query_origin='user' AND EXISTS (
   SELECT 1 FROM free_query_iterations first JOIN runs original ON original.run_id=first.run_id
   WHERE first.session_id=s.session_id AND first.iteration=1 AND original.query_origin='user')
 AND NOT EXISTS (
   SELECT 1 FROM free_query_iterations si JOIN runs sr ON sr.run_id=si.run_id
   WHERE si.session_id=s.session_id AND COALESCE(sr.query_origin,'system')!='user')
 UNION ALL
 SELECT r.run_id, NULL, r.run_id, r.query, 1, NULL, r.created_at,
   (SELECT d.draft_id FROM drafts d WHERE d.run_id=r.run_id ORDER BY d.created_at DESC LIMIT 1),
   COALESCE(r.completed_at,r.created_at), COALESCE(r.completed_at,r.created_at), r.status,
   r.status IN ('queued','planning','validating','running')
 FROM runs r WHERE r.mode='free_query' AND r.query_origin='user'
 AND NOT EXISTS(SELECT 1 FROM free_query_iterations i WHERE i.run_id=r.run_id)
)
"""


class FreeQueryHistory:
    def __init__(self, store: RunStore, data_root: Path, coordinator: Any = None,
                 *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.data_root = data_root.resolve()
        self.coordinator = coordinator
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.draft_check: Callable[[str, str | None], str | None] | None = None
        self._protected: Counter[str] = Counter()
        self._task: asyncio.Task[Any] | None = None

    def settings(self) -> dict[str, int]:
        with self.store._connect() as db:
            days = db.execute("SELECT retention_days FROM free_query_history_settings WHERE singleton=1").fetchone()[0]
        return {"retention_days": int(days)}

    def update_settings(self, days: int) -> dict[str, int]:
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("Retention must be a positive whole number of days.")
        with self.store._lock, self.store._connect() as db:
            db.execute("UPDATE free_query_history_settings SET retention_days=? WHERE singleton=1", (days,))
        return self.settings()

    def _rows(self, *, available: bool, history_id: str | None = None,
              limit: int | None = None, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        days = self.settings()["retention_days"]
        where = " WHERE NOT EXISTS(SELECT 1 FROM free_query_history_cleanup c WHERE c.history_id=h.history_id)"
        values: list[Any] = []
        if available:
            where += " AND (h.active OR julianday(h.anchor_at) > julianday(?) - ?)"
            values += [self.now().isoformat(), days]
        if history_id is not None:
            where += " AND h.history_id=?"
            values.append(history_id)
        with self.store._connect() as db:
            total = db.execute(_HISTORY_SQL + " SELECT COUNT(*) FROM history h" + where, values).fetchone()[0]
            sql = _HISTORY_SQL + " SELECT h.* FROM history h" + where + " ORDER BY julianday(h.activity_at) DESC,h.history_id DESC"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                values += [limit, offset]
            rows = [dict(row) for row in db.execute(sql, values)]
        return rows, int(total)

    def _expires(self, row: dict[str, Any]) -> datetime:
        anchor = datetime.fromisoformat(row["anchor_at"]).astimezone(timezone.utc)
        try:
            return anchor + timedelta(days=self.settings()["retention_days"])
        except OverflowError:
            return datetime.max.replace(tzinfo=timezone.utc)

    def list(self, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self._rows(available=True, limit=limit, offset=offset)
        for row in rows:
            row["expires_at"] = None if row["active"] else self._expires(row).isoformat()
            row.pop("anchor_at")
            row["active"] = bool(row["active"])
            reason = "query_not_successful" if row["status"] != "completed" else None
            if reason is None and self.draft_check:
                reason = self.draft_check(row["run_id"], row["session_id"])
            row["can_create_draft"] = reason is None
            row["draft_blocker"] = reason
            imported = self.store.get_draft_import(row["draft_id"]) if row.get("draft_id") else None
            row["managed_draft_id"] = imported["managed_draft_id"] if imported else None
        return {"items": rows, "total": total, "limit": limit, "offset": offset, **self.settings()}

    def retry_query(self, history_id: str) -> dict[str, str]:
        rows, _ = self._rows(available=True, history_id=history_id)
        if not rows:
            raise KeyError(history_id)
        row = rows[0]
        messages = [str(row["query"] or "")]
        additions: list[tuple[str, str, str]] = []
        run_ids = [row["run_id"]]
        if row["session_id"]:
            # Feedback requests include failed/cancelled attempts which never
            # produced an iteration. Do not copy results, plans or secret inputs.
            with self.store._connect() as db:
                requests = db.execute("SELECT * FROM free_query_feedback_requests WHERE session_id=? ORDER BY created_at,feedback_request_id", (row["session_id"],)).fetchall()
            for item in requests:
                additions.append((item["created_at"], item["feedback_request_id"], item["feedback"]))
                if item["supplemental_input"]:
                    additions.append((item["updated_at"], item["feedback_request_id"], item["supplemental_input"]))
            iterations = self.store.list_free_query_iterations(row["session_id"])
            run_ids = [item["run_id"] for item in iterations]
            if not requests:  # compatibility with older synchronous feedback
                additions += [(item["created_at"], item["run_id"], item["feedback"]) for item in iterations if item.get("feedback")]
        for run_id in run_ids:
            for event in self.store.events_after(run_id):
                if event.type == "input_received" and not event.data.get("secure_fields"):
                    value = event.data.get("input")
                    if isinstance(value, str) and value:
                        additions.append((event.created_at, str(event.sequence), value))
        messages += [text for _, _, text in sorted(additions) if text]
        return {"query": "\n\n".join(messages)}

    @contextmanager
    def protect(self, *, run_id: str | None = None, session_id: str | None = None) -> Iterator[None]:
        """Hold a source across asynchronous authoring, without holding a DB lock."""
        with self.store._lock:
            if session_id:
                session = self.store.get_free_query_session(session_id)
                ids = [item["run_id"] for item in self.store.list_free_query_iterations(session_id)]
                history_id = session["session_id"]
            else:
                assert run_id is not None
                self.store.get_run(run_id)
                session = self.store.get_free_query_session_by_run(run_id)
                history_id = session["session_id"] if session else run_id
                ids = [run_id]
            rows, _ = self._rows(available=True, history_id=history_id)
            user_history = self.store.get_run(ids[0]).query_origin == "user"
            if user_history and not rows and not any(self._protected[item] for item in ids):
                raise KeyError(history_id)
            self._protected.update(ids)
        try:
            yield
        finally:
            with self.store._lock:
                for item in ids:
                    self._protected[item] -= 1
                    if self._protected[item] == 0:
                        del self._protected[item]

    async def start(self) -> None:
        try:
            await self.cleanup()
        except Exception:
            LOGGER.exception("Free-query history startup cleanup will retry.")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.cleanup()
            except Exception:
                LOGGER.exception("Free-query history maintenance will retry.")

    async def cleanup(self) -> None:
        rows, _ = self._rows(available=False)
        for row in rows:
            if row["active"] or self._expires(row) > self.now():
                continue
            try:
                self._mark(row["history_id"])
            except (KeyError, OSError, ValueError, sqlite3.Error):
                LOGGER.warning("Free-query history could not be marked for cleanup.", exc_info=True)
        with self.store._connect() as db:
            jobs = [dict(row) for row in db.execute("SELECT * FROM free_query_history_cleanup")]
        for job in jobs:
            try:
                if job["status"] == "cancelling":
                    if not self.coordinator:
                        continue
                    for feedback_id in json.loads(job["feedback_ids_json"]):
                        await self.coordinator.cancel_free_query_feedback(job["session_id"], feedback_id)
                    ready = True
                    for run_id in json.loads(job["run_ids_json"]):
                        run = self.store.get_run(run_id)
                        if run.status.value == "waiting_input":
                            await self.coordinator.cancel(run_id)
                        # Retry bounded tool/task closure even when a previous
                        # attempt already changed the run status to cancelled.
                        ready = await self.coordinator.cancel_acceptance_run(run_id) and ready
                    if not ready:
                        continue
                    with self.store._lock, self.store._connect() as db:
                        db.execute("UPDATE free_query_history_cleanup SET status='pending' WHERE history_id=?", (job["history_id"],))
                        db.executemany("INSERT OR IGNORE INTO free_query_history_expired_runs VALUES (?,?)", [(item, job["history_id"]) for item in json.loads(job["run_ids_json"])])
                await asyncio.to_thread(self._finish, job)
            except (OSError, ValueError, KeyError, sqlite3.Error):
                LOGGER.warning("Free-query history cleanup will retry.", exc_info=True)

    def _mark(self, history_id: str) -> None:
        with self.store._lock, self.store._connect() as db:
            rows, _ = self._rows(available=False, history_id=history_id)
            if not rows or rows[0]["active"] or self._expires(rows[0]) > self.now():
                return
            row = rows[0]
            ids = [row["run_id"]]
            feedback_ids: list[str] = []
            if row["session_id"]:
                ids = [i[0] for i in db.execute("SELECT run_id FROM free_query_iterations WHERE session_id=?", (row["session_id"],))]
                feedback_ids = [i[0] for i in db.execute("SELECT feedback_request_id FROM free_query_feedback_requests WHERE session_id=?", (row["session_id"],))]
            if any(self._protected[item] for item in ids):
                return
            for subject in ids + feedback_ids:
                job = self.store.latest_execution_job_for_subject(subject, statuses=("running",))
                if job:
                    return
            # An evidence reuse outside the retiring session is still owned by
            # another query. Keep its source until that consumer has retired.
            for run_id in ids:
                consumers = db.execute("SELECT run_id FROM free_query_evidence_links WHERE source_run_id=?", (run_id,))
                if any(i[0] not in ids for i in consumers):
                    return
            waiting = any(self.store.get_run(item).status.value == "waiting_input" for item in ids)
            if feedback_ids:
                waiting = waiting or any(i[0] == 'waiting_input' for i in db.execute("SELECT status FROM free_query_feedback_requests WHERE session_id=?", (row["session_id"],)))
            needs_closure = waiting or any(self.store.get_harness_state(item).get("cleanup_incomplete") for item in ids)
            stage = 'cancelling' if needs_closure else 'pending'
            db.execute("INSERT INTO free_query_history_cleanup VALUES (?,?,?,?,?,?)", (history_id, row["session_id"], json.dumps(ids), json.dumps(feedback_ids), self.now().isoformat(), stage))
            if stage == 'pending':
                db.executemany("INSERT INTO free_query_history_expired_runs VALUES (?,?)", [(item, history_id) for item in ids])

    def _finish(self, job: dict[str, Any]) -> None:
        ids = json.loads(job["run_ids_json"])
        feedback_ids = json.loads(job["feedback_ids_json"])
        for run_id in ids + feedback_ids:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
                raise ValueError("Invalid history cleanup identifier.")
            for name in ("artifacts", "restricted-artifacts", "harness"):
                registered_root = self.data_root / name
                root = registered_root.resolve()
                if (root.parent != self.data_root or registered_root.is_symlink()
                        or getattr(registered_root, "is_junction", lambda: False)()):
                    raise ValueError("History cleanup root escaped its data directory.")
                target = root / run_id
                # Resolve and check the final target before recursive deletion;
                # reject symlinks/junctions rather than following them.
                if target.resolve().parent != root or target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                    raise ValueError("History cleanup escaped its registered directory.")
                if target.exists():
                    shutil.rmtree(target)
        with self.store._lock, self.store._connect() as db:
            for feedback_id in feedback_ids:
                db.execute("DELETE FROM free_query_feedback_events WHERE feedback_request_id=?", (feedback_id,))
                db.execute("DELETE FROM free_query_feedback_requests WHERE feedback_request_id=?", (feedback_id,))
            if job["session_id"]:
                db.execute("DELETE FROM free_query_iterations WHERE session_id=?", (job["session_id"],))
                db.execute("DELETE FROM free_query_sessions WHERE session_id=?", (job["session_id"],))
            for run_id in ids:
                for table in ("artifact_reveal_tokens", "structured_artifacts", "run_secret_bindings", "events", "harness_tool_calls", "harness_tool_candidates", "harness_state", "free_query_evidence_links"):
                    db.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
                db.execute("DELETE FROM runs WHERE run_id=? AND mode='free_query'", (run_id,))
            for subject in ids + feedback_ids:
                db.execute("DELETE FROM execution_jobs WHERE subject_id=?", (subject,))
            db.execute("DELETE FROM free_query_history_expired_runs WHERE history_id=?", (job["history_id"],))
            db.execute("DELETE FROM free_query_history_cleanup WHERE history_id=?", (job["history_id"],))
