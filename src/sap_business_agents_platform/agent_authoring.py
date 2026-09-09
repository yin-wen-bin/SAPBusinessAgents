"""Draft-only authoring operations. No SAP or repository publication side effects."""
from __future__ import annotations

import asyncio
import base64
import copy
import difflib
import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .managed_rules import validate_managed_rule
from .manifests import validate_execution
from .models import utc_now


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

    def _apply_package(self, draft_id: str, expected_revision: int, package: dict[str, Any], *, kind: str, operation_id: str, record_turn: bool = True, decision: dict[str, Any] | None = None, safe_runtime: bool = False) -> dict[str, Any]:
        self._assert_operation(draft_id, operation_id, expected_revision)
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        previous = self.store.get_agent_authoring_revision(draft_id, expected_revision)["package"]
        package = copy.deepcopy(package)
        manifest = package.get("manifest")
        if not isinstance(manifest, dict) or str(manifest.get("slug") or "") != draft["agent_id"]:
            raise self._authoring_error("Agent ID cannot change inside a draft.", "agent_id_immutable")
        if manifest.get("module") != previous["manifest"].get("module"):
            raise self._authoring_error("Agent module cannot change inside a draft.", "agent_module_immutable")
        self._assert_manageable(manifest)
        manifest["version"] = draft.get("target_version") or manifest.get("version")
        # A user/Runtime must never author its own PASS certificate.
        manifest["validation"] = copy.deepcopy(previous["manifest"].get("validation") or {})
        self._validate_package_paths(package)
        if safe_runtime:
            validate_execution(manifest, f"agent-draft:{draft_id}")
            if package.get("rules"):
                validate_managed_rule(package["rules"], expected_digest=(manifest.get("managedRule") or {}).get("sha256"))
            elif manifest.get("managedRule"):
                raise self._authoring_error("The declared managed rule source is missing.", "agent_managed_rule_missing")
        diff = package_changes(previous, package)
        if not diff:
            return self.get_draft(draft_id)
        risk = self._risk_class(draft, package)
        draft.update(status="draft", revision=expected_revision + 1, risk_class=risk, validation_run_id=None, validation={}, updated_at=utc_now())
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
            path.replace(backup)
            staging.replace(path)
            try:
                self.store.save_agent_authoring_draft(draft, package=package, diff=diff, expected_revision=expected_revision, operation_id=operation_id)
                committed = True
            except Exception:
                shutil.rmtree(path)
                backup.replace(path)
                raise
        except Exception:
            if backup.exists() and not committed:
                if path.exists():
                    shutil.rmtree(path)
                backup.replace(path)
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
        return {"draft_id": draft_id, "turns": self.store.list_agent_conversation_turns(draft_id), "active_task": self.store.get_agent_operation(draft_id)}

    async def submit_feedback(self, draft_id: str, payload: Any) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        revision = int(getattr(payload, "expected_revision", None) or payload.base_revision)
        turns = self.store.list_agent_conversation_turns(draft_id)
        latest_turn = max((int(turn["turn"]) for turn in turns), default=0)
        fingerprint = hashlib.sha256(json.dumps({"revision": revision, "feedback": str(payload.feedback), "base_turn": int(payload.base_turn), "locale": str(payload.locale)}, sort_keys=True).encode()).hexdigest()
        operation = self._reserve_operation(draft_id, revision, "feedback", getattr(payload, "request_id", None), fingerprint)
        if operation.get("reused"):
            match = next((item for item in turns if item.get("decision", {}).get("task_id") == operation["operation_id"]), None)
            return {"draft_id": draft_id, "task_id": operation["operation_id"], "status": (match or operation).get("status"), "turn": (match or {}).get("turn"), "base_revision": revision}
        if int(payload.base_turn) != latest_turn:
            self._finish_operation(draft_id, operation["operation_id"], "failed")
            raise self._authoring_error("Agent conversation changed.", "agent_draft_conflict")
        turn = {"draft_id": draft_id, "turn": latest_turn + 1, "parent_turn": latest_turn or None, "kind": "feedback", "status": "queued", "user_message": str(payload.feedback), "decision": {"task_id": operation["operation_id"]}, "base_revision": revision, "result_revision": None, "diff": [], "created_at": utc_now()}
        self.store.save_agent_conversation_turn(turn)
        self.store.update_agent_operation(draft_id, operation["operation_id"], detail={"turn": turn["turn"]})
        task = asyncio.create_task(self._run_feedback(draft_id, turn, payload, operation["operation_id"]))
        self._feedback_tasks[operation["operation_id"]] = task
        task.add_done_callback(lambda _: self._feedback_tasks.pop(operation["operation_id"], None))
        return {"draft_id": draft_id, "task_id": operation["operation_id"], "turn": turn["turn"], "status": "queued", "base_revision": revision}

    async def _run_feedback(self, draft_id: str, turn: dict[str, Any], payload: Any, operation_id: str) -> None:
        status = "failed"
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
            snapshot = (draft.get("metadata") or {}).get("runtime_snapshot") or {}
            if not snapshot.get("provider_id") or not snapshot.get("model"):
                # Draft creation and deterministic editing do not require a Runtime.
                # Bind legacy/offline drafts once, at their first actual Runtime turn.
                snapshot = self._new_runtime_snapshot() or {}
                if not snapshot.get("provider_id") or not snapshot.get("model"):
                    raise self._authoring_error("Select an available Runtime and explicit model before starting authoring.", "agent_runtime_binding_missing")
                self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
                draft["metadata"] = {**(draft.get("metadata") or {}), "runtime_snapshot": copy.deepcopy(snapshot)}
                self.store.save_agent_authoring_draft(draft, expected_revision=int(turn["base_revision"]), operation_id=operation_id)
            pin = getattr(self.runtime, "pin", None)
            context = pin(snapshot["provider_id"], snapshot["model"]) if callable(pin) else nullcontext()
            history = [{"user_message": item.get("user_message"), "summary": item.get("decision", {}).get("summary"), "action": item.get("decision", {}).get("action")} for item in self.store.list_agent_conversation_turns(draft_id) if item["turn"] < turn["turn"]]
            with context:
                supports = getattr(self.runtime, "supports", None)
                if callable(supports) and not supports("review_agent_feedback"):
                    raise self._authoring_error("The Runtime does not support Agent authoring.", "runtime_agent_feedback_unavailable")
                decision = await asyncio.wait_for(self.runtime.review_agent_feedback(feedback=str(payload.feedback), locale=str(payload.locale), package=runtime_package, history=history, thread_id=draft.get("thread_id")), timeout=self.feedback_timeout_seconds)
            self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
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
                result = self._apply_package(draft_id, int(turn["base_revision"]), package, kind="feedback", operation_id=operation_id, record_turn=False, safe_runtime=True)
            refreshed = self.store.get_agent_authoring_draft(draft_id)
            refreshed["thread_id"] = decision.get("thread_id") or refreshed.get("thread_id")
            self.store.save_agent_authoring_draft(refreshed, expected_revision=int(refreshed["revision"]), operation_id=operation_id)
            turn.update(status="completed", result_revision=int(refreshed["revision"]), decision={"task_id": operation_id, "action": action, "summary": decision["summary"], "changed": bool(result and int(result["revision"]) != int(turn["base_revision"]))}, diff=result["diff"] if result and int(result["revision"]) != int(turn["base_revision"]) else [])
            status = "completed"
        except asyncio.CancelledError:
            status = "cancelled"
            turn.update(status=status, decision={"task_id": operation_id, "error_code": "agent_feedback_cancelled"})
        except Exception as exc:
            code = "agent_feedback_timeout" if isinstance(exc, TimeoutError) else getattr(exc, "code", "runtime_agent_feedback_failed")
            turn.update(status="failed", decision={"task_id": operation_id, "error_code": code})
        finally:
            operation = self.store.get_agent_operation_by_id(draft_id, operation_id)
            if operation["status"] == "cancelling":
                # Cancellation is a persisted write barrier, even if a Provider
                # swallows CancelledError and returns a late revision proposal.
                status = "cancelled"
                turn.update(status=status, result_revision=None, diff=[], decision={"task_id": operation_id, "error_code": "agent_feedback_cancelled"})
            turn["completed_at"] = utc_now()
            self.store.save_agent_conversation_turn(turn)
            self._finish_operation(draft_id, operation_id, status)

    async def cancel_feedback(self, draft_id: str, turn_number: int) -> dict[str, Any]:
        turns = self.store.list_agent_conversation_turns(draft_id)
        turn = next((item for item in turns if item["turn"] == turn_number), None)
        if turn is None:
            raise KeyError((draft_id, turn_number))
        if turn["status"] not in {"queued", "running"}:
            return self.conversation(draft_id)
        operation_id = turn.get("decision", {}).get("task_id")
        if operation_id:
            self.store.update_agent_operation(draft_id, operation_id, status="cancelling")
        task = self._feedback_tasks.get(operation_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            turn = next(item for item in self.store.list_agent_conversation_turns(draft_id) if item["turn"] == turn_number)
        if turn["status"] in {"queued", "running"}:
            turn.update(status="cancelled", completed_at=utc_now(), decision={"task_id": operation_id, "error_code": "agent_feedback_cancelled"})
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
