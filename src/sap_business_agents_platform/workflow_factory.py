from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .database import RunStore
from .engine import RunCoordinator
from .models import RunStatus, TERMINAL_STATUSES, WorkflowDraftRecord, utc_now
from .workflows import (
    WorkflowError,
    agent_digest,
    normalize_workflow,
    validate_workflow,
    workflow_digest,
)


class WorkflowDraftError(RuntimeError):
    def __init__(self, message: str, *, code: str = "workflow_draft_error", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class WorkflowDraftService:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        agents: Any,
        coordinator: RunCoordinator,
        sap_read: Any,
        author: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.coordinator = coordinator
        self.sap_read = sap_read
        self.author = author
        self._tasks: set[asyncio.Task[Any]] = set()

    def create(
        self,
        title: dict[str, str],
        description: dict[str, str],
        workflow: dict[str, Any] | None,
    ) -> WorkflowDraftRecord:
        draft_id = f"workflow_draft_{uuid.uuid4().hex[:12]}"
        slug = f"workflow-{draft_id[-8:]}"
        value = workflow or {
            "schemaVersion": 1,
            "id": slug,
            "version": "0.1.0",
            "title": title,
            "description": description,
            "mode": "deterministic",
            "readOnly": True,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "nodes": [],
            "connections": [],
            "outputs": [],
            "policies": {"onInconclusive": "continue_if_required_outputs_present"},
        }
        value = normalize_workflow(value, self.agents)
        draft_dir = (self.settings.draft_root / "workflows" / draft_id).resolve()
        expected_root = (self.settings.draft_root / "workflows").resolve()
        if expected_root not in draft_dir.parents:
            raise WorkflowDraftError("Workflow draft path escaped the configured authoring root.")
        draft_dir.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        draft = WorkflowDraftRecord(
            draft_id=draft_id,
            status="draft",
            revision=1,
            workflow=value,
            path=str(draft_dir),
            validation={"valid": False, "issues": ["Workflow has not been validated."]},
            created_at=now,
            updated_at=now,
        )
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=[{"op": "create", "path": "/"}])
        return draft

    def get(self, draft_id: str) -> WorkflowDraftRecord:
        return self.store.get_workflow_draft(draft_id)

    def revisions(self, draft_id: str) -> list[dict[str, Any]]:
        self.store.get_workflow_draft(draft_id)
        return self.store.list_workflow_revisions(draft_id)

    def update(
        self, draft_id: str, expected_revision: int, workflow: dict[str, Any]
    ) -> WorkflowDraftRecord:
        current = self.store.get_workflow_draft(draft_id)
        if current.status == "published":
            raise WorkflowDraftError("A published draft is immutable.", code="workflow_draft_published")
        if current.revision != expected_revision:
            raise WorkflowDraftError(
                "Workflow draft revision changed; reload before saving.",
                code="workflow_revision_conflict",
                detail={"expected": expected_revision, "actual": current.revision},
            )
        normalized = normalize_workflow(workflow, self.agents)
        diff = _json_diff(current.workflow, normalized)
        current.workflow = normalized
        current.revision += 1
        current.status = "draft"
        current.validation_run_id = None
        current.validation = {"valid": False, "issues": ["Draft changed after validation."]}
        current.updated_at = utc_now()
        self._write_draft(current)
        self.store.save_workflow_draft(current, diff=diff)
        return current

    def validate_structure(self, draft_id: str) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(draft_id)
        try:
            validate_workflow(
                draft.workflow,
                self.agents,
                source=f"workflow-draft:{draft_id}",
                require_pins=True,
            )
        except WorkflowError as exc:
            draft.status = "invalid"
            draft.validation = {
                "valid": False,
                "issues": [{"code": exc.code, "message": str(exc), "detail": exc.detail}],
                "workflow_hash": workflow_digest(draft.workflow),
            }
        else:
            draft.status = "draft"
            draft.validation = {
                "valid": True,
                "issues": [],
                "workflow_hash": workflow_digest(draft.workflow),
            }
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        return draft

    async def validate_live(
        self,
        draft_id: str,
        supplied_input: dict[str, Any],
        *,
        auto_discover: bool,
    ) -> WorkflowDraftRecord:
        draft = self.validate_structure(draft_id)
        if draft.validation.get("valid") is not True:
            raise WorkflowDraftError(
                "Workflow structure is invalid.",
                code="workflow_validation_failed",
                detail=draft.validation,
            )
        validation_input = dict(supplied_input)
        if auto_discover:
            validation_input = await self._discover_missing_inputs(draft, validation_input)
        review_workflow = getattr(self.author, "review_workflow", None)
        if callable(review_workflow):
            try:
                review = await review_workflow(
                    workflow=draft.workflow,
                    agent_contracts=self._agent_contracts(draft.workflow),
                    validation_input={key: "<provided>" for key in validation_input},
                    thread_id=draft.thread_id,
                )
                draft.thread_id = str(review.get("thread_id") or draft.thread_id or "") or None
                draft.validation["codex_review"] = {
                    "zh": str(review.get("zh") or ""),
                    "en": str(review.get("en") or ""),
                }
            except Exception as exc:
                draft.validation["codex_review"] = {
                    "warning": "Codex review was unavailable; deterministic validation continued.",
                    "error_type": type(exc).__name__,
                }
        run_id = await self.coordinator.submit_workflow_snapshot(
            draft.workflow,
            validation_input,
            draft_id=draft_id,
            revision=draft.revision,
        )
        self.store.append_event(
            run_id,
            "candidate_discovery_completed",
            {"auto_discover": auto_discover, "input_fields": sorted(validation_input)},
        )
        draft.status = "needs_review"
        draft.validation_run_id = run_id
        draft.validation.update(
            {
                "valid": True,
                "live_status": "running",
                "validated_revision": draft.revision,
                "workflow_hash": workflow_digest(draft.workflow),
                "repair_attempts": 0,
            }
        )
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        task = asyncio.create_task(
            self._monitor_validation(draft_id, run_id, validation_input, repair_attempt=0),
            name=f"workflow-validation-{draft_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return draft

    def publish(self, draft_id: str, *, acknowledge_inconclusive: bool) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(draft_id)
        if draft.status not in {"validated", "inconclusive"}:
            raise WorkflowDraftError("Only a live-validated workflow can be published.")
        if draft.validation.get("workflow_hash") != workflow_digest(draft.workflow):
            raise WorkflowDraftError("Workflow changed after validation.", code="workflow_changed_after_validation")
        if draft.status == "inconclusive" and not acknowledge_inconclusive:
            raise WorkflowDraftError(
                "Publishing an inconclusive workflow requires explicit acknowledgement.",
                code="inconclusive_acknowledgement_required",
            )
        validate_workflow(draft.workflow, self.agents, require_pins=True)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            raise WorkflowDraftError("Publish requires a clean Git worktree.", code="git_worktree_dirty")
        workflow = json.loads(json.dumps(draft.workflow))
        workflow["version"] = _published_version(str(workflow.get("version") or "0.1.0"))
        workflow["status"] = "Published"
        workflow["validation"] = {
            "run_id": draft.validation_run_id,
            "workflow_hash": workflow_digest(draft.workflow),
            "status": draft.status,
            "acknowledged_inconclusive": bool(acknowledge_inconclusive),
            "validated_at": draft.validation.get("completed_at"),
        }
        branch_version = re.sub(r"[^0-9A-Za-z._-]", "-", workflow["version"])
        branch = f"codex/workflow-{workflow['id']}-v{branch_version}"
        target = self.settings.repository_root / "workflows" / "Common" / str(workflow["id"])
        if target.exists():
            raise WorkflowDraftError(f"Workflow target already exists: {target}")
        subprocess.run(
            ["git", "switch", "-c", branch],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        target.mkdir(parents=True)
        (target / "workflow.json").write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (target / "README.md").write_text(_workflow_readme(workflow), encoding="utf-8")
        (target / "validation.json").write_text(
            json.dumps(workflow["validation"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        publish_diff = _json_diff(draft.workflow, workflow)
        published_from_revision = draft.revision
        draft.workflow = workflow
        draft.revision += 1
        draft.status = "published"
        draft.validation.update(
            {
                "branch": branch,
                "target": str(target),
                "acknowledged_inconclusive": acknowledge_inconclusive,
                "published_from_revision": published_from_revision,
                "published_workflow_hash": workflow_digest(workflow),
            }
        )
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=publish_diff)
        return draft

    async def _monitor_validation(
        self,
        draft_id: str,
        run_id: str,
        validation_input: dict[str, Any],
        *,
        repair_attempt: int,
    ) -> None:
        while True:
            record = self.store.get_run(run_id)
            if record.status in TERMINAL_STATUSES:
                break
            await asyncio.sleep(0.25)
        draft = self.store.get_workflow_draft(draft_id)
        if draft.validation_run_id != run_id:
            return
        if record.status in {RunStatus.completed, RunStatus.inconclusive}:
            draft.status = "validated" if record.status == RunStatus.completed else "inconclusive"
            draft.validation.update(
                {
                    "live_status": record.status.value,
                    "result_completeness": (
                        record.result.completeness.model_dump(mode="json") if record.result else {}
                    ),
                    "completed_at": record.completed_at,
                    "repair_attempts": repair_attempt,
                }
            )
            draft.updated_at = utc_now()
            self.store.save_workflow_draft(draft)
            return
        repair = getattr(self.author, "repair_workflow", None)
        if repair_attempt >= 2 or not callable(repair):
            draft.status = "needs_review"
            draft.validation.update(
                {
                    "live_status": record.status.value,
                    "error": record.error,
                    "repair_attempts": repair_attempt,
                    "completed_at": record.completed_at,
                }
            )
            draft.updated_at = utc_now()
            self.store.save_workflow_draft(draft)
            return
        try:
            proposal = await repair(
                workflow=draft.workflow,
                agent_contracts=self._agent_contracts(draft.workflow),
                error=record.error or {},
                thread_id=draft.thread_id,
            )
            connections = proposal.get("connections")
            if not isinstance(connections, list):
                raise WorkflowDraftError("Codex repair did not return a connection list.")
            before_nodes = draft.workflow.get("nodes")
            repaired = json.loads(json.dumps(draft.workflow))
            repaired["connections"] = connections
            if repaired.get("nodes") != before_nodes:
                raise WorkflowDraftError("Codex repair attempted to change workflow nodes.")
            repaired = normalize_workflow(repaired, self.agents)
            validate_workflow(repaired, self.agents, require_pins=True)
        except Exception as exc:
            draft.status = "needs_review"
            draft.validation.update(
                {
                    "live_status": "repair_failed",
                    "repair_error": type(exc).__name__,
                    "repair_attempts": repair_attempt,
                }
            )
            draft.updated_at = utc_now()
            self.store.save_workflow_draft(draft)
            return
        diff = _json_diff(draft.workflow, repaired)
        draft.workflow = repaired
        draft.revision += 1
        draft.thread_id = str(proposal.get("thread_id") or draft.thread_id or "") or None
        draft.validation["repair_attempts"] = repair_attempt + 1
        draft.validation.setdefault("repair_diffs", []).append(
            {"revision": draft.revision, "diff": diff, "reason": proposal.get("reason")}
        )
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=diff)
        next_run = await self.coordinator.submit_workflow_snapshot(
            repaired,
            validation_input,
            draft_id=draft_id,
            revision=draft.revision,
        )
        self.store.append_event(
            next_run,
            "codex_repair_applied",
            {"draft_id": draft_id, "revision": draft.revision, "attempt": repair_attempt + 1},
        )
        draft.validation_run_id = next_run
        draft.validation["workflow_hash"] = workflow_digest(repaired)
        self.store.save_workflow_draft(draft)
        await self._monitor_validation(
            draft_id,
            next_run,
            validation_input,
            repair_attempt=repair_attempt + 1,
        )

    async def _discover_missing_inputs(
        self, draft: WorkflowDraftRecord, supplied: dict[str, Any]
    ) -> dict[str, Any]:
        resolved = dict(supplied)
        required = [str(item) for item in draft.workflow["inputSchema"].get("required") or []]
        missing = [name for name in required if resolved.get(name) in (None, "")]
        if not missing:
            return resolved
        if "as_of" in missing:
            resolved["as_of"] = date.today().isoformat()
            missing.remove("as_of")
        discovery_specs = {
            "purchase_order": {
                "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                "entity_set": "A_PurchaseOrder",
                "field": "PurchaseOrder",
                "select_fields": ["PurchaseOrder", "CompanyCode", "Supplier"],
            },
            "sales_order": {
                "service_name": "API_SALES_ORDER_SRV",
                "entity_set": "A_SalesOrder",
                "field": "SalesOrder",
                "select_fields": ["SalesOrder", "SoldToParty"],
            },
        }
        for name in list(missing):
            spec = discovery_specs.get(name)
            if not spec:
                continue
            plan = {
                "service_name": spec["service_name"],
                "entity_set": spec["entity_set"],
                "http_method": "GET",
                "plan_kind": "direct",
                "select_fields": spec["select_fields"],
                "top": 5,
                "rationale": "Bounded read-only candidate discovery for workflow validation.",
            }
            validation = await self.sap_read.validate_plan(plan, "workflow validation candidate")
            if validation.get("ok") is not True:
                continue
            response = await self.sap_read.execute_plan(plan, "workflow validation candidate")
            rows = _extract_rows(response)
            candidates = [str(row.get(spec["field"]) or "").strip() for row in rows]
            candidates = [candidate for candidate in candidates if candidate]
            if candidates:
                resolved[name] = candidates[0]
                missing.remove(name)
        if missing:
            raise WorkflowDraftError(
                "Automatic candidate discovery could not provide: " + ", ".join(missing),
                code="workflow_validation_input_unavailable",
            )
        return resolved

    def _agent_contracts(self, workflow: dict[str, Any]) -> list[dict[str, Any]]:
        contracts: list[dict[str, Any]] = []
        for node in workflow.get("nodes") or []:
            agent = self.agents.get(str(node["agentId"]))
            contracts.append(
                {
                    "node_id": node["id"],
                    "agent_id": node["agentId"],
                    "version": agent.get("version"),
                    "digest": agent_digest(agent),
                    "input_schema": agent["execution"]["inputSchema"],
                    "output_schema": agent["execution"].get("outputSchema"),
                }
            )
        return contracts

    def _write_draft(self, draft: WorkflowDraftRecord) -> None:
        path = Path(draft.path)
        path.mkdir(parents=True, exist_ok=True)
        (path / "workflow.json").write_text(
            json.dumps(draft.workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (path / "draft.json").write_text(
            json.dumps(
                draft.model_dump(mode="json", exclude={"workflow"}),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _extract_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"results", "value", "rows"} and isinstance(child, list):
                rows.extend(dict(item) for item in child if isinstance(item, dict))
            elif key in {"step_results", "data", "result"}:
                rows.extend(_extract_rows(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_extract_rows(child))
    return rows


def _json_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            pointer = f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            if key not in after:
                changes.append({"op": "remove", "path": pointer})
            elif key not in before:
                changes.append({"op": "add", "path": pointer, "value": after[key]})
            else:
                changes.extend(_json_diff(before[key], after[key], pointer))
        return changes
    return [{"op": "replace", "path": path or "/", "value": after}]


def _published_version(version: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", version) and not version.startswith("0."):
        return version
    return "1.0.0"


def _workflow_readme(workflow: dict[str, Any]) -> str:
    title = workflow.get("title") if isinstance(workflow.get("title"), dict) else {}
    return (
        f"# {title.get('zh') or workflow['id']}\n\n"
        f"{(workflow.get('description') or {}).get('zh', '')}\n\n"
        "- 固定、确定性、严格只读的组合工作流。\n"
        "- Agent版本或摘要变化后必须停止运行并重新验证。\n"
        "- 工作流执行完成不自动代表相关SAP业务流程已经完成。\n\n"
        f"## {title.get('en') or workflow['id']}\n\n"
        f"{(workflow.get('description') or {}).get('en', '')}\n\n"
        "- Fixed, deterministic, strictly read-only composite workflow.\n"
        "- Agent version or digest drift requires revalidation.\n"
        "- Successful execution does not by itself prove business-process completion.\n"
    )
