"""Draft-only authoring operations. No SAP or repository publication side effects."""
from __future__ import annotations

import asyncio
import base64
import copy
import difflib
import hashlib
import json
import re
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .managed_rules import validate_managed_rule
from .manifests import ManifestError, derive_input_display, validate_execution
from .models import utc_now


class _FeedbackDeadlineExceeded(TimeoutError):
    """Only the platform deadline, never a shorter SDK/network timeout."""


def apply_package_edits(package: dict[str, Any], edits: Any) -> dict[str, Any]:
    """Apply a bounded, non-overlapping JSON Pointer edit set to a pinned copy."""
    from .agent_lifecycle import AgentLifecycleError

    def invalid() -> None:
        raise AgentLifecycleError("Runtime returned an invalid targeted Agent edit.", code="runtime_agent_feedback_invalid")

    if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
        invalid()
    try:
        if len(json.dumps(edits, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 100_000:
            invalid()
    except (TypeError, ValueError):
        invalid()
    parsed: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []
    seen: list[tuple[str, ...]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            invalid()
        op, path = edit.get("op"), edit.get("path")
        required = {"op", "path"} if op == "remove" else {"op", "path", "value"}
        if op not in {"add", "replace", "remove"} or set(edit) != required or not isinstance(path, str) or not path.startswith("/") or re.search(r"~(?![01])", path):
            invalid()
        tokens = tuple(token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/"))
        if tokens[0] not in {"manifest", "readme", "rules", "files"} or any(not token for token in tokens):
            invalid()
        if tokens[0] == "manifest" and (len(tokens) == 1 or tokens[1] in {"validation", "slug", "module", "version"}):
            invalid()
        if any(tokens[:len(previous)] == previous or previous[:len(tokens)] == tokens for previous in seen):
            invalid()
        seen.append(tokens)
        parsed.append((str(op), tokens, edit))
    result = copy.deepcopy(package)

    def index(token: str, array: list[Any], *, insertion: bool = False) -> int:
        if token == "-" and insertion:
            return len(array)
        if not re.fullmatch(r"0|[1-9][0-9]*", token):
            invalid()
        number = int(token)
        if number >= len(array) + (1 if insertion else 0):
            invalid()
        return number

    for op, tokens, edit in parsed:
        parent: Any = result
        for token in tokens[:-1]:
            if isinstance(parent, dict) and token in parent:
                parent = parent[token]
            elif isinstance(parent, list):
                parent = parent[index(token, parent)]
            else:
                invalid()
        final = tokens[-1]
        if isinstance(parent, dict):
            if (op in {"replace", "remove"} and final not in parent) or (op == "add" and final in parent):
                invalid()
            if op == "remove":
                del parent[final]
            else:
                parent[final] = copy.deepcopy(edit["value"])
        elif isinstance(parent, list):
            offset = index(final, parent, insertion=op == "add")
            if op == "remove":
                parent.pop(offset)
            elif op == "add":
                parent.insert(offset, copy.deepcopy(edit["value"]))
            else:
                parent[offset] = copy.deepcopy(edit["value"])
        else:
            invalid()
    return result


def package_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Real values, JSON Pointer paths and stable-ID sequence matching, never truncation."""
    changes: list[dict[str, Any]] = []

    def visit(old: Any, new: Any, path: str, old_exists: bool = True, new_exists: bool = True) -> None:
        if not old_exists and not new_exists:
            return
        if old_exists and new_exists and old == new:
            return
        if old_exists and new_exists and isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                visit(old.get(key), new.get(key), path + "/" + str(key).replace("~", "~0").replace("/", "~1"), key in old, key in new)
            return
        if old_exists and new_exists and isinstance(old, list) and isinstance(new, list):
            def keyed(items: list[Any]) -> bool:
                return all(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] for item in items) and len({item["id"] for item in items}) == len(items)
            if (old or new) and keyed(old) and keyed(new):
                old_map, new_map = {item["id"]: item for item in old}, {item["id"]: item for item in new}
                if list(old_map) != list(new_map):
                    changes.append({"path": path, "change": "reordered", "kind": "order", "before": list(old_map), "after": list(new_map), "before_exists": True, "after_exists": True})
                for key in sorted(set(old_map) | set(new_map)):
                    visit(old_map.get(key), new_map.get(key), path + "/@" + key.replace("~", "~0").replace("/", "~1"), key in old_map, key in new_map)
                return
        change = "added" if not old_exists else "removed" if not new_exists else "type_changed" if type(old) is not type(new) else "modified"
        item = {"path": path, "change": change, "kind": "value", "before": old, "after": new, "before_exists": old_exists, "after_exists": new_exists}
        if path.startswith(("/readme", "/rules", "/files/")):
            item.update(kind="text", unified_diff="\n".join(difflib.unified_diff(str(old or "").splitlines(), str(new or "").splitlines(), fromfile="before", tofile="after", lineterm="")))
        if path.startswith("/binary_files/"):
            def binary_summary(value: Any) -> Any:
                if value is None:
                    return None
                raw = base64.b64decode(str(value), validate=True)
                return {"byte_count": len(raw), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
            item.update(kind="binary", before=binary_summary(old), after=binary_summary(new))
        changes.append(item)

    for key in ("manifest", "readme", "rules", "files", "binary_files"):
        visit(before.get(key), after.get(key), "/" + key, key in before, key in after)
    for item in changes:
        path = item["path"]
        item["group"] = "rules" if path.startswith("/rules") else "documentation" if path.startswith(("/readme", "/files", "/binary_files")) else "inputs_outputs" if any(token in path for token in ("inputSchema", "outputSchema", "/inputs", "/outputs")) else "steps" if any(token in path for token in ("/workflow", "/steps")) else "data_sources" if any(token in path for token in ("/apis", "/sapModules", "/tables")) else "basic"
    return changes


class AgentAuthoringMixin:
    """Composes with the lifecycle service and its persistent CAS operation store."""

    def _authoring_error(self, message: str, code: str) -> Exception:
        from .agent_lifecycle import AgentLifecycleError
        return AgentLifecycleError(message, code=code)

    def _reserve_operation(self, draft_id: str, expected_revision: int, kind: str, request_id: str | None = None, input_hash: str | None = None) -> dict[str, Any]:
        active = self.store.get_agent_operation(draft_id)
        if active and active.get("kind") == "trial":
            # A completed run must not leave a draft locked just because its browser
            # was closed before the next progress poll.
            try:
                self.validation_report(draft_id)
            except KeyError:
                pass
        try:
            return self.store.reserve_agent_operation(draft_id, expected_revision=expected_revision, kind=kind, request_id=request_id, input_hash=input_hash)
        except ValueError as exc:
            raise self._authoring_error("The Agent draft changed or already has an active operation.", str(exc)) from exc

    def _finish_operation(self, draft_id: str, operation_id: str, status: str = "completed") -> None:
        self.store.finish_agent_operation(draft_id, operation_id, status=status)

    def _assert_operation(self, draft_id: str, operation_id: str, expected_revision: int) -> None:
        if not self.store.assert_agent_operation(draft_id, operation_id, expected_revision=expected_revision):
            raise self._authoring_error("The draft operation is stale.", "agent_draft_conflict")

    def get_diff(self, draft_id: str, from_revision: int | None = None, to_revision: int | None = None) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        target = int(to_revision if to_revision is not None else draft["revision"])
        source = int(from_revision if from_revision is not None else max(1, target - 1))
        before = self.store.get_agent_authoring_revision(draft_id, source)["package"]
        after = self.store.get_agent_authoring_revision(draft_id, target)["package"]
        return {"draft_id": draft_id, "from_revision": source, "to_revision": target, "changes": package_changes(before, after)}

    def _apply_package(self, draft_id: str, expected_revision: int, package: dict[str, Any], *, kind: str, operation_id: str, record_turn: bool = True, decision: dict[str, Any] | None = None, safe_runtime: bool = False, deadline_check: Any = None, runtime_thread_id: str | None = None) -> dict[str, Any]:
        self._assert_operation(draft_id, operation_id, expected_revision)
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        previous = self.store.get_agent_authoring_revision(draft_id, expected_revision)["package"]
        package = copy.deepcopy(package)
        manifest = package.get("manifest")
        identity_changed = self._prepare_identity_package(draft, package, kind=kind)
        if not isinstance(manifest, dict) or str(manifest.get("slug") or "") != draft["agent_id"]:
            raise self._authoring_error("Agent ID cannot change inside a draft.", "agent_id_immutable")
        if manifest.get("module") != previous["manifest"].get("module"):
            raise self._authoring_error("Agent module cannot change inside a draft.", "agent_module_immutable")
        self._assert_manageable(manifest)
        manifest["version"] = draft.get("target_version") or manifest.get("version")
        # A user/Runtime must never author its own PASS certificate.
        manifest["validation"] = copy.deepcopy(previous["manifest"].get("validation") or {})
        if identity_changed:
            manifest["validation"] = self._not_tested_identity_validation()
        self._validate_package_paths(package)
        if kind in {"feedback", "manual_edit"}:
            try:
                manifest["inputs"] = derive_input_display(manifest.get("execution", {}).get("inputSchema"))
            except ManifestError:
                # The manual editor may save incomplete definitions for later
                # correction. Runtime proposals must pass the full gate now.
                if safe_runtime:
                    raise
        if safe_runtime:
            validate_execution(manifest, f"agent-draft:{draft_id}")
            from .authoring_checks import candidate_input_issues
            input_issues = candidate_input_issues(manifest)
            if input_issues:
                raise ManifestError("Candidate input references require conditional execution.",
                                    issue_code=input_issues[0]["code"], path=input_issues[0]["path"])
            if package.get("rules"):
                validate_managed_rule(package["rules"], expected_digest=(manifest.get("managedRule") or {}).get("sha256"))
            elif manifest.get("managedRule"):
                raise self._authoring_error("The declared managed rule source is missing.", "agent_managed_rule_missing")
        diff = package_changes(previous, package)
        if not diff:
            return self.get_draft(draft_id)
        risk = self._risk_class(draft, package)
        draft.update(status="draft", revision=expected_revision + 1, risk_class=risk, validation_run_id=None, validation={}, updated_at=utc_now())
        if runtime_thread_id:
            draft["thread_id"] = runtime_thread_id
        draft["metadata"] = {**(draft.get("metadata") or {}), "static_checks": None, "trial": None}
        path = self._draft_path(draft_id)
        if Path(draft["path"]).resolve() != path:
            raise self._authoring_error("Agent draft escaped its registered directory.", "agent_draft_path_invalid")
        # Immutable DB revision is authority; update its local package projection atomically.
        staging = path.with_name(path.name + "-stage-" + uuid.uuid4().hex[:8])
        backup = path.with_name(path.name + "-backup-" + uuid.uuid4().hex[:8])
        import shutil
        committed = False
        try:
            self._write_package(staging, package)
            if callable(deadline_check):
                deadline_check()
            path.replace(backup)
            staging.replace(path)
            try:
                if callable(deadline_check):
                    deadline_check()
                self.store.save_agent_authoring_draft(draft, package=package, diff=diff, expected_revision=expected_revision, operation_id=operation_id, **({"deadline_check": deadline_check} if callable(deadline_check) else {}))
                committed = True
            except Exception:
                shutil.rmtree(path)
                backup.replace(path)
                raise
        except Exception as exc:
            if backup.exists() and not committed:
                if path.exists():
                    shutil.rmtree(path)
                backup.replace(path)
            if isinstance(exc, ValueError) and str(exc).startswith("agent_technical_id_"):
                raise self._authoring_error("The requested Agent ID is unavailable.", str(exc)) from exc
            raise
        finally:
            for temporary in (staging, backup):
                if temporary.exists() and self.draft_root in temporary.resolve().parents:
                    shutil.rmtree(temporary)
        if record_turn:
            turns = self.store.list_agent_conversation_turns(draft_id)
            self.store.save_agent_conversation_turn({"draft_id": draft_id, "turn": max((int(turn["turn"]) for turn in turns), default=0) + 1, "kind": kind, "status": "completed", "decision": decision or {"risk_class": risk}, "base_revision": expected_revision, "result_revision": draft["revision"], "diff": diff, "completed_at": utc_now()})
        return self.get_draft(draft_id)

    def _validate_package_paths(self, package: dict[str, Any]) -> None:
        if not isinstance(package.get("readme"), str) or (package.get("rules") is not None and not isinstance(package["rules"], str)):
            raise self._authoring_error("README and managed rules must be text.", "agent_package_invalid")
        if set(package) - {"manifest", "readme", "rules", "files", "binary_files"}:
            raise self._authoring_error("Agent package contains unsupported entries.", "agent_package_invalid")
        names: set[str] = set()
        for section in ("files", "binary_files"):
            files = package.get(section) or {}
            if not isinstance(files, dict):
                raise self._authoring_error("Agent package files must be an object.", "agent_package_invalid")
            for name, value in files.items():
                normalized = str(name).replace("\\", "/")
                if not isinstance(name, str) or not name or any(part in {"", ".", "..", "versions", ".git", ".local-data", "node_modules"} for part in normalized.split("/")) or ":" in normalized or normalized.startswith("/") or normalized.lower() in {"agent.json", "readme.md", "rules.py", "publication.json", "validation.json"} or normalized.lower() in names or not isinstance(value, str):
                    raise self._authoring_error("Agent package contains an unsafe or conflicting path.", "agent_package_path_invalid")
                names.add(normalized.lower())
                if section == "binary_files":
                    base64.b64decode(value, validate=True)

    def conversation(self, draft_id: str) -> dict[str, Any]:
        self.store.get_agent_authoring_draft(draft_id)
        return {"draft_id": draft_id, "turns": [self._public_feedback_turn(item) for item in self.store.list_agent_conversation_turns(draft_id)], "active_task": self.store.get_agent_operation(draft_id)}

    def _public_feedback_turn(self, original: dict[str, Any]) -> dict[str, Any]:
        """Derive live elapsed time without rewriting history or fabricating old limits."""
        item = copy.deepcopy(original)
        execution = item.get("decision", {}).get("execution")
        if isinstance(execution, dict) and execution.get("started_at") and item.get("status") in {"running", "queued", "cancelling"}:
            started = datetime.fromisoformat(execution["started_at"])
            execution["elapsed_seconds"] = max(0, (datetime.now(timezone.utc) - started).total_seconds())
        return item

    def _feedback_runtime_snapshot(self, draft: dict[str, Any]) -> dict[str, Any]:
        previous = (draft.get("metadata") or {}).get("runtime_snapshot") or {}
        if previous.get("provider_id") and previous.get("model"):
            resolve = getattr(self.runtime, "snapshot_for_model", None)
            if callable(resolve):
                snapshot = copy.deepcopy(resolve(previous["provider_id"], previous["model"]))
                if snapshot.get("provider_id") != previous["provider_id"] or snapshot.get("model") != previous["model"]:
                    raise self._authoring_error("The Runtime returned a different model binding.", "agent_runtime_snapshot_failed")
                snapshot["model_source"] = "agent_draft_binding"
            else:
                # Compatibility for explicit, statically injected test runtimes.
                # Production AgentRuntimeCapability always exposes the resolver.
                snapshot = copy.deepcopy(previous)
        else:
            snapshot = self._new_runtime_snapshot() or {}
            if previous.get("model") and snapshot.get("model") != previous["model"]:
                raise self._authoring_error("The bound draft model is unavailable.", "agent_runtime_binding_missing")
        if not snapshot.get("provider_id") or not snapshot.get("model"):
            raise self._authoring_error("Select an available Runtime and explicit model.", "agent_runtime_binding_missing")
        return copy.deepcopy(snapshot)

    async def _stop_feedback_runtime(self, task: asyncio.Task, operation_id: str) -> None:
        task.cancel()
        cleanup_seconds = min(10.0, max(0.001, float(self.settings.agent_feedback_cleanup_seconds)))
        cleanup_started = time.monotonic()
        done, _ = await asyncio.wait({task}, timeout=cleanup_seconds * 0.75)
        abort = getattr(self.runtime, "abort_agent_feedback", None)
        stopped = bool(done)
        if callable(abort):
            # A finished coroutine alone cannot prove an SDK subprocess has
            # stopped: cancellation can interrupt asynchronous client startup.
            stopped = False
            abort_task = asyncio.create_task(abort(operation_id))
            try:
                abort_done, _ = await asyncio.wait({abort_task}, timeout=max(0, cleanup_seconds - (time.monotonic() - cleanup_started)))
                if abort_done:
                    stopped = bool(abort_task.result())
            except (Exception, asyncio.CancelledError):
                pass
            finally:
                if not abort_task.done():
                    abort_task.cancel()
                    abort_task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
        # A detached provider can never apply a revision: only the parent
        # commits, guarded by the persisted operation/revision barrier.
        if not task.done():
            task.cancel()
            task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
        elif not task.cancelled():
            task.exception()
        if not stopped:
            raise self._authoring_error("Runtime shutdown could not be confirmed. Restart the API before retrying.", "agent_feedback_cleanup_failed")

    async def _await_feedback_runtime(self, awaitable: Any, *, draft_id: str, operation_id: str, timeout: float) -> Any:
        task = asyncio.create_task(awaitable)
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0, timeout))
            if not done:
                self.store.update_agent_operation(draft_id, operation_id, status="cancelling")
                await self._stop_feedback_runtime(task, operation_id)
                raise _FeedbackDeadlineExceeded()
            return task.result()
        except asyncio.CancelledError:
            try:
                await self._stop_feedback_runtime(task, operation_id)
            except asyncio.CancelledError as exc:
                raise self._authoring_error("Runtime shutdown was interrupted. Restart the API before retrying.", "agent_feedback_cleanup_failed") from exc
            raise

    async def submit_feedback(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        revision = int(getattr(payload, "expected_revision", None) or payload.base_revision)
        turns = self.store.list_agent_conversation_turns(draft_id)
        latest_turn = max((int(turn["turn"]) for turn in turns), default=0)
        retry_of_turn = getattr(payload, "retry_of_turn", None)
        fingerprint = hashlib.sha256(json.dumps({"revision": revision, "feedback": str(payload.feedback), "base_turn": int(payload.base_turn), "locale": str(payload.locale), "retry_of_turn": retry_of_turn}, sort_keys=True).encode()).hexdigest()
        operation = self._reserve_operation(draft_id, revision, "feedback", getattr(payload, "request_id", None), fingerprint)
        if operation.get("reused"):
            match = next((item for item in turns if item.get("decision", {}).get("task_id") == operation["operation_id"]), None)
            return {"draft_id": draft_id, "task_id": operation["operation_id"], "status": (match or operation).get("status"), "turn": (match or {}).get("turn"), "base_revision": revision}
        if int(payload.base_turn) != latest_turn:
            self._finish_operation(draft_id, operation["operation_id"], "failed")
            raise self._authoring_error("Agent conversation changed.", "agent_draft_conflict")
        if retry_of_turn is not None:
            source = next((item for item in turns if item["turn"] == retry_of_turn), None)
            if not source or source["kind"] != "feedback" or source["status"] not in {"failed", "cancelled", "interrupted"}:
                self._finish_operation(draft_id, operation["operation_id"], "failed")
                raise self._authoring_error("Only a failed or interrupted turn in this draft can be retried.", "agent_feedback_retry_invalid")
        try:
            snapshot = self._feedback_runtime_snapshot(draft)
            binding_error = None
        except Exception as exc:
            snapshot = copy.deepcopy((draft.get("metadata") or {}).get("runtime_snapshot") or {})
            binding_error = self._feedback_error_code(exc)
            if binding_error == "runtime_agent_feedback_failed":
                binding_error = "agent_runtime_snapshot_failed"
        decision = {"task_id": operation["operation_id"], "runtime_snapshot": snapshot,
                    "agent_id": draft["agent_id"], "retry_of_turn": retry_of_turn,
                    "execution": {"timeout_seconds": float(self.feedback_timeout_seconds), "elapsed_seconds": 0}}
        if snapshot.get("provider_id") == "codex":
            # New submissions only. Existing persisted/offline operations retain
            # their original permissions. The user accepted loopback reachability;
            # do not translate that into a successful network-isolation claim.
            from .runtime_execution import FULL_ACCESS_POLICY
            decision["authoring_policy"] = copy.deepcopy(FULL_ACCESS_POLICY)
        if binding_error:
            decision["binding_error"] = binding_error
        turn = {"draft_id": draft_id, "turn": latest_turn + 1, "parent_turn": latest_turn or None, "kind": "feedback", "status": "queued", "user_message": str(payload.feedback), "decision": decision, "base_revision": revision, "result_revision": None, "diff": [], "created_at": utc_now()}
        self.store.save_agent_conversation_turn(turn)
        self.store.update_agent_operation(draft_id, operation["operation_id"], detail={"turn": turn["turn"]})
        task = asyncio.create_task(self._run_feedback(draft_id, turn, payload, operation["operation_id"]))
        self._feedback_tasks[operation["operation_id"]] = task
        task.add_done_callback(lambda _: self._feedback_tasks.pop(operation["operation_id"], None))
        return {"draft_id": draft_id, "task_id": operation["operation_id"], "turn": turn["turn"], "status": "queued", "base_revision": revision}

    async def _run_feedback(self, draft_id: str, turn: dict[str, Any], payload: Any, operation_id: str) -> None:
        status = "failed"
        monotonic = getattr(self, "_feedback_clock", time.monotonic)
        started = monotonic()
        execution = turn["decision"]["execution"]
        budget = float(execution["timeout_seconds"])
        started_at = datetime.now(timezone.utc)
        execution.update(started_at=started_at.isoformat(), deadline_at=(started_at + timedelta(seconds=budget)).isoformat())
        def check_deadline() -> None:
            if monotonic() - started >= budget:
                raise _FeedbackDeadlineExceeded()

        try:
            self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
            turn["status"] = "running"
            self.store.save_agent_conversation_turn(turn)
            draft = self.store.get_agent_authoring_draft(draft_id)
            current = self.store.get_agent_authoring_revision(draft_id, int(turn["base_revision"]))["package"]
            runtime_package = copy.deepcopy(current)
            private_files = set((draft.get("metadata") or {}).get("private_source_files") or [])
            runtime_package["files"] = {key: value for key, value in (runtime_package.get("files") or {}).items() if key not in private_files}
            runtime_package.pop("binary_files", None)
            snapshot = turn["decision"]["runtime_snapshot"]
            if turn["decision"].get("binding_error"):
                raise self._authoring_error("The draft Runtime configuration needs attention.", turn["decision"]["binding_error"])
            if not ((draft.get("metadata") or {}).get("runtime_snapshot") or {}).get("model"):
                self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
                draft["metadata"] = {**(draft.get("metadata") or {}), "runtime_snapshot": copy.deepcopy(snapshot)}
                self.store.save_agent_authoring_draft(draft, expected_revision=int(turn["base_revision"]), operation_id=operation_id)
            pin = getattr(self.runtime, "pin", None)
            pin_options = {"reasoning_effort": snapshot["reasoning_effort"]} if snapshot.get("reasoning_effort") is not None else {}
            context = pin(snapshot["provider_id"], snapshot["model"], **pin_options) if callable(pin) else nullcontext()
            history = [{"user_message": item.get("user_message"), "summary": item.get("decision", {}).get("summary"), "action": item.get("decision", {}).get("action")} for item in self.store.list_agent_conversation_turns(draft_id) if item["turn"] < turn["turn"] and item["status"] == "completed"]
            with context:
                supports = getattr(self.runtime, "supports", None)
                if callable(supports) and not supports("review_agent_feedback"):
                    raise self._authoring_error("The Runtime does not support Agent authoring.", "runtime_agent_feedback_unavailable")
                tool_options = {"tool_policy": copy.deepcopy(turn["decision"]["authoring_policy"])} if turn["decision"].get("authoring_policy") else {}
                decision = await self._await_feedback_runtime(self.runtime.review_agent_feedback(feedback=str(payload.feedback), locale=str(payload.locale), package=runtime_package, history=history, thread_id=None if turn["decision"].get("retry_of_turn") else draft.get("thread_id"), operation_id=operation_id, **tool_options), draft_id=draft_id, operation_id=operation_id, timeout=budget - (monotonic() - started))
            check_deadline()
            self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
            if isinstance(decision.get("harness"), dict):
                turn["decision"]["harness"] = copy.deepcopy(decision["harness"])
            action = decision.get("action")
            if action not in {"clarify", "reply", "revise_agent"} or not isinstance(decision.get("summary"), dict) or not all(isinstance(decision["summary"].get(lang), str) for lang in ("zh", "en")):
                raise self._authoring_error("The Runtime response is invalid.", "runtime_agent_feedback_invalid")
            result = None
            if action == "revise_agent":
                if "edits" in decision and decision.get("package") is not None:
                    raise self._authoring_error("Runtime must choose targeted edits or a complete package.", "runtime_agent_feedback_invalid")
                package = apply_package_edits(runtime_package, decision["edits"]) if "edits" in decision else decision.get("package")
                if not isinstance(package, dict) or not {"manifest", "readme", "rules", "files"}.issubset(package):
                    raise self._authoring_error("The Runtime must return a complete Agent package.", "runtime_agent_feedback_invalid")
                if not isinstance(package["files"], dict) or private_files.intersection(package["files"]):
                    raise self._authoring_error("The Runtime cannot modify private source material.", "runtime_agent_feedback_invalid")
                package["files"].update({key: value for key, value in (current.get("files") or {}).items() if key in private_files})
                if current.get("binary_files"):
                    package["binary_files"] = copy.deepcopy(current["binary_files"])
                if "edits" in decision and isinstance(package.get("rules"), str) and package["rules"] and package["rules"] != current.get("rules"):
                    from .managed_rules import source_digest
                    if isinstance(package["manifest"].get("managedRule"), dict):
                        package["manifest"]["managedRule"]["sha256"] = source_digest(package["rules"])
                check_deadline()
                result = self._apply_package(draft_id, int(turn["base_revision"]), package, kind="feedback", operation_id=operation_id, record_turn=False, safe_runtime=True, deadline_check=check_deadline, runtime_thread_id=decision.get("thread_id"))
            changed = bool(result and int(result["revision"]) != int(turn["base_revision"]))
            if changed:
                # The thread and content revision share one commit. A later
                # metadata write must not turn an applied change into a failure.
                refreshed = result
            else:
                refreshed = self.store.get_agent_authoring_draft(draft_id)
                refreshed["thread_id"] = decision.get("thread_id") or refreshed.get("thread_id")
                check_deadline()
                self.store.save_agent_authoring_draft(refreshed, expected_revision=int(refreshed["revision"]), operation_id=operation_id, deadline_check=check_deadline)
            turn.update(status="completed", result_revision=int(refreshed["revision"]), decision={**turn["decision"], "action": action, "summary": decision["summary"], "changed": bool(result and int(result["revision"]) != int(turn["base_revision"]))}, diff=result["diff"] if result and int(result["revision"]) != int(turn["base_revision"]) else [])
            status = "completed"
        except asyncio.CancelledError:
            status = "cancelled"
            turn.update(status=status, decision={**turn["decision"], "error_code": "agent_feedback_cancelled"})
        except Exception as exc:
            code = self._feedback_error_code(exc)
            turn.update(status="failed", decision={**turn["decision"], "error_code": code})
            if isinstance(exc, ManifestError):
                turn["decision"]["validation_issues"] = [exc.public_issue()]
        finally:
            operation = self.store.get_agent_operation_by_id(draft_id, operation_id)
            code = turn["decision"].get("error_code")
            if operation["status"] == "cancelling" and code not in {"agent_feedback_timeout", "agent_feedback_cleanup_failed"}:
                # Cancellation is a persisted write barrier, even if a Provider
                # swallows CancelledError and returns a late revision proposal.
                status = "cancelled"
                turn.update(status=status, result_revision=None, diff=[], decision={**turn["decision"], "error_code": "agent_feedback_cancelled"})
            turn["completed_at"] = utc_now()
            turn["decision"]["execution"].update(completed_at=turn["completed_at"], elapsed_seconds=max(0, monotonic() - started))
            self.store.save_agent_conversation_turn(turn)
            if code != "agent_feedback_cleanup_failed":
                self._finish_operation(draft_id, operation_id, status)

    @staticmethod
    def _feedback_error_code(exc: Exception) -> str:
        """Persist a safe failure category, never SDK messages or business text."""
        if isinstance(exc, _FeedbackDeadlineExceeded):
            return "agent_feedback_timeout"
        if isinstance(exc, TimeoutError):
            return "runtime_agent_feedback_connection_timeout"
        if isinstance(exc, json.JSONDecodeError):
            return "runtime_agent_feedback_invalid"
        if isinstance(exc, ManifestError):
            return "agent_definition_invalid"
        import re
        code = getattr(exc, "code", None)
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            return code
        message = str(exc).lower()
        if message in {"runtime_agent_feedback_invalid", "agent_authoring_context_too_large", "agent_authoring_isolation_failed", "agent_runtime_binding_missing"}:
            return message
        if any(token in message for token in ("unauthorized", "authentication", "not logged in", "invalid api key", "token expired")):
            return "runtime_model_authentication_failed"
        if any(token in message for token in ("requires a newer version", "model_not_found", "unknown model", "model is not supported")):
            return "runtime_model_incompatible"
        if any(token in message for token in ("permission denied", "do not have access", "access denied")):
            return "runtime_model_access_unavailable"
        if isinstance(exc, ConnectionError) or any(token in message for token in ("connection", "stream disconnected", "network", "broken pipe")):
            return "runtime_agent_feedback_connection_failed"
        return "runtime_agent_feedback_failed"

    async def cancel_feedback(self, draft_id: str, turn_number: int) -> dict[str, Any]:
        turns = self.store.list_agent_conversation_turns(draft_id)
        turn = next((item for item in turns if item["turn"] == turn_number), None)
        if turn is None:
            raise KeyError((draft_id, turn_number))
        if turn["status"] not in {"queued", "running"}:
            return self.conversation(draft_id)
        operation_id = turn.get("decision", {}).get("task_id")
        already_cancelling = False
        if operation_id:
            already_cancelling = self.store.get_agent_operation_by_id(draft_id, operation_id)["status"] == "cancelling"
            self.store.update_agent_operation(draft_id, operation_id, status="cancelling")
        task = self._feedback_tasks.get(operation_id)
        if task and not task.done():
            if not already_cancelling:
                task.cancel()
            # Repeated requests (or a disconnected HTTP client) must not
            # interrupt the task's bounded shutdown a second time.
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            turn = next(item for item in self.store.list_agent_conversation_turns(draft_id) if item["turn"] == turn_number)
        if turn["status"] in {"queued", "running"}:
            turn.update(status="cancelled", completed_at=utc_now(), decision={**(turn.get("decision") or {}), "task_id": operation_id, "error_code": "agent_feedback_cancelled"})
            self.store.save_agent_conversation_turn(turn)
            if operation_id:
                self._finish_operation(draft_id, operation_id, "cancelled")
        return self.conversation(draft_id)

    async def stop(self) -> None:
        for draft in self.store.list_agent_authoring_drafts():
            for turn in self.store.list_agent_conversation_turns(draft["draft_id"]):
                # Persisted turns may outlive the in-memory asyncio task (for
                # example after a graceful shutdown or a task-start race).  A
                # shutdown must still release their operation reservation and
                # make the user-visible turn terminal; cancel_feedback also
                # establishes the cancelling write barrier before cancelling a
                # live task so a late Runtime response cannot revise a draft.
                if turn["status"] in {"queued", "running"}:
                    await self.cancel_feedback(draft["draft_id"], int(turn["turn"]))
