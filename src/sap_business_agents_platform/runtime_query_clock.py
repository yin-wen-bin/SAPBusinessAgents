"""Persisted active-time accounting for one free-query run, not an authorization."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

from .runtime_contract import RuntimeContractError


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("A clock timestamp must include its timezone")
    return result


def checked(state):
    clock = state.get("execution_clock")
    if clock is None:
        return None  # Legacy runs retain their existing accounting; no invented credit.
    try:
        if clock["version"] != 1 or clock["mode"] not in {"active", "waiting", "queued", "stopped"}:
            raise ValueError()
        for field in ("elapsed_seconds", "waiting_seconds"):
            value = clock[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError()
        if type(clock["generation"]) is not int or clock["generation"] < 1:
            raise ValueError()
        if clock["mode"] == "active":
            timestamp(clock["active_started_at"])
        if clock["mode"] == "waiting":
            timestamp(clock["waiting_started_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise RuntimeContractError("runtime_execution_clock_invalid") from None
    return clock


def resume_wait(state, at):
    """Called inside the input-consumption transaction. Queue time is not execution."""
    clock = checked(state)
    if clock is None:
        return
    if clock["mode"] != "waiting":
        raise RuntimeContractError("runtime_execution_clock_not_waiting")
    clock.update(mode="queued", waiting_seconds=clock["waiting_seconds"] +
                 max(0, (at - timestamp(clock["waiting_started_at"])).total_seconds()),
                 waiting_started_at=None, generation=clock["generation"] + 1)


class RunClock:
    def __init__(self, store, cache=None):
        self.store = store
        self.cache = cache if cache is not None else {}

    def elapsed(self, run_id, state=None):
        clock = checked(state if state is not None else self.store.get_harness_state(run_id))
        if clock is None:
            return None
        elapsed = clock["elapsed_seconds"]
        if clock["mode"] != "active":
            return elapsed
        signature = (clock["generation"], clock["active_started_at"])
        cached = self.cache.get(run_id)
        if not cached or cached[0] != signature:
            # On process recovery, count downtime conservatively. Never restart the budget.
            initial = (now() - timestamp(clock["active_started_at"])).total_seconds()
            if initial < 0:
                raise RuntimeContractError("runtime_execution_clock_unconfirmed")
            cached = (signature, time.monotonic() - initial)
            self.cache[run_id] = cached
        return elapsed + max(0, time.monotonic() - cached[1])

    def transition(self, run_id, action, *, initial_elapsed=0):
        with self.store._lock, self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state_json FROM harness_state WHERE run_id=?", (run_id,)).fetchone()
            state = json.loads(row[0]) if row else {}
            clock = checked(state)
            at = now().isoformat()
            if clock is None:
                if action != "start":
                    return None
                clock = {"version": 1, "mode": "queued", "elapsed_seconds": initial_elapsed,
                         "waiting_seconds": 0, "active_started_at": None,
                         "waiting_started_at": None, "generation": 1}
                state["execution_clock"] = clock
            if action == "start":
                if clock["mode"] == "active":
                    return clock  # Duplicate preparation cannot grant more time.
                if clock["mode"] != "queued":
                    raise RuntimeContractError("runtime_execution_clock_not_queued")
                clock.update(mode="active", active_started_at=at)
            else:
                target = "waiting" if action == "pause" else "stopped"
                if clock["mode"] == target:
                    return clock
                if clock["mode"] != "active":
                    raise RuntimeContractError("runtime_execution_clock_not_active")
                clock.update(elapsed_seconds=self.elapsed(run_id, state), mode=target,
                             active_started_at=None, waiting_started_at=at if action == "pause" else None)
            clock["generation"] += 1
            db.execute("INSERT INTO harness_state(run_id,state_json,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(run_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                       (run_id, json.dumps(state, ensure_ascii=False), at))
        return clock


def queue_legacy_input(store, run_id, query):
    """The old native waiting_input path uses the same budget and cancellation guard."""
    with store._lock, store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state_json FROM harness_state WHERE run_id=?", (run_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        run = db.execute("SELECT status,cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run or run[0] != "waiting_input" or run[1]:
            raise RuntimeContractError("input_request_consumed_or_closed")
        at = now()
        resume_wait(state, at)
        db.execute("UPDATE harness_state SET state_json=?,updated_at=? WHERE run_id=?",
                   (json.dumps(state, ensure_ascii=False), at.isoformat(), run_id))
        db.execute("UPDATE runs SET status='queued',query=?,error_json=NULL WHERE run_id=?", (query, run_id))
