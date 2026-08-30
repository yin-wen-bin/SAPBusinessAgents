from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import uuid
from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .config import Settings
from .database import RunStore
from .engine import RunCoordinator
from .models import RunStatus, TERMINAL_STATUSES, WorkflowDraftRecord, utc_now
from .workflow_composer import (
    WORKFLOW_COMPILER_VERSION,
    WorkflowCompositionError,
    compact_agent_catalog,
    compile_workflow_proposal,
    gap_free_query_prompt,
)
from .workflows import (
    WORKFLOW_REVIEW_POLICY_VERSION,
    WorkflowError,
    agent_digest,
    normalize_workflow,
    validate_workflow,
    workflow_review_contract,
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
            validation={
                "valid": False,
                "issues": ["Workflow has not been validated."],
                "phase": "not_started",
                "verdict": "pending",
            },
            created_at=now,
            updated_at=now,
        )
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=[{"op": "create", "path": "/"}])
        return draft

    def create_version(
        self,
        workflow: dict[str, Any],
        *,
        workflow_id: str,
        source_version: str,
        source_hash: str,
        target_version: str,
    ) -> WorkflowDraftRecord:
        draft = self.create(
            workflow.get("title") or {"zh": workflow_id, "en": workflow_id},
            workflow.get("description") or {"zh": "", "en": ""},
            workflow,
        )
        draft.composition["version_origin"] = {
            "workflow_id": workflow_id,
            "source_version": source_version,
            "source_hash": source_hash,
            "target_version": target_version,
        }
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft)
        return draft

    def get(self, draft_id: str) -> WorkflowDraftRecord:
        return self.store.get_workflow_draft(draft_id)

    def revisions(self, draft_id: str) -> list[dict[str, Any]]:
        self.store.get_workflow_draft(draft_id)
        return self.store.list_workflow_revisions(draft_id)

    def start_composition(self, requirement: str, locale: str) -> WorkflowDraftRecord:
        draft = self.create(
            {"zh": "正在生成工作流", "en": "Generating workflow"},
            {"zh": requirement, "en": requirement},
            None,
        )
        draft.status = "planning"
        draft.composition = {
            "requirement": requirement,
            "locale": locale,
            "runtime_provider_id": str(
                getattr(self.author, "current_provider_id", "codex")
            ),
            "catalog_digest": "",
            "stages": [],
            "gaps": [],
            "validation_defaults": {},
            "clarification_question": "",
            "clarification_history": [],
            "error": None,
            "compiler_version": WORKFLOW_COMPILER_VERSION,
        }
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        self._schedule_composition(draft.draft_id)
        return draft

    def provide_composition_input(
        self, draft_id: str, clarification_input: str
    ) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(draft_id)
        if draft.status != "waiting_input":
            raise WorkflowDraftError(
                "This workflow draft is not waiting for composition input.",
                code="workflow_composition_not_waiting",
            )
        history = list(draft.composition.get("clarification_history") or [])
        history.append(
            {
                "question": str(draft.composition.get("clarification_question") or ""),
                "answer": clarification_input,
            }
        )
        draft.composition["clarification_history"] = history
        draft.composition["clarification_question"] = ""
        draft.status = "planning"
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        self._schedule_composition(draft_id, clarification_input=clarification_input)
        return draft

    def reconcile(self, draft_id: str) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(draft_id)
        previous_revision = draft.revision
        migrated = self._ensure_current_compiler(draft)
        if migrated.revision != previous_revision:
            return migrated
        draft = migrated
        if draft.status == "needs_review" and isinstance(
            draft.composition.get("error"), dict
        ):
            error = draft.composition["error"]
            if (
                error.get("code") != "workflow_conditional_skip_output_unavailable"
                or int(draft.composition.get("compiler_version") or 0)
                >= WORKFLOW_COMPILER_VERSION
            ):
                raise WorkflowDraftError(
                    "This composition error cannot be retried automatically.",
                    code="workflow_composition_retry_not_allowed",
                    detail=deepcopy_json(error),
                )
            proposal = draft.composition.get("proposal_snapshot")
            if isinstance(proposal, dict) and isinstance(proposal.get("stages"), list):
                return self._apply_compiled_proposal(
                    draft,
                    proposal=proposal,
                    catalog=compact_agent_catalog(self.agents),
                    provider_id=str(
                        draft.composition.get("runtime_provider_id") or "codex"
                    ),
                )
            draft.status = "planning"
            draft.composition["error"] = None
            draft.composition["reconciling"] = True
            draft.updated_at = utc_now()
            self.store.save_workflow_draft(draft)
            self._schedule_composition(draft_id)
            return draft
        if draft.status != "needs_agents":
            return draft
        current_catalog = compact_agent_catalog(self.agents)
        if draft.composition.get("catalog_digest") == current_catalog["digest"]:
            return draft
        draft.status = "planning"
        draft.composition["reconciling"] = True
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        self._schedule_composition(draft_id)
        return draft

    def gap(self, draft_id: str, gap_id: str, *, locale: str) -> dict[str, Any]:
        draft = self.store.get_workflow_draft(draft_id)
        gap = next(
            (
                item
                for item in draft.composition.get("gaps") or []
                if str(item.get("gap_id") or "") == gap_id
            ),
            None,
        )
        if gap is None:
            raise KeyError(gap_id)
        return {
            "workflow_draft_id": draft_id,
            "gap": gap,
            "prompt": gap_free_query_prompt(gap, locale=locale),
        }

    def link_agent_draft(
        self, workflow_draft_id: str, gap_id: str, agent_draft_id: str
    ) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(workflow_draft_id)
        gaps = list(draft.composition.get("gaps") or [])
        found = False
        for gap in gaps:
            if str(gap.get("gap_id") or "") != gap_id:
                continue
            gap["status"] = "agent_draft_created"
            gap["agent_draft_id"] = agent_draft_id
            found = True
            break
        if not found:
            raise KeyError(gap_id)
        draft.composition["gaps"] = gaps
        draft.updated_at = utc_now()
        self.store.save_workflow_draft(draft)
        return draft

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
        current.status = "needs_agents" if current.composition.get("gaps") else "draft"
        current.validation_run_id = None
        current.validation = {
            "valid": False,
            "issues": ["Draft changed after validation."],
            "phase": "not_started",
            "verdict": "pending",
        }
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
                "phase": "completed",
                "verdict": "blocked",
            }
        else:
            draft.status = "draft"
            draft.validation = {
                "valid": True,
                "issues": [],
                "workflow_hash": workflow_digest(draft.workflow),
                "phase": "preflight",
                "verdict": "pending",
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
        expectations: list[dict[str, Any]] | None = None,
    ) -> WorkflowDraftRecord:
        current = self._ensure_current_compiler(self.store.get_workflow_draft(draft_id))
        gaps = list(current.composition.get("gaps") or [])
        if gaps:
            raise WorkflowDraftError(
                "Workflow validation is blocked until all missing Agents are available.",
                code="workflow_gaps_unresolved",
                detail={"gaps": [str(item.get("gap_id") or "") for item in gaps]},
            )
        draft = self.validate_structure(draft_id)
        if draft.validation.get("valid") is not True:
            raise WorkflowDraftError(
                "Workflow structure is invalid.",
                code="workflow_validation_failed",
                detail=draft.validation,
            )
        validated_expectations = _validate_expectation_contracts(
            draft.workflow, expectations or []
        )
        required_inputs = [
            str(item) for item in draft.workflow.get("inputSchema", {}).get("required") or []
        ]
        review_contract = workflow_review_contract(draft.workflow, self.agents)
        validation_input_shape = {
            name: (
                "<auto_discover>"
                if _validation_input_is_missing(supplied_input.get(name)) and auto_discover
                else "<provided>"
                if not _validation_input_is_missing(supplied_input.get(name))
                else "<missing>"
            )
            for name in sorted(set(required_inputs).union(supplied_input))
        }
        review_workflow = getattr(self.author, "review_workflow", None)
        if not callable(review_workflow):
            self._fail_runtime_review(
                draft,
                code="workflow_runtime_review_unavailable",
                message="The selected Agent Runtime does not support workflow review.",
                error_type="UnsupportedCapability",
            )
        try:
            provider_id = str(
                draft.composition.get("runtime_provider_id") or "codex"
            )
            pin = getattr(self.author, "pin", None)
            context = pin(provider_id) if callable(pin) else nullcontext()
            with context:
                raw_review = await asyncio.wait_for(
                    review_workflow(
                        workflow=draft.workflow,
                        agent_contracts=self._agent_contracts(draft.workflow),
                        validation_input=validation_input_shape,
                        review_contract=review_contract,
                        thread_id=draft.thread_id,
                    ),
                    timeout=min(180.0, max(1.0, float(self.settings.max_run_seconds))),
                )
            review = _reconcile_runtime_review(
                _validated_runtime_review(raw_review),
                review_contract=review_contract,
                workflow=draft.workflow,
            )
        except Exception as exc:
            self._fail_runtime_review(
                draft,
                code="workflow_runtime_review_unavailable",
                message="Agent Runtime review was unavailable or returned an invalid contract.",
                error_type=type(exc).__name__,
            )
        draft.thread_id = str(raw_review.get("thread_id") or draft.thread_id or "") or None
        draft.validation["runtime_review"] = review
        draft.validation["preflight_review"] = review
        draft.validation["review_contract"] = review_contract
        draft.validation["review_policy_version"] = WORKFLOW_REVIEW_POLICY_VERSION
        if review["verdict"] == "block":
            draft.status = "needs_review"
            draft.validation_run_id = None
            draft.validation.update(
                {
                    "valid": True,
                    "live_status": "blocked",
                    "phase": "completed",
                    "verdict": "blocked",
                    "issues": review["issues"],
                    "workflow_hash": workflow_digest(draft.workflow),
                }
            )
            draft.updated_at = utc_now()
            self._write_draft(draft)
            self.store.save_workflow_draft(draft)
            raise WorkflowDraftError(
                "Agent Runtime blocked live validation. Resolve the reported workflow issues first.",
                code="workflow_runtime_review_blocked",
                detail={"review": review},
            )
        validation_input = dict(supplied_input)
        discovery_used = auto_discover and any(
            _validation_input_is_missing(validation_input.get(name)) for name in required_inputs
        )
        if auto_discover:
            validation_input = await self._discover_missing_inputs(draft, validation_input)
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
                "phase": "queued",
                "verdict": "pending",
                "progress": {"state": "queued"},
                "expectations": validated_expectations,
                "sample_source": "auto_discovered" if discovery_used else "user",
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

    def _fail_runtime_review(
        self,
        draft: WorkflowDraftRecord,
        *,
        code: str,
        message: str,
        error_type: str,
    ) -> None:
        issue = {
            "code": code,
            "severity": "error",
            "node_id": None,
            "port": None,
            "message": {
                "zh": "Agent Runtime 复核不可用或返回格式无效，真机验证已停止。",
                "en": message,
            },
        }
        draft.status = "needs_review"
        draft.validation_run_id = None
        draft.validation.update(
            {
                "valid": True,
                "live_status": "blocked",
                "phase": "completed",
                "verdict": "blocked",
                "issues": [issue],
                "runtime_review": {
                    "raw_verdict": "block",
                    "verdict": "block",
                    "issues": [issue],
                    "dismissed_issues": [],
                    "review_policy_version": WORKFLOW_REVIEW_POLICY_VERSION,
                    "summary": {
                        "zh": "Agent Runtime 复核失败，未启动 SAP 查询。",
                        "en": message,
                    },
                    "error_type": error_type,
                },
                "workflow_hash": workflow_digest(draft.workflow),
            }
        )
        draft.validation["preflight_review"] = deepcopy_json(
            draft.validation["runtime_review"]
        )
        draft.validation["review_policy_version"] = WORKFLOW_REVIEW_POLICY_VERSION
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft)
        raise WorkflowDraftError(message, code=code, detail={"review": draft.validation["runtime_review"]})

    def _schedule_composition(
        self, draft_id: str, *, clarification_input: str | None = None
    ) -> None:
        task = asyncio.create_task(
            self._compose_draft(draft_id, clarification_input=clarification_input),
            name=f"workflow-composition-{draft_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _compose_draft(
        self, draft_id: str, *, clarification_input: str | None = None
    ) -> None:
        draft = self.store.get_workflow_draft(draft_id)
        requirement = str(draft.composition.get("requirement") or "").strip()
        locale = str(draft.composition.get("locale") or "zh")
        try:
            compose = getattr(self.author, "compose_workflow", None)
            supports = getattr(self.author, "supports", None)
            if not callable(compose) or (callable(supports) and not supports("compose_workflow")):
                raise WorkflowDraftError(
                    "The selected Agent Runtime does not support workflow composition.",
                    code="workflow_composition_unavailable",
                )
            catalog = compact_agent_catalog(self.agents)
            provider_id = str(
                draft.composition.get("runtime_provider_id")
                or getattr(self.author, "current_provider_id", "codex")
            )
            draft.composition["runtime_provider_id"] = provider_id
            pin = getattr(self.author, "pin", None)
            context = pin(provider_id) if callable(pin) else nullcontext()
            with context:
                result = await compose(
                    requirement=requirement,
                    catalog=catalog,
                    locale=locale,
                    thread_id=draft.thread_id,
                    clarification_input=clarification_input,
                    previous=draft.composition,
                )
            draft = self.store.get_workflow_draft(draft_id)
            draft.thread_id = str(result.get("thread_id") or draft.thread_id or "") or None
            if result.get("needs_clarification"):
                question = str(result.get("clarification_question") or "").strip()
                if not question:
                    raise WorkflowCompositionError(
                        "The Agent Runtime requested clarification without returning a question."
                    )
                draft.status = "waiting_input"
                draft.composition.update(
                    {
                        "catalog_digest": catalog["digest"],
                        "clarification_question": question,
                        "error": None,
                    }
                )
                draft.updated_at = utc_now()
                self.store.save_workflow_draft(draft)
                return
            proposal_snapshot = deepcopy_json(result.get("proposal") or {})
            draft.composition["proposal_snapshot"] = proposal_snapshot
            draft.updated_at = utc_now()
            self.store.save_workflow_draft(draft)
            self._apply_compiled_proposal(
                draft,
                proposal=proposal_snapshot,
                catalog=catalog,
                provider_id=provider_id,
            )
        except Exception as exc:
            draft = self.store.get_workflow_draft(draft_id)
            draft.status = "needs_review"
            draft.composition["error"] = {
                "code": getattr(exc, "code", "workflow_composition_failed"),
                "message": str(exc),
                "type": type(exc).__name__,
                "detail": deepcopy_json(getattr(exc, "detail", None)),
            }
            draft.composition["reconciling"] = False
            draft.updated_at = utc_now()
            self._write_draft(draft)
            self.store.save_workflow_draft(draft)

    def _apply_compiled_proposal(
        self,
        draft: WorkflowDraftRecord,
        *,
        proposal: dict[str, Any],
        catalog: dict[str, Any],
        provider_id: str,
    ) -> WorkflowDraftRecord:
        workflow, composition = compile_workflow_proposal(
            workflow_id=str(draft.workflow["id"]),
            requirement=str(draft.composition.get("requirement") or "").strip(),
            locale=str(draft.composition.get("locale") or "zh"),
            proposal=proposal,
            catalog=catalog,
            agents=self.agents,
        )
        old_gaps = {
            str(item.get("gap_id") or ""): item
            for item in draft.composition.get("gaps") or []
        }
        for gap in composition.get("gaps") or []:
            previous_gap = old_gaps.get(str(gap.get("gap_id") or "")) or {}
            if previous_gap.get("agent_draft_id"):
                gap["agent_draft_id"] = previous_gap["agent_draft_id"]
                gap["status"] = previous_gap.get("status") or "agent_draft_created"
        composition["clarification_history"] = list(
            draft.composition.get("clarification_history") or []
        )
        composition["runtime_provider_id"] = provider_id
        composition["reconciling"] = False
        diff = _json_diff(draft.workflow, workflow)
        draft.workflow = workflow
        if diff:
            draft.revision += 1
        draft.composition = composition
        draft.status = "needs_agents" if composition.get("gaps") else "draft"
        draft.validation_run_id = None
        draft.validation = {
            "valid": False,
            "issues": (
                ["Missing Agents must be created and accepted before validation."]
                if composition.get("gaps")
                else ["Generated workflow has not been validated."]
            ),
        }
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=diff if diff else None)
        return draft

    def _ensure_current_compiler(self, draft: WorkflowDraftRecord) -> WorkflowDraftRecord:
        if draft.status == "published":
            return draft
        stages = draft.composition.get("stages")
        requirement = str(draft.composition.get("requirement") or "").strip()
        if not isinstance(stages, list) or not stages or not requirement:
            return draft
        catalog = compact_agent_catalog(self.agents)
        if (
            int(draft.composition.get("compiler_version") or 0) >= WORKFLOW_COMPILER_VERSION
            and draft.composition.get("catalog_digest") == catalog["digest"]
        ):
            return draft
        proposal = {
            "intent": deepcopy_json(draft.composition.get("intent") or {}),
            "title": deepcopy_json(draft.workflow.get("title") or {}),
            "description": deepcopy_json(draft.workflow.get("description") or {}),
            "validation_defaults": deepcopy_json(
                draft.composition.get("validation_defaults") or {}
            ),
            "stages": deepcopy_json(stages),
        }
        try:
            workflow, composition = compile_workflow_proposal(
                workflow_id=str(draft.workflow["id"]),
                requirement=requirement,
                locale=str(draft.composition.get("locale") or "zh"),
                proposal=proposal,
                catalog=catalog,
                agents=self.agents,
            )
        except WorkflowCompositionError as exc:
            raise WorkflowDraftError(
                str(exc),
                code=getattr(exc, "code", "workflow_recompile_failed"),
                detail=getattr(exc, "detail", None),
            ) from exc
        previous_gaps = {
            str(item.get("gap_id") or ""): item
            for item in draft.composition.get("gaps") or []
            if isinstance(item, dict)
        }
        for gap in composition.get("gaps") or []:
            prior = previous_gaps.get(str(gap.get("gap_id") or "")) or {}
            if prior.get("agent_draft_id"):
                gap["agent_draft_id"] = prior["agent_draft_id"]
                gap["status"] = prior.get("status") or "agent_draft_created"
        composition["clarification_history"] = deepcopy_json(
            draft.composition.get("clarification_history") or []
        )
        composition["runtime_provider_id"] = str(
            draft.composition.get("runtime_provider_id") or "codex"
        )
        composition["reconciling"] = False
        diff = _json_diff(draft.workflow, workflow)
        draft.workflow = workflow
        draft.composition = composition
        if diff:
            draft.revision += 1
        draft.status = "needs_agents" if composition.get("gaps") else "draft"
        draft.validation_run_id = None
        draft.validation = {
            "valid": False,
            "issues": [
                f"Workflow was recompiled with compiler version {WORKFLOW_COMPILER_VERSION} and must be validated."
            ],
        }
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=diff if diff else None)
        return draft

    def validation_report(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_workflow_draft(draft_id)
        run_id = str(draft.validation_run_id or "")
        if not run_id:
            raise WorkflowDraftError(
                "This workflow draft has no live-validation run.",
                code="workflow_validation_report_unavailable",
            )
        existing = draft.validation.get("validation_report")
        if (
            isinstance(existing, dict)
            and existing.get("run_id") == run_id
            and existing.get("workflow_hash") == workflow_digest(draft.workflow)
        ):
            return existing
        record = self.store.get_run(run_id)
        if record.status not in TERMINAL_STATUSES:
            return {
                "schema_version": 1,
                "run_id": run_id,
                "workflow_revision": draft.revision,
                "workflow_hash": workflow_digest(draft.workflow),
                "phase": "running",
                "verdict": "pending",
                "preflight_review": draft.validation.get("preflight_review")
                or draft.validation.get("runtime_review")
                or {},
                "progress": record.progress.model_dump(mode="json"),
            }
        return self._finalize_validation_report(draft, record)

    def validation_artifact(self, draft_id: str, name: str) -> tuple[Path, str]:
        if name not in {
            "workflow-validation-report.json",
            "workflow-validation-report.md",
        }:
            raise KeyError(name)
        report = self.validation_report(draft_id)
        if report.get("phase") != "completed":
            raise WorkflowDraftError(
                "The live-validation report is not complete.",
                code="workflow_validation_report_pending",
            )
        draft = self.store.get_workflow_draft(draft_id)
        root = (Path(draft.path) / "validation" / str(report["run_id"])).resolve()
        expected_root = (Path(draft.path) / "validation").resolve()
        path = (root / name).resolve()
        if expected_root not in path.parents or not path.is_file():
            raise KeyError(name)
        media_type = "application/json" if name.endswith(".json") else "text/markdown"
        return path, media_type

    def publish(
        self,
        draft_id: str,
        *,
        acknowledge_inconclusive: bool,
        validation_run_id: str | None,
        validation_report_digest: str | None,
        accepted_gap_codes: list[str],
    ) -> WorkflowDraftRecord:
        draft = self.store.get_workflow_draft(draft_id)
        if draft.status not in {"validated", "inconclusive"}:
            raise WorkflowDraftError("Only a live-validated workflow can be published.")
        if draft.validation.get("workflow_hash") != workflow_digest(draft.workflow):
            raise WorkflowDraftError("Workflow changed after validation.", code="workflow_changed_after_validation")
        report = self.validation_report(draft_id)
        verdict = str(report.get("verdict") or "")
        if verdict not in {"pass", "inconclusive"}:
            raise WorkflowDraftError(
                "Only a passed or explicitly acknowledged inconclusive validation can be published.",
                code="workflow_validation_not_publishable",
            )
        gap_codes = sorted(
            {
                str(item.get("code") or "")
                for item in report.get("evidence_gaps") or []
                if isinstance(item, dict) and str(item.get("code") or "")
            }
        )
        acknowledgement: dict[str, Any] | None = None
        if verdict == "inconclusive":
            supplied_gap_codes = sorted(set(str(item) for item in accepted_gap_codes if str(item)))
            if (
                not acknowledge_inconclusive
                or validation_run_id != draft.validation_run_id
                or validation_report_digest != report.get("report_digest")
                or supplied_gap_codes != gap_codes
            ):
                raise WorkflowDraftError(
                    "Publishing an inconclusive workflow requires acknowledgement of the current validation report and every current evidence gap.",
                    code="inconclusive_acknowledgement_required",
                    detail={
                        "validation_run_id": draft.validation_run_id,
                        "validation_report_digest": report.get("report_digest"),
                        "required_gap_codes": gap_codes,
                    },
                )
            acknowledgement = {
                "validation_run_id": draft.validation_run_id,
                "validation_report_digest": report.get("report_digest"),
                "accepted_gap_codes": gap_codes,
                "acknowledged_at": utc_now(),
            }
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
        version_origin = draft.composition.get("version_origin")
        version_origin = version_origin if isinstance(version_origin, dict) else None
        workflow = json.loads(json.dumps(draft.workflow))
        workflow["version"] = (
            str(version_origin.get("target_version"))
            if version_origin
            else _published_version(str(workflow.get("version") or "0.1.0"))
        )
        workflow["status"] = "Published"
        workflow["validation"] = {
            "run_id": draft.validation_run_id,
            "workflow_hash": workflow_digest(draft.workflow),
            "status": verdict,
            "report_digest": report.get("report_digest"),
            "evidence_gap_codes": gap_codes,
            "acknowledgement": acknowledgement,
            "validated_at": draft.validation.get("completed_at"),
        }
        branch_version = re.sub(r"[^0-9A-Za-z._-]", "-", workflow["version"])
        branch = f"codex/workflow-{workflow['id']}-v{branch_version}"
        target = self.settings.repository_root / "workflows" / "Common" / str(workflow["id"])
        current_workflow: dict[str, Any] | None = None
        current_lifecycle: dict[str, Any] | None = None
        if target.exists() and not version_origin:
            raise WorkflowDraftError(f"Workflow target already exists: {target}")
        if version_origin:
            if not target.is_dir():
                raise WorkflowDraftError(
                    "The source published workflow no longer exists.",
                    code="workflow_version_source_missing",
                )
            source_path = target / "workflow.json"
            try:
                current_workflow = json.loads(source_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WorkflowDraftError(
                    f"Cannot load the current published workflow: {exc}",
                    code="workflow_version_source_invalid",
                ) from exc
            actual_version = str(current_workflow.get("version") or "")
            actual_hash = workflow_digest(current_workflow)
            if (
                str(version_origin.get("workflow_id") or "") != str(workflow["id"])
                or str(version_origin.get("source_version") or "") != actual_version
                or str(version_origin.get("source_hash") or "") != actual_hash
            ):
                raise WorkflowDraftError(
                    "The published workflow changed after this version draft was created.",
                    code="workflow_version_source_changed",
                    detail={
                        "actual_version": actual_version,
                        "actual_workflow_hash": actual_hash,
                    },
                )
            if workflow["version"] == actual_version:
                raise WorkflowDraftError(
                    "The new workflow version must differ from the current version.",
                    code="workflow_version_not_incremented",
                )
            archive = target / "versions" / actual_version
            if archive.exists():
                raise WorkflowDraftError(
                    f"Archived workflow version already exists: {actual_version}",
                    code="workflow_version_archive_exists",
                )
            publication_path = target / "publication.json"
            if publication_path.is_file():
                try:
                    current_lifecycle = json.loads(publication_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise WorkflowDraftError(
                        f"Cannot load workflow publication metadata: {exc}",
                        code="workflow_publication_invalid",
                    ) from exc
        subprocess.run(
            ["git", "switch", "-c", branch],
            cwd=self.settings.repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        lifecycle_state = str((current_lifecycle or {}).get("state") or "active")
        publication = {
            "schemaVersion": 1,
            "workflowId": str(workflow["id"]),
            "state": lifecycle_state,
            "currentVersion": str(workflow["version"]),
            "currentWorkflowHash": workflow_digest(workflow),
            "publishedAt": utc_now(),
            "deactivatedAt": (current_lifecycle or {}).get("deactivatedAt")
            if lifecycle_state == "inactive"
            else None,
            "deactivationReason": (current_lifecycle or {}).get("deactivationReason")
            if lifecycle_state == "inactive"
            else None,
            "updatedAt": utc_now(),
        }
        archive: Path | None = None
        try:
            if current_workflow is None:
                target.mkdir(parents=True)
            else:
                archive = target / "versions" / str(current_workflow["version"])
                archive.mkdir(parents=True)
                for name in ("workflow.json", "validation.json", "README.md"):
                    source = target / name
                    if source.is_file():
                        shutil.copy2(source, archive / name)
            _atomic_write_text(
                target / "workflow.json",
                json.dumps(workflow, ensure_ascii=False, indent=2) + "\n",
            )
            _atomic_write_text(target / "README.md", _workflow_readme(workflow))
            _atomic_write_text(
                target / "validation.json",
                json.dumps(workflow["validation"], ensure_ascii=False, indent=2) + "\n",
            )
            _atomic_write_text(
                target / "publication.json",
                json.dumps(publication, ensure_ascii=False, indent=2) + "\n",
            )
        except Exception:
            if archive and archive.is_dir():
                for name in ("workflow.json", "validation.json", "README.md"):
                    archived = archive / name
                    if archived.is_file():
                        shutil.copy2(archived, target / name)
                shutil.rmtree(archive)
                publication_path = target / "publication.json"
                if current_lifecycle is not None:
                    _atomic_write_text(
                        publication_path,
                        json.dumps(current_lifecycle, ensure_ascii=False, indent=2) + "\n",
                    )
                elif publication_path.exists():
                    publication_path.unlink()
            elif current_workflow is None and target.exists():
                shutil.rmtree(target)
            raise
        publish_diff = _json_diff(draft.workflow, workflow)
        published_from_revision = draft.revision
        draft.workflow = workflow
        draft.revision += 1
        draft.status = "published"
        draft.validation.update(
            {
                "branch": branch,
                "target": str(target),
                "acknowledged_inconclusive": acknowledgement is not None,
                "acknowledgement": acknowledgement,
                "published_from_revision": published_from_revision,
                "published_workflow_hash": workflow_digest(workflow),
                "published_version": str(workflow["version"]),
                "publication_state": lifecycle_state,
            }
        )
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft, diff=publish_diff)
        if version_origin:
            self.store.append_workflow_management_event(
                event_id=f"workflow_event_{uuid.uuid4().hex[:16]}",
                workflow_id=str(workflow["id"]),
                action="version_published",
                from_version=str(version_origin.get("source_version") or ""),
                to_version=str(workflow["version"]),
                workflow_hash=workflow_digest(workflow),
                branch=branch,
                detail={"draft_id": draft.draft_id, "state": lifecycle_state},
            )
        return draft

    def _finalize_validation_report(
        self, draft: WorkflowDraftRecord, record: Any
    ) -> dict[str, Any]:
        report = _build_validation_report(
            draft=draft,
            record=record,
            store=self.store,
            expectations=list(draft.validation.get("expectations") or []),
            sample_source=str(draft.validation.get("sample_source") or "user"),
        )
        _write_validation_report_artifacts(draft, report)
        verdict = str(report["verdict"])
        draft.status = (
            "validated"
            if verdict == "pass"
            else "inconclusive"
            if verdict == "inconclusive"
            else "needs_review"
        )
        draft.validation.update(
            {
                "live_status": record.status.value,
                "phase": "completed",
                "verdict": verdict,
                "progress": record.progress.model_dump(mode="json"),
                "result_completeness": (
                    record.result.completeness.model_dump(mode="json")
                    if record.result
                    else {}
                ),
                "validation_report": report,
                "completed_at": record.completed_at,
            }
        )
        draft.updated_at = utc_now()
        self._write_draft(draft)
        self.store.save_workflow_draft(draft)
        return report

    async def _monitor_validation(
        self,
        draft_id: str,
        run_id: str,
        validation_input: dict[str, Any],
        *,
        repair_attempt: int,
    ) -> None:
        last_phase = "queued"
        while True:
            record = self.store.get_run(run_id)
            if record.status in TERMINAL_STATUSES:
                break
            if last_phase != "running" and record.status != RunStatus.queued:
                draft = self.store.get_workflow_draft(draft_id)
                if draft.validation_run_id != run_id:
                    return
                draft.validation.update(
                    {
                        "phase": "running",
                        "progress": record.progress.model_dump(mode="json"),
                    }
                )
                draft.updated_at = utc_now()
                self.store.save_workflow_draft(draft)
                last_phase = "running"
            await asyncio.sleep(0.25)
        draft = self.store.get_workflow_draft(draft_id)
        if draft.validation_run_id != run_id:
            return
        if record.status in {RunStatus.completed, RunStatus.inconclusive}:
            draft.validation["repair_attempts"] = repair_attempt
            self._finalize_validation_report(draft, record)
            return
        repair = getattr(self.author, "repair_workflow", None)
        if repair_attempt >= 2 or not callable(repair):
            draft.validation.update(
                {
                    "error": record.error,
                    "repair_attempts": repair_attempt,
                }
            )
            self._finalize_validation_report(draft, record)
            return
        try:
            provider_id = str(
                draft.composition.get("runtime_provider_id") or "codex"
            )
            pin = getattr(self.author, "pin", None)
            context = pin(provider_id) if callable(pin) else nullcontext()
            with context:
                proposal = await repair(
                    workflow=draft.workflow,
                    agent_contracts=self._agent_contracts(draft.workflow),
                    error=record.error or {},
                    thread_id=draft.thread_id,
                )
            connections = proposal.get("connections")
            if not isinstance(connections, list):
                raise WorkflowDraftError("Agent Runtime repair did not return a connection list.")
            before_nodes = draft.workflow.get("nodes")
            repaired = json.loads(json.dumps(draft.workflow))
            repaired["connections"] = connections
            if repaired.get("nodes") != before_nodes:
                raise WorkflowDraftError("Agent Runtime repair attempted to change workflow nodes.")
            repaired = normalize_workflow(repaired, self.agents)
            validate_workflow(repaired, self.agents, require_pins=True)
        except Exception as exc:
            draft.validation.update(
                {
                    "repair_error": type(exc).__name__,
                    "repair_attempts": repair_attempt,
                }
            )
            self._finalize_validation_report(draft, record)
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
        draft.validation.update(
            {
                "workflow_hash": workflow_digest(repaired),
                "phase": "queued",
                "verdict": "pending",
                "progress": {"state": "queued"},
                "validation_report": None,
            }
        )
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
        missing = [name for name in required if _validation_input_is_missing(resolved.get(name))]
        if not missing:
            return resolved
        if "as_of" in missing:
            resolved["as_of"] = date.today().isoformat()
            missing.remove("as_of")
        discovery_specs = {
            "purchase_order": {
                "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                "odata_version": "2.0",
                "entity_set": "A_PurchaseOrder",
                "field": "PurchaseOrder",
                "select_fields": ["PurchaseOrder", "CompanyCode", "Supplier"],
                "cardinality": "scalar",
            },
            "purchase_orders": {
                "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                "odata_version": "2.0",
                "entity_set": "A_PurchaseOrder",
                "field": "PurchaseOrder",
                "select_fields": ["PurchaseOrder", "CompanyCode", "Supplier"],
                "cardinality": "array",
            },
            "sales_order": {
                "service_name": "API_SALES_ORDER_SRV",
                "odata_version": "2.0",
                "entity_set": "A_SalesOrder",
                "field": "SalesOrder",
                "select_fields": ["SalesOrder", "SoldToParty"],
                "cardinality": "scalar",
            },
            "sales_orders": {
                "service_name": "API_SALES_ORDER_SRV",
                "odata_version": "2.0",
                "entity_set": "A_SalesOrder",
                "field": "SalesOrder",
                "select_fields": ["SalesOrder", "SoldToParty"],
                "cardinality": "array",
            },
        }
        for name in list(missing):
            spec = discovery_specs.get(name)
            if not spec:
                continue
            property_schema = (
                draft.workflow.get("inputSchema", {}).get("properties", {}).get(name, {})
            )
            minimum_items = max(1, int(property_schema.get("minItems") or 1))
            discovery_limit = max(5, minimum_items)
            discovery_limit = min(discovery_limit, 50)
            plan = {
                "service_name": spec["service_name"],
                "odata_version": spec["odata_version"],
                "entity_set": spec["entity_set"],
                "http_method": "GET",
                "plan_kind": "direct",
                "select_fields": spec["select_fields"],
                "order_by": [spec["field"]],
                "top": discovery_limit,
                "rationale": "Bounded read-only candidate discovery for workflow validation.",
            }
            validation = await self.sap_read.validate_plan(plan, "workflow validation candidate")
            if validation.get("ok") is not True:
                continue
            response = await self.sap_read.execute_plan(plan, "workflow validation candidate")
            rows = _extract_rows(response)
            candidates = list(
                dict.fromkeys(
                    candidate
                    for candidate in (
                        str(row.get(spec["field"]) or "").strip() for row in rows
                    )
                    if candidate
                )
            )
            if spec["cardinality"] == "array" and len(candidates) >= minimum_items:
                resolved[name] = candidates[:minimum_items]
                missing.remove(name)
            elif spec["cardinality"] == "scalar" and candidates:
                resolved[name] = candidates[0]
                missing.remove(name)
        if missing:
            raise WorkflowDraftError(
                "Automatic candidate discovery could not provide: " + ", ".join(missing),
                code="workflow_validation_input_unavailable",
                detail={
                    "missing_fields": missing,
                    "supported_fields": sorted(discovery_specs),
                },
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


def _validation_input_is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _validate_expectation_contracts(
    workflow: dict[str, Any], expectations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    properties = workflow.get("outputSchema", {}).get("properties", {})
    normalized: list[dict[str, Any]] = []
    for raw in expectations:
        item = deepcopy_json(raw)
        output = str(item.get("output") or "")
        operator = str(item.get("operator") or "")
        schema = properties.get(output)
        if not isinstance(schema, dict):
            raise WorkflowDraftError(
                f"Validation expectation references unknown workflow output: {output}",
                code="workflow_validation_expectation_invalid",
                detail={"output": output, "reason": "unknown_output"},
            )
        candidates: list[Any] = []
        if operator == "equals":
            candidates = [item.get("expected")]
        elif operator == "one_of":
            expected = item.get("expected")
            if not isinstance(expected, list) or not expected:
                raise WorkflowDraftError(
                    "one_of requires a non-empty expected array.",
                    code="workflow_validation_expectation_invalid",
                    detail={"output": output, "reason": "expected_array_required"},
                )
            candidates = list(expected)
        elif operator == "decimal_within":
            try:
                expected_decimal = Decimal(str(item.get("expected")))
                tolerance = Decimal(str(item.get("tolerance")))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise WorkflowDraftError(
                    "decimal_within requires decimal expected and tolerance values.",
                    code="workflow_validation_expectation_invalid",
                    detail={"output": output, "reason": "decimal_required"},
                ) from exc
            if tolerance < 0:
                raise WorkflowDraftError(
                    "decimal_within tolerance must not be negative.",
                    code="workflow_validation_expectation_invalid",
                    detail={"output": output, "reason": "negative_tolerance"},
                )
            item["expected"] = format(expected_decimal, "f")
            item["tolerance"] = format(tolerance, "f")
        elif operator not in {"exists", "non_empty"}:
            raise WorkflowDraftError(
                f"Unsupported validation expectation operator: {operator}",
                code="workflow_validation_expectation_invalid",
                detail={"output": output, "reason": "unsupported_operator"},
            )
        for candidate in candidates:
            errors = list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                    candidate
                )
            )
            if errors:
                raise WorkflowDraftError(
                    f"Expected value does not match the output schema for {output}.",
                    code="workflow_validation_expectation_invalid",
                    detail={"output": output, "reason": errors[0].message},
                )
        normalized.append(item)
    return normalized


def _build_validation_report(
    *,
    draft: WorkflowDraftRecord,
    record: Any,
    store: RunStore,
    expectations: list[dict[str, Any]],
    sample_source: str,
) -> dict[str, Any]:
    result = record.result
    workflow_output = dict(result.workflow_output) if result else {}
    workflow = draft.workflow
    output_schema = workflow.get("outputSchema", {})
    required_outputs = [str(item) for item in output_schema.get("required") or []]
    output_properties = output_schema.get("properties") or {}
    required_output_checks: list[dict[str, Any]] = []
    for name in required_outputs:
        present = name in workflow_output
        schema = output_properties.get(name) or {}
        issues = (
            list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(workflow_output.get(name))
            )
            if present
            else []
        )
        required_output_checks.append(
            {
                "output": name,
                "present": present,
                "schema_valid": present and not issues,
                "status": "pass" if present and not issues else "fail",
                "value_summary": _value_summary(workflow_output.get(name)) if present else "—",
                "issue": issues[0].message if issues else None,
            }
        )

    workflow_nodes = {
        str(item.get("id")): item for item in workflow.get("nodes") or [] if isinstance(item, dict)
    }
    node_results: list[dict[str, Any]] = []
    all_tool_calls: list[dict[str, Any]] = list(result.tool_calls) if result else []
    for node in list(result.node_results) if result else []:
        node_id = str(node.get("node_id") or "")
        child_run_id = str(node.get("run_id") or "") or None
        child = None
        if child_run_id:
            try:
                child = store.get_run(child_run_id)
            except KeyError:
                child = None
        child_calls = list(child.result.tool_calls) if child and child.result else []
        all_tool_calls.extend(child_calls)
        output = node.get("output") if isinstance(node.get("output"), dict) else {}
        completeness = (
            node.get("completeness") if isinstance(node.get("completeness"), dict) else {}
        )
        node_results.append(
            {
                "node_id": node_id,
                "agent_id": str(node.get("agent_id") or ""),
                "status": str(node.get("status") or "unknown"),
                "child_run_id": child_run_id,
                "duration_ms": _duration_ms(
                    child.started_at if child else None,
                    child.completed_at if child else None,
                ),
                "business_status": output.get("business_status"),
                "source_complete": bool(completeness.get("source_complete")),
                "evidence_complete": bool(completeness.get("business_complete")),
                "tool_call_count": len(child_calls),
                "reason": node.get("reason")
                or (node.get("error") or {}).get("message")
                or "",
                "conditional_skip_valid": (
                    str(node.get("status")) != "skipped"
                    or bool(workflow_nodes.get(node_id, {}).get("runIf"))
                    and bool(workflow_nodes.get(node_id, {}).get("onSkip"))
                ),
            }
        )

    runtime_review = draft.validation.get("preflight_review") or draft.validation.get(
        "runtime_review"
    ) or {}
    read_only_ok, read_only_detail = _read_only_audit(workflow, all_tool_calls)
    node_failures = [
        item
        for item in node_results
        if item["status"] in {"failed", "cancelled"} or not item["conditional_skip_valid"]
    ]
    node_inconclusive = [item for item in node_results if item["status"] == "inconclusive"]
    required_outputs_ok = all(item["status"] == "pass" for item in required_output_checks)
    result_completeness = (
        result.completeness.model_dump(mode="json")
        if result
        else {
            "source_complete": False,
            "business_complete": False,
            "reason": "No run result was produced.",
            "missing_evidence": ["workflow_run_result_missing"],
        }
    )
    falsely_promoted = any(
        (not item["source_complete"] or not item["evidence_complete"])
        for item in node_results
    ) and (
        bool(result_completeness.get("source_complete"))
        or bool(result_completeness.get("business_complete"))
    )
    completeness_outputs = {
        name: value
        for name, value in workflow_output.items()
        if name.endswith("source_complete")
        or name.endswith("evidence_complete")
        or name in {"source_complete", "evidence_complete", "business_complete"}
    }
    business_reports = {
        name: value
        for name, value in workflow_output.items()
        if name.endswith("report") and isinstance(value, dict)
    }
    automatic_checks = [
        _validation_check(
            "workflow_structure",
            "contract",
            "pass",
            "工作流结构、输入映射和固定Agent契约有效。",
            "Workflow structure, input mappings, and pinned Agent contracts are valid.",
        ),
        _validation_check(
            "runtime_preflight",
            "preflight",
            "pass" if runtime_review.get("verdict") == "pass" else "fail",
            "Agent Runtime设计预审已通过。"
            if runtime_review.get("verdict") == "pass"
            else "Agent Runtime设计预审未通过。",
            "Agent Runtime design preflight passed."
            if runtime_review.get("verdict") == "pass"
            else "Agent Runtime design preflight did not pass.",
        ),
        _validation_check(
            "read_only_audit",
            "safety",
            "pass" if read_only_ok else "fail",
            "所有已审计调用保持只读边界。" if read_only_ok else read_only_detail,
            "All audited calls stayed inside the read-only boundary."
            if read_only_ok
            else read_only_detail,
        ),
        _validation_check(
            "node_execution",
            "execution",
            "fail" if node_failures else "warning" if node_inconclusive else "pass",
            "节点执行符合条件分支契约。"
            if not node_failures and not node_inconclusive
            else "部分节点无法确认，但条件跳过没有被误报为成功。"
            if not node_failures
            else "至少一个节点执行失败或条件跳过契约无效。",
            "Node execution matches the conditional branch contract."
            if not node_failures and not node_inconclusive
            else "Some nodes are inconclusive, without treating conditional skips as success."
            if not node_failures
            else "At least one node failed or has an invalid conditional-skip contract.",
        ),
        _validation_check(
            "required_outputs",
            "contract",
            "pass" if required_outputs_ok else "fail",
            "全部必需终端输出均存在且符合Schema。"
            if required_outputs_ok
            else "至少一个必需终端输出缺失或不符合Schema。",
            "All required terminal outputs are present and schema-valid."
            if required_outputs_ok
            else "At least one required terminal output is missing or schema-invalid.",
        ),
        _validation_check(
            "completeness_propagation",
            "completeness",
            "fail"
            if falsely_promoted
            else "pass"
            if result_completeness.get("source_complete")
            and result_completeness.get("business_complete")
            and all(value is not False for value in completeness_outputs.values())
            else "warning",
            "完整性已正确传播。"
            if not falsely_promoted
            else "下游或整体结果错误提升了不完整的上游证据。",
            "Completeness is propagated without promotion."
            if not falsely_promoted
            else "A downstream or aggregate result incorrectly promoted incomplete upstream evidence.",
        ),
        _validation_check(
            "business_report",
            "presentation",
            "pass" if business_reports else "fail",
            "已生成结构化业务报告。" if business_reports else "缺少结构化业务报告。",
            "A structured business report was generated."
            if business_reports
            else "The structured business report is missing.",
        ),
    ]
    expectation_results = [
        _evaluate_expectation(item, workflow_output) for item in expectations
    ]
    failed_checks = [item for item in automatic_checks if item["status"] == "fail"]
    failed_expectations = [item for item in expectation_results if item["status"] == "fail"]
    has_incomplete_output = any(value is False for value in completeness_outputs.values())
    if (
        record.status in {RunStatus.failed, RunStatus.cancelled}
        or failed_checks
        or failed_expectations
    ):
        verdict = "fail"
    elif (
        record.status == RunStatus.inconclusive
        or not result_completeness.get("source_complete")
        or not result_completeness.get("business_complete")
        or has_incomplete_output
    ):
        verdict = "inconclusive"
    else:
        verdict = "pass"

    gap_codes = list(
        dict.fromkeys(
            str(item)
            for item in result_completeness.get("missing_evidence") or []
            if str(item)
        )
    )
    if verdict == "inconclusive" and not gap_codes:
        gap_codes.append("workflow_evidence_incomplete")
    evidence_gaps = [_evidence_gap(code) for code in gap_codes]
    status_outputs = {
        name: value
        for name, value in workflow_output.items()
        if name.endswith("status") and not isinstance(value, (dict, list))
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "phase": "completed",
        "run_id": record.run_id,
        "workflow_revision": draft.revision,
        "workflow_hash": workflow_digest(workflow),
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "verdict": verdict,
        "normalized_input": deepcopy_json(record.input),
        "sample_source": "auto_discovered"
        if sample_source == "auto_discovered"
        else "user",
        "preflight_review": deepcopy_json(runtime_review),
        "automatic_checks": automatic_checks,
        "user_expectations": expectation_results,
        "node_results": node_results,
        "required_output_checks": required_output_checks,
        "business_result": {
            "run_status": record.status.value,
            "summary": deepcopy_json(result.summary) if result else {},
            "status_outputs": deepcopy_json(status_outputs),
            "business_reports": deepcopy_json(business_reports),
        },
        "completeness": result_completeness,
        "evidence_gaps": evidence_gaps,
        "errors": deepcopy_json(result.errors) if result else [record.error or {}],
        "artifacts": [
            {
                "name": "workflow-validation-report.json",
                "media_type": "application/json",
            },
            {
                "name": "workflow-validation-report.md",
                "media_type": "text/markdown",
            },
        ],
    }
    digest_input = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report["report_digest"] = "sha256:" + hashlib.sha256(digest_input).hexdigest()
    return report


def _validation_check(
    check_id: str,
    category: str,
    status: str,
    zh: str,
    en: str,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "category": category,
        "status": status,
        "summary": {"zh": zh, "en": en},
    }


def _evaluate_expectation(
    expectation: dict[str, Any], workflow_output: dict[str, Any]
) -> dict[str, Any]:
    output = str(expectation["output"])
    operator = str(expectation["operator"])
    present = output in workflow_output
    actual = workflow_output.get(output)
    passed = False
    issue = ""
    if operator == "exists":
        passed = present
    elif operator == "non_empty":
        passed = present and not _validation_input_is_missing(actual)
    elif operator == "equals":
        passed = present and actual == expectation.get("expected")
    elif operator == "one_of":
        passed = present and actual in list(expectation.get("expected") or [])
    elif operator == "decimal_within":
        try:
            passed = present and abs(
                Decimal(str(actual)) - Decimal(str(expectation.get("expected")))
            ) <= Decimal(str(expectation.get("tolerance")))
        except (InvalidOperation, TypeError, ValueError):
            passed = False
            issue = "The actual output is not a decimal value."
    return {
        **deepcopy_json(expectation),
        "status": "pass" if passed else "fail",
        "actual": deepcopy_json(actual) if present else None,
        "present": present,
        "issue": issue or None,
    }


def _read_only_audit(
    workflow: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> tuple[bool, str]:
    if workflow.get("readOnly") is not True:
        return False, "Workflow readOnly is not true."
    for call in tool_calls:
        if call.get("read_only") is False:
            return False, "A tool call explicitly declared read_only=false."
        capability = str(call.get("capability") or "")
        if capability.startswith("sap_read"):
            methods = _find_http_methods(call)
            unsafe = sorted(method for method in methods if method != "GET")
            if unsafe:
                return False, "SAP read call used unsupported method(s): " + ", ".join(unsafe)
    return True, "GET-only SAP calls and approved read-only capabilities were used."


def _find_http_methods(value: Any) -> set[str]:
    methods: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"http_method", "httpMethod"} and isinstance(child, str):
                methods.add(child.upper())
            else:
                methods.update(_find_http_methods(child))
    elif isinstance(value, list):
        for child in value:
            methods.update(_find_http_methods(child))
    return methods


def _value_summary(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{len(value)} field(s)"
    if value is None:
        return "null"
    return str(value)


def _duration_ms(started_at: str | None, completed_at: str | None) -> int | None:
    if not started_at or not completed_at:
        return None
    try:
        return max(
            0,
            round(
                (
                    datetime.fromisoformat(completed_at)
                    - datetime.fromisoformat(started_at)
                ).total_seconds()
                * 1000
            ),
        )
    except ValueError:
        return None


def _evidence_gap(code: str) -> dict[str, Any]:
    known = {
        "bank_settlement_not_proven": {
            "missing": {
                "zh": "缺少银行实际扣款或银行对账证据。",
                "en": "Actual bank-debit or bank-reconciliation evidence is missing.",
            },
            "impact": {
                "zh": "不能确认款项已经从银行账户实际扣除。",
                "en": "The workflow cannot confirm that funds were actually debited by the bank.",
            },
        },
        "payment_run_and_bank_master_evidence": {
            "missing": {
                "zh": "付款运行和银行主数据证据不完整。",
                "en": "Payment-run and bank-master evidence is incomplete.",
            },
            "impact": {
                "zh": "不能完整确认付款准备度和付款路径。",
                "en": "Payment readiness and the payment route cannot be fully confirmed.",
            },
        },
        "no_ap_payment_scopes": {
            "missing": {
                "zh": "P2P未生成可供AP复核的证据分组。",
                "en": "P2P produced no evidence scope for AP review.",
            },
            "impact": {
                "zh": "AP付款准备阶段未执行。",
                "en": "The AP payment-readiness stage was not executed.",
            },
        },
    }
    detail = known.get(
        code,
        {
            "missing": {
                "zh": f"缺少或无法确认证据：{code}",
                "en": f"Evidence is missing or unconfirmed: {code}",
            },
            "impact": {
                "zh": "相关业务结论无法由当前证据完全确认。",
                "en": "The affected business conclusion is not fully supported by current evidence.",
            },
        },
    )
    return {
        "code": code,
        **detail,
        "display_behavior": {
            "zh": "固定工作流继续显示不确定状态，并保留false完整性标志。",
            "en": "The fixed workflow continues to show an inconclusive status and false completeness flags.",
        },
    }


def _write_validation_report_artifacts(
    draft: WorkflowDraftRecord, report: dict[str, Any]
) -> None:
    root = (Path(draft.path) / "validation" / str(report["run_id"])).resolve()
    expected_root = (Path(draft.path) / "validation").resolve()
    if expected_root not in root.parents:
        raise WorkflowDraftError("Validation artifact path escaped the draft root.")
    root.mkdir(parents=True, exist_ok=True)
    (root / "workflow-validation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "workflow-validation-report.md").write_text(
        _validation_report_markdown(report), encoding="utf-8"
    )


def _validation_report_markdown(report: dict[str, Any]) -> str:
    def text(value: Any, locale: str) -> str:
        return str(value.get(locale) or "") if isinstance(value, dict) else str(value or "")

    lines = [
        "# 工作流真机验证报告 / Workflow Live Validation Report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Verdict: `{report['verdict']}`",
        f"- Started: `{report.get('started_at') or '—'}`",
        f"- Completed: `{report.get('completed_at') or '—'}`",
        f"- Report digest: `{report['report_digest']}`",
        "",
        "## 自动检查 / Automatic checks",
        "",
        "| Check | Status | 说明 |",
        "|---|---|---|",
    ]
    for item in report.get("automatic_checks") or []:
        summary_zh = text(item.get("summary"), "zh").replace("|", "\\|")
        lines.append(
            f"| {item['id']} | {item['status']} | {summary_zh} |"
        )
    lines.extend(
        [
            "",
            "## 节点结果 / Node results",
            "",
            "| Node | Agent | Status | Child run | Business | Source complete | Evidence complete |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for item in report.get("node_results") or []:
        lines.append(
            "| {node_id} | {agent_id} | {status} | {child_run_id} | {business_status} | {source_complete} | {evidence_complete} |".format(
                **{key: "—" if value is None else value for key, value in item.items()}
            )
        )
    lines.extend(["", "## 完整性缺口 / Evidence gaps", ""])
    gaps = report.get("evidence_gaps") or []
    if gaps:
        for item in gaps:
            lines.append(
                f"- `{item['code']}` — {text(item.get('missing'), 'zh')} {text(item.get('impact'), 'zh')}"
            )
    else:
        lines.append("- 无 / None")
    return "\n".join(lines) + "\n"


def deepcopy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _validated_runtime_review(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("verdict") not in {"pass", "block"}:
        raise WorkflowDraftError(
            "Agent Runtime review did not return a valid verdict.",
            code="workflow_runtime_review_unavailable",
        )
    raw_issues = value.get("issues")
    summary = value.get("summary")
    if not isinstance(raw_issues, list) or not isinstance(summary, dict):
        raise WorkflowDraftError(
            "Agent Runtime review did not return issues and a bilingual summary.",
            code="workflow_runtime_review_unavailable",
        )
    if not all(isinstance(summary.get(language), str) for language in ("zh", "en")):
        raise WorkflowDraftError(
            "Agent Runtime review summary is invalid.",
            code="workflow_runtime_review_unavailable",
        )
    issues: list[dict[str, Any]] = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            raise WorkflowDraftError(
                "Agent Runtime review issue is invalid.",
                code="workflow_runtime_review_unavailable",
            )
        message = raw.get("message")
        if (
            not str(raw.get("code") or "").strip()
            or raw.get("severity") not in {"error", "warning", "info"}
            or not isinstance(message, dict)
            or not all(str(message.get(language) or "").strip() for language in ("zh", "en"))
        ):
            raise WorkflowDraftError(
                "Agent Runtime review issue contract is invalid.",
                code="workflow_runtime_review_unavailable",
            )
        issues.append(
            {
                "code": str(raw["code"]),
                "severity": str(raw["severity"]),
                "node_id": str(raw["node_id"]) if raw.get("node_id") is not None else None,
                "port": str(raw["port"]) if raw.get("port") is not None else None,
                "message": {"zh": str(message["zh"]), "en": str(message["en"])},
            }
        )
    if value["verdict"] == "pass" and any(item["severity"] == "error" for item in issues):
        raise WorkflowDraftError(
            "Agent Runtime review returned pass with blocking issues.",
            code="workflow_runtime_review_unavailable",
        )
    if value["verdict"] == "block" and not issues:
        raise WorkflowDraftError(
            "Agent Runtime review returned block without an issue.",
            code="workflow_runtime_review_unavailable",
        )
    return {
        "verdict": str(value["verdict"]),
        "issues": issues,
        "summary": {"zh": str(summary["zh"]), "en": str(summary["en"])},
    }


def _reconcile_runtime_review(
    review: dict[str, Any],
    *,
    review_contract: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, Any]:
    """Apply deterministic platform contracts to model-supplied review issues."""

    required_by_node = {
        str(node_id): {str(port) for port in ports or []}
        for node_id, ports in (
            review_contract.get("required_on_skip_outputs_by_node") or {}
        ).items()
    }
    nodes = {
        str(node.get("id") or ""): node
        for node in workflow.get("nodes") or []
        if isinstance(node, dict)
    }
    effective_issues: list[dict[str, Any]] = []
    dismissed_issues: list[dict[str, Any]] = []
    for issue in review.get("issues") or []:
        if issue.get("code") != "workflow_conditional_skip_output_missing":
            effective_issues.append(issue)
            continue
        node_id = str(issue.get("node_id") or "")
        port = str(issue.get("port") or "")
        node = nodes.get(node_id) or {}
        on_skip = node.get("onSkip") if isinstance(node.get("onSkip"), dict) else {}
        skip_outputs = on_skip.get("outputs") if isinstance(on_skip, dict) else {}
        is_required = port in required_by_node.get(node_id, set())
        is_missing = not isinstance(skip_outputs, dict) or port not in skip_outputs
        if is_required and is_missing:
            effective_issues.append(issue)
            continue
        dismissed = deepcopy_json(issue)
        dismissed["dismissal_reason"] = {
            "zh": "该端口不是工作流必需终态输出，也未被下游消费，因此条件跳过时无需合成。",
            "en": "The workflow neither requires nor consumes this port, so the conditional skip path does not need to synthesize it.",
        }
        dismissed_issues.append(dismissed)

    raw_verdict = str(review.get("verdict") or "block")
    effective_verdict = raw_verdict
    if raw_verdict == "block" and not effective_issues:
        effective_verdict = "pass"
    summary = deepcopy_json(review.get("summary") or {})
    if effective_verdict == "pass" and raw_verdict == "block":
        count = len(dismissed_issues)
        summary = {
            "zh": f"设计预审通过。Agent Runtime提出的{count}项条件跳过要求不适用于当前工作流终态契约，平台已保留为审计记录。",
            "en": f"Design preflight passed. The platform retained {count} Agent Runtime conditional-skip finding(s) as audit records because they do not apply to this workflow's terminal contract.",
        }
    return {
        "raw_verdict": raw_verdict,
        "verdict": effective_verdict,
        "issues": effective_issues,
        "dismissed_issues": dismissed_issues,
        "review_policy_version": int(
            review_contract.get("review_policy_version")
            or WORKFLOW_REVIEW_POLICY_VERSION
        ),
        "summary": summary,
    }


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


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


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
