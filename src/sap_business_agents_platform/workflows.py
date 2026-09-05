from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .manifests import ManifestError, validate_execution, validator_schema
from .models import utc_now


ALLOWED_TRANSFORMS = {
    "identity",
    "to_string",
    "to_integer",
    "format_date",
    "first",
    "join",
    "wrap_array",
}

WORKFLOW_REVIEW_POLICY_VERSION = 2


class WorkflowError(ValueError):
    def __init__(self, message: str, *, code: str = "workflow_invalid", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class WorkflowRepository:
    def __init__(self, root: Path, agents: Any, store: Any = None) -> None:
        self.root = root
        self.agents = agents
        self.store = store

    def list(self) -> list[dict[str, Any]]:
        return [
            self._load(path)
            for path in sorted(self.root.glob("*/*/workflow.json"))
            if self._lifecycle(path.parent)["state"] == "active"
        ]

    def catalog(self, state: str = "active") -> list[dict[str, Any]]:
        if state not in {"active", "inactive", "all"}:
            raise WorkflowError(
                "Workflow catalog state must be active, inactive, or all.",
                code="workflow_catalog_state_invalid",
            )
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/*/workflow.json")):
            workflow = self._load(path)
            lifecycle = self._lifecycle(path.parent, workflow)
            if state != "all" and lifecycle["state"] != state:
                continue
            validation = workflow.get("validation")
            if not isinstance(validation, dict):
                validation = self._load_publication_validation(path.parent)
            acknowledgement = validation.get("acknowledgement") if validation else None
            blockers = self.delete_blockers(str(workflow.get("id") or ""))
            open_version_drafts = self._open_version_drafts(
                str(workflow.get("id") or "")
            )
            items.append(
                {
                    "id": workflow.get("id"),
                    "version": workflow.get("version"),
                    "title": workflow.get("title"),
                    "description": workflow.get("description"),
                    "read_only": workflow.get("readOnly") is True,
                    "node_count": len(workflow.get("nodes") or []),
                    "lifecycle": {
                        "state": lifecycle["state"],
                        "current_version": str(workflow.get("version") or ""),
                        "workflow_hash": workflow_digest(workflow),
                        "version_count": 1 + len(self._archived_version_paths(path.parent)),
                        "deactivated_at": lifecycle.get("deactivatedAt"),
                        "deactivation_reason": lifecycle.get("deactivationReason"),
                        "business_run_count": self._business_run_count(
                            str(workflow.get("id") or "")
                        ),
                    },
                    "management": {
                        "can_create_version": not open_version_drafts,
                        "can_deactivate": lifecycle["state"] == "active",
                        "can_activate": lifecycle["state"] == "inactive",
                        "can_delete": not blockers,
                        "blockers": blockers,
                    },
                    "publication": {
                        "validation_status": str(validation.get("status") or "unknown")
                        if validation
                        else "unknown",
                        "validation_run_id": validation.get("run_id") if validation else None,
                        "validated_at": validation.get("validated_at") if validation else None,
                        "evidence_gap_codes": sorted(
                            {
                                str(code)
                                for code in (validation.get("evidence_gap_codes") or [])
                                if str(code)
                            }
                        )
                        if validation
                        else [],
                        "acknowledgement_recorded": isinstance(acknowledgement, dict),
                    },
                }
            )
        return items

    def get(self, workflow_id: str) -> dict[str, Any]:
        path = self._current_path(workflow_id)
        return self._load(path)

    def get_runnable(self, workflow_id: str) -> dict[str, Any]:
        path = self._current_path(workflow_id)
        workflow = self._load(path)
        if self._lifecycle(path.parent, workflow)["state"] != "active":
            raise WorkflowError(
                "The published workflow is inactive.",
                code="workflow_inactive",
                detail={"workflow_id": workflow_id},
            )
        return workflow

    def versions(self, workflow_id: str) -> list[dict[str, Any]]:
        current_path = self._current_path(workflow_id)
        current = self._load(current_path)
        values = [self._version_summary(current, current_path.parent, current=True)]
        for path in self._archived_version_paths(current_path.parent):
            values.append(
                self._version_summary(
                    self._load(path, require_pins=False), path.parent, current=False
                )
            )
        return sorted(values, key=lambda item: _semver_tuple(item["version"]), reverse=True)

    def get_version(self, workflow_id: str, version: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise KeyError(version)
        current_path = self._current_path(workflow_id)
        current = self._load(current_path)
        if str(current.get("version") or "") == version:
            return current
        path = current_path.parent / "versions" / version / "workflow.json"
        expected = (current_path.parent / "versions").resolve()
        resolved = path.resolve()
        if expected not in resolved.parents or not path.is_file():
            raise KeyError(version)
        payload = self._load(path, require_pins=False)
        if str(payload.get("id") or "") != workflow_id:
            raise WorkflowError("Archived workflow ID does not match its directory.")
        return payload

    def lifecycle(self, workflow_id: str) -> dict[str, Any]:
        path = self._current_path(workflow_id)
        workflow = self._load(path)
        return self._lifecycle(path.parent, workflow)

    def delete_blockers(self, workflow_id: str) -> list[str]:
        try:
            path = self._current_path(workflow_id)
        except KeyError:
            return ["workflow_not_found"]
        blockers: list[str] = []
        lifecycle = self._lifecycle(path.parent)
        if lifecycle["state"] != "inactive":
            blockers.append("workflow_must_be_inactive")
        if self._business_run_count(workflow_id) > 0:
            blockers.append("workflow_has_business_runs")
        if self._open_version_drafts(workflow_id):
            blockers.append("workflow_has_open_version_drafts")
        if self._dependent_workflows(workflow_id):
            blockers.append("workflow_is_referenced")
        return blockers

    def directory(self, workflow_id: str) -> Path:
        return self._current_path(workflow_id).parent

    def open_version_drafts(self, workflow_id: str) -> list[str]:
        return self._open_version_drafts(workflow_id)

    def _current_path(self, workflow_id: str) -> Path:
        for path in self.root.glob("*/*/workflow.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("id") == workflow_id or path.parent.name == workflow_id:
                return path
        raise KeyError(workflow_id)

    def _lifecycle(
        self, directory: Path, workflow: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        path = directory / "publication.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowError(f"Cannot load {path}: {exc}") from exc
            if payload.get("state") not in {"active", "inactive"}:
                raise WorkflowError(f"{path}.state must be active or inactive")
            return payload
        workflow = workflow or self._load(directory / "workflow.json")
        return {
            "schemaVersion": 1,
            "workflowId": str(workflow.get("id") or directory.name),
            "state": "active",
            "currentVersion": str(workflow.get("version") or ""),
            "currentWorkflowHash": workflow_digest(workflow),
            "publishedAt": ((workflow.get("validation") or {}).get("validated_at")),
            "deactivatedAt": None,
            "deactivationReason": None,
        }

    def _version_summary(
        self, workflow: dict[str, Any], directory: Path, *, current: bool
    ) -> dict[str, Any]:
        validation = workflow.get("validation")
        if not isinstance(validation, dict):
            validation = self._load_publication_validation(directory)
        return {
            "version": str(workflow.get("version") or ""),
            "current": current,
            "workflow_hash": workflow_digest(workflow),
            "validation_status": str(validation.get("status") or "unknown")
            if validation
            else "unknown",
            "validation_run_id": validation.get("run_id") if validation else None,
            "validated_at": validation.get("validated_at") if validation else None,
        }

    @staticmethod
    def _archived_version_paths(directory: Path) -> list[Path]:
        return sorted((directory / "versions").glob("*/workflow.json"))

    def _business_run_count(self, workflow_id: str) -> int:
        if self.store is None:
            return 0
        return int(self.store.workflow_business_run_count(workflow_id))

    def _open_version_drafts(self, workflow_id: str) -> list[str]:
        if self.store is None:
            return []
        active_statuses = {
            "planning",
            "waiting_input",
            "needs_agents",
            "draft",
            "validated",
            "inconclusive",
            "needs_review",
        }
        return [
            draft.draft_id
            for draft in self.store.list_workflow_drafts()
            if draft.status in active_statuses
            and (draft.composition.get("version_origin") or {}).get("workflow_id")
            == workflow_id
        ]

    def _dependent_workflows(self, workflow_id: str) -> list[str]:
        dependencies: list[str] = []
        for path in self.root.glob("*/*/workflow.json"):
            payload = self._load(path)
            candidate_id = str(payload.get("id") or "")
            if candidate_id == workflow_id:
                continue
            for node in payload.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                if node.get("workflowId") == workflow_id or node.get("workflowRef") == workflow_id:
                    dependencies.append(candidate_id)
                    break
        return dependencies

    def _load(self, path: Path, *, require_pins: bool = True) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"Cannot load {path}: {exc}") from exc
        validate_workflow(payload, self.agents, source=str(path), require_pins=require_pins)
        return payload

    @staticmethod
    def _load_publication_validation(directory: Path) -> dict[str, Any]:
        path = directory / "validation.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


class WorkflowManagementService:
    def __init__(
        self,
        *,
        repository_root: Path,
        repository: WorkflowRepository,
        store: Any,
        drafts: Any,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.repository = repository
        self.store = store
        self.drafts = drafts

    def create_version_draft(
        self,
        workflow_id: str,
        *,
        bump: str,
        expected_version: str,
        expected_workflow_hash: str,
    ) -> dict[str, Any]:
        workflow = self._assert_expected(
            workflow_id, expected_version, expected_workflow_hash
        )
        open_drafts = self.repository.open_version_drafts(workflow_id)
        if open_drafts:
            raise WorkflowError(
                "An unfinished version draft already exists for this workflow.",
                code="workflow_version_draft_exists",
                detail={"draft_ids": open_drafts},
            )
        target_version = _bump_semver(expected_version, bump)
        if any(
            item["version"] == target_version
            for item in self.repository.versions(workflow_id)
        ):
            raise WorkflowError(
                f"Workflow version already exists: {target_version}",
                code="workflow_version_exists",
            )
        clone = json.loads(json.dumps(workflow))
        clone["version"] = target_version
        clone.pop("status", None)
        clone.pop("validation", None)
        draft = self.drafts.create_version(
            clone,
            workflow_id=workflow_id,
            source_version=expected_version,
            source_hash=expected_workflow_hash,
            target_version=target_version,
        )
        self._audit(
            workflow_id=workflow_id,
            action="version_draft_created",
            from_version=expected_version,
            to_version=target_version,
            workflow_hash=expected_workflow_hash,
            branch=None,
            detail={"draft_id": draft.draft_id, "bump": bump},
        )
        return {
            "draft": draft.model_dump(mode="json"),
            "target_version": target_version,
        }

    def deactivate(
        self,
        workflow_id: str,
        *,
        expected_version: str,
        expected_workflow_hash: str,
        reason: str | None,
    ) -> dict[str, Any]:
        workflow = self._assert_expected(
            workflow_id, expected_version, expected_workflow_hash
        )
        directory = self.repository.directory(workflow_id)
        lifecycle = self.repository.lifecycle(workflow_id)
        if lifecycle["state"] != "active":
            raise WorkflowError(
                "The workflow is already inactive.", code="workflow_already_inactive"
            )
        branch = self._prepare_git_branch(workflow_id, "deactivate", expected_version)
        payload = self._lifecycle_payload(
            workflow,
            state="inactive",
            existing=lifecycle,
            reason=reason,
        )
        self._write_json(directory / "publication.json", payload)
        self._audit(
            workflow_id=workflow_id,
            action="deactivated",
            from_version=expected_version,
            to_version=expected_version,
            workflow_hash=expected_workflow_hash,
            branch=branch,
            detail={"reason": reason},
        )
        return self._mutation_result(workflow_id, branch, payload)

    def activate(
        self,
        workflow_id: str,
        *,
        expected_version: str,
        expected_workflow_hash: str,
        reason: str | None,
    ) -> dict[str, Any]:
        workflow = self._assert_expected(
            workflow_id, expected_version, expected_workflow_hash
        )
        directory = self.repository.directory(workflow_id)
        lifecycle = self.repository.lifecycle(workflow_id)
        if lifecycle["state"] != "inactive":
            raise WorkflowError(
                "The workflow is already active.", code="workflow_already_active"
            )
        branch = self._prepare_git_branch(workflow_id, "activate", expected_version)
        payload = self._lifecycle_payload(
            workflow,
            state="active",
            existing=lifecycle,
            reason=reason,
        )
        self._write_json(directory / "publication.json", payload)
        self._audit(
            workflow_id=workflow_id,
            action="activated",
            from_version=expected_version,
            to_version=expected_version,
            workflow_hash=expected_workflow_hash,
            branch=branch,
            detail={"reason": reason},
        )
        return self._mutation_result(workflow_id, branch, payload)

    def delete(
        self,
        workflow_id: str,
        *,
        expected_version: str,
        expected_workflow_hash: str,
        confirm_workflow_id: str,
    ) -> dict[str, Any]:
        self._assert_expected(workflow_id, expected_version, expected_workflow_hash)
        if confirm_workflow_id != workflow_id:
            raise WorkflowError(
                "Workflow ID confirmation does not match.",
                code="workflow_delete_confirmation_mismatch",
            )
        blockers = self.repository.delete_blockers(workflow_id)
        if blockers:
            raise WorkflowError(
                "The workflow does not meet the permanent deletion requirements.",
                code="workflow_delete_blocked",
                detail={"blockers": blockers},
            )
        directory = self.repository.directory(workflow_id)
        expected_root = self.repository.root.resolve()
        resolved = directory.resolve()
        if expected_root not in resolved.parents:
            raise WorkflowError(
                "Workflow deletion target escaped the repository root.",
                code="workflow_delete_path_invalid",
            )
        branch = self._prepare_git_branch(workflow_id, "delete", expected_version)
        trash_root = self.repository_root / ".prototype" / "management-trash"
        trash = trash_root / f"{workflow_id}-{uuid.uuid4().hex[:12]}"
        trash_root.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(directory), str(trash))
            shutil.rmtree(trash)
        except Exception:
            if trash.exists() and not directory.exists():
                shutil.move(str(trash), str(directory))
            raise
        self._audit(
            workflow_id=workflow_id,
            action="deleted",
            from_version=expected_version,
            to_version=None,
            workflow_hash=expected_workflow_hash,
            branch=branch,
            detail={"git_history_recoverable": True},
        )
        return {
            "workflow_id": workflow_id,
            "deleted": True,
            "branch": branch,
            "commit_required": True,
        }

    def _assert_expected(
        self, workflow_id: str, expected_version: str, expected_workflow_hash: str
    ) -> dict[str, Any]:
        workflow = self.repository.get(workflow_id)
        actual_version = str(workflow.get("version") or "")
        actual_hash = workflow_digest(workflow)
        if actual_version != expected_version or actual_hash != expected_workflow_hash:
            raise WorkflowError(
                "The published workflow changed; reload before continuing.",
                code="workflow_management_conflict",
                detail={
                    "expected_version": expected_version,
                    "actual_version": actual_version,
                    "expected_workflow_hash": expected_workflow_hash,
                    "actual_workflow_hash": actual_hash,
                },
            )
        return workflow

    def _prepare_git_branch(self, workflow_id: str, action: str, version: str) -> str:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            raise WorkflowError(
                "Workflow management requires a clean Git worktree.",
                code="git_worktree_dirty",
            )
        safe_version = re.sub(r"[^0-9A-Za-z._-]", "-", version)
        branch = (
            f"codex/workflow-{workflow_id}-{action}-v{safe_version}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        subprocess.run(
            ["git", "switch", "-c", branch],
            cwd=self.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return branch

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)

    @staticmethod
    def _lifecycle_payload(
        workflow: dict[str, Any], *, state: str, existing: dict[str, Any], reason: str | None
    ) -> dict[str, Any]:
        now = utc_now()
        return {
            "schemaVersion": 1,
            "workflowId": str(workflow.get("id") or ""),
            "state": state,
            "currentVersion": str(workflow.get("version") or ""),
            "currentWorkflowHash": workflow_digest(workflow),
            "publishedAt": existing.get("publishedAt")
            or ((workflow.get("validation") or {}).get("validated_at")),
            "deactivatedAt": now if state == "inactive" else None,
            "deactivationReason": reason if state == "inactive" else None,
            "updatedAt": now,
        }

    @staticmethod
    def _mutation_result(
        workflow_id: str, branch: str, lifecycle: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "workflow_id": workflow_id,
            "state": lifecycle["state"],
            "branch": branch,
            "commit_required": True,
            "lifecycle": lifecycle,
        }

    def _audit(
        self,
        *,
        workflow_id: str,
        action: str,
        from_version: str | None,
        to_version: str | None,
        workflow_hash: str | None,
        branch: str | None,
        detail: dict[str, Any],
    ) -> None:
        self.store.append_workflow_management_event(
            event_id=f"workflow_event_{uuid.uuid4().hex[:16]}",
            workflow_id=workflow_id,
            action=action,
            from_version=from_version,
            to_version=to_version,
            workflow_hash=workflow_hash,
            branch=branch,
            detail=detail,
        )

def agent_digest(agent: dict[str, Any]) -> str:
    selected = {
        "schemaVersion": agent.get("schemaVersion"),
        "slug": agent.get("slug"),
        "version": agent.get("version"),
        "execution": agent.get("execution"),
    }
    canonical = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workflow_digest(workflow: dict[str, Any]) -> str:
    canonical = json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _semver_tuple(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(version))
    if not match:
        raise WorkflowError(
            f"Published workflow version is not valid SemVer: {version}",
            code="workflow_version_invalid",
        )
    return tuple(int(value) for value in match.groups())


def _bump_semver(version: str, bump: str) -> str:
    major, minor, patch = _semver_tuple(version)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise WorkflowError("Unsupported version bump.", code="workflow_version_bump_invalid")


def normalize_workflow(workflow: dict[str, Any], agents: Any) -> dict[str, Any]:
    normalized = json.loads(json.dumps(workflow))
    for node in normalized.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("agentId"):
            continue
        agent = agents.get(str(node["agentId"]))
        node.setdefault("agentVersion", str(agent.get("version") or "0.0.0"))
        node.setdefault("agentDigest", agent_digest(agent))
    normalized.setdefault("schemaVersion", 1)
    normalized.setdefault("version", "0.1.0")
    normalized.setdefault("mode", "deterministic")
    normalized.setdefault("readOnly", True)
    normalized.setdefault("inputSchema", {"type": "object", "properties": {}, "additionalProperties": False})
    normalized.setdefault("outputSchema", {"type": "object", "properties": {}, "additionalProperties": False})
    normalized.setdefault("connections", [])
    normalized.setdefault("outputs", [])
    normalized.setdefault("policies", {"onInconclusive": "continue_if_required_outputs_present"})
    return normalized


def validate_workflow(
    workflow: dict[str, Any],
    agents: Any,
    *,
    source: str = "workflow.json",
    require_pins: bool = True,
) -> list[str]:
    if workflow.get("schemaVersion") not in {1, 2}:
        raise WorkflowError(f"{source}.schemaVersion must be 1 or 2")
    workflow_id = str(workflow.get("id") or "")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", workflow_id):
        raise WorkflowError(f"{source}.id is invalid")
    if workflow.get("mode") != "deterministic" or workflow.get("readOnly") is not True:
        raise WorkflowError(f"{source} must be deterministic and readOnly=true")
    _validate_object_schema(workflow.get("inputSchema"), f"{source}.inputSchema")
    _validate_object_schema(workflow.get("outputSchema"), f"{source}.outputSchema")
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowError(f"{source}.nodes must be a non-empty array")
    node_by_id: dict[str, dict[str, Any]] = {}
    agent_by_node: dict[str, dict[str, Any]] = {}
    drift: list[str] = []
    for index, node in enumerate(nodes):
        location = f"{source}.nodes[{index}]"
        if not isinstance(node, dict):
            raise WorkflowError(f"{location} must be an object")
        node_id = str(node.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", node_id) or node_id in node_by_id:
            raise WorkflowError(f"{location}.id is invalid or duplicated")
        agent_id = str(node.get("agentId") or "")
        try:
            if require_pins and callable(getattr(agents, "get_version", None)):
                agent = agents.get_version(
                    agent_id,
                    str(node.get("agentVersion") or ""),
                    str(node.get("agentDigest") or "") or None,
                )
            else:
                agent = agents.get(agent_id)
        except ManifestError as exc:
            raise WorkflowError(
                "One or more Agent versions changed after workflow validation.",
                code="agent_version_mismatch",
                detail={"nodes": [node_id]},
            ) from exc
        except KeyError as exc:
            # A missing pinned version is version drift, while an unknown Agent ID
            # remains a catalog availability error.  Keep the established error
            # contract so callers can distinguish remediation from bad references.
            if require_pins and node.get("agentVersion"):
                raise WorkflowError(
                    "One or more Agent versions changed after workflow validation.",
                    code="agent_version_mismatch",
                    detail={"nodes": [node_id]},
                ) from exc
            raise WorkflowError(f"{location}.agentId is unavailable: {agent_id}") from exc
        validate_execution(agent, f"agent:{agent_id}")
        execution = agent["execution"]
        if not isinstance(execution.get("outputSchema"), dict):
            raise WorkflowError(
                f"Agent {agent_id} has no composable execution.outputSchema",
                code="agent_not_composable",
            )
        expected_version = str(agent.get("version") or "0.0.0")
        expected_digest = agent_digest(agent)
        if require_pins and (
            node.get("agentVersion") != expected_version or node.get("agentDigest") != expected_digest
        ):
            drift.append(node_id)
        node_by_id[node_id] = node
        agent_by_node[node_id] = agent
        if node.get("forEach") is not None:
            if workflow.get("schemaVersion") != 2:
                raise WorkflowError(f"{location}.forEach requires workflow schemaVersion 2")
            _validate_foreach(node, workflow, node_by_id, agent_by_node, location)
        if node.get("runIf") is not None and workflow.get("schemaVersion") != 2:
            raise WorkflowError(f"{location}.runIf requires workflow schemaVersion 2")
        if node.get("onSkip") is not None and node.get("runIf") is None:
            raise WorkflowError(
                f"{location}.onSkip requires runIf",
                code="workflow_conditional_skip_output_invalid",
            )
    if drift:
        raise WorkflowError(
            "One or more Agent versions changed after workflow validation.",
            code="agent_version_mismatch",
            detail={"nodes": drift},
        )

    connections = workflow.get("connections")
    if not isinstance(connections, list):
        raise WorkflowError(f"{source}.connections must be an array")
    target_ports: set[tuple[str, str]] = set()
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    for index, connection in enumerate(connections):
        location = f"{source}.connections[{index}]"
        if not isinstance(connection, dict):
            raise WorkflowError(f"{location} must be an object")
        source_spec = connection.get("from")
        target_spec = connection.get("to")
        if not isinstance(source_spec, dict) or not isinstance(target_spec, dict):
            raise WorkflowError(f"{location} must contain from/to objects")
        target_node = str(target_spec.get("nodeId") or "")
        target_port = str(target_spec.get("port") or "")
        if target_node not in node_by_id:
            raise WorkflowError(f"{location}.to.nodeId is unavailable")
        target_schema = agent_by_node[target_node]["execution"]["inputSchema"]
        target_type = _property_schema(target_schema, target_port, f"{location}.to.port")
        target_key = (target_node, target_port)
        if target_key in target_ports:
            raise WorkflowError(f"{location} maps the same target input more than once")
        target_ports.add(target_key)
        if source_spec.get("scope") == "node_output":
            source_node_id = str(source_spec.get("nodeId") or "")
            source_port = str(source_spec.get("port") or "")
            source_node = node_by_id.get(source_node_id) or {}
            if isinstance(source_node.get("runIf"), dict) and not _conditional_skip_has_port(
                source_node, source_port
            ):
                raise WorkflowError(
                    f"{location} consumes conditional output "
                    f"{source_node_id}.{source_port} without an onSkip value",
                    code="workflow_conditional_skip_output_missing",
                    detail={"node_id": source_node_id, "port": source_port},
                )
        source_type = _source_type(
            source_spec, workflow, node_by_id, agent_by_node, dependencies, target_node, location
        )
        transform = connection.get("transform") or {"type": "identity"}
        transformed_type = _validate_transform(source_type, transform, location)
        if not _types_compatible(transformed_type, _schema_primary_type(target_type.get("type"))):
            raise WorkflowError(
                f"{location} maps incompatible types {transformed_type!r} -> "
                f"{target_type.get('type')!r}",
                code="workflow_port_type_mismatch",
            )
    for index, node in enumerate(nodes):
        node_id = str(node["id"])
        if node.get("runIf") is not None:
            _validate_run_if(
                node,
                workflow,
                node_by_id,
                agent_by_node,
                dependencies,
                f"{source}.nodes[{index}]",
            )
            _validate_on_skip(
                node,
                agent_by_node[node_id],
                f"{source}.nodes[{index}]",
            )
    for node_id, agent in agent_by_node.items():
        input_schema = agent["execution"]["inputSchema"]
        required = input_schema.get("required") or []
        properties = input_schema.get("properties") or {}
        missing = [
            port
            for port in required
            if (node_id, str(port)) not in target_ports
            and not (
                isinstance(properties.get(str(port)), dict)
                and (
                    properties[str(port)].get("x-sapba-server-default") == "business_date"
                    or (
                        properties[str(port)].get("x-sapba-server-default") is True
                        and "default" in properties[str(port)]
                    )
                )
            )
        ]
        if missing:
            raise WorkflowError(
                f"Node {node_id} is missing required inputs: {', '.join(missing)}",
                code="workflow_required_input_unmapped",
            )
        _validate_one_of_mapping(
            node_id,
            input_schema,
            {
                str((item.get("to") or {}).get("port") or ""): item.get("from") or {}
                for item in connections
                if str((item.get("to") or {}).get("nodeId") or "") == node_id
            },
        )
    topological_order(workflow)
    _validate_outputs(workflow, node_by_id, agent_by_node)
    return drift


def workflow_review_contract(workflow: dict[str, Any], agents: Any) -> dict[str, Any]:
    """Build the deterministic contract that bounds an Agent Runtime review.

    The Runtime may review graph semantics, but it must not expand conditional
    skip requirements to Agent outputs that the workflow never consumes.
    """

    nodes = {
        str(node.get("id") or ""): node
        for node in workflow.get("nodes") or []
        if isinstance(node, dict)
    }
    required_terminal_outputs = sorted(
        str(name)
        for name in workflow.get("outputSchema", {}).get("required") or []
    )
    required_terminal_set = set(required_terminal_outputs)
    required_on_skip: dict[str, set[str]] = {
        node_id: set()
        for node_id, node in nodes.items()
        if isinstance(node.get("runIf"), dict)
    }

    def require_source(source: Any) -> None:
        if not isinstance(source, dict) or source.get("scope") != "node_output":
            return
        node_id = str(source.get("nodeId") or "")
        port = str(source.get("port") or "")
        if node_id in required_on_skip and port:
            required_on_skip[node_id].add(port)

    for connection in workflow.get("connections") or []:
        if isinstance(connection, dict):
            require_source(connection.get("from"))
    for output in workflow.get("outputs") or []:
        if not isinstance(output, dict) or str(output.get("name") or "") not in required_terminal_set:
            continue
        aggregate = output.get("aggregate")
        if isinstance(aggregate, dict):
            for source in aggregate.get("sources") or []:
                require_source(source)
        else:
            require_source(output.get("source"))

    selected_branches: dict[str, dict[str, Any]] = {}
    connections = [item for item in workflow.get("connections") or [] if isinstance(item, dict)]
    for node_id, node in nodes.items():
        agent = agents.get(str(node.get("agentId") or ""))
        input_schema = agent["execution"]["inputSchema"]
        branches = input_schema.get("oneOf")
        if not isinstance(branches, list) or not branches:
            continue
        mappings = {
            str((item.get("to") or {}).get("port") or ""): item.get("from") or {}
            for item in connections
            if str((item.get("to") or {}).get("nodeId") or "") == node_id
        }
        matches: list[tuple[int, list[str]]] = []
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                continue
            required = sorted(str(item) for item in branch.get("required") or [])
            if set(required).issubset(mappings):
                matches.append((index, required))
        if len(matches) != 1:
            continue
        branch_index, required_ports = matches[0]
        constant_inputs = {
            port: source.get("value")
            for port, source in mappings.items()
            if isinstance(source, dict) and source.get("scope") == "constant"
        }
        selected_branches[node_id] = {
            "branch_index": branch_index,
            "required_ports": required_ports,
            "constant_inputs": constant_inputs,
        }

    return {
        "review_policy_version": WORKFLOW_REVIEW_POLICY_VERSION,
        "required_terminal_outputs": required_terminal_outputs,
        "required_on_skip_outputs_by_node": {
            node_id: sorted(ports) for node_id, ports in sorted(required_on_skip.items())
        },
        "selected_one_of_branches": selected_branches,
        "completeness_outputs": sorted(
            name for name in required_terminal_outputs if "complete" in name
        ),
    }


def topological_order(workflow: dict[str, Any]) -> list[str]:
    node_ids = [str(item["id"]) for item in workflow.get("nodes") or []]
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for connection in workflow.get("connections") or []:
        source = connection.get("from") or {}
        target = connection.get("to") or {}
        if source.get("scope") == "node_output":
            dependencies[str(target.get("nodeId"))].add(str(source.get("nodeId")))
    for node in workflow.get("nodes") or []:
        foreach = node.get("forEach") if isinstance(node, dict) else None
        source = foreach.get("source") if isinstance(foreach, dict) else None
        if isinstance(source, dict) and source.get("scope") == "node_output":
            dependencies[str(node.get("id"))].add(str(source.get("nodeId")))
        run_if = node.get("runIf") if isinstance(node, dict) else None
        run_source = run_if.get("source") if isinstance(run_if, dict) else None
        if isinstance(run_source, dict) and run_source.get("scope") == "node_output":
            dependencies[str(node.get("id"))].add(str(run_source.get("nodeId")))
    remaining = {node: set(values) for node, values in dependencies.items()}
    order: list[str] = []
    while remaining:
        ready = [node for node in node_ids if node in remaining and not remaining[node]]
        if not ready:
            raise WorkflowError("Workflow graph contains a cycle.", code="workflow_cycle_detected")
        for node in ready:
            order.append(node)
            remaining.pop(node)
            for values in remaining.values():
                values.discard(node)
    return order


def apply_transform(value: Any, transform: dict[str, Any] | None) -> Any:
    transform = transform or {"type": "identity"}
    kind = str(transform.get("type") or "identity")
    if kind == "identity":
        return value
    if kind == "to_string":
        return str(value)
    if kind == "to_integer":
        if isinstance(value, bool):
            raise WorkflowError("Boolean values cannot be converted to integer.", code="mapping_failed")
        return int(value)
    if kind == "format_date":
        text = str(value)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?", text):
            raise WorkflowError("Date transform requires an ISO date value.", code="mapping_failed")
        return text[:10]
    if kind == "first":
        if not isinstance(value, list) or not value:
            raise WorkflowError("first transform requires a non-empty list.", code="mapping_failed")
        return value[0]
    if kind == "join":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise WorkflowError("join transform requires a string list.", code="mapping_failed")
        return str(transform.get("separator") or ",").join(value)
    if kind == "wrap_array":
        return [value]
    raise WorkflowError(f"Unsupported workflow transform: {kind}", code="mapping_failed")


def validate_value(value: dict[str, Any], schema: dict[str, Any], *, label: str) -> None:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object", code="workflow_contract_violation")
    properties = schema.get("properties") or {}
    required = [str(item) for item in schema.get("required") or []]
    missing = [
        name
        for name in required
        if name not in value
        or (value[name] in (None, "") and not _schema_allows_null((properties.get(name) or {}).get("type")))
    ]
    if missing:
        raise WorkflowError(
            f"{label} is missing required fields: {', '.join(missing)}",
            code="workflow_output_unavailable",
        )
    unknown = sorted(set(value).difference(properties))
    if schema.get("additionalProperties") is False and unknown:
        raise WorkflowError(
            f"{label} contains undeclared fields: {', '.join(unknown)}",
            code="workflow_contract_violation",
        )
    for name, item in value.items():
        if name not in properties:
            continue
        expected = (properties[name] or {}).get("type")
        if not _value_matches_type(item, expected):
            raise WorkflowError(
                f"{label}.{name} does not match type {expected}",
                code="workflow_contract_violation",
            )
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        error = errors[0]
        path = ".".join(str(item) for item in error.absolute_path)
        suffix = f".{path}" if path else ""
        raise WorkflowError(
            f"{label}{suffix} violates {error.validator or 'schema'}",
            code="workflow_contract_violation",
        )


def _validate_object_schema(value: Any, location: str) -> None:
    if not isinstance(value, dict) or value.get("type") != "object" or not isinstance(
        value.get("properties"), dict
    ):
        raise WorkflowError(f"{location} must be an object JSON Schema")
    try:
        Draft202012Validator.check_schema(validator_schema(value))
    except SchemaError as exc:
        raise WorkflowError(f"{location} must be a valid JSON Schema") from exc


def _property_schema(schema: dict[str, Any], name: str, location: str) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    value = properties.get(name)
    if not isinstance(value, dict):
        raise WorkflowError(f"{location} references an undeclared port: {name}")
    return value


def _source_type(
    source: dict[str, Any],
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    target_node: str,
    location: str,
) -> str:
    scope = source.get("scope")
    if scope == "workflow_input":
        return _schema_primary_type(_property_schema(workflow["inputSchema"], str(source.get("port") or ""), location).get("type"))
    if scope == "node_output":
        source_node = str(source.get("nodeId") or "")
        if source_node not in nodes or source_node == target_node:
            raise WorkflowError(f"{location}.from.nodeId is unavailable")
        dependencies[target_node].add(source_node)
        output_schema = agents[source_node]["execution"]["outputSchema"]
        source_port_type = _schema_primary_type(
            _property_schema(output_schema, str(source.get("port") or ""), location).get("type")
        )
        return "array" if nodes[source_node].get("forEach") is not None else source_port_type
    if scope == "iteration_item":
        node = nodes.get(target_node) or {}
        foreach = node.get("forEach")
        if not isinstance(foreach, dict):
            raise WorkflowError(f"{location} uses iteration_item outside a foreach node")
        item_schema = _foreach_item_schema(foreach, workflow, nodes, agents, location)
        pointer = str(source.get("pointer") or "")
        selected = _schema_at_pointer(item_schema, pointer, location)
        return _schema_primary_type(selected.get("type"))
    if scope == "constant":
        return _json_type(source.get("value"))
    raise WorkflowError(f"{location}.from.scope is unsupported")


def _validate_transform(source_type: str, transform: Any, location: str) -> str:
    if not isinstance(transform, dict):
        raise WorkflowError(f"{location}.transform must be an object")
    kind = str(transform.get("type") or "identity")
    if kind not in ALLOWED_TRANSFORMS:
        raise WorkflowError(f"{location}.transform.type is not allowed")
    if kind == "identity":
        return source_type
    if kind == "to_string":
        return "string"
    if kind == "to_integer":
        if source_type not in {"string", "integer", "number"}:
            raise WorkflowError(f"{location} cannot convert {source_type} to integer")
        return "integer"
    if kind == "format_date":
        if source_type != "string":
            raise WorkflowError(f"{location} date formatting requires a string")
        return "string"
    if kind in {"first", "join"}:
        if source_type != "array":
            raise WorkflowError(f"{location}.{kind} requires an array source")
        return "string" if kind == "join" else str(transform.get("resultType") or "string")
    if kind == "wrap_array":
        return "array"
    return source_type


def _validate_foreach(
    node: dict[str, Any],
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    location: str,
) -> None:
    foreach = node.get("forEach")
    if not isinstance(foreach, dict):
        raise WorkflowError(f"{location}.forEach must be an object")
    source = foreach.get("source")
    if not isinstance(source, dict) or source.get("scope") not in {"workflow_input", "node_output"}:
        raise WorkflowError(f"{location}.forEach.source must be a workflow input or prior node output")
    item_schema = _foreach_item_schema(foreach, workflow, nodes, agents, location)
    if not isinstance(item_schema, dict):
        raise WorkflowError(f"{location}.forEach source must declare array items")
    maximum = foreach.get("maxItems", 50)
    concurrency = foreach.get("maxConcurrency", 4)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 50:
        raise WorkflowError(f"{location}.forEach.maxItems must be between 1 and 50")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= 8:
        raise WorkflowError(f"{location}.forEach.maxConcurrency must be between 1 and 8")
    if foreach.get("onItemError", "collect_inconclusive") != "collect_inconclusive":
        raise WorkflowError(f"{location}.forEach.onItemError is unsupported")


def _validate_run_if(
    node: dict[str, Any],
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    location: str,
) -> None:
    condition = node.get("runIf")
    if not isinstance(condition, dict):
        raise WorkflowError(f"{location}.runIf must be an object")
    if condition.get("operator") != "non_empty":
        raise WorkflowError(f"{location}.runIf.operator must be non_empty")
    source = condition.get("source")
    if not isinstance(source, dict) or source.get("scope") not in {
        "workflow_input",
        "node_output",
    }:
        raise WorkflowError(
            f"{location}.runIf.source must be a workflow input or prior node output"
        )
    source_type = _source_type(
        source,
        workflow,
        nodes,
        agents,
        dependencies,
        str(node.get("id") or ""),
        f"{location}.runIf",
    )
    if source_type not in {"array", "object", "string"}:
        raise WorkflowError(f"{location}.runIf.source must be a collection or string")


def _validate_on_skip(
    node: dict[str, Any], agent: dict[str, Any], location: str
) -> None:
    on_skip = node.get("onSkip")
    if on_skip is None:
        return
    if not isinstance(on_skip, dict):
        raise WorkflowError(
            f"{location}.onSkip must be an object",
            code="workflow_conditional_skip_output_invalid",
        )
    reason_code = on_skip.get("reasonCode")
    if not isinstance(reason_code, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]*", reason_code
    ):
        raise WorkflowError(
            f"{location}.onSkip.reasonCode is invalid",
            code="workflow_conditional_skip_output_invalid",
        )
    outputs = on_skip.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise WorkflowError(
            f"{location}.onSkip.outputs must be a non-empty object",
            code="workflow_conditional_skip_output_invalid",
        )
    properties = agent["execution"]["outputSchema"].get("properties") or {}
    for port, value in outputs.items():
        schema = properties.get(str(port))
        if not isinstance(schema, dict):
            raise WorkflowError(
                f"{location}.onSkip.outputs references undeclared port: {port}",
                code="workflow_conditional_skip_output_invalid",
                detail={"node_id": node.get("id"), "port": str(port)},
            )
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                value
            ),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise WorkflowError(
                f"{location}.onSkip.outputs.{port} violates "
                f"{errors[0].validator or 'schema'}",
                code="workflow_conditional_skip_output_invalid",
                detail={"node_id": node.get("id"), "port": str(port)},
            )


def _validate_one_of_mapping(
    node_id: str,
    input_schema: dict[str, Any],
    mappings: dict[str, dict[str, Any]],
) -> None:
    branches = input_schema.get("oneOf")
    if not isinstance(branches, list) or not branches:
        return
    matches: list[int] = []
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            continue
        required = {str(item) for item in branch.get("required") or []}
        if not required.issubset(mappings):
            continue
        constants = {
            str(port): constraint["const"]
            for port, constraint in (branch.get("properties") or {}).items()
            if isinstance(constraint, dict) and "const" in constraint
        }
        if any(
            port not in mappings
            or mappings[port].get("scope") != "constant"
            or mappings[port].get("value") != expected
            for port, expected in constants.items()
        ):
            continue
        forbidden = {
            str(item)
            for item in ((branch.get("not") or {}).get("required") or [])
        }
        if forbidden and forbidden.issubset(mappings):
            continue
        matches.append(index)
    if len(matches) == 1:
        return
    code = "workflow_branch_unmatched" if not matches else "workflow_branch_ambiguous"
    raise WorkflowError(
        f"Node {node_id} must explicitly satisfy exactly one oneOf input branch.",
        code=code,
        detail={"node_id": node_id, "matching_branches": matches},
    )


def _foreach_item_schema(
    foreach: dict[str, Any],
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    location: str,
) -> dict[str, Any]:
    source = foreach.get("source") or {}
    if source.get("scope") == "workflow_input":
        schema = _property_schema(
            workflow["inputSchema"], str(source.get("port") or ""), f"{location}.forEach.source"
        )
    elif source.get("scope") == "node_output":
        source_node = str(source.get("nodeId") or "")
        if source_node not in nodes:
            raise WorkflowError(f"{location}.forEach.source node must precede the foreach node")
        schema = _property_schema(
            agents[source_node]["execution"]["outputSchema"],
            str(source.get("port") or ""),
            f"{location}.forEach.source",
        )
    else:
        raise WorkflowError(f"{location}.forEach.source is unsupported")
    if _schema_primary_type(schema.get("type")) != "array" or not isinstance(schema.get("items"), dict):
        raise WorkflowError(f"{location}.forEach.source must be an array with an item schema")
    item_schema = schema["items"]
    group_by = foreach.get("groupBy")
    if group_by is None:
        return item_schema
    if not isinstance(group_by, dict) or not group_by:
        raise WorkflowError(f"{location}.forEach.groupBy must be a non-empty object")
    key_properties: dict[str, Any] = {}
    for name, pointer in group_by.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(name)) or not isinstance(pointer, str):
            raise WorkflowError(f"{location}.forEach.groupBy contains an invalid key")
        key_properties[str(name)] = _schema_at_pointer(item_schema, pointer, location)
    return {
        "type": "object",
        "properties": {
            "key": {"type": "object", "properties": key_properties, "required": list(key_properties), "additionalProperties": False},
            "items": {"type": "array", "items": item_schema},
        },
        "required": ["key", "items"],
        "additionalProperties": False,
    }


def _schema_at_pointer(schema: dict[str, Any], pointer: str, location: str) -> dict[str, Any]:
    if pointer in {"", "/"}:
        return schema
    if not pointer.startswith("/"):
        raise WorkflowError(f"{location} JSON Pointer must start with /")
    current: Any = schema
    for raw in pointer[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            raise WorkflowError(f"{location} JSON Pointer is outside the declared schema")
        if _schema_primary_type(current.get("type")) == "object":
            current = (current.get("properties") or {}).get(part)
        elif _schema_primary_type(current.get("type")) == "array" and part.isdigit():
            current = current.get("items")
        else:
            current = None
        if not isinstance(current, dict):
            raise WorkflowError(f"{location} JSON Pointer is outside the declared schema")
    return current


def _validate_aggregate(
    aggregate: Any,
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    location: str,
) -> str:
    if not isinstance(aggregate, dict):
        raise WorkflowError(f"{location}.aggregate must be an object")
    operator = str(aggregate.get("operator") or "")
    if operator not in {"status_precedence", "all_true", "collect"}:
        raise WorkflowError(f"{location}.aggregate.operator is unsupported")
    sources = aggregate.get("sources")
    if not isinstance(sources, list) or not sources:
        raise WorkflowError(f"{location}.aggregate.sources must be non-empty")
    source_types = [
        _source_type(source, workflow, nodes, agents, dependencies, "", location)
        for source in sources
        if isinstance(source, dict)
    ]
    if len(source_types) != len(sources):
        raise WorkflowError(f"{location}.aggregate.sources contains an invalid source")
    if operator == "status_precedence":
        precedence = aggregate.get("precedence")
        if not isinstance(precedence, list) or not precedence or len(precedence) != len(set(precedence)):
            raise WorkflowError(f"{location}.aggregate.precedence must contain unique statuses")
        if any(item not in {"string", "array"} for item in source_types):
            raise WorkflowError(f"{location}.aggregate.status_precedence requires strings")
        return "string"
    if operator == "all_true":
        if any(item not in {"boolean", "array"} for item in source_types):
            raise WorkflowError(f"{location}.aggregate.all_true requires booleans")
        return "boolean"
    return "array"


def _validate_outputs(
    workflow: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    agents: dict[str, dict[str, Any]],
) -> None:
    output_defs = workflow.get("outputs")
    if not isinstance(output_defs, list):
        raise WorkflowError("workflow.outputs must be an array")
    declared = workflow["outputSchema"].get("properties") or {}
    seen: set[str] = set()
    dummy_dependencies = {node: set() for node in nodes}
    dummy_dependencies[""] = set()
    for index, output in enumerate(output_defs):
        if not isinstance(output, dict):
            raise WorkflowError(f"workflow.outputs[{index}] must be an object")
        name = str(output.get("name") or "")
        if name not in declared or name in seen:
            raise WorkflowError(f"workflow.outputs[{index}].name is undeclared or duplicated")
        seen.add(name)
        aggregate = output.get("aggregate")
        if aggregate is not None:
            transformed = _validate_aggregate(
                aggregate,
                workflow,
                nodes,
                agents,
                dummy_dependencies,
                f"workflow.outputs[{index}]",
            )
        else:
            source_type = _source_type(
                output.get("source") or {}, workflow, nodes, agents, dummy_dependencies, "", f"workflow.outputs[{index}]"
            )
            transformed = _validate_transform(source_type, output.get("transform") or {"type": "identity"}, f"workflow.outputs[{index}]")
        if name in set(workflow["outputSchema"].get("required") or []):
            missing_skip = _conditional_output_ports_without_skip(output, nodes)
        else:
            missing_skip = []
        if missing_skip:
            raise WorkflowError(
                f"workflow.outputs[{index}] requires conditional outputs without onSkip values",
                code="workflow_conditional_skip_output_missing",
                detail={"sources": missing_skip},
            )
        if not _types_compatible(transformed, _schema_primary_type(declared[name].get("type"))):
            raise WorkflowError(f"workflow.outputs[{index}] has an incompatible type")
    missing = sorted(set(workflow["outputSchema"].get("required") or []).difference(seen))
    if missing:
        raise WorkflowError("Required workflow outputs are not mapped: " + ", ".join(missing))


def _conditional_output_ports_without_skip(
    output: dict[str, Any], nodes: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    aggregate = output.get("aggregate")
    sources = (
        aggregate.get("sources")
        if isinstance(aggregate, dict)
        else [output.get("source") or {}]
    )
    node_sources = [
        source
        for source in sources or []
        if isinstance(source, dict) and source.get("scope") == "node_output"
    ]
    missing: list[dict[str, str]] = []
    for source in node_sources:
        node_id = str(source.get("nodeId") or "")
        port = str(source.get("port") or "")
        node = nodes.get(node_id) or {}
        if isinstance(node.get("runIf"), dict) and not _conditional_skip_has_port(
            node, port
        ):
            missing.append({"node_id": node_id, "port": port})
    return missing


def _conditional_skip_has_port(node: dict[str, Any], port: str) -> bool:
    on_skip = node.get("onSkip")
    outputs = on_skip.get("outputs") if isinstance(on_skip, dict) else None
    return isinstance(outputs, dict) and port in outputs


def _types_compatible(source: str, target: str) -> bool:
    return source == target or (source == "integer" and target == "number")


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"


def _schema_allows_null(value: Any) -> bool:
    return isinstance(value, list) and "null" in value


def _schema_primary_type(value: Any) -> str:
    if isinstance(value, list):
        return next((str(item) for item in value if item != "null"), "null")
    return str(value or "")


def _value_matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_value_matches_type(value, item) for item in expected)
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def iter_node_connections(workflow: dict[str, Any], node_id: str) -> Iterable[dict[str, Any]]:
    for connection in workflow.get("connections") or []:
        if str((connection.get("to") or {}).get("nodeId") or "") == node_id:
            yield connection
