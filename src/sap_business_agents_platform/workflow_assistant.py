"""Persistent, revision-bound workflow conversation orchestration (v2 only)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import nullcontext
from typing import Any

from .models import utc_now
from .workflow_factory import WorkflowDraftError, _json_diff
from .workflow_composer import compact_agent_catalog
from .workflows import validate_workflow, workflow_digest
from .workflow_candidate import checked_scope, check_candidate
from .runtime_contract import RuntimeContractError

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "withdrawn"}


def state(draft: Any) -> dict:
    return draft.composition.setdefault("assistant_v2", {"version": 2, "rounds": [], "events": []})


def event(draft: Any, request: str, phase: str, data: dict | None = None) -> None:
    value = state(draft)
    value["events"].append({"id": len(value["events"]) + 1, "request_id": request,
                            "phase": phase, "data": data or {}, "created_at": utc_now()})


def fingerprint(payload: dict) -> str:
    # Preserve fingerprints of pre-scope requests, including model_dump defaults.
    payload = {k: v for k, v in payload.items() if k != "allowedChangePaths" or v is not None}
    # New optional reference metadata must not change old request fingerprints.
    if "references" in payload:
        payload["references"] = [{k: v for k, v in ref.items()
            if k not in {"comment", "objectId", "objectKind"} or v is not None}
            for ref in payload["references"]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def checked_reference_identity(workflow: dict, reference: dict) -> None:
    """A revision-bound array path must agree with its stable business object."""
    object_id, kind = reference.get("objectId"), reference.get("objectKind")
    if object_id is None and kind is None:
        return  # Historical references remain revision-bound as before.
    section = {"node": "nodes", "connection": "connections"}.get(kind)
    match = re.fullmatch(r"/(nodes|connections)/(0|[1-9][0-9]*)(?:/.*)?", reference["path"])
    if not section or not match or match[1] != section:
        raise WorkflowDraftError("Object identity does not match its path.", code="workflow_reference_invalid")
    rows = workflow.get(section, [])
    index = int(match[2])
    def identity(row):
        if kind == "node":
            return row.get("id")
        source, target = row.get("from", {}), row.get("to", {})
        scope = source.get("scope")
        pointer = "/" if source.get("pointer") is None else source["pointer"]
        port = "value" if source.get("port") is None else source["port"]
        start = (f'{source.get("nodeId")}:{source.get("port")}' if scope == "node_output" else
            f'iteration:{pointer}' if scope == "iteration_item" else f'{scope}:{port}')
        return f'{start}->{target.get("nodeId")}:{target.get("port")}'
    if index >= len(rows) or identity(rows[index]) != object_id or sum(identity(row) == object_id for row in rows) != 1:
        raise WorkflowDraftError("Referenced object changed or is ambiguous.", code="workflow_reference_stale")


def fixed_bindings(workflow: dict) -> dict:
    result = {}
    for section in ("nodes", "integrationInputs", "outputActions"):
        keys = ("id", "agentId", "agentVersion", "agentDigest", "connectionId", "integrationBackendId",
                "bindingId", "nativeServer", "nativeTool", "schemaHash", "bindingSnapshot")
        result[section] = [{key: row[key] for key in keys if key in row} for row in workflow.get(section, [])]
    return result


def safe_context(value: Any) -> Any:
    """Never include raw validation rows or transport secrets in model context."""
    if isinstance(value, dict):
        return {k: safe_context(v) for k, v in value.items()
                if not any(word in k.lower() for word in ("password", "credential", "secret", "token", "raw_rows", "authorization"))}
    if isinstance(value, list):
        return [safe_context(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?i)\b(password|token|secret|api[_-]?key|authorization)\s*[:=]\s*\S+", r"\1=[redacted]", value)
    return value


def reference_value(workflow: dict, path: str) -> Any:
    if not path.startswith("/"):
        raise WorkflowDraftError("Reference must be a JSON pointer.", code="workflow_reference_invalid")
    value: Any = workflow
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list) and (not part.isdigit() or str(int(part)) != part):
                raise ValueError("invalid array index")
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, ValueError, IndexError, TypeError):
        raise WorkflowDraftError("The referenced workflow field no longer exists.", code="workflow_reference_invalid") from None
    return value


class WorkflowAssistant:
    def __init__(self, service: Any):
        self.service, self.store = service, service.store
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.closed = False

    def recover(self) -> None:
        self.closed = False
        for draft in self.store.list_workflow_drafts():
            if "assistant_v2" not in draft.composition:
                continue
            def recover_one(current: Any):
                for item in state(current)["rounds"]:
                    if item["status"] in {"running", "queued"}:
                        # After a hard restart we cannot prove the old SDK tree
                        # is gone. Do not release ownership on an assumption.
                        item["status"] = "cleanup_pending" if item["status"] == "running" else "needs_review"
                        item["failure_code"] = "workflow_assistant_interrupted"
                        event(current, item["request_id"], item["status"])
            self.store.mutate_workflow_assistant(draft.draft_id, recover_one)
            self.sync_projection(draft.draft_id)

    def sync_projection(self, draft_id: str) -> None:
        """DB is authoritative; workflow/draft JSON files are rebuildable mirrors."""
        with self.store._lock:
            current = self.store.get_workflow_draft(draft_id)
            try:
                self.service._write_draft(current)
            except OSError:
                def diagnose(draft: Any):
                    state(draft)["projection_sync"] = "failed"
                self.store.mutate_workflow_assistant(draft_id, diagnose)

    def snapshot(self, draft_id: str) -> dict:
        draft = self.store.get_workflow_draft(draft_id)
        from .workflow_authoring_runtime import capabilities
        if (state(draft).get("runtime_snapshot") or {}).get("provider_id") == "workbuddy":
            from .workbuddy_authoring import capabilities
        return {**state(draft), "draft_id": draft_id, "revision": draft.revision,
                "workflow_hash": workflow_digest(draft.workflow), "capabilities": capabilities("restricted")}

    def submit(self, draft_id: str, payload: dict) -> Any:
        request = payload["requestId"]
        digest = fingerprint(payload)
        def add(draft: Any):
            value = state(draft)
            prior = next((r for r in value["rounds"] if r["request_id"] == request), None)
            if prior:
                if prior["fingerprint"] != digest:
                    raise WorkflowDraftError("Request ID was reused with different content.", code="workflow_request_conflict")
                return
            if self.closed:
                raise WorkflowDraftError("Assistant is stopping.", code="workflow_assistant_stopping")
            if draft.revision != payload["baseRevision"]:
                raise WorkflowDraftError("Workflow revision changed.", code="workflow_revision_conflict")
            if payload["executionMode"] == "full_access" and not payload.get("trustedLocalConfirmed"):
                raise WorkflowDraftError("Trusted local execution requires explicit confirmation.", code="workflow_trusted_confirmation_required")
            if payload["intent"] == "revise" and draft.status == "published":
                raise WorkflowDraftError("Create a new version before modifying a published workflow.", code="workflow_draft_published")
            legacy = draft.composition.get("conversation") or {}
            if legacy.get("status") in {"composing", "waiting_input"}:
                raise WorkflowDraftError("Finish the legacy conversation first.", code="workflow_legacy_turn_active")
            if not value.get("runtime_snapshot"):
                bound = legacy.get("runtime_snapshot") or {}
                method = getattr(self.service.author, "snapshot", None)
                provider = str(getattr(self.service.author, "current_provider_id", "codex"))
                value["runtime_snapshot"] = bound or (method(provider) if callable(method) else {"provider_id": provider})
            if value["runtime_snapshot"].get("provider_id") == "workbuddy" and payload["executionMode"] != "full_access":
                raise WorkflowDraftError("WorkBuddy Windows restricted mode is unverified. Select trusted local explicitly.", code="workbuddy_windows_restricted_unverified")
            pending = [r for r in value["rounds"] if r["status"] == "queued"]
            if len(pending) >= 3:
                raise WorkflowDraftError("There are already three waiting messages.", code="workflow_assistant_queue_full")
            refs = []
            seen_refs = set()
            for ref in payload.get("references", []):
                if ref["draftId"] != draft_id or ref["revision"] != draft.revision:
                    raise WorkflowDraftError("Reference belongs to another snapshot.", code="workflow_reference_stale")
                if ref["kind"] != "workflow_field":
                    raise WorkflowDraftError("Diagnostic reference is not supported yet.", code="workflow_reference_unsupported")
                key = (ref["kind"], ref["path"])
                if key in seen_refs:
                    raise WorkflowDraftError("Duplicate annotation target.", code="workflow_reference_invalid")
                seen_refs.add(key)
                checked_reference_identity(draft.workflow, ref)
                clean = {k: v for k, v in ref.items() if v is not None}
                refs.append({**safe_context(clean), "value": safe_context(reference_value(draft.workflow, ref["path"]))})
            try:
                allowed_paths = checked_scope(payload.get("allowedChangePaths"), refs, intent=payload["intent"])
            except RuntimeContractError as error:
                raise WorkflowDraftError("Change scope does not match verified field references.", code=error.code) from None
            reply_id = payload.get("replyToClarificationId")
            if reply_id:
                original = next((r for r in value["rounds"] if r.get("clarification_id") == reply_id), None)
                if not original or original["status"] != "waiting_input" or original["base_revision"] != draft.revision or original["intent"] != payload["intent"]:
                    raise WorkflowDraftError("Clarification is no longer applicable.", code="workflow_clarification_stale")
                original["status"] = "completed"
                original["completed_at"] = utc_now()
            value["rounds"].append({"request_id": request, "fingerprint": digest, "intent": payload["intent"],
                "status": "queued", "message": payload["feedback"], "locale": payload["locale"],
                "execution_mode": payload["executionMode"], "budget_seconds": payload["budgetSeconds"],
                "runtime_snapshot": value["runtime_snapshot"],
                "base_revision": draft.revision, "base_hash": workflow_digest(draft.workflow),
                "references": refs, "allowed_change_paths": allowed_paths,
                "reply_to": reply_id, "created_at": utc_now()})
            event(draft, request, "queued")
        result = self.store.mutate_workflow_assistant(draft_id, add)
        self.dispatch(draft_id)
        return result

    def dispatch(self, draft_id: str) -> None:
        if self.closed or any(key[0] == draft_id for key in self.tasks):
            return
        selected: list[dict] = []
        def claim(draft: Any):
            value = state(draft)
            if any(r["status"] in {"running", "cleanup_pending", "waiting_input"} for r in value["rounds"]):
                return
            queued = next((r for r in value["rounds"] if r["status"] == "queued"), None)
            if not queued:
                return
            legacy = draft.composition.get("conversation") or {}
            if draft.status == "planning" or legacy.get("status") == "composing":
                queued["status"] = "needs_review"
                event(draft, queued["request_id"], "needs_review", {"code": "workflow_legacy_turn_active"})
                return
            if queued["base_revision"] != draft.revision or queued["base_hash"] != workflow_digest(draft.workflow):
                queued["status"] = "needs_review"
                event(draft, queued["request_id"], "needs_review", {"code": "workflow_revision_conflict"})
                return
            if queued["intent"] == "revise" and (legacy.get("status") == "validating" or draft.validation.get("phase") in {"queued", "running"}):
                queued["status"] = "needs_review"
                event(draft, queued["request_id"], "needs_review", {"code": "workflow_validation_active"})
                return
            queued["status"] = "running"
            queued["started_at"] = utc_now()
            event(draft, queued["request_id"], "started")
            selected.append(json.loads(json.dumps(queued)))
        self.store.mutate_workflow_assistant(draft_id, claim)
        if selected:
            request = selected[0]["request_id"]
            key = (draft_id, request)
            task = asyncio.create_task(self.run(draft_id, selected[0]))
            self.tasks[key] = task
            def finished(task: asyncio.Task):
                self.tasks.pop(key, None)
                if task.cancelled():
                    # Cancellation before coroutine entry has no SDK process to
                    # clean up and must not leave a permanent cancelling round.
                    def cancelled_before_start(current: Any):
                        round_ = next(r for r in state(current)["rounds"] if r["request_id"] == request)
                        if round_["status"] == "cancelling":
                            round_.update(status="cancelled", completed_at=utc_now())
                            event(current, request, "cancelled")
                            for other in state(current)["rounds"]:
                                if other["status"] == "queued":
                                    other["status"] = "needs_review"
                    self.store.mutate_workflow_assistant(draft_id, cancelled_before_start)
                if not task.cancelled():
                    task.exception()
                self.dispatch(draft_id)
            task.add_done_callback(finished)

    async def run(self, draft_id: str, item: dict) -> None:
        if (item.get("runtime_snapshot", {}).get("provider_id") == "workbuddy"
                and not self.service.author.workbuddy_reserved()):
            async with self.service.author.workbuddy_reservation():
                return await self.run(draft_id, item)
        request = item["request_id"]
        cleanup: dict = {"complete": True}
        try:
            draft = self.store.get_workflow_draft(draft_id)
            snapshot = item["runtime_snapshot"]
            # Keep original account even for a historical round with no new field.
            # Resolving a binding checks ownership without choosing another SDK.
            resolve_binding = getattr(self.service.author, "snapshot_for_binding", None)
            if callable(resolve_binding):
                resolve_binding(snapshot)
            if snapshot.get("provider_id") == "workbuddy":
                def acquired(current: Any):
                    round_ = next(r for r in state(current)["rounds"] if r["request_id"] == request)
                    if current.revision != item["base_revision"] or workflow_digest(current.workflow) != item["base_hash"]:
                        round_.update(status="needs_review", failure_code="workflow_revision_conflict")
                        event(current, request, "needs_review")
                    elif round_["status"] == "running":
                        round_["started_at"] = utc_now()
                        event(current, request, "execution_started", {"provider": "workbuddy"})
                self.store.mutate_workflow_assistant(draft_id, acquired)
                draft = self.store.get_workflow_draft(draft_id)
                if next(r for r in state(draft)["rounds"] if r["request_id"] == request)["status"] != "running":
                    return
            provider, model, effort = str(snapshot.get("provider_id") or "codex"), snapshot.get("model"), snapshot.get("reasoning_effort")
            def emit(phase: str, data: dict):
                def append(current: Any):
                    round_ = next(r for r in state(current)["rounds"] if r["request_id"] == request)
                    if round_["status"] == "running":
                        event(current, request, phase, data)
                self.store.mutate_workflow_assistant(draft_id, append)
            emit("context_ready", {"provider": provider, "context_mode": "platform_history"})
            history = [{k: r.get(k) for k in ("message", "answer", "question", "base_revision", "intent", "status")}
                       for r in state(draft)["rounds"] if r["request_id"] != request]
            # Legacy final decisions are supplied as history, not SDK thread IDs.
            history = [{"message": r.get("user_message"), "answer": safe_context(r.get("decision"))}
                       for r in self.store.list_workflow_conversation_turns(draft_id)] + history
            method = getattr(self.service.author, "author_workflow_v2", None)
            if not callable(method):
                raise WorkflowDraftError("The provider does not implement workflow_authoring.v2.", code="workflow_provider_unavailable")
            pin = getattr(self.service.author, "pin", None)
            bound = getattr(self.service.author, "pin_snapshot", None)
            context = bound(snapshot) if (provider == "workbuddy" or "authentication_binding_revision" in snapshot) and callable(bound) else pin(provider, model, effort) if callable(pin) else nullcontext()
            with context:
                from .runtime_contract import business_scope, await_business
                async with business_scope(self.service.author, "workflow_authoring.v2", seconds=item["budget_seconds"]) as deadline:
                    result = await await_business(method(workflow=safe_context(draft.workflow), message=item["message"], intent=item["intent"],
                        execution_mode=item["execution_mode"], history=safe_context(history),
                        catalog=compact_agent_catalog(self.service.agents), references=item["references"],
                        allowed_paths=item.get("allowed_change_paths"),
                        emit=emit, cleanup_state=cleanup,
                        request_id=request, deadline=deadline))
            def commit(current: Any):
                round_ = next(r for r in state(current)["rounds"] if r["request_id"] == request)
                if round_["status"] != "running":
                    return
                if current.revision != item["base_revision"] or workflow_digest(current.workflow) != item["base_hash"]:
                    round_.update(status="needs_review", failure_code="workflow_revision_conflict", candidate=result.get("workflow"))
                    event(current, request, "needs_review")
                    return
                action = result.get("action")
                if action not in {"explain", "revise", "clarify"}:
                    raise WorkflowDraftError("Unsupported assistant action.", code="workflow_assistant_contract_invalid")
                if item["intent"] == "explain" and action == "revise":
                    raise WorkflowDraftError("An explanation cannot modify the workflow.", code="workflow_explain_cannot_modify")
                round_.update(answer=str(result.get("answer") or ""), runtime=snapshot,
                              capabilities=result.get("capabilities", {}), completed_at=utc_now())
                if result.get("presentation_version"):
                    round_["presentation_version"] = result["presentation_version"]
                if action == "clarify":
                    question = str(result.get("question") or "").strip()
                    if not question:
                        raise WorkflowDraftError("Clarification question is missing.", code="workflow_assistant_contract_invalid")
                    round_.update(status="waiting_input", question=question, clarification_id=f"{request}:clarification")
                    event(current, request, "waiting_input")
                    return
                diff = None
                if action == "revise":
                    if current.status == "published":
                        raise WorkflowDraftError("Published draft is immutable.", code="workflow_draft_published")
                    candidate = result.get("workflow")
                    if not isinstance(candidate, dict) or candidate.get("id") != current.workflow.get("id") or candidate.get("version") != current.workflow.get("version"):
                        raise WorkflowDraftError("Candidate identity changed.", code="workflow_candidate_identity_changed")
                    # No normalization: missing pins may not silently resolve to
                    # today's active Agent/version/connection schema.
                    check_candidate(current.workflow, candidate,
                        allowed_paths=item.get("allowed_change_paths"), agents=self.service.agents)
                    diff = _json_diff(current.workflow, candidate)
                    round_["diff"] = diff
                    round_["binding_changes"] = fixed_bindings(current.workflow) != fixed_bindings(candidate)
                    if diff:
                        current.workflow = candidate
                        current.revision += 1
                        current.validation_run_id = None
                        current.validation = {"valid": False, "phase": "not_started", "verdict": "pending", "issues": ["Workflow changed."]}
                        current.status = "draft"
                        conversation = current.composition.setdefault("conversation", {})
                        conversation["requires_design_acceptance"] = True
                        conversation.setdefault("current_turn", 1)
                        self.service._invalidate_acceptance(current, design=True)
                    else:
                        diff = None
                round_.update(status="completed", result_revision=current.revision)
                event(current, request, "completed", {"revision": current.revision})
                return diff
            self.store.mutate_workflow_assistant(draft_id, commit)
            self.sync_projection(draft_id)
        except BaseException as exc:
            cancelled = isinstance(exc, asyncio.CancelledError)
            def fail(current: Any):
                round_ = next(r for r in state(current)["rounds"] if r["request_id"] == request)
                if round_["status"] not in {"running", "cancelling"}:
                    return
                status = "cleanup_pending" if not cleanup.get("complete") else "cancelled" if cancelled else "failed"
                code = getattr(exc, "code", "workflow_assistant_timeout" if isinstance(exc, TimeoutError) else "workflow_assistant_failed")
                round_.update(status=status, failure_code=code, completed_at=utc_now())
                if code in {"workflow_candidate_contract_invalid", "workflow_change_scope_violation"}:
                    # These paths are generated by the platform, never SDK error text.
                    round_["validation_issues"] = getattr(exc, "detail", {}).get("validation_issues", [])
                if "result" in locals_for_failure:
                    candidate = locals_for_failure["result"].get("workflow")
                    if isinstance(candidate, dict):
                        round_["candidate"] = safe_context(candidate)
                event(current, request, status, {"code": code, "validation_issues": round_.get("validation_issues", [])})
                for other in state(current)["rounds"]:
                    if other["status"] == "queued":
                        other["status"] = "needs_review"
                        event(current, other["request_id"], "needs_review")
            locals_for_failure = {"result": result} if "result" in locals() else {}
            self.store.mutate_workflow_assistant(draft_id, fail)
            if cancelled:
                raise

    def cancel(self, draft_id: str, request_id: str) -> Any:
        def cancel_one(draft: Any):
            round_ = next((r for r in state(draft)["rounds"] if r["request_id"] == request_id), None)
            if round_ is None:
                raise WorkflowDraftError("Round not found.", code="workflow_round_not_found")
            if round_["status"] == "running":
                round_["status"] = "cancelling"
            elif round_["status"] in {"queued", "needs_review", "waiting_input"}:
                round_.update(status="withdrawn", completed_at=utc_now())
            event(draft, request_id, round_["status"])
        result = self.store.mutate_workflow_assistant(draft_id, cancel_one)
        task = self.tasks.get((draft_id, request_id))
        if task:
            task.cancel()
        else:
            self.dispatch(draft_id)
        return result

    async def stop(self) -> None:
        self.closed = True
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.wait(list(self.tasks.values()), timeout=10)
