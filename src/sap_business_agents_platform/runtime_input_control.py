"""Run-bound input controls, separate from model business terminals."""
from __future__ import annotations

import json
import uuid

VERSION = 2
TOOLS = (
    {"name": "request_query_clarification", "description": "Pause this query to ask for one missing business condition. No external reads.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["question"],
                     "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 2000}}}},
    {"name": "request_secure_receipt_reference", "description": "Pause to request a bank receipt reference through the protected UI. Never pass its value to this tool.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["question"],
                     "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 2000}}}},
)
NAMES = frozenset(t["name"] for t in TOOLS)


class InputControlError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class InputPause(Exception):
    """A committed control event; never a fabricated native business result."""


def pending(store, run_id):
    item = store.get_harness_state(run_id).get("input_control") or {}
    return item if item.get("status") in {"pending", "waiting"} else None


def tools_for_task(tools, *, formal=False, version=2):
    return [tool for tool in tools if tool["name"] not in NAMES or
            version == VERSION and (not formal or tool["name"] == "request_query_clarification")]


def request(store, run_id, name, arguments):
    from jsonschema import Draft202012Validator
    from .models import utc_now
    tool = next(t for t in TOOLS if t["name"] == name)
    if list(Draft202012Validator(tool["inputSchema"]).iter_errors(arguments)):
        raise InputControlError("input_request_invalid")
    question = arguments["question"].strip()
    if not question:
        raise InputControlError("input_request_invalid")
    with store._lock, store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state_json FROM harness_state WHERE run_id=?", (run_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        run = db.execute("SELECT status,cancel_requested FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if state.get("terminal_protocol") != VERSION or not run or run[1] or run[0] in {"completed", "failed", "cancelled", "inconclusive"}:
            raise InputControlError("input_request_not_authorized")
        if name == "request_secure_receipt_reference" and state.get("acceptance_spec") is not None:
            raise InputControlError("acceptance_secure_input_unavailable")
        prior = state.get("input_control") or {}
        if prior.get("status") in {"pending", "waiting"}:
            if prior["tool"] != name or prior["question"] != question:
                raise InputControlError("input_request_conflict")
            return {"ok": True, "input_request_id": prior["id"], "control": "waiting_input"}
        item = {"id": "input_" + uuid.uuid4().hex, "tool": name, "question": question,
                "status": "pending", "created_at": utc_now(), "generation": int(prior.get("generation", 0)) + 1}
        state["input_control"] = item
        db.execute("UPDATE harness_state SET state_json=?,updated_at=? WHERE run_id=?",
                   (json.dumps(state, ensure_ascii=False), utc_now(), run_id))
    store.append_event(run_id, "input_request_committed", {"input_request_id": item["id"], "tool": name})
    return {"ok": True, "input_request_id": item["id"], "control": "waiting_input"}


def outcome(controller, run_id, context, turn_count, started):
    import time
    from .harness import HarnessOutcome, _evidence_sources_complete
    from .models import utc_now
    item = pending(controller.store, run_id)
    if not item:
        raise InputControlError("input_request_missing")
    if not context["cleanup_state"].get("complete"):
        controller.store.update_harness_state(run_id, {"cleanup_incomplete": True})
        raise InputControlError("runtime_cleanup_incomplete")
    item = {**item, "status": "waiting", "cleanup_confirmed_at": utc_now()}
    if controller.store.get_harness_state(run_id).get("acceptance_spec") is not None:
        item["status"] = "missing_input"
        controller.store.update_harness_state(run_id, {"input_control": item})
        calls, evidence = controller.broker.snapshot(run_id)
        return HarnessOutcome(status="inconclusive", stop_reason="capability_unavailable",
            thread_id=context.get("thread_id"), turn_count=turn_count,
            summary={"zh": item["question"], "en": item["question"]}, missing_evidence=["acceptance_input_missing"],
            business_complete=False, tool_calls=calls, evidence=evidence,
            elapsed_seconds=int(time.monotonic() - started))
    controller.store.update_harness_state(run_id, {"input_control": item})
    calls, evidence = controller.broker.snapshot(run_id)
    secure = item["tool"] == "request_secure_receipt_reference"
    return HarnessOutcome(status="waiting_input", stop_reason="waiting_input", thread_id=context.get("thread_id"),
        turn_count=turn_count, clarification_question=item["question"],
        input_kind="secure_business_reference" if secure else None,
        input_field="receipt_reference" if secure else None, source_complete=_evidence_sources_complete(evidence),
        business_complete=False, summary={"zh": item["question"], "en": item["question"]}, tool_calls=calls, evidence=evidence,
        elapsed_seconds=int(time.monotonic() - started))


def resume(store, run_id, *, request_id, value, sensitive, protector):
    """Consume request, save protected input and enqueue run state atomically."""
    from .models import utc_now
    prepared = None
    if sensitive:
        if set(sensitive) != {"receipt_reference"} or value:
            raise InputControlError("input_request_kind_mismatch")
        raw = sensitive["receipt_reference"].strip()
        if not raw:
            raise InputControlError("input_blank")
        ref, protected, descriptor = protector.create_secret_ref(
            run_id=run_id, field="receipt_reference", value=raw, domain="bank-receipt-reference")
        prepared = (ref, protected, descriptor)
    elif not value or not value.strip():
        raise InputControlError("input_blank")
    with store._lock, store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state_json FROM harness_state WHERE run_id=?", (run_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        item = state.get("input_control") or {}
        run = db.execute("SELECT status,cancel_requested,query,input_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if request_id is not None and item.get("id") != request_id:
            raise InputControlError("input_request_stale")
        if not run or run[0] != "waiting_input" or run[1] or item.get("status") != "waiting":
            raise InputControlError("input_request_consumed_or_closed")
        if bool(prepared) != (item["tool"] == "request_secure_receipt_reference"):
            raise InputControlError("input_request_kind_mismatch")
        public = json.loads(run[3])
        if prepared:
            ref, protected, descriptor = prepared
            db.execute("INSERT INTO run_secret_bindings(secret_ref,run_id,field_name,protected_value,hmac_json,created_at) VALUES (?,?,?,?,?,?) "
                       "ON CONFLICT(run_id,field_name) DO UPDATE SET secret_ref=excluded.secret_ref,protected_value=excluded.protected_value,hmac_json=excluded.hmac_json,created_at=excluded.created_at,deleted_at=NULL",
                       (ref, run_id, "receipt_reference", protected, json.dumps(descriptor), utc_now()))
            public["receipt_reference"] = {"provided": True, "secret_ref": ref, "hmac": descriptor}
            text = "A bank receipt reference was supplied through the protected input channel."
        else:
            text = value.strip()
        item.update(status="consumed", consumed_at=utc_now())
        state["input_control"] = item
        state["platform_clarification_question"] = None
        query = (run[2] or "") + "\nAdditional user information / 用户补充：" + text
        db.execute("UPDATE harness_state SET state_json=?,updated_at=? WHERE run_id=?",
                   (json.dumps(state, ensure_ascii=False), utc_now(), run_id))
        db.execute("UPDATE runs SET status='queued',query=?,input_json=?,error_json=NULL WHERE run_id=?",
                   (query, json.dumps(public, ensure_ascii=False), run_id))
    store.append_event(run_id, "input_request_consumed", {"input_request_id": item["id"]})
    return query


def recover(store, run_id, *, cleanup_complete):
    """Recover a committed pause without replaying an interrupted model turn."""
    from .models import RunStatus, utc_now
    item = pending(store, run_id)
    if not item:
        return False
    if not cleanup_complete:
        store.update_harness_state(run_id, {"cleanup_incomplete": True})
        store.update_run(run_id, status=RunStatus.inconclusive, error_json={
            "code": "runtime_cleanup_incomplete", "message": "Input pause cleanup requires confirmation.",
            "detail": {"input_request_id": item["id"], "phase": "input_pause", "cleanup_complete": False}})
        return True
    formal = store.get_harness_state(run_id).get("acceptance_spec") is not None
    item = {**item, "status": "missing_input" if formal else "waiting", "cleanup_confirmed_at": utc_now()}
    secure = item["tool"] == "request_secure_receipt_reference"
    store.update_harness_state(run_id, {"input_control": item, "cleanup_incomplete": False})
    store.update_run(run_id, status=RunStatus.inconclusive if formal else RunStatus.waiting_input,
        error_json={"code": "acceptance_input_missing" if formal else "clarification_required",
            "message": item["question"], "detail": {"input_request_id": item["id"],
                **({"input_kind": "secure_business_reference", "field": "receipt_reference"} if secure else {})}})
    session = store.get_free_query_session_by_run(run_id)
    if session:
        store.update_free_query_session(session["session_id"], status="inconclusive" if formal else "waiting_input")
    store.append_event(run_id, "input_pause_recovered", {"input_request_id": item["id"], "cleanup_complete": True})
    return True
