"""Draft-only authoring operations. No SAP or repository publication side effects."""
from __future__ import annotations

import asyncio
import base64
import binascii
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
from types import SimpleNamespace
from typing import Any

from .acceptance import agent_execution_digest
from .managed_rules import validate_managed_rule
from .manifests import ManifestError, derive_input_display, validate_execution
from .models import utc_now
from .relationships import apply_advisory_relationship_policy


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

    def _reserve_operation(self, draft_id: str, expected_revision: int, kind: str, request_id: str | None = None, input_hash: str | None = None, *, allow_published: bool = False) -> dict[str, Any]:
        active = self.store.get_agent_operation(draft_id)
        if active and active.get("kind") == "trial":
            # A completed run must not leave a draft locked just because its browser
            # was closed before the next progress poll.
            try:
                self.validation_report(draft_id)
            except KeyError:
                pass
        try:
            return self.store.reserve_agent_operation(draft_id, expected_revision=expected_revision, kind=kind, request_id=request_id, input_hash=input_hash, allow_published=allow_published)
        except ValueError as exc:
            raise self._authoring_error("The Agent draft changed or already has an active operation.", str(exc)) from exc

    def _finish_operation(self, draft_id: str, operation_id: str, status: str = "completed") -> None:
        self.store.finish_agent_operation(draft_id, operation_id, status=status)

    def _assert_operation(self, draft_id: str, operation_id: str, expected_revision: int) -> None:
        if not self.store.assert_agent_operation(draft_id, operation_id, expected_revision=expected_revision):
            raise self._authoring_error("The draft operation is stale.", "agent_draft_conflict")

    @staticmethod
    def _limited_feedback_report(report: Any) -> dict[str, Any]:
        """Keep useful prior evidence while excluding submitted parameters and credentials."""
        if not isinstance(report, dict):
            return {}
        allowed = {
            "type", "status", "verdict", "completed_at", "business_output_available",
            "output_schema_valid", "read_only_audit", "source_complete", "evidence_complete",
            "errors", "business_report", "presentation", "blocking_limitations",
            "report_digest", "case_count", "passed_cases", "failed_cases",
        }
        blocked_tokens = {"input", "secret", "credential", "password", "token", "authorization",
                          "raw_rows", "source_rows", "encrypted", "sealed", "protected"}

        def clean(value: Any, depth: int = 0) -> Any:
            if depth > 8:
                return None
            if isinstance(value, dict):
                restricted = value.get("restricted") is True or value.get("raw_rows_restricted") is True
                return {
                    str(key): clean(item, depth + 1)
                    for key, item in list(value.items())[:200]
                    if not any(token in str(key).lower() for token in blocked_tokens)
                    and not (restricted and str(key).lower() in {"rows", "records", "values", "items"})
                }
            if isinstance(value, list):
                return [clean(item, depth + 1) for item in value[:200]]
            if isinstance(value, str):
                return value[:10_000]
            if isinstance(value, (int, float, bool)) or value is None:
                return value
            return str(value)

        return {key: clean(report[key]) for key in allowed if key in report}

    def _feedback_context(self, draft_id: str, revision: int, payload: Any) -> dict[str, Any]:
        context = {key: value for key, value in {
            "step": getattr(payload, "step", None),
            "field_path": getattr(payload, "field_path", None),
            "run_id": getattr(payload, "run_id", None),
            "acceptance_campaign_id": getattr(payload, "acceptance_campaign_id", None),
        }.items() if value is not None}
        field_path = context.get("field_path")
        if field_path:
            if not isinstance(field_path, str) or not field_path.startswith("/manifest/"):
                raise self._authoring_error("The feedback field path is invalid.", "agent_feedback_context_invalid")
            current: Any = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
            try:
                for raw in field_path.split("/")[1:]:
                    key = raw.replace("~1", "/").replace("~0", "~")
                    current = current[int(key)] if isinstance(current, list) else current[key]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise self._authoring_error("The feedback field path does not exist.", "agent_feedback_context_invalid") from exc
        run_id = context.get("run_id")
        if run_id:
            try:
                attempt = self.store.get_agent_validation_attempt(draft_id, str(run_id))
            except KeyError as exc:
                raise self._authoring_error("The referenced run does not belong to this draft.", "agent_feedback_context_invalid") from exc
            context["run_evidence"] = self._limited_feedback_report(attempt.get("report"))
        campaign_id = context.get("acceptance_campaign_id")
        if campaign_id:
            try:
                campaign = self.store.get_agent_acceptance_campaign(draft_id, str(campaign_id))
            except KeyError as exc:
                raise self._authoring_error("The referenced acceptance campaign does not belong to this draft.", "agent_feedback_context_invalid") from exc
            context["acceptance_evidence"] = {
                "status": campaign.get("status"),
                "phase": campaign.get("phase"),
                "report": self._limited_feedback_report(campaign.get("report")),
                "cases": [
                    {key: item.get(key) for key in ("case_id", "status", "verdict", "error_code") if item.get(key) is not None}
                    for item in campaign.get("cases") or []
                ],
            }
        annotations = getattr(payload, "annotations", None) or []
        if annotations:
            current_package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
            checked: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str | None]] = set()
            for annotation in annotations:
                item = annotation.model_dump() if hasattr(annotation, "model_dump") else dict(annotation)
                kind, path = item["kind"], item["path"]
                if int(item["revision"]) != revision or re.search(r"~(?![01])", path):
                    raise self._authoring_error("The annotation is stale or invalid.", "agent_feedback_annotation_invalid")
                key = (kind, path, item.get("source_id"))
                if key in seen:
                    raise self._authoring_error("Duplicate annotations are not allowed.", "agent_feedback_annotation_invalid")
                seen.add(key)
                if kind == "manifest_field":
                    if not path.startswith("/manifest/") or item.get("source_id") or item.get("source_digest"):
                        raise self._authoring_error("The annotation target is invalid.", "agent_feedback_annotation_invalid")
                    source: Any = current_package
                else:
                    if not item.get("source_digest"):
                        raise self._authoring_error("The diagnostic binding is missing.", "agent_feedback_annotation_invalid")
                    if kind == "static_issue":
                        if item.get("source_id"):
                            raise self._authoring_error("The diagnostic binding is invalid.", "agent_feedback_annotation_invalid")
                        report = self.get_draft(draft_id).get("static_checks") or {}
                        source = {"errors": report.get("errors") or []}
                        digest = self._feedback_digest(source)
                    elif kind == "trial_issue":
                        if not item.get("source_id"):
                            raise self._authoring_error("The run ID is missing.", "agent_feedback_annotation_invalid")
                        try:
                            attempt = self.store.get_agent_validation_attempt(draft_id, item["source_id"])
                        except KeyError as exc:
                            raise self._authoring_error("The referenced run does not belong to this draft.", "agent_feedback_annotation_invalid") from exc
                        if int(attempt["revision"]) != revision:
                            raise self._authoring_error("The run belongs to another revision.", "agent_feedback_annotation_invalid")
                        source = self._limited_feedback_report(attempt.get("report"))
                        digest = self._feedback_digest(source)
                    else:
                        if not item.get("source_id"):
                            raise self._authoring_error("The campaign ID is missing.", "agent_feedback_annotation_invalid")
                        try:
                            campaign = self.store.get_agent_acceptance_campaign(draft_id, item["source_id"])
                        except KeyError as exc:
                            raise self._authoring_error("The referenced campaign does not belong to this draft.", "agent_feedback_annotation_invalid") from exc
                        if int(campaign["revision"]) != revision:
                            raise self._authoring_error("The campaign belongs to another revision.", "agent_feedback_annotation_invalid")
                        source = self._limited_feedback_report(campaign.get("report"))
                        digest = self._feedback_digest(source)
                    if item["source_digest"] != digest:
                        raise self._authoring_error("The diagnostic changed.", "agent_feedback_annotation_invalid")
                try:
                    for raw in path.split("/")[1:]:
                        token = raw.replace("~1", "/").replace("~0", "~")
                        source = source[int(token)] if isinstance(source, list) else source[token]
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise self._authoring_error("The annotation target does not exist.", "agent_feedback_annotation_invalid") from exc
                checked.append({key: item[key] for key in ("kind", "path", "comment", "revision", "source_id", "source_digest") if item.get(key) is not None})
            context["annotations"] = checked
        image_ids = getattr(payload, "image_ids", None) or []
        if image_ids:
            if len(set(image_ids)) != len(image_ids):
                raise self._authoring_error("Duplicate screenshots are not allowed.", "agent_feedback_image_invalid")
            for image_id in image_ids:
                self._feedback_image_data(draft_id, image_id)
            context["image_ids"] = list(image_ids)
        selection = getattr(payload, "selection", None)
        if selection is not None:
            selected = selection.model_dump() if hasattr(selection, "model_dump") else dict(selection)
            kind, path = selected["kind"], selected["path"]
            if int(selected["revision"]) != revision or re.search(r"~(?![01])", path):
                raise self._authoring_error("The selected text is stale.", "agent_feedback_selection_invalid")
            package = self.store.get_agent_authoring_revision(draft_id, revision)["package"]
            if kind == "manifest_field" and path.startswith("/manifest/"):
                source = package
            elif kind in {"readme", "rules"} and path == f"/{kind}":
                source = package
            elif kind in {"trial_report", "acceptance_report"} and selected.get("source_id") and selected.get("source_digest"):
                if kind == "trial_report":
                    try:
                        attempt = self.store.get_agent_validation_attempt(draft_id, selected["source_id"])
                    except KeyError as exc:
                        raise self._authoring_error("The selected run does not belong to this draft.", "agent_feedback_selection_invalid") from exc
                    source = self._limited_feedback_report(attempt.get("report"))
                    source_revision = attempt["revision"]
                else:
                    try:
                        campaign = self.store.get_agent_acceptance_campaign(draft_id, selected["source_id"])
                    except KeyError as exc:
                        raise self._authoring_error("The selected campaign does not belong to this draft.", "agent_feedback_selection_invalid") from exc
                    source = self._limited_feedback_report(campaign.get("report"))
                    source_revision = campaign["revision"]
                if int(source_revision) != revision or self._feedback_digest(source) != selected["source_digest"]:
                    raise self._authoring_error("The selected report changed.", "agent_feedback_selection_invalid")
            else:
                raise self._authoring_error("The selected text has no supported source.", "agent_feedback_selection_invalid")
            try:
                for raw in ([] if path == "/" and kind in {"trial_report", "acceptance_report"} else path.split("/")[1:]):
                    token = raw.replace("~1", "/").replace("~0", "~")
                    source = source[int(token)] if isinstance(source, list) else source[token]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise self._authoring_error("The selected text source does not exist.", "agent_feedback_selection_invalid") from exc
            excerpt = re.sub(r"\s+", " ", selected["excerpt"].strip())
            source_text = re.sub(r"\s+", " ", source if isinstance(source, str) else json.dumps(source, ensure_ascii=False, default=str))
            if not excerpt or excerpt not in source_text:
                raise self._authoring_error("The selected text no longer matches the source.", "agent_feedback_selection_invalid")
            context["selection"] = {**{key: selected[key] for key in ("kind", "path", "revision", "source_id", "source_digest") if selected.get(key) is not None}, "excerpt": excerpt}
        return context

    @staticmethod
    def _feedback_digest(value: Any) -> str:
        return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

    def _feedback_image_directory(self, draft_id: str) -> Path:
        key = hashlib.sha256(draft_id.encode("utf-8")).hexdigest()
        return Path(self.settings.data_root).resolve() / "assistant-images" / key

    def save_feedback_image(self, draft_id: str, data_url: str) -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        snapshot = self._feedback_runtime_snapshot(draft)
        if not self._assistant_capabilities(snapshot)["image_input"]:
            raise self._authoring_error("This Runtime does not accept images.", "agent_feedback_image_unsupported")
        match = re.fullmatch(r"data:(image/png|image/jpeg);base64,([A-Za-z0-9+/=]+)", data_url)
        if not match:
            raise self._authoring_error("Only PNG or JPEG screenshots are supported.", "agent_feedback_image_invalid")
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise self._authoring_error("The screenshot is invalid.", "agent_feedback_image_invalid") from exc
        if not content or len(content) > 1_000_000:
            raise self._authoring_error("The screenshot exceeds the 1 MB limit.", "agent_feedback_image_invalid")
        if not (len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n") and content[12:16] == b"IHDR"
                if match.group(1) == "image/png" else len(content) >= 4 and content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")):
            raise self._authoring_error("The screenshot content does not match its type.", "agent_feedback_image_invalid")
        folder = self._feedback_image_directory(draft_id)
        folder.mkdir(parents=True, exist_ok=True)
        image_root = folder.parent
        for draft_folder in image_root.iterdir():
            if not draft_folder.is_dir() or draft_folder.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", draft_folder.name):
                continue
            for old in draft_folder.glob("feedback_image_*.bin"):
                if old.is_file() and not old.is_symlink() and time.time() - old.stat().st_mtime > 86400:
                    old.unlink()
        if sum(1 for item in folder.glob("feedback_image_*.bin") if item.is_file() and not item.is_symlink()) >= 20:
            raise self._authoring_error("Too many active screenshots for this draft.", "agent_feedback_image_limit")
        image_id = f"feedback_image_{uuid.uuid4().hex}"
        (folder / f"{image_id}.bin").write_bytes(content)
        return {"image_id": image_id, "mime_type": match.group(1),
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}

    def _feedback_image_data(self, draft_id: str, image_id: str) -> str:
        if not re.fullmatch(r"feedback_image_[0-9a-f]{32}", image_id):
            raise self._authoring_error("The screenshot reference is invalid.", "agent_feedback_image_invalid")
        path = self._feedback_image_directory(draft_id) / f"{image_id}.bin"
        if not path.is_file() or time.time() - path.stat().st_mtime > 86400:
            raise self._authoring_error("The screenshot expired or is unavailable.", "agent_feedback_image_expired")
        content = path.read_bytes()
        mime = "image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if content.startswith(b"\xff\xd8\xff") else None
        if mime is None or len(content) > 1_000_000:
            raise self._authoring_error("The screenshot content is invalid.", "agent_feedback_image_invalid")
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    def get_diff(self, draft_id: str, from_revision: int | None = None, to_revision: int | None = None, *, baseline: str = "revision") -> dict[str, Any]:
        draft = self.store.get_agent_authoring_draft(draft_id)
        target = int(to_revision if to_revision is not None else draft["revision"])
        after = self.store.get_agent_authoring_revision(draft_id, target)["package"]
        if baseline == "source":
            if draft.get("source_version"):
                try:
                    published = self.agents.package(
                        draft["agent_id"], draft["source_version"], draft.get("source_hash")
                    )
                    before = self._capture_package(Path(published["directory"]))
                except Exception as exc:
                    raise self._authoring_error("The pinned source version is unavailable.", "agent_source_baseline_unavailable") from exc
                source_label: int | str = str(draft["source_version"])
            else:
                before = {}
                source_label = "new_agent"
            return {"draft_id": draft_id, "baseline": "source", "from_revision": source_label,
                    "to_revision": target, "changes": package_changes(before, after)}
        source = int(from_revision if from_revision is not None else max(1, target - 1))
        before = self.store.get_agent_authoring_revision(draft_id, source)["package"]
        return {"draft_id": draft_id, "baseline": "revision", "from_revision": source,
                "to_revision": target, "changes": package_changes(before, after)}

    def _apply_package(self, draft_id: str, expected_revision: int, package: dict[str, Any], *, kind: str, operation_id: str, record_turn: bool = True, decision: dict[str, Any] | None = None, safe_runtime: bool = False, deadline_check: Any = None, runtime_thread_id: str | None = None, allow_catalog_module_change: bool = False, preserve_validation: bool = False) -> dict[str, Any]:
        self._assert_operation(draft_id, operation_id, expected_revision)
        draft = self.store.get_agent_authoring_draft(draft_id)
        self._assert_editable(draft)
        previous = self.store.get_agent_authoring_revision(draft_id, expected_revision)["package"]
        package = copy.deepcopy(package)
        manifest = package.get("manifest")
        identity_changed = self._prepare_identity_package(draft, package, kind=kind)
        if not isinstance(manifest, dict) or str(manifest.get("slug") or "") != draft["agent_id"]:
            raise self._authoring_error("Agent ID cannot change inside a draft.", "agent_id_immutable")
        if manifest.get("module") != previous["manifest"].get("module") and not allow_catalog_module_change:
            raise self._authoring_error("Agent module cannot change inside a draft.", "agent_module_immutable")
        self._assert_manageable(manifest)
        manifest["version"] = draft.get("target_version") or manifest.get("version")
        # Any explicit edit adopts the current controlled relationship policy.
        # Existing persisted drafts are not rewritten until such a revision occurs.
        apply_advisory_relationship_policy(manifest)
        if not preserve_validation and self._risk_class(draft, package) != "metadata_only":
            acceptance = manifest["execution"].setdefault("acceptance", {})
            acceptance["contractVersion"] = "1.0"
            acceptance.setdefault("requiredAssessments", [])
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
            from .authoring_checks import (
                candidate_business_output_issues,
                candidate_input_issues,
            )
            input_issues = candidate_input_issues(manifest)
            if input_issues:
                raise ManifestError("Candidate input references require conditional execution.",
                                    issue_code=input_issues[0]["code"], path=input_issues[0]["path"])
            business_output_issues = candidate_business_output_issues(manifest)
            if business_output_issues:
                raise ManifestError(
                    "A SAP-reading Agent must produce a deterministic business result.",
                    issue_code=business_output_issues[0]["code"],
                    path=business_output_issues[0]["path"],
                )
            if package.get("rules"):
                validate_managed_rule(package["rules"], expected_digest=(manifest.get("managedRule") or {}).get("sha256"))
            elif manifest.get("managedRule"):
                raise self._authoring_error("The declared managed rule source is missing.", "agent_managed_rule_missing")
        diff = package_changes(previous, package)
        if not diff:
            return self.get_draft(draft_id)
        risk = self._risk_class(draft, package)
        preserved_status = draft.get("status") or "draft"
        preserved_validation_run_id = draft.get("validation_run_id")
        preserved_validation = copy.deepcopy(draft.get("validation") or {})
        preserved_metadata = copy.deepcopy(draft.get("metadata") or {})
        draft.update(
            status=preserved_status if preserve_validation else "draft",
            revision=expected_revision + 1,
            risk_class=risk,
            validation_run_id=preserved_validation_run_id if preserve_validation else None,
            validation=preserved_validation if preserve_validation else {},
            updated_at=utc_now(),
        )
        if runtime_thread_id:
            draft["thread_id"] = runtime_thread_id
        if preserve_validation:
            static_checks = copy.deepcopy(preserved_metadata.get("static_checks"))
            if isinstance(static_checks, dict):
                static_checks["catalog_metadata_revision"] = expected_revision + 1
                static_checks["catalog_metadata_reuse"] = True
            trial = copy.deepcopy(preserved_metadata.get("trial"))
            if isinstance(trial, dict):
                trial["catalog_metadata_revision"] = expected_revision + 1
                trial["catalog_metadata_reuse"] = True
            draft["metadata"] = {
                **preserved_metadata,
                "static_checks": static_checks,
                "trial": trial,
                "catalog_module": str(manifest.get("module") or "Common"),
                "catalog_revision": int(preserved_metadata.get("catalog_revision") or 1) + 1,
                "catalog_updated_at": utc_now(),
                "acceptance_reuse": {
                    "reason": "catalog_module_only",
                    "from_revision": expected_revision,
                    "execution_digest": agent_execution_digest(manifest, package.get("rules")),
                },
            }
        else:
            draft["metadata"] = {
                **(draft.get("metadata") or {}),
                "static_checks": None,
                "trial": None,
            }
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

    def _assistant_capabilities(self, snapshot: dict[str, Any]) -> dict[str, bool]:
        declared = getattr(self.runtime, "feedback_capabilities", None)
        if callable(declared):
            try:
                value = declared(str(snapshot.get("provider_id") or ""), snapshot.get("model"))
            except Exception:
                value = {}
        else:
            # Legacy injected runtimes predate declarations. Preserve their
            # Codex screenshot behavior, never infer it for another provider.
            value = {"image_input": snapshot.get("provider_id") == "codex"}
        return {"stream_events": True, "provider_stream_events": bool(value.get("stream_events")),
                "resume": bool(value.get("resume")), "steer": bool(value.get("steer")),
                "image_input": bool(value.get("image_input"))}

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
                current = self.store.get_agent_operation_by_id(draft_id, operation_id)
                self.store.update_agent_operation(
                    draft_id,
                    operation_id,
                    status="cancelling",
                    detail={**(current.get("detail") or {}), "phase": "cancelling"},
                )
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
        intent = str(getattr(payload, "intent", "revise") or "revise")
        request_id = getattr(payload, "request_id", None)
        annotations = getattr(payload, "annotations", None) or []
        enqueue_if_busy = bool(getattr(payload, "enqueue_if_busy", False))
        if (annotations or getattr(payload, "selection", None) or getattr(payload, "reply_to_clarification_id", None)
                or getattr(payload, "image_ids", None) or enqueue_if_busy) and not request_id:
            raise self._authoring_error("A request ID is required for this interaction.", "agent_feedback_request_id_required")
        fingerprint_data = (payload.model_dump(by_alias=True, exclude={"request_id"})
                            if hasattr(payload, "model_dump") else {
                                "baseTurn": payload.base_turn, "baseRevision": revision,
                                "feedback": payload.feedback, "locale": payload.locale,
                                "intent": intent, "retryOfTurn": retry_of_turn,
                            })
        fingerprint = self._feedback_digest(fingerprint_data)
        if request_id:
            prior = next((item for item in turns if item.get("request_id") == request_id), None)
            if prior is not None:
                if prior.get("input_hash") != fingerprint:
                    raise self._authoring_error("Request ID was reused with different content.", "agent_request_conflict")
                return {"draft_id": draft_id, "task_id": prior["decision"].get("task_id"),
                        "status": prior["status"], "turn": prior["turn"],
                        "base_revision": prior["base_revision"]}
        if int(payload.base_turn) != latest_turn:
            raise self._authoring_error("Agent conversation changed.", "agent_draft_conflict")
        if retry_of_turn is not None:
            source = next((item for item in turns if item["turn"] == retry_of_turn), None)
            if not source or source["kind"] != "feedback" or source["status"] not in {"failed", "cancelled", "interrupted"}:
                raise self._authoring_error("Only a failed or interrupted turn in this draft can be retried.", "agent_feedback_retry_invalid")
        feedback_context = self._feedback_context(draft_id, revision, payload)
        reply_id = getattr(payload, "reply_to_clarification_id", None)
        prior_feedback = next((item for item in reversed(turns) if item["kind"] == "feedback"), None)
        pending = (prior_feedback or {}).get("decision", {}).get("pending_clarification")
        pending_valid = (prior_feedback is not None and prior_feedback["status"] == "completed"
                         and isinstance(pending, dict) and int(pending.get("revision") or 0) == revision)
        short_reply = str(payload.feedback).strip().casefold() in {"可以", "好", "是", "同意", "确认", "yes", "ok", "okay", "no", "不", "不要"}
        if reply_id:
            if not pending_valid or pending.get("id") != reply_id:
                raise self._authoring_error("The clarification is no longer current.", "agent_feedback_clarification_stale")
        elif short_reply and pending_valid:
            reply_id = pending["id"]
        elif short_reply:
            raise self._authoring_error("This short reply has no unique current question.", "agent_feedback_clarification_required")
        if reply_id:
            feedback_context["clarification"] = {"id": reply_id, "question": pending["question"],
                                                   "revision": revision}
        try:
            snapshot = self._feedback_runtime_snapshot(draft)
            binding_error = None
        except Exception as exc:
            snapshot = copy.deepcopy((draft.get("metadata") or {}).get("runtime_snapshot") or {})
            binding_error = self._feedback_error_code(exc)
            if binding_error == "runtime_agent_feedback_failed":
                binding_error = "agent_runtime_snapshot_failed"
        if getattr(payload, "image_ids", None) and not self._assistant_capabilities(snapshot)["image_input"]:
            raise self._authoring_error("This Runtime does not accept images.", "agent_feedback_image_unsupported")
        if intent == "explain" and snapshot.get("provider_id") != "codex":
            binding_error = "runtime_agent_explanation_unavailable"
        decision = {"runtime_snapshot": snapshot,
                    "agent_id": draft["agent_id"], "retry_of_turn": retry_of_turn,
                    "intent": intent, "context": feedback_context, "locale": str(payload.locale),
                    "reply_to_clarification_id": reply_id,
                    "execution": {"timeout_seconds": float(self.feedback_timeout_seconds), "elapsed_seconds": 0}}
        if intent == "revise" and snapshot.get("provider_id") == "codex":
            # New submissions only. Existing persisted/offline operations retain
            # their original permissions. The user accepted loopback reachability;
            # do not translate that into a successful network-isolation claim.
            from .runtime_execution import FULL_ACCESS_POLICY
            decision["authoring_policy"] = copy.deepcopy(FULL_ACCESS_POLICY)
        if binding_error:
            decision["binding_error"] = binding_error
        turn = {"draft_id": draft_id, "parent_turn": latest_turn or None, "kind": "feedback",
                "user_message": str(payload.feedback), "decision": decision, "base_revision": revision,
                "result_revision": None, "diff": [], "created_at": utc_now(),
                "request_id": request_id, "input_hash": fingerprint}
        try:
            accepted = self.store.create_agent_feedback_turn(
                draft_id, item=turn, base_turn=int(payload.base_turn), input_hash=fingerprint,
                enqueue_if_busy=enqueue_if_busy,
            )
        except ValueError as exc:
            raise self._authoring_error("The draft or conversation changed.", str(exc)) from exc
        if accepted["reused"]:
            return {"draft_id": draft_id, **{key: accepted[key] for key in ("task_id", "turn", "status")},
                    "base_revision": revision}
        self.store.append_agent_conversation_event(draft_id, "turn_queued", {"status": accepted["status"]}, accepted["turn"])
        if accepted["task_id"]:
            self._start_feedback_task(draft_id, turn, payload, accepted["task_id"])
        return {"draft_id": draft_id, "task_id": accepted["task_id"], "turn": accepted["turn"],
                "status": accepted["status"], "base_revision": revision}

    def _start_feedback_task(self, draft_id: str, turn: dict[str, Any], payload: Any, operation_id: str) -> None:
        task = asyncio.create_task(self._run_feedback(draft_id, turn, payload, operation_id))
        self._feedback_tasks[operation_id] = task
        task.add_done_callback(lambda _: self._feedback_tasks.pop(operation_id, None))

    def _dispatch_waiting_feedback(self, draft_id: str) -> None:
        from .agent_lifecycle import AgentLifecycleError
        claimed = self.store.claim_next_agent_feedback(draft_id)
        if not claimed:
            return
        turn = next(item for item in self.store.list_agent_conversation_turns(draft_id)
                    if item["turn"] == claimed["turn"])
        saved_context = turn["decision"].get("context") or {}
        try:
            verified_context = self._feedback_context(draft_id, int(turn["base_revision"]), SimpleNamespace(
                step=saved_context.get("step"), field_path=saved_context.get("field_path"),
                run_id=saved_context.get("run_id"),
                acceptance_campaign_id=saved_context.get("acceptance_campaign_id"),
                annotations=saved_context.get("annotations"),
                image_ids=saved_context.get("image_ids"),
                selection=saved_context.get("selection"),
            ))
        except (AgentLifecycleError, KeyError, ValueError):
            turn.update(status="needs_review", completed_at=utc_now())
            turn["decision"]["error_code"] = "agent_feedback_context_stale"
            self.store.save_agent_conversation_turn(turn)
            self._finish_operation(draft_id, claimed["task_id"], "failed")
            self.store.pause_agent_feedback_queue(draft_id)
            self.store.append_agent_conversation_event(
                draft_id, "turn_finished", {"status": "needs_review",
                                             "error_code": "agent_feedback_context_stale"}, turn["turn"])
            return
        turn["decision"]["context"] = {**verified_context, **{
            key: saved_context[key] for key in ("clarification",) if key in saved_context}}
        self.store.save_agent_conversation_turn(turn)
        payload = SimpleNamespace(feedback=turn["user_message"],
                                  locale=turn["decision"].get("locale") or "zh")
        self.store.append_agent_conversation_event(
            draft_id, "turn_dispatched", {"status": "queued"}, turn["turn"])
        self._start_feedback_task(draft_id, turn, payload, claimed["task_id"])

    async def _run_feedback(self, draft_id: str, turn: dict[str, Any], payload: Any, operation_id: str) -> None:
        status = "failed"
        monotonic = getattr(self, "_feedback_clock", time.monotonic)
        started = monotonic()
        execution = turn["decision"]["execution"]
        budget = float(execution["timeout_seconds"])
        started_at = datetime.now(timezone.utc)
        execution.update(started_at=started_at.isoformat(), deadline_at=(started_at + timedelta(seconds=budget)).isoformat())
        def progress(phase: str, completed_units: int) -> None:
            """Persist public authoring progress without exposing prompts or tool output."""
            try:
                current = self.store.get_agent_operation_by_id(draft_id, operation_id)
            except KeyError:
                return
            detail = {
                "turn": turn["turn"],
                "phase": phase,
                "completed_units": completed_units,
                "total_units": 4,
                "timeout_seconds": budget,
                "started_at": execution["started_at"],
                "deadline_at": execution["deadline_at"],
            }
            self.store.merge_agent_operation_detail(draft_id, operation_id, detail)
            self.store.append_agent_conversation_event(
                draft_id, "phase", {"phase": phase, "completed_units": completed_units,
                                    "total_units": 4}, turn["turn"])

        def check_deadline() -> None:
            if monotonic() - started >= budget:
                raise _FeedbackDeadlineExceeded()

        tool_broker = getattr(self, "authoring_tool_broker", None)
        tool_session = None
        try:
            self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
            turn["status"] = "running"
            self.store.save_agent_conversation_turn(turn)
            progress("preparing_context", 0)
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
            history = []
            def safe_history(value: Any) -> Any:
                if isinstance(value, str):
                    return re.sub(
                        r"(?i)\b(password|token|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
                        r"\1=[redacted]", value[:4000],
                    )
                if isinstance(value, dict):
                    return {key: safe_history(item) for key, item in value.items()
                            if not any(token in str(key).lower() for token in
                                       ("password", "secret", "credential", "authorization", "token", "raw_rows", "protected"))}
                if isinstance(value, list):
                    return [safe_history(item) for item in value[:20]]
                return value if isinstance(value, (int, float, bool)) or value is None else None
            for item in self.store.list_agent_conversation_turns(draft_id):
                if item["turn"] >= turn["turn"] or item["kind"] != "feedback" or item["status"] != "completed":
                    continue
                prior = item.get("decision") or {}
                history.append({"turn": item["turn"], "user_message": safe_history(str(item.get("user_message") or "")),
                                "assistant_message": safe_history(prior.get("assistant_message") or prior.get("summary")),
                                "action": prior.get("action"),
                                "decision": safe_history(prior.get("pending_clarification")),
                                "base_revision": item.get("base_revision"),
                                "result_revision": item.get("result_revision")})
            history = history[-20:]
            with context:
                supports = getattr(self.runtime, "supports", None)
                if callable(supports) and not supports("review_agent_feedback"):
                    raise self._authoring_error("The Runtime does not support Agent authoring.", "runtime_agent_feedback_unavailable")
                tool_options = {"tool_policy": copy.deepcopy(turn["decision"]["authoring_policy"])} if turn["decision"].get("authoring_policy") else {}
                explanation = turn["decision"].get("intent") == "explain"
                if (not explanation and tool_options and tool_broker is not None
                        and str(snapshot.get("provider_id") or "") == "codex"):
                    digest = hashlib.sha256(str(payload.feedback).encode("utf-8")).hexdigest()
                    ddic_fields = tuple(sorted({(table.upper(), field.upper()) for table, field in
                        re.findall(r"\b([A-Za-z][A-Za-z0-9_]{0,29})[.-]([A-Za-z][A-Za-z0-9_]{0,29})\b",
                                   str(payload.feedback))}))
                    capability = tool_broker.open_authoring_session(
                        draft_id, operation_id, int(turn["base_revision"]), digest,
                        started_at + timedelta(seconds=budget), ddic_fields,
                    )
                    tool_session = {"operation_id": operation_id, "capability": capability,
                                    "internal_api_url": tool_broker.settings.internal_api_url}
                    tool_options["tool_session"] = tool_session
                progress("generating_revision", 1)
                images = [self._feedback_image_data(draft_id, image_id)
                          for image_id in (turn["decision"].get("context") or {}).get("image_ids") or []]
                decision = await self._await_feedback_runtime(self.runtime.review_agent_feedback(feedback=str(payload.feedback), locale=str(payload.locale), package=runtime_package, history=history, thread_id=None if explanation or turn["decision"].get("retry_of_turn") else draft.get("thread_id"), operation_id=operation_id, intent=turn["decision"].get("intent", "revise"), feedback_context=copy.deepcopy(turn["decision"].get("context") or {}), **({"image_inputs": images} if images else {}), **({} if explanation else tool_options)), draft_id=draft_id, operation_id=operation_id, timeout=budget - (monotonic() - started))
            check_deadline()
            self._assert_operation(draft_id, operation_id, int(turn["base_revision"]))
            progress("validating_response", 2)
            if isinstance(decision.get("harness"), dict):
                turn["decision"]["harness"] = copy.deepcopy(decision["harness"])
            action = decision.get("action")
            if action not in {"clarify", "reply", "revise_agent"} or not isinstance(decision.get("summary"), dict) or not all(isinstance(decision["summary"].get(lang), str) for lang in ("zh", "en")):
                raise self._authoring_error("The Runtime response is invalid.", "runtime_agent_feedback_invalid")
            if turn["decision"].get("intent") == "explain" and action == "revise_agent":
                raise self._authoring_error("Explanation mode cannot modify the Agent.", "agent_explanation_write_rejected")
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
                progress("applying_revision", 3)
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
            progress("finalizing", 3)
            completed_decision = {**turn["decision"], "action": action,
                                  "summary": decision["summary"],
                                  "assistant_message": decision["summary"], "changed": changed}
            if action == "clarify":
                completed_decision["pending_clarification"] = {
                    "id": f"clarify_{uuid.uuid4().hex[:20]}", "question": decision["summary"],
                    "revision": int(refreshed["revision"]), "turn": turn["turn"],
                }
            turn.update(status="completed", result_revision=int(refreshed["revision"]),
                        decision=completed_decision, diff=result["diff"] if changed else [])
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
            if tool_session is not None:
                tool_broker.close_authoring_session(operation_id)
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
            progress("cancelled" if status == "cancelled" else "completed" if status == "completed" else "failed", 4)
            if code != "agent_feedback_cleanup_failed":
                self._finish_operation(draft_id, operation_id, status)
            self.store.append_agent_conversation_event(
                draft_id, "turn_finished", {"status": status, "error_code": code,
                                           "result_revision": turn.get("result_revision"),
                                           "diff_ready": bool(turn.get("diff"))}, turn["turn"])
            if status == "completed" and not turn["decision"].get("changed") and turn["decision"].get("action") != "clarify":
                self._dispatch_waiting_feedback(draft_id)
            else:
                self.store.pause_agent_feedback_queue(draft_id)

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
        if turn["status"] == "waiting":
            turn.update(status="withdrawn", completed_at=utc_now())
            self.store.save_agent_conversation_turn(turn)
            self.store.append_agent_conversation_event(
                draft_id, "turn_withdrawn", {"status": "withdrawn"}, turn_number)
            return self.conversation(draft_id)
        if turn["status"] not in {"queued", "running"}:
            return self.conversation(draft_id)
        operation_id = turn.get("decision", {}).get("task_id")
        already_cancelling = False
        if operation_id:
            already_cancelling = self.store.get_agent_operation_by_id(draft_id, operation_id)["status"] == "cancelling"
            current = self.store.get_agent_operation_by_id(draft_id, operation_id)
            self.store.update_agent_operation(
                draft_id,
                operation_id,
                status="cancelling",
                detail={**(current.get("detail") or {}), "phase": "cancelling"},
            )
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
        publication_tasks = list(getattr(self, "_publication_tasks", {}).values())
        if publication_tasks:
            # Publication owns Git and immutable-build state. A graceful API
            # shutdown waits for that bounded operation instead of cancelling
            # an asyncio wrapper while its worker thread continues mutating Git.
            await asyncio.gather(*publication_tasks, return_exceptions=True)
