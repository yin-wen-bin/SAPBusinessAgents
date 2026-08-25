from __future__ import annotations

import asyncio
import copy
import csv
import json
import re
import time
import uuid
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import rules
from .codex_planner import Planner
from .config import Settings
from .database import RunStore
from .harness import CodexHarnessController
from .manifests import AgentRepository, ManifestError, is_agent_executable, validate_execution
from .models import (
    Completeness,
    HarnessLimits,
    HarnessLimitUsage,
    HarnessResult,
    LocalizedText,
    PlannerDecision,
    PresentationBlock,
    PresentationColumn,
    PresentationEntry,
    PresentationMetric,
    PresentationRow,
    RunCreate,
    RunMode,
    RunPresentation,
    RunResult,
    RunStatus,
    TERMINAL_STATUSES,
    utc_now,
)
from .normalization import (
    SapInputNormalizationError,
    SapValueNormalizer,
    discover_agent_input_references,
)
from .plugins import PluginError, SapReadCapability
from .relationships import RelationshipCatalog
from .sap_read import SapReadError
from .skills import SkillError, SkillRegistry
from .workflows import (
    WorkflowError,
    WorkflowRepository,
    apply_transform,
    iter_node_connections,
    topological_order,
    validate_value,
    validate_workflow,
    workflow_digest,
)


class RunExecutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "run_failed", detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class RunCoordinator:
    def __init__(
        self,
        settings: Settings,
        store: RunStore,
        agents: AgentRepository,
        sap_read: SapReadCapability,
        skills: SkillRegistry,
        planner: Planner,
        workflows: WorkflowRepository | None = None,
        harness: CodexHarnessController | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.sap_read = sap_read
        self.skills = skills
        self.planner = planner
        self.workflows = workflows
        self.harness = harness
        self.relationships = RelationshipCatalog.load(
            settings.repository_root / "config" / "business-relationships.json"
        )
        self.normalizer = SapValueNormalizer(
            settings.repository_root / "config" / "sap-value-normalization.json"
        )
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._acceptance_runs: set[str] = set()

    async def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker(), name="sapba-local-worker")
            if self.harness is not None:
                for record in self.store.list_recoverable_free_query_runs():
                    self.store.update_run(record.run_id, status=RunStatus.queued, error_json=None)
                    self.store.append_event(
                        record.run_id,
                        "harness_resumed",
                        {
                            "thread_id": record.thread_id,
                            "reason": "runtime_restart",
                            "sap_queries_replayed": False,
                        },
                    )
                    await self._queue.put(record.run_id)

    async def stop(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(None)
            await self._worker_task
            self._worker_task = None

    async def submit(self, request: RunCreate) -> str:
        defaulted_fields: list[str] = []
        workflow: dict[str, Any] | None = None
        if request.mode == RunMode.agent:
            try:
                agent = self.agents.get(str(request.agent_id))
            except (KeyError, PluginError) as exc:
                raise RunExecutionError("Agent not found.", code="agent_not_found") from exc
            if self.settings.enforce_agent_acceptance and not is_agent_executable(agent):
                raise RunExecutionError(
                    "The fixed Agent has not passed live three-stage acceptance.",
                    code="agent_live_validation_required",
                    detail={
                        "agent_id": request.agent_id,
                        "verdict": (agent.get("validation") or {}).get("verdict", "NOT_TESTED"),
                    },
                )
            try:
                effective_input, defaulted_fields = _resolve_server_defaults(
                    request.input,
                    agent["execution"]["inputSchema"],
                )
                effective_input = self.normalizer.normalize_input(
                    effective_input,
                    agent["execution"]["inputSchema"],
                    field_references=discover_agent_input_references(agent),
                )
                _validate_input(effective_input, agent["execution"]["inputSchema"])
            except (KeyError, SapInputNormalizationError, ValueError) as exc:
                error_code = (
                    exc.code
                    if isinstance(exc, SapInputNormalizationError)
                    and exc.code == "sap_input_normalization_conflict"
                    else "agent_validation_failed"
                )
                raise RunExecutionError(
                    str(exc),
                    code=error_code,
                    detail=getattr(exc, "detail", None),
                ) from exc
            request = request.model_copy(update={"input": effective_input})
        if request.mode == RunMode.workflow:
            if self.workflows is None:
                raise RunExecutionError("Workflow runtime is unavailable.", code="workflow_unavailable")
            workflow = self.workflows.get(str(request.workflow_id))
            try:
                normalized_input = self.normalizer.normalize_input(
                    request.input, workflow["inputSchema"]
                )
                _validate_input(normalized_input, workflow["inputSchema"])
            except (SapInputNormalizationError, ValueError) as exc:
                raise RunExecutionError(
                    str(exc),
                    code=str(getattr(exc, "code", "workflow_validation_failed")),
                    detail=getattr(exc, "detail", None),
                ) from exc
            request = request.model_copy(update={"input": normalized_input})
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        self.store.create_run(run_id, request)
        if defaulted_fields:
            self.store.append_event(
                run_id,
                "input_defaults_applied",
                {
                    "scope": "agent",
                    "agent_id": request.agent_id,
                    "fields": defaulted_fields,
                },
            )
        if workflow is not None:
            self.store.save_workflow_snapshot(run_id, workflow)
        self.store.append_event(run_id, "run_queued", {"mode": request.mode.value})
        await self._queue.put(run_id)
        return run_id

    async def submit_acceptance(self, request: RunCreate) -> str:
        """Run an unaccepted fixed Agent only from an in-process validation campaign."""

        if request.mode != RunMode.agent:
            raise RunExecutionError(
                "Acceptance submission only supports fixed Agents.",
                code="acceptance_mode_invalid",
            )
        try:
            agent = self.agents.get(str(request.agent_id))
            effective_input, defaulted_fields = _resolve_server_defaults(
                request.input,
                agent["execution"]["inputSchema"],
            )
            effective_input = self.normalizer.normalize_input(
                effective_input,
                agent["execution"]["inputSchema"],
                field_references=discover_agent_input_references(agent),
            )
            _validate_input(effective_input, agent["execution"]["inputSchema"])
        except (KeyError, ManifestError, PluginError, SapInputNormalizationError, ValueError) as exc:
            error_code = (
                exc.code
                if isinstance(exc, SapInputNormalizationError)
                and exc.code == "sap_input_normalization_conflict"
                else "agent_validation_failed"
            )
            raise RunExecutionError(
                str(exc),
                code=error_code,
                detail=getattr(exc, "detail", None),
            ) from exc
        request = request.model_copy(update={"input": effective_input})
        run_id = f"acceptance_{uuid.uuid4().hex[:16]}"
        self._acceptance_runs.add(run_id)
        self.store.create_run(run_id, request)
        if defaulted_fields:
            self.store.append_event(
                run_id,
                "input_defaults_applied",
                {
                    "scope": "acceptance_agent",
                    "agent_id": request.agent_id,
                    "fields": defaulted_fields,
                },
            )
        self.store.append_event(
            run_id,
            "run_queued",
            {"mode": request.mode.value, "acceptance_campaign": True},
        )
        await self._queue.put(run_id)
        return run_id

    async def submit_workflow_snapshot(
        self,
        workflow: dict[str, Any],
        input_value: dict[str, Any],
        *,
        draft_id: str | None = None,
        revision: int | None = None,
    ) -> str:
        if self.workflows is None:
            raise RunExecutionError("Workflow runtime is unavailable.", code="workflow_unavailable")
        validate_workflow(workflow, self.agents, source=f"workflow:{workflow.get('id')}")
        try:
            input_value = self.normalizer.normalize_input(
                input_value, workflow["inputSchema"]
            )
            _validate_input(input_value, workflow["inputSchema"])
        except (SapInputNormalizationError, ValueError) as exc:
            raise RunExecutionError(
                str(exc),
                code=str(getattr(exc, "code", "workflow_validation_failed")),
                detail=getattr(exc, "detail", None),
            ) from exc
        request = RunCreate(
            mode=RunMode.workflow,
            workflowId=str(workflow.get("id")),
            input=input_value,
        )
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        self.store.create_run(run_id, request)
        self.store.save_workflow_snapshot(
            run_id, workflow, draft_id=draft_id, revision=revision
        )
        self.store.append_event(
            run_id,
            "run_queued",
            {"mode": request.mode.value, "draft_id": draft_id, "revision": revision},
        )
        await self._queue.put(run_id)
        return run_id

    async def provide_input(self, run_id: str, value: str) -> str:
        value = self.normalizer.strip_text(value)
        if not value:
            raise RunExecutionError(
                "Supplemental input must not be blank.", code="input_blank"
            )
        record = self.store.get_run(run_id)
        if (
            record.mode == RunMode.free_query
            and self.harness is not None
            and record.status not in TERMINAL_STATUSES
            and await self.harness.steer(run_id, value)
        ):
            return "steer"
        if record.status != RunStatus.waiting_input:
            raise RunExecutionError("This run is not waiting for input.", code="run_not_waiting_input")
        query = f"{record.query or ''}\nAdditional user information / 用户补充：{value}".strip()
        self.store.update_run(run_id, query=query, status=RunStatus.queued, error_json=None)
        self.store.append_event(
            run_id, "input_received", {"input": value, "mode": "clarification"}
        )
        await self._queue.put(run_id)
        return "clarification"

    async def cancel(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        if record.status in {RunStatus.completed, RunStatus.inconclusive, RunStatus.failed, RunStatus.cancelled}:
            return
        self.store.update_run(run_id, cancel_requested=True)
        self.store.append_event(run_id, "cancellation_requested", {})
        if record.mode == RunMode.free_query and self.harness is not None:
            await self.harness.interrupt(run_id)

    async def _worker(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                if run_id is None:
                    return
                await asyncio.wait_for(self._execute(run_id), timeout=self.settings.max_run_seconds)
            except asyncio.CancelledError:
                if run_id is not None and self.store.get_run(run_id).status == RunStatus.cancelled:
                    continue
                raise
            except TimeoutError:
                if run_id is not None:
                    self._finish_error(
                        run_id,
                        RunExecutionError("Run exceeded the local timeout.", code="run_timeout"),
                        RunStatus.inconclusive,
                    )
            except Exception as exc:  # keep the single local worker alive
                if run_id is not None:
                    self._finish_error(run_id, exc, RunStatus.failed)
            finally:
                if run_id is not None:
                    self._acceptance_runs.discard(run_id)
                self._queue.task_done()

    async def _execute(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        if record.cancel_requested:
            self._finish_cancelled(run_id)
            return
        started = record.started_at or utc_now()
        self.store.update_run(run_id, started_at=started)
        self.store.append_event(run_id, "run_started", {"mode": record.mode.value})
        self._set_progress(run_id, phase="preparing", state="active")
        if record.mode == RunMode.agent:
            await self._execute_agent(run_id)
        elif record.mode == RunMode.free_query:
            await self._execute_free_query(run_id)
        else:
            await self._execute_workflow(run_id)

    async def _execute_agent(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        self.store.update_run(run_id, status=RunStatus.validating)
        self.store.append_event(run_id, "validation_started", {"agent_id": record.agent_id})
        try:
            agent = self.agents.get(str(record.agent_id))
            validate_execution(agent, f"agent:{record.agent_id}")
            if (
                self.settings.enforce_agent_acceptance
                and run_id not in self._acceptance_runs
                and not is_agent_executable(agent)
            ):
                raise RunExecutionError(
                    "The fixed Agent has not passed live three-stage acceptance.",
                    code="agent_live_validation_required",
                    detail={
                        "agent_id": record.agent_id,
                        "verdict": (agent.get("validation") or {}).get("verdict", "NOT_TESTED"),
                    },
                )
            _validate_input(record.input, agent["execution"]["inputSchema"])
        except (KeyError, ManifestError, PluginError, ValueError) as exc:
            raise RunExecutionError(str(exc), code="agent_validation_failed") from exc

        execution = agent["execution"]
        total_steps = len(execution["steps"])
        self._set_progress(
            run_id,
            phase="preparing",
            state="active",
            completed_units=0,
            total_units=total_steps,
            determinate=True,
        )
        context: dict[str, Any] = {"input": record.input, "steps": {}}
        result = RunResult(
            run_id=run_id,
            mode=RunMode.agent,
            agent_id=record.agent_id,
            input=record.input,
            plan={"mode": "deterministic", "steps": execution["steps"]},
            started_at=record.started_at,
        )
        self.store.update_run(run_id, status=RunStatus.running, plan_json=result.plan)

        sap_activity_started = False
        for index, step in enumerate(execution["steps"]):
            self._ensure_not_cancelled(run_id)
            step_id = step["id"]
            if not _when_matches(step.get("when"), context):
                timestamp = utc_now()
                skipped = {
                    "ok": True,
                    "status": "skipped",
                    "reason": "condition_false",
                    "source_complete": True,
                    "required": False,
                }
                context["steps"][step_id] = {"output": skipped}
                result.steps.append(
                    {
                        "step_id": step_id,
                        "executor": step["executor"],
                        "operation": step.get("operation"),
                        "status": "skipped",
                        "reason": "condition_false",
                        "started_at": timestamp,
                        "completed_at": timestamp,
                    }
                )
                self.store.append_event(
                    run_id,
                    "step_skipped",
                    {"step_id": step_id, "reason": "condition_false", "index": index},
                )
                self._set_progress(
                    run_id,
                    phase="validating_evidence" if sap_activity_started else "preparing",
                    state="active",
                    current_step_id=step_id,
                    completed_units=index + 1,
                    total_units=total_steps,
                    determinate=True,
                )
                self.store.update_run(run_id, result_json=result)
                continue
            executor = str(step.get("executor") or "")
            phase = (
                "reading_sap"
                if executor in {"sap_read", "skill"}
                else "validating_evidence"
                if sap_activity_started
                else "preparing"
            )
            if executor in {"sap_read", "skill"}:
                sap_activity_started = True
            self._set_progress(
                run_id,
                phase=phase,
                state="active",
                current_step_id=step_id,
                current_tool="sap_read" if executor == "sap_read" else executor or None,
                completed_units=index,
                total_units=total_steps,
                determinate=True,
            )
            self.store.append_event(
                run_id,
                "step_started",
                {"step_id": step_id, "executor": step["executor"], "index": index},
            )
            started_at = utc_now()
            started_monotonic = time.perf_counter()
            call_id = (
                f"call_{uuid.uuid4().hex[:16]}"
                if step["executor"] in {"sap_read", "skill"}
                else None
            )
            rendered = _render_template(step.get("request") or step.get("inputMapping") or {}, context)
            try:
                output = await self._execute_step(
                    run_id, step, rendered, record.query or "", call_id=call_id
                )
            except (
                SapReadError,
                SkillError,
                PluginError,
                RunExecutionError,
                ValueError,
            ) as exc:
                result.steps.append(
                    {
                        "step_id": step_id,
                        "executor": step["executor"],
                        "operation": step.get("operation"),
                        "status": "failed",
                        "started_at": started_at,
                        "completed_at": utc_now(),
                        "error": _error_payload(exc),
                    }
                )
                if step.get("failurePolicy", "fail_run") != "record_gap":
                    self.store.update_run(run_id, result_json=result)
                    raise
                gap = {
                    "ok": False,
                    "status": "capability_blocked",
                    "source_complete": False,
                    "source_truncated": False,
                    "step_id": step_id,
                    "error": _error_payload(exc),
                }
                output = _redact_sensitive(gap)
                context["steps"][step_id] = {"output": output}
                result.errors.append(gap["error"])
                result.evidence.append(
                    {
                        "step_id": step_id,
                        "source": step["executor"],
                        "operation": step.get("operation"),
                        "payload": output,
                    }
                )
                if step["executor"] in {"sap_read", "skill"}:
                    result.tool_calls.append(
                        {
                            **_plugin_trace(
                                self.skills
                                if step["executor"] == "skill"
                                else self.sap_read,
                                "skill_execute.v1"
                                if step["executor"] == "skill"
                                else "sap_read.v2",
                                "execute"
                                if step["executor"] == "skill"
                                else str(step.get("operation") or ""),
                            ),
                            "call_id": call_id,
                            "step_id": step_id,
                            "tool": "sap_read"
                            if step["executor"] in {"sap_read"}
                            else "skill",
                            "operation": step.get("operation"),
                            "status": "capability_blocked",
                            "duration_ms": round(
                                (time.perf_counter() - started_monotonic) * 1000, 3
                            ),
                            "error": gap["error"],
                        }
                    )
                self.store.append_event(
                    run_id,
                    "evidence_gap_recorded",
                    {"step_id": step_id, "error": gap["error"]},
                )
                self._set_progress(
                    run_id,
                    phase=phase,
                    state="active",
                    current_step_id=step_id,
                    completed_units=index + 1,
                    total_units=total_steps,
                    determinate=True,
                )
                self.store.update_run(run_id, result_json=result)
                continue
            output = _redact_sensitive(output)
            context["steps"][step_id] = {"output": output}
            public_output = _public_step_output(step, output)
            step_record = {
                "step_id": step_id,
                "executor": step["executor"],
                "operation": step.get("operation"),
                "status": "completed",
                "started_at": started_at,
                "completed_at": utc_now(),
                "output_reference": f"steps.{step_id}.output",
            }
            result.steps.append(step_record)
            if step["executor"] in {"sap_read", "skill"}:
                operation = "execute" if step["executor"] == "skill" else str(step.get("operation") or "")
                plan_trace = (
                    _sap_plan_trace_fields(
                        rendered.get("plan") if isinstance(rendered.get("plan"), dict) else rendered
                    )
                    if step["executor"] == "sap_read"
                    else {}
                )
                trace = _plugin_trace(
                    self.skills if step["executor"] == "skill" else self.sap_read,
                    "skill_execute.v1" if step["executor"] == "skill" else "sap_read.v2",
                    operation,
                )
                result.tool_calls.append(
                    {
                        **trace,
                        "call_id": call_id,
                        "step_id": step_id,
                        "tool": "sap_read" if step["executor"] in {"sap_read"} else step["executor"],
                        "operation": operation,
                        **plan_trace,
                        "status": "completed",
                        "duration_ms": round((time.perf_counter() - started_monotonic) * 1000, 3),
                    }
                )
                result.evidence.append(
                    {
                        **trace,
                        "call_id": call_id,
                        "step_id": step_id,
                        "source": "sap_read" if step["executor"] in {"sap_read"} else step["executor"],
                        "payload": public_output,
                    }
                )
                self.store.append_event(
                    run_id,
                    "evidence_received",
                    {"step_id": step_id, **trace, "call_id": call_id},
                )
            if step["executor"] == "rule":
                result.rule_results.append(output)
                rule_summary = output.get("summary") if isinstance(output, dict) else None
                if isinstance(rule_summary, dict):
                    result.summary = {
                        key: str(value)
                        for key, value in rule_summary.items()
                        if key in {"zh", "en"} and value is not None
                    }
                self.store.append_event(run_id, "rule_completed", {"step_id": step_id, "result": output})
            self.store.append_event(run_id, "tool_completed", {"step_id": step_id})
            self._set_progress(
                run_id,
                phase=phase,
                state="active",
                current_step_id=step_id,
                completed_units=index + 1,
                total_units=total_steps,
                determinate=True,
            )
            self.store.update_run(run_id, result_json=result)

        output_schema = execution.get("outputSchema")
        output_mapping = execution.get("outputMapping")
        if isinstance(output_schema, dict) and isinstance(output_mapping, dict):
            try:
                result.workflow_output = _render_template(output_mapping, context)
                validate_value(
                    result.workflow_output,
                    output_schema,
                    label=f"Agent {record.agent_id} workflow output",
                )
            except WorkflowError as exc:
                raise RunExecutionError(
                    str(exc), code=exc.code, detail=exc.detail
                ) from exc
        self._set_progress(
            run_id,
            phase="preparing_result",
            state="active",
            completed_units=total_steps,
            total_units=total_steps,
            determinate=True,
        )
        self._complete_result(run_id, result, output_schema=output_schema)
        self._acceptance_runs.discard(run_id)

    async def _execute_step(
        self,
        run_id: str,
        step: dict[str, Any],
        rendered: dict[str, Any],
        query: str,
        *,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        executor = step["executor"]
        operation = step.get("operation")
        if executor in {"sap_read"}:
            trace = _plugin_trace(self.sap_read, "sap_read.v2", str(operation or ""))
            candidate_plan = rendered.get("plan") if isinstance(rendered.get("plan"), dict) else rendered
            normalized_plan = self.normalizer.normalize_plan(candidate_plan)
            if isinstance(rendered.get("plan"), dict):
                rendered["plan"] = normalized_plan
            else:
                rendered.clear()
                rendered.update(normalized_plan)
            candidate_plan = normalized_plan
            self.store.append_event(
                run_id,
                "tool_started",
                {
                    "step_id": step["id"],
                    "tool": "sap_read",
                    "operation": operation,
                    "call_id": call_id,
                    **_sap_plan_trace_fields(candidate_plan),
                    **trace,
                },
            )
            if operation == "execute_plan":
                plan = rendered.get("plan") if "plan" in rendered else rendered
                relationship_failures = self.relationships.validate_plans(
                    [(str(step.get("id") or "deterministic_sap_plan"), plan)]
                )
                if relationship_failures:
                    raise RunExecutionError(
                        "The deterministic Agent plan uses an unapproved cross-entity business relationship.",
                        code="agent_relationship_rejected",
                        detail={"failures": relationship_failures},
                    )
                validation = await self.sap_read.validate_plan(plan, query)
                if validation.get("ok") is not True:
                    raise SapReadError(
                        "The selected SAP Provider rejected the deterministic Agent plan.",
                        code="sap_read_plan_rejected",
                        detail=validation,
                    )
                return await self.sap_read.execute_plan(plan, query)
            if operation == "execute_get":
                return await self.sap_read.execute_get(rendered)
        if executor == "skill":
            skill_id = str(step.get("skillId") or "")
            try:
                skill = self.skills.get(skill_id)
            except KeyError:
                skill = {}
            rendered = self.normalizer.normalize_input(
                rendered, skill.get("input_schema") or {"type": "object"}
            )
            trace = _plugin_trace(self.skills, "skill_execute.v1", "execute")
            self.store.append_event(
                run_id,
                "tool_started",
                {
                    "step_id": step["id"],
                    "tool": "skill",
                    "skill_id": skill_id,
                    "call_id": call_id,
                    **trace,
                },
            )
            return await self.skills.execute(skill_id, rendered)
        if executor == "rule":
            return rules.evaluate(str(operation), rendered)
        raise ValueError(f"Unsupported executor: {executor}")

    async def _execute_workflow(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        if self.workflows is None:
            raise RunExecutionError("Workflow runtime is unavailable.", code="workflow_unavailable")
        try:
            workflow = self.store.get_workflow_snapshot(run_id)
            validate_workflow(
                workflow,
                self.agents,
                source=f"workflow:{record.workflow_id}",
                require_pins=True,
            )
            _validate_input(record.input, workflow["inputSchema"])
        except (KeyError, WorkflowError, ValueError) as exc:
            code = getattr(exc, "code", "workflow_validation_failed")
            raise RunExecutionError(str(exc), code=code, detail=getattr(exc, "detail", None)) from exc

        self.store.update_run(run_id, status=RunStatus.running, plan_json=workflow)
        node_order = topological_order(workflow)
        total_nodes = len(node_order)
        self._set_progress(
            run_id,
            phase="preparing",
            state="active",
            completed_units=0,
            total_units=total_nodes,
            determinate=True,
        )
        self.store.append_event(
            run_id,
            "workflow_started",
            {
                "workflow_id": record.workflow_id,
                "revision": workflow_digest(workflow),
                "node_count": len(workflow.get("nodes") or []),
            },
        )
        result = RunResult(
            run_id=run_id,
            mode=RunMode.workflow,
            workflow_id=record.workflow_id,
            workflow_revision=workflow_digest(workflow),
            input=record.input,
            plan=workflow,
            started_at=record.started_at,
        )
        nodes = {str(item["id"]): item for item in workflow.get("nodes") or []}
        node_outputs: dict[str, dict[str, Any]] = {}
        degraded = False
        blocked_nodes: set[str] = set()

        for node_index, node_id in enumerate(node_order):
            self._ensure_not_cancelled(run_id)
            node = nodes[node_id]
            agent_id = str(node["agentId"])
            self._set_progress(
                run_id,
                phase="preparing",
                state="active",
                current_node_id=node_id,
                completed_units=node_index,
                total_units=total_nodes,
                determinate=True,
            )
            self.store.append_event(
                run_id,
                "node_started",
                {"node_id": node_id, "agent_id": agent_id},
            )
            try:
                node_input = _resolve_node_input(
                    workflow,
                    node_id,
                    record.input,
                    node_outputs,
                )
                agent = self.agents.get(agent_id)
                node_input, defaulted_fields = _resolve_server_defaults(
                    node_input,
                    agent["execution"]["inputSchema"],
                )
                node_input = self.normalizer.normalize_input(
                    node_input,
                    agent["execution"]["inputSchema"],
                    field_references=discover_agent_input_references(agent),
                )
                _validate_input(node_input, agent["execution"]["inputSchema"])
            except WorkflowError as exc:
                if exc.code == "workflow_output_unavailable":
                    degraded = True
                    blocked_nodes.add(node_id)
                    result.node_results.append(
                        {
                            "node_id": node_id,
                            "agent_id": agent_id,
                            "status": "skipped",
                            "reason": str(exc),
                            "error": {"code": exc.code, "message": str(exc)},
                        }
                    )
                    self.store.append_event(
                        run_id,
                        "node_skipped",
                        {"node_id": node_id, "agent_id": agent_id, "reason": str(exc)},
                    )
                    self._set_progress(
                        run_id,
                        phase="preparing",
                        state="active",
                        current_node_id=node_id,
                        completed_units=node_index + 1,
                        total_units=total_nodes,
                        determinate=True,
                    )
                    continue
                self.store.append_event(
                    run_id,
                    "mapping_failed",
                    {"node_id": node_id, "agent_id": agent_id, "error": str(exc)},
                )
                raise RunExecutionError(str(exc), code=exc.code, detail=exc.detail) from exc
            except (SapInputNormalizationError, ValueError) as exc:
                if str(exc).startswith("Missing required input:"):
                    degraded = True
                    blocked_nodes.add(node_id)
                    result.node_results.append(
                        {
                            "node_id": node_id,
                            "agent_id": agent_id,
                            "status": "skipped",
                            "reason": str(exc),
                            "error": {
                                "code": "workflow_output_unavailable",
                                "message": str(exc),
                            },
                        }
                    )
                    self.store.append_event(
                        run_id,
                        "node_skipped",
                        {"node_id": node_id, "agent_id": agent_id, "reason": str(exc)},
                    )
                    continue
                self.store.append_event(
                    run_id,
                    "mapping_failed",
                    {"node_id": node_id, "agent_id": agent_id, "error": str(exc)},
                )
                raise RunExecutionError(str(exc), code="mapping_failed") from exc
            except KeyError as exc:
                self.store.append_event(
                    run_id,
                    "mapping_failed",
                    {"node_id": node_id, "agent_id": agent_id, "error": str(exc)},
                )
                raise RunExecutionError(str(exc), code="mapping_failed") from exc

            if defaulted_fields:
                self.store.append_event(
                    run_id,
                    "input_defaults_applied",
                    {
                        "scope": "workflow_node",
                        "node_id": node_id,
                        "agent_id": agent_id,
                        "fields": defaulted_fields,
                    },
                )

            self.store.append_event(
                run_id,
                "node_input_resolved",
                {"node_id": node_id, "agent_id": agent_id, "fields": sorted(node_input)},
            )
            child_run_id = f"run_{uuid.uuid4().hex[:16]}"
            child_request = RunCreate(mode=RunMode.agent, agentId=agent_id, input=node_input)
            self.store.create_run(
                child_run_id,
                child_request,
                parent_run_id=run_id,
                node_id=node_id,
            )
            self.store.append_event(
                child_run_id,
                "run_queued",
                {"mode": RunMode.agent.value, "parent_run_id": run_id, "node_id": node_id},
            )
            try:
                await self._execute(child_run_id)
            except Exception as exc:
                self._finish_error(child_run_id, exc, RunStatus.failed)
            child = self.store.get_run(child_run_id)
            if child.status in {RunStatus.failed, RunStatus.cancelled} or child.result is None:
                error = child.error or {
                    "code": "workflow_node_failed",
                    "message": f"Agent node {node_id} did not return a result.",
                }
                result.node_results.append(
                    {
                        "node_id": node_id,
                        "agent_id": agent_id,
                        "run_id": child_run_id,
                        "status": child.status.value,
                        "input": node_input,
                        "error": error,
                    }
                )
                self.store.update_run(run_id, result_json=result)
                raise RunExecutionError(
                    str(error.get("message") or "Workflow node failed."),
                    code=str(error.get("code") or "workflow_node_failed"),
                    detail={"node_id": node_id, "child_run_id": child_run_id},
                )
            node_output = child.result.workflow_output
            try:
                agent = self.agents.get(agent_id)
                validate_value(
                    node_output,
                    agent["execution"]["outputSchema"],
                    label=f"Node {node_id} output",
                )
            except WorkflowError as exc:
                raise RunExecutionError(str(exc), code=exc.code, detail=exc.detail) from exc
            node_outputs[node_id] = node_output
            node_result = {
                "node_id": node_id,
                "agent_id": agent_id,
                "run_id": child_run_id,
                "status": child.status.value,
                "input": node_input,
                "output": node_output,
                "completeness": child.result.completeness.model_dump(mode="json"),
            }
            result.node_results.append(node_result)
            result.evidence.extend(
                [{**item, "node_id": node_id, "agent_id": agent_id} for item in child.result.evidence]
            )
            result.tool_calls.extend(
                [{**item, "node_id": node_id, "agent_id": agent_id} for item in child.result.tool_calls]
            )
            result.rule_results.extend(
                [{**item, "node_id": node_id, "agent_id": agent_id} for item in child.result.rule_results]
            )
            if child.status == RunStatus.inconclusive:
                degraded = True
                self.store.append_event(
                    run_id,
                    "node_inconclusive",
                    {"node_id": node_id, "agent_id": agent_id, "run_id": child_run_id},
                )
            else:
                self.store.append_event(
                    run_id,
                    "node_completed",
                    {"node_id": node_id, "agent_id": agent_id, "run_id": child_run_id},
                )
            self.store.update_run(run_id, result_json=result)
            self._set_progress(
                run_id,
                phase="validating_evidence",
                state="active",
                current_node_id=node_id,
                completed_units=node_index + 1,
                total_units=total_nodes,
                determinate=True,
            )

        try:
            result.workflow_output = _resolve_workflow_output(
                workflow, record.input, node_outputs
            )
            validate_value(
                result.workflow_output,
                workflow["outputSchema"],
                label=f"Workflow {record.workflow_id} output",
            )
        except WorkflowError as exc:
            if exc.code == "workflow_output_unavailable":
                degraded = True
                result.errors.append({"code": exc.code, "message": str(exc)})
            else:
                raise RunExecutionError(str(exc), code=exc.code, detail=exc.detail) from exc
        self._set_progress(
            run_id,
            phase="preparing_result",
            state="active",
            completed_units=total_nodes,
            total_units=total_nodes,
            determinate=True,
        )
        self._complete_workflow_result(run_id, result, degraded=degraded or bool(blocked_nodes))

    def _complete_workflow_result(
        self, run_id: str, result: RunResult, *, degraded: bool
    ) -> None:
        node_completeness = [
            item.get("completeness")
            for item in result.node_results
            if isinstance(item.get("completeness"), dict)
        ]
        source_complete = bool(node_completeness) and all(
            item.get("source_complete") is True for item in node_completeness
        )
        business_complete = bool(node_completeness) and all(
            item.get("business_complete") is True for item in node_completeness
        )
        missing = sorted(
            {
                str(gap)
                for item in node_completeness
                for gap in item.get("missing_evidence") or []
                if str(gap)
            }
        )
        if any(item.get("status") == "skipped" for item in result.node_results):
            missing.append("workflow_node_skipped")
        result.completeness = Completeness(
            source_complete=source_complete,
            business_complete=business_complete and not degraded,
            reason=(
                "All workflow nodes completed with complete source and business evidence."
                if source_complete and business_complete and not degraded
                else "At least one workflow node is inconclusive, skipped, bounded, or incomplete."
            ),
            missing_evidence=sorted(set(missing)),
        )
        result.summary = {
            "zh": "工作流已完成。" if not degraded else "工作流已完成，但存在未确认或范围受限的结果。",
            "en": "Workflow completed." if not degraded else "Workflow completed with inconclusive or bounded results.",
        }
        result.completed_at = utc_now()
        result.artifacts = self._write_artifacts(result)
        status = (
            RunStatus.completed
            if source_complete and business_complete and not degraded
            else RunStatus.inconclusive
        )
        self._set_progress(
            run_id,
            phase="preparing_result",
            state=status.value,
            completed_units=max(1, len(result.node_results)),
            total_units=max(1, len(result.node_results)),
            determinate=True,
        )
        self.store.update_run(
            run_id,
            status=status,
            result_json=result,
            completed_at=result.completed_at,
            error_json=None,
        )
        self.store.append_event(
            run_id,
            "workflow_completed" if status == RunStatus.completed else "workflow_inconclusive",
            {"status": status.value, "completeness": result.completeness.model_dump()},
        )

    async def _execute_free_query(self, run_id: str) -> None:
        if self.harness is not None and self.settings.free_query_runtime == "harness":
            await self._execute_free_query_harness(run_id)
            return
        await self._execute_free_query_legacy(run_id)

    async def _execute_free_query_harness(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        query = str(record.query or "").strip()
        harness_query = query
        if record.agent_id:
            try:
                guided_agent = self.agents.get(str(record.agent_id))
            except (KeyError, PluginError) as exc:
                raise RunExecutionError(
                    f"Guided Agent context is unavailable: {record.agent_id}",
                    code="guided_agent_not_found",
                ) from exc
            harness_query = _guided_agent_question(guided_agent, query)
        self.store.update_run(run_id, status=RunStatus.planning)
        self._set_progress(run_id, phase="preparing", state="active", determinate=False)
        self.store.append_event(
            run_id,
            "planning_started",
            {"query": query, "agent_id": record.agent_id, "runtime": "codex_app_server"},
        )
        outcome = await self.harness.run(run_id, harness_query, record.thread_id)
        self.store.update_run(run_id, thread_id=outcome.thread_id)
        if outcome.status == "waiting_input":
            question = outcome.clarification_question or "请补充完成查询所必需的信息。"
            self.store.update_run(
                run_id,
                status=RunStatus.waiting_input,
                error_json={"code": "clarification_required", "message": question},
            )
            self._set_progress(
                run_id, phase="preparing", state="waiting_input", determinate=False
            )
            self.store.append_event(
                run_id, "waiting_input", {"question": question, "runtime": "codex_app_server"}
            )
            return
        if outcome.stop_reason == "interrupted" and self.store.get_run(run_id).cancel_requested:
            self._finish_cancelled(run_id)
            return
        plan = {
            "kind": "sap_business_agents_harness",
            "runtime": "codex_app_server",
            "steps": outcome.executed_plans,
        }
        result = RunResult(
            run_id=run_id,
            mode=RunMode.free_query,
            agent_id=record.agent_id,
            query=query,
            plan=plan,
            steps=[
                {
                    "step_id": call["call_id"],
                    "executor": "codex_harness",
                    "operation": call["tool"],
                    "status": call["status"],
                }
                for call in outcome.tool_calls
            ],
            tool_calls=outcome.tool_calls,
            evidence=outcome.evidence,
            rule_results=[
                {
                    "rule_id": "harness_evidence_contract",
                    "business_complete": outcome.business_complete,
                    "missing_evidence": outcome.missing_evidence,
                    "evidence_refs": outcome.evidence_refs,
                },
                *outcome.verified_rule_results,
            ],
            summary=outcome.summary,
            presentation=outcome.presentation,
            errors=(
                []
                if outcome.status == "completed"
                else [
                    {
                        "code": "harness_inconclusive",
                        "message": "The Codex Harness could not establish a complete conclusion.",
                        "missing_evidence": outcome.missing_evidence,
                    }
                ]
            ),
            thread_id=outcome.thread_id,
            harness=HarnessResult(
                thread_id=outcome.thread_id,
                turn_count=outcome.turn_count,
                tool_call_count=len(outcome.tool_calls),
                budgeted_tool_call_count=outcome.budgeted_tool_call_count,
                web_search_count=outcome.web_search_count,
                discovered_tool_count=outcome.discovered_tool_count,
                activated_tool_count=outcome.activated_tool_count,
                stop_reason=outcome.stop_reason,
                limits=HarnessLimits(
                    tool_calls=HarnessLimitUsage(
                        limit=self.settings.max_tool_calls,
                        used=outcome.budgeted_tool_call_count,
                        reached=outcome.limit_kind == "tool_calls",
                    ),
                    turns=HarnessLimitUsage(
                        limit=self.settings.max_harness_turns,
                        used=outcome.turn_count,
                        reached=outcome.limit_kind == "turns",
                    ),
                    runtime_seconds=HarnessLimitUsage(
                        limit=self.settings.max_run_seconds,
                        used=outcome.elapsed_seconds,
                        reached=outcome.limit_kind == "runtime_seconds",
                    ),
                    reached_kind=outcome.limit_kind,
                ),
            ),
            started_at=record.started_at,
        )
        self.store.update_run(run_id, status=RunStatus.running, plan_json=plan)
        self.store.append_event(
            run_id,
            "harness_completed",
            {
                "thread_id": outcome.thread_id,
                "turn_count": outcome.turn_count,
                "tool_call_count": len(outcome.tool_calls),
                "web_search_count": outcome.web_search_count,
                "stop_reason": outcome.stop_reason,
            },
        )
        self._set_progress(
            run_id, phase="preparing_result", state="active", determinate=False
        )
        self._complete_result(run_id, result)

    async def _execute_free_query_legacy(self, run_id: str) -> None:
        free_query_started = time.monotonic()
        record = self.store.get_run(run_id)
        query = str(record.query or "").strip()
        planner_query = query
        if record.agent_id:
            try:
                guided_agent = self.agents.get(str(record.agent_id))
            except (KeyError, PluginError) as exc:
                raise RunExecutionError(
                    f"Guided Agent context is unavailable: {record.agent_id}",
                    code="guided_agent_not_found",
                ) from exc
            planner_query = _guided_agent_question(guided_agent, query)
        self.store.update_run(run_id, status=RunStatus.planning)
        self._set_progress(run_id, phase="preparing", state="active", determinate=False)
        self.store.append_event(
            run_id,
            "planning_started",
            {"query": query, "agent_id": record.agent_id},
        )
        catalog = await self.sap_read.catalog(
            query=planner_query,
            limit=min((self.settings.max_tool_calls or 25) * 4, 100),
        )
        guidance = await self.sap_read.guidance(planner_query)
        guidance_data = guidance.get("data") if isinstance(guidance, dict) else None
        guidance = {
            **(guidance if isinstance(guidance, dict) else {}),
            "data": {
                **(guidance_data if isinstance(guidance_data, dict) else {}),
                "business_relationship_contract": self.relationships.snapshot(),
                "max_tool_calls": self.settings.max_tool_calls,
            },
        }
        decision: PlannerDecision = await self.planner.plan(
            planner_query,
            catalog,
            guidance,
            self.skills.list(),
            thread_id=record.thread_id,
        )
        self.store.update_run(run_id, thread_id=decision.thread_id)
        if decision.needs_clarification:
            self.store.update_run(
                run_id,
                status=RunStatus.waiting_input,
                error_json={"code": "clarification_required", "message": decision.clarification_question},
            )
            self._set_progress(
                run_id, phase="preparing", state="waiting_input", determinate=False
            )
            self.store.append_event(
                run_id,
                "waiting_input",
                {"question": decision.clarification_question, "intent": decision.intent},
            )
            return
        if not decision.plan:
            raise RunExecutionError("Codex did not return a query plan.", code="codex_plan_missing")
        decision = await self._ground_and_validate_free_plan(run_id, planner_query, decision)
        if not decision.plan:
            raise RunExecutionError(
                "Codex could not produce a plan supported by the live SAP schemas.",
                code="codex_grounded_plan_missing",
            )
        self.store.update_run(run_id, thread_id=decision.thread_id)
        harness_steps = _normalize_free_steps(decision.plan)
        self.store.update_run(run_id, status=RunStatus.validating, plan_json=decision.plan)
        self.store.append_event(
            run_id, "plan_created", {"intent": decision.intent, "plan": decision.plan}
        )
        self.store.update_run(run_id, status=RunStatus.running)
        context: dict[str, Any] = {"query": query, "steps": {}}
        actual_steps: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        last_sap_response: dict[str, Any] | None = None

        for index, step in enumerate(harness_steps, start=1):
            self._ensure_not_cancelled(run_id)
            step_id = str(step.get("id") or f"step_{index}")
            tool = str(step.get("tool") or "")
            reason = str(step.get("reason") or step.get("purpose") or "")
            started_monotonic = time.perf_counter()
            call_id = f"call_{uuid.uuid4().hex[:16]}"
            self._set_progress(
                run_id,
                phase="reading_sap",
                state="active",
                current_step_id=step_id,
                current_tool="sap_read" if tool == "sap_read" else tool,
                determinate=False,
            )
            self.store.append_event(
                run_id,
                "step_started",
                {"step_id": step_id, "tool": tool, "reason": reason},
            )
            if tool in {"sap_read"}:
                sap_plan = step.get("plan")
                if not isinstance(sap_plan, dict):
                    raise RunExecutionError(
                        f"Free-query step {step_id} has no SAP read plan.",
                        code="invalid_codex_plan",
                    )
                sap_plan = self.normalizer.normalize_plan(sap_plan)
                step["plan"] = sap_plan
                validation = await self.sap_read.validate_plan(sap_plan, query)
                if validation.get("ok") is not True:
                    raise RunExecutionError(
                        f"The selected SAP Provider rejected Codex step {step_id}.",
                        code="free_query_plan_rejected",
                        detail=validation,
                    )
                self.store.append_event(
                    run_id,
                    "tool_started",
                    {
                        "step_id": step_id,
                        "tool": "sap_read.execute-plan",
                        "reason": reason,
                        "call_id": call_id,
                        **_sap_plan_trace_fields(sap_plan),
                        **_plugin_trace(self.sap_read, "sap_read.v2", "execute_plan"),
                    },
                )
                output = await self.sap_read.execute_plan(
                    sap_plan, query, conversation_id=decision.thread_id
                )
                output = _redact_sensitive(output)
                last_sap_response = output
                call = {
                    **_plugin_trace(self.sap_read, "sap_read.v2", "execute_plan"),
                    "step_id": step_id,
                    "tool": "sap_read",
                    "operation": "execute_plan",
                    **_sap_plan_trace_fields(sap_plan),
                    "reason": reason or sap_plan.get("rationale"),
                }
            elif tool == "skill":
                skill_id = str(step.get("skill_id") or "")
                try:
                    skill = self.skills.get(skill_id)
                except KeyError as exc:
                    raise RunExecutionError(
                        f"Codex selected an unregistered Skill: {skill_id}",
                        code="unregistered_skill_rejected",
                    ) from exc
                rendered_input = _render_template(step.get("input") or {}, context)
                if not isinstance(rendered_input, dict):
                    raise RunExecutionError(
                        f"Skill step {step_id} input must be an object.",
                        code="invalid_codex_plan",
                    )
                rendered_input = self.normalizer.normalize_input(
                    rendered_input,
                    skill.get("input_schema") or {"type": "object"},
                )
                self.store.append_event(
                    run_id,
                    "tool_started",
                    {
                        "step_id": step_id,
                        "tool": "skill",
                        "skill_id": skill_id,
                        "reason": reason,
                        "call_id": call_id,
                        **_plugin_trace(self.skills, "skill_execute.v1", "execute"),
                    },
                )
                output = await self.skills.execute(skill_id, rendered_input)
                output = _redact_sensitive(output)
                call = {
                    **_plugin_trace(self.skills, "skill_execute.v1", "execute"),
                    "step_id": step_id,
                    "tool": "skill",
                    "operation": "execute",
                    "skill_id": skill["skill_id"],
                    "reason": reason,
                }
            else:
                raise RunExecutionError(
                    f"Codex selected an unsupported tool: {tool}",
                    code="unregistered_tool_rejected",
                )
            context["steps"][step_id] = {"output": output}
            call["call_id"] = call_id
            call["status"] = "completed" if output.get("ok", True) else "failed"
            call["duration_ms"] = round((time.perf_counter() - started_monotonic) * 1000, 3)
            actual_steps.append(
                {
                    "step_id": step_id,
                    "executor": "sap_read" if tool in {"sap_read"} else tool,
                    "operation": call["operation"],
                    "status": "completed" if output.get("ok", True) else "failed",
                }
            )
            tool_calls.append(call)
            evidence.append(
                {
                    "source": "sap_read" if tool in {"sap_read"} else tool,
                    "step_id": step_id,
                    "payload": output,
                    "call_id": call["call_id"],
                    "plugin_id": call["plugin_id"],
                    "plugin_version": call["plugin_version"],
                    "capability": call["capability"],
                }
            )
            self.store.append_event(
                run_id,
                "tool_completed",
                {
                    "step_id": step_id,
                    "tool": "sap_read" if tool in {"sap_read"} else tool,
                    "ok": output.get("ok", True),
                    "call_id": call["call_id"],
                    "plugin_id": call["plugin_id"],
                },
            )
            self.store.append_event(
                run_id,
                "evidence_received",
                {"step_id": step_id, "source": "sap_read" if tool in {"sap_read"} else tool, "case_id": output.get("case_id")},
            )

        self._set_progress(
            run_id, phase="validating_evidence", state="active", determinate=False
        )
        rule_result = rules.evidence_summary({"evidence": evidence})
        self.store.append_event(run_id, "rule_completed", {"rule": rule_result})
        summary = {
            "zh": _safe_message(last_sap_response or {}, "zh", "基于当前只读 SAP 证据返回结果。"),
            "en": _safe_message(last_sap_response or {}, "en", "Result based on the current read-only SAP evidence."),
        }
        summary_errors: list[dict[str, Any]] = []
        summarize = getattr(self.planner, "summarize", None)
        supports = getattr(self.planner, "supports", None)
        summary_supported = not callable(supports) or bool(supports("summarize"))
        if callable(summarize) and summary_supported and decision.thread_id:
            self._set_progress(
                run_id, phase="preparing_result", state="active", determinate=False
            )
            self.store.append_event(run_id, "summary_started", {})
            remaining = (
                self.settings.max_run_seconds
                - (time.monotonic() - free_query_started)
                - 1.0
            )
            if remaining <= 0:
                summary_errors.append(
                    {
                        "code": "codex_summary_skipped_deadline",
                        "message": "Codex explanation was skipped to preserve the run deadline.",
                        "detail": "SAP evidence and deterministic rule results remain available.",
                    }
                )
            else:
                try:
                    summary = await asyncio.wait_for(
                        summarize(
                            thread_id=decision.thread_id,
                            query=query,
                            plan=decision.plan,
                            evidence=evidence,
                            rule_results=[rule_result],
                        ),
                        timeout=min(20.0, remaining),
                    )
                except TimeoutError:
                    summary_errors.append(
                        {
                            "code": "codex_summary_timeout",
                            "message": "Codex explanation exceeded its bounded summary time.",
                            "detail": "SAP evidence and deterministic rule results remain available.",
                        }
                    )
                except Exception as exc:
                    summary_errors.append(
                        {
                            "code": "codex_summary_failed",
                            "message": str(exc),
                            "detail": "SAP evidence and deterministic rule results remain available.",
                        }
                    )
        result = RunResult(
            run_id=run_id,
            mode=RunMode.free_query,
            agent_id=record.agent_id,
            query=query,
            plan=decision.plan,
            steps=actual_steps,
            tool_calls=tool_calls,
            rule_results=[rule_result],
            evidence=evidence,
            summary=summary,
            errors=summary_errors,
            thread_id=decision.thread_id,
            started_at=record.started_at,
        )
        self._set_progress(
            run_id, phase="preparing_result", state="active", determinate=False
        )
        self._complete_result(run_id, result)

    async def _ground_and_validate_free_plan(
        self,
        run_id: str,
        query: str,
        decision: PlannerDecision,
    ) -> PlannerDecision:
        if not decision.plan:
            return decision
        decision = decision.model_copy(
            update={"plan": self.normalizer.normalize_plan(decision.plan)}
        )
        _validate_free_plan_limits(decision.plan, self.settings.max_tool_calls)
        original_refs = _collect_sap_entity_refs(decision.plan)
        if not original_refs:
            return decision
        self.store.update_run(run_id, status=RunStatus.validating)
        self.store.append_event(
            run_id,
            "validation_started",
            {"phase": "live_schema_grounding", "entity_count": len(original_refs)},
        )
        schemas = await self._load_live_schemas(query, original_refs)
        metadata_rules = {
            (
                str(field.get("service_name") or ""),
                str(field.get("odata_version") or ""),
                str(field.get("entity_set") or ""),
                str(field.get("field_name") or ""),
            ): field
            for response in schemas
            for field in ((response.get("data") or {}).get("fields") or [])
            if isinstance(field, dict) and field.get("field_name")
        }
        decision = decision.model_copy(
            update={
                "plan": self.normalizer.normalize_plan(
                    decision.plan, metadata=metadata_rules
                )
            }
        )
        relationship_contract = self.relationships.snapshot_for(original_refs)
        self.store.append_event(
            run_id,
            "schema_received",
            {
                "services": len({(service, version) for service, version, _entity in original_refs}),
                "entities": len(original_refs),
                "authoritative": True,
            },
        )

        decision, canonicalized_order_fields = _canonicalize_plan_order_by(decision)
        decision, removed_unsupported_order_fields = _remove_unsupported_order_by(
            decision, schemas
        )
        if canonicalized_order_fields:
            self.store.append_event(
                run_id,
                "plan_canonicalized",
                {
                    "rule": "sap_read_bare_order_by_fields",
                    "field_count": canonicalized_order_fields,
                },
            )
        if removed_unsupported_order_fields:
            self.store.append_event(
                run_id,
                "plan_canonicalized",
                {
                    "rule": "remove_metadata_unsupported_order_by",
                    "field_count": removed_unsupported_order_fields,
                },
            )

        failures = self._validate_harness_relationships(decision.plan)
        failures.extend(await self._validate_harness_sap_plans(decision.plan, query))
        repair_used = False
        supports = getattr(self.planner, "supports", None)
        grounding_supported = (
            callable(getattr(self.planner, "ground_plan", None))
            and (not callable(supports) or bool(supports("ground_plan")))
        )
        if failures and grounding_supported:
            repair_used = True
            repaired = await self.planner.ground_plan(
                query=query,
                decision=decision,
                schemas=schemas,
                relationships=relationship_contract,
                validation_failures=failures,
                repair_attempt=1,
            )
            decision = _require_grounded_decision(repaired, original_refs)
            if decision.plan:
                decision = decision.model_copy(
                    update={
                        "plan": self.normalizer.normalize_plan(
                            decision.plan, metadata=metadata_rules
                        )
                    }
                )
            decision, repaired_order_fields = _canonicalize_plan_order_by(decision)
            decision, repaired_removed_order_fields = _remove_unsupported_order_by(
                decision, schemas
            )
            canonicalized_order_fields += repaired_order_fields
            removed_unsupported_order_fields += repaired_removed_order_fields
            _validate_free_plan_limits(decision.plan, self.settings.max_tool_calls)
            self.store.append_event(
                run_id,
                "plan_repaired",
                {"attempt": 1, "previous_validation_failures": len(failures)},
            )
            failures = self._validate_harness_relationships(decision.plan)
            failures.extend(await self._validate_harness_sap_plans(decision.plan, query))
        if failures:
            relationship_rejected = any(
                failure.get("layer") == "business_relationship" for failure in failures
            )
            raise RunExecutionError(
                (
                    "The schema-grounded Codex plan uses an unapproved cross-entity "
                    "business relationship."
                    if relationship_rejected
                    else "The selected SAP Provider rejected the schema-grounded Codex plan."
                ),
                code=(
                    "free_query_relationship_rejected"
                    if relationship_rejected
                    else "free_query_plan_rejected"
                ),
                detail={"attempts": 1 if grounding_supported else 0, "failures": failures},
            )
        self.store.append_event(
            run_id,
            "plan_validated",
            {
                "entity_count": len(original_refs),
                "repair_used": repair_used,
            },
        )
        return decision

    def _validate_harness_relationships(
        self,
        plan: dict[str, Any],
    ) -> list[dict[str, Any]]:
        sap_plans: list[tuple[str, dict[str, Any]]] = []
        for step in _normalize_free_steps(plan):
            if step.get("tool") not in {"sap_read"}:
                continue
            sap_plan = step.get("plan")
            if isinstance(sap_plan, dict):
                sap_plans.append((str(step.get("id") or "sap_read_plan"), sap_plan))
        return self.relationships.validate_plans(sap_plans)

    async def _load_live_schemas(
        self,
        query: str,
        refs: set[tuple[str, str, str]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[str]] = {}
        for service_name, odata_version, entity_set in sorted(refs):
            grouped.setdefault((service_name, odata_version), []).append(entity_set)
        responses: list[dict[str, Any]] = []
        confirmed: set[tuple[str, str, str]] = set()
        issues: list[dict[str, Any]] = []
        for (service_name, odata_version), entity_sets in grouped.items():
            response = await self.sap_read.schema(
                service_name,
                entity_sets,
                query,
                odata_version=odata_version,
                include_fields=True,
                max_fields=5000,
            )
            responses.append(response)
            data = response.get("data") if isinstance(response, dict) else None
            if response.get("ok") is not True or not isinstance(data, dict):
                issues.append(
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_sets": entity_sets,
                        "validation_issues": response.get("validation_issues") or [],
                    }
                )
                continue
            if data.get("schema_authority") is not True or data.get("fields_truncated") is True:
                issues.append(
                    {
                        "service_name": service_name,
                        "odata_version": odata_version,
                        "entity_sets": entity_sets,
                        "schema_authority": data.get("schema_authority"),
                        "fields_truncated": data.get("fields_truncated"),
                    }
                )
                continue
            for entity in data.get("entities") or []:
                if isinstance(entity, dict) and entity.get("runtime_available") is not False:
                    confirmed.add(
                        (
                            str(entity.get("service_name") or service_name),
                            str(entity.get("odata_version") or odata_version),
                            str(entity.get("entity_set") or ""),
                        )
                    )
        missing = sorted(refs.difference(confirmed))
        if issues or missing:
            raise RunExecutionError(
                "Live SAP schema grounding is unavailable for one or more planned entities.",
                code="free_query_schema_unavailable",
                detail={"issues": issues, "missing_entities": missing},
            )
        return responses

    async def _validate_harness_sap_plans(
        self,
        plan: dict[str, Any],
        query: str,
    ) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for step in _normalize_free_steps(plan):
            if step.get("tool") not in {"sap_read"}:
                continue
            sap_plan = step.get("plan")
            if not isinstance(sap_plan, dict):
                failures.append(
                    {"step_id": step.get("id"), "code": "missing_sap_read_plan"}
                )
                continue
            validation = await self.sap_read.validate_plan(sap_plan, query)
            if validation.get("ok") is not True:
                failures.append(
                    {
                        "step_id": step.get("id"),
                        "layer": "sap_read_schema",
                        "status": validation.get("status"),
                        "validation_issues": validation.get("validation_issues") or [],
                        "error": validation.get("error"),
                    }
                )
        return failures

    def _complete_result(
        self,
        run_id: str,
        result: RunResult,
        *,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        completeness_evidence, evidence_scope = _completeness_evidence_scope(result)
        flags = rules._collect_source_complete(completeness_evidence)
        evidence_source_complete = (
            bool(flags)
            and all(flags)
            and evidence_scope["missing_reference_count"] == 0
        )
        explicit_top_bounds = (
            _count_free_query_top_bounds(result.plan)
            if result.mode == RunMode.free_query and isinstance(result.plan, dict)
            else 0
        )
        source_complete = evidence_source_complete and explicit_top_bounds == 0
        if explicit_top_bounds:
            completeness_reason = (
                f"Free-query plan contains {explicit_top_bounds} explicit top bound(s); "
                "bounded evidence cannot establish source completeness even when the SAP Provider "
                "reports no next page."
            )
        elif source_complete and evidence_scope["final_report_scoped"]:
            completeness_reason = (
                "All final-report evidence sources report source_complete=true."
            )
            audit_only_count = evidence_scope["audit_only_count"]
            if audit_only_count:
                completeness_reason += (
                    f" {audit_only_count} non-final diagnostic evidence source(s) remain "
                    "available in the audit log and do not affect the final report scope."
                )
        elif source_complete:
            completeness_reason = "All SAP evidence sources report source_complete=true."
        elif evidence_scope["missing_reference_count"]:
            completeness_reason = (
                "At least one final-report evidence reference is unavailable; source "
                "completeness cannot be established."
            )
        elif evidence_scope["final_report_scoped"]:
            completeness_reason = (
                "At least one final-report evidence source is bounded, incomplete, or "
                "lacks a completeness assertion."
            )
        else:
            completeness_reason = (
                "At least one evidence source is bounded, incomplete, or lacks a "
                "completeness assertion."
            )
        missing_evidence = sorted(
            {
                str(item)
                for rule_result in result.rule_results
                if isinstance(rule_result, dict)
                for item in rule_result.get("missing_evidence") or []
                if str(item)
            }
        )
        conclusive_rules = [
            item
            for item in result.rule_results
            if isinstance(item, dict) and item.get("rule_id") != "evidence_completeness"
        ]
        # ``business_complete`` means that the evidence contract needed for a
        # conclusion is covered.  It does not mean that the business process itself
        # is finished: a fully evidenced blocked or partial P2P/O2C process is still
        # a valid completed query.  Legacy and free-query rules remain conclusive
        # unless they explicitly declare otherwise.
        business_complete = all(
            item.get("business_complete", True) is not False
            for item in conclusive_rules
        )
        if missing_evidence:
            completeness_reason += " Missing required business evidence: " + ", ".join(
                missing_evidence
            ) + "."
        result.completeness = Completeness(
            source_complete=source_complete,
            business_complete=business_complete,
            reason=completeness_reason,
            missing_evidence=missing_evidence,
        )
        if result.presentation is None:
            result.presentation = _default_presentation(result, output_schema=output_schema)
        result.completed_at = utc_now()
        result.artifacts = self._write_artifacts(result)
        status = (
            RunStatus.completed
            if source_complete and business_complete
            else RunStatus.inconclusive
        )
        current_progress = self.store.get_run(run_id).progress
        self._set_progress(
            run_id,
            phase="preparing_result",
            state=status.value,
            completed_units=(
                current_progress.total_units
                if current_progress.determinate and current_progress.total_units is not None
                else current_progress.completed_units
            ),
            total_units=current_progress.total_units,
            determinate=current_progress.determinate,
        )
        self.store.update_run(
            run_id,
            status=status,
            result_json=result,
            completed_at=result.completed_at,
            error_json=None,
        )
        self.store.append_event(
            run_id,
            "run_completed" if status == RunStatus.completed else "run_inconclusive",
            {
                "status": status.value,
                "completeness": result.completeness.model_dump(),
                "evidence_scope": evidence_scope,
            },
        )

    def _write_artifacts(self, result: RunResult) -> list[dict[str, Any]]:
        artifact_root = (self.settings.data_root / "artifacts" / result.run_id).resolve()
        expected_root = (self.settings.data_root / "artifacts").resolve()
        if expected_root not in artifact_root.parents:
            raise RunExecutionError("Artifact path escaped the local data root.")
        artifact_root.mkdir(parents=True, exist_ok=True)
        snapshot = result.model_dump(mode="json", exclude={"artifacts"})
        (artifact_root / "result.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        csv_buffer = StringIO()
        writer = csv.DictWriter(
            csv_buffer,
            fieldnames=["step_id", "source", "payload_json"],
            lineterminator="\n",
        )
        writer.writeheader()
        for item in result.evidence:
            writer.writerow(
                {
                    "step_id": item.get("step_id", ""),
                    "source": item.get("source", ""),
                    "payload_json": json.dumps(item.get("payload"), ensure_ascii=False),
                }
            )
        (artifact_root / "evidence.csv").write_text(csv_buffer.getvalue(), encoding="utf-8-sig")
        business_report = _find_business_report(result.rule_results)
        report = _business_markdown_report(result, business_report)
        (artifact_root / "report.md").write_text(report, encoding="utf-8")
        artifacts = [{"name": "report.md", "media_type": "text/markdown"}]
        if business_report:
            stage_buffer = StringIO()
            stage_writer = csv.DictWriter(
                stage_buffer,
                fieldnames=["stage", "status", "business_explanation"],
                lineterminator="\n",
            )
            stage_writer.writeheader()
            for stage in business_report.get("stages") or []:
                if not isinstance(stage, dict):
                    continue
                stage_writer.writerow(
                    {
                        "stage": _localized_text(stage.get("label"), "zh"),
                        "status": _localized_text(stage.get("state_label"), "zh"),
                        "business_explanation": _localized_text(stage.get("detail"), "zh"),
                    }
                )
            (artifact_root / "business-stages.csv").write_text(
                stage_buffer.getvalue(), encoding="utf-8-sig"
            )
            artifacts.append(
                {"name": "business-stages.csv", "media_type": "text/csv"}
            )
            for table in business_report.get("action_tables") or []:
                if not isinstance(table, dict):
                    continue
                artifact_name = str(table.get("artifact_name") or "").strip()
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}\.csv", artifact_name):
                    raise RunExecutionError("Action-table artifact name is invalid.")
                columns = [
                    column
                    for column in table.get("columns") or []
                    if isinstance(column, dict) and str(column.get("key") or "").strip()
                ]
                rows = [row for row in table.get("rows") or [] if isinstance(row, dict)]
                action_buffer = StringIO()
                action_writer = csv.writer(action_buffer, lineterminator="\n")
                action_writer.writerow(
                    [_localized_text(column.get("label"), "zh") for column in columns]
                )
                for row in rows:
                    action_writer.writerow(
                        [
                            _localized_text(row.get(str(column.get("key"))), "zh")
                            for column in columns
                        ]
                    )
                (artifact_root / artifact_name).write_text(
                    action_buffer.getvalue(), encoding="utf-8-sig"
                )
                artifacts.append({"name": artifact_name, "media_type": "text/csv"})
        artifacts.extend(
            [
                {"name": "evidence.csv", "media_type": "text/csv"},
                {"name": "result.json", "media_type": "application/json"},
            ]
        )
        return artifacts

    def _ensure_not_cancelled(self, run_id: str) -> None:
        if self.store.get_run(run_id).cancel_requested:
            self._finish_cancelled(run_id)
            raise asyncio.CancelledError()

    def _set_progress(
        self,
        run_id: str,
        *,
        phase: str,
        state: str,
        current_step_id: str | None = None,
        current_node_id: str | None = None,
        current_tool: str | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        determinate: bool | None = None,
    ) -> None:
        self.store.set_progress(
            run_id,
            phase=phase,
            state=state,
            current_step_id=current_step_id,
            current_node_id=current_node_id,
            current_tool=current_tool,
            completed_units=completed_units,
            total_units=total_units,
            determinate=determinate,
        )
        record = self.store.get_run(run_id)
        if not record.parent_run_id:
            return
        parent = self.store.get_run(record.parent_run_id)
        if parent.status in TERMINAL_STATUSES:
            return
        # A workflow remains active while its child runs. It mirrors the child's
        # real phase and tool, but keeps the parent's node-level unit counts.
        self.store.set_progress(
            record.parent_run_id,
            phase=phase,
            state="active",
            current_step_id=current_step_id,
            current_node_id=record.node_id,
            current_tool=current_tool,
            completed_units=parent.progress.completed_units,
            total_units=parent.progress.total_units,
            determinate=parent.progress.determinate,
        )

    def _finish_cancelled(self, run_id: str) -> None:
        completed = utc_now()
        progress = self.store.get_run(run_id).progress
        self._set_progress(
            run_id,
            phase=progress.phase,
            state="cancelled",
            current_step_id=progress.current_step_id,
            current_node_id=progress.current_node_id,
            current_tool=progress.current_tool,
            completed_units=progress.completed_units,
            total_units=progress.total_units,
            determinate=progress.determinate,
        )
        self.store.update_run(run_id, status=RunStatus.cancelled, completed_at=completed)
        self.store.append_event(run_id, "run_cancelled", {})

    def _finish_error(self, run_id: str, exc: Exception, status: RunStatus) -> None:
        if isinstance(exc, asyncio.CancelledError):
            return
        error = _error_payload(exc)
        completed = utc_now()
        try:
            progress = self.store.get_run(run_id).progress
            self._set_progress(
                run_id,
                phase=progress.phase,
                state="inconclusive" if status == RunStatus.inconclusive else "failed",
                current_step_id=progress.current_step_id,
                current_node_id=progress.current_node_id,
                current_tool=progress.current_tool,
                completed_units=progress.completed_units,
                total_units=progress.total_units,
                determinate=progress.determinate,
            )
            self.store.update_run(
                run_id,
                status=status,
                error_json=error,
                completed_at=completed,
            )
            self.store.append_event(
                run_id,
                "run_inconclusive" if status == RunStatus.inconclusive else "run_failed",
                {"error": error},
            )
        except KeyError:
            pass


def _error_payload(exc: Exception) -> dict[str, Any]:
    return _redact_sensitive({
        "code": str(getattr(exc, "code", "run_failed")),
        "message": str(exc),
        "detail": getattr(exc, "detail", None),
    })


def _plugin_trace(provider: Any, capability: str, operation: str) -> dict[str, Any]:
    describe = getattr(provider, "plugin_metadata", None)
    if callable(describe):
        return dict(describe(operation))
    return {
        "plugin_id": "legacy-injected-provider",
        "plugin_version": "0.0.0",
        "capability": capability,
    }


def _resolve_server_defaults(
    value: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    resolved = copy.deepcopy(value)
    applied: list[str] = []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return resolved, applied
    for name, property_schema in properties.items():
        if name in resolved or not isinstance(property_schema, dict):
            continue
        if property_schema.get("x-sapba-server-default") is not True:
            continue
        if "default" not in property_schema:
            continue
        resolved[name] = copy.deepcopy(property_schema["default"])
        applied.append(str(name))
    return resolved, applied


def _validate_input(value: dict[str, Any], schema: dict[str, Any]) -> None:
    required = schema.get("required") or []
    missing = [name for name in required if value.get(name) in (None, "")]
    if missing:
        raise ValueError("Missing required input: " + ", ".join(missing))
    properties = schema.get("properties") or {}
    unknown = sorted(set(value).difference(properties))
    if schema.get("additionalProperties") is False and unknown:
        raise ValueError("Unknown input fields: " + ", ".join(unknown))
    for name, property_schema in properties.items():
        if name not in value or not isinstance(property_schema, dict):
            continue
        item = value[name]
        property_type = property_schema.get("type")
        if property_type == "integer":
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"Input {name} must be an integer.")
            minimum_value = property_schema.get("minimum")
            maximum_value = property_schema.get("maximum")
            if isinstance(minimum_value, (int, float)) and item < minimum_value:
                raise ValueError(f"Input {name} must be at least {minimum_value}.")
            if isinstance(maximum_value, (int, float)) and item > maximum_value:
                raise ValueError(f"Input {name} must be at most {maximum_value}.")
            continue
        if property_schema.get("type") != "string" or not isinstance(item, str):
            continue
        minimum = property_schema.get("minLength")
        maximum = property_schema.get("maxLength")
        pattern = property_schema.get("pattern")
        if isinstance(minimum, int) and len(item) < minimum:
            raise ValueError(f"Input {name} must contain at least {minimum} character(s).")
        if isinstance(maximum, int) and len(item) > maximum:
            raise ValueError(f"Input {name} must contain at most {maximum} character(s).")
        if isinstance(pattern, str) and re.search(pattern, item) is None:
            raise ValueError(f"Input {name} has an invalid format.")
        if property_schema.get("format") == "date":
            try:
                date.fromisoformat(item)
            except ValueError as exc:
                raise ValueError(f"Input {name} must be an ISO date (YYYY-MM-DD).") from exc
    for pair in schema.get("dateRangePairs") or []:
        if not isinstance(pair, dict):
            continue
        start_name = str(pair.get("from") or "")
        end_name = str(pair.get("to") or "")
        if start_name not in value or end_name not in value:
            continue
        try:
            start = date.fromisoformat(str(value[start_name]))
            end = date.fromisoformat(str(value[end_name]))
        except ValueError as exc:
            raise ValueError("Date range inputs must use YYYY-MM-DD.") from exc
        if end < start:
            raise ValueError(f"Input {end_name} must not be earlier than {start_name}.")
        maximum = pair.get("maxDays")
        if isinstance(maximum, int) and (end - start).days > maximum:
            raise ValueError(f"The date range must not exceed {maximum} days.")
    for pair in schema.get("numericOrderPairs") or []:
        if not isinstance(pair, dict):
            continue
        lower_name = str(pair.get("lower") or "")
        upper_name = str(pair.get("upper") or "")
        if lower_name not in value or upper_name not in value:
            continue
        if value[lower_name] >= value[upper_name]:
            raise ValueError(f"Input {lower_name} must be less than {upper_name}.")


_TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _render_template(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [_render_template(child, context) for child in value]
    if not isinstance(value, str):
        return value
    exact = _TEMPLATE.fullmatch(value)
    if exact:
        return _lookup(context, exact.group(1))
    return _TEMPLATE.sub(lambda match: str(_lookup(context, match.group(1))), value)


def _lookup(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Template path is unavailable: {path}")
        current = current[part]
    return current


def _when_matches(value: Any, context: dict[str, Any]) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        raise ValueError("Agent step condition must be an object.")
    actual = _render_template(value.get("source"), context)
    expected = value.get("equals")
    if not isinstance(actual, bool) or not isinstance(expected, bool):
        raise ValueError("Agent step condition must compare booleans.")
    return actual is expected


def _resolve_node_input(
    workflow: dict[str, Any],
    node_id: str,
    workflow_input: dict[str, Any],
    node_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for connection in iter_node_connections(workflow, node_id):
        target = connection.get("to") or {}
        port = str(target.get("port") or "")
        value = _resolve_workflow_source(
            connection.get("from") or {}, workflow_input, node_outputs
        )
        try:
            resolved[port] = apply_transform(value, connection.get("transform"))
        except (ValueError, TypeError, WorkflowError) as exc:
            if isinstance(exc, WorkflowError):
                raise
            raise WorkflowError(str(exc), code="mapping_failed") from exc
    return resolved


def _resolve_workflow_output(
    workflow: dict[str, Any],
    workflow_input: dict[str, Any],
    node_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for item in workflow.get("outputs") or []:
        name = str(item.get("name") or "")
        value = _resolve_workflow_source(
            item.get("source") or {}, workflow_input, node_outputs
        )
        output[name] = apply_transform(value, item.get("transform"))
    return output


def _resolve_workflow_source(
    source: dict[str, Any],
    workflow_input: dict[str, Any],
    node_outputs: dict[str, dict[str, Any]],
) -> Any:
    scope = source.get("scope")
    if scope == "constant":
        return source.get("value")
    if scope == "workflow_input":
        port = str(source.get("port") or "")
        if port not in workflow_input:
            raise WorkflowError(
                f"Workflow input {port!r} is unavailable.",
                code="workflow_output_unavailable",
            )
        return workflow_input[port]
    if scope == "node_output":
        node_id = str(source.get("nodeId") or "")
        port = str(source.get("port") or "")
        if node_id not in node_outputs or port not in node_outputs[node_id]:
            raise WorkflowError(
                f"Node output {node_id}.{port} is unavailable.",
                code="workflow_output_unavailable",
            )
        return node_outputs[node_id][port]
    raise WorkflowError("Workflow mapping source is unsupported.", code="mapping_failed")


def _normalize_free_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("kind") != "sap_business_agents_harness":
        return [
            {
                "id": "sap_read_plan",
                "tool": "sap_read",
                "plan": plan,
                "reason": plan.get("rationale", "Execute the validated SAP read plan."),
            }
        ]
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RunExecutionError("Harness plan steps must be a non-empty array.", code="invalid_codex_plan")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise RunExecutionError("Harness plan contains an invalid step.", code="invalid_codex_plan")
        step_id = str(step.get("id") or f"step_{index}")
        if step_id in seen or not re.fullmatch(r"[a-z][a-z0-9_-]*", step_id):
            raise RunExecutionError(f"Invalid or duplicate harness step id: {step_id}", code="invalid_codex_plan")
        seen.add(step_id)
        normalized.append({**step, "id": step_id})
    return normalized


def _guided_agent_question(agent: dict[str, Any], query: str) -> str:
    title = agent.get("title") if isinstance(agent.get("title"), dict) else {}
    summary = agent.get("summary") if isinstance(agent.get("summary"), dict) else {}
    workflow = [
        {"id": step.get("id"), "title": step.get("title")}
        for step in agent.get("workflow") or []
        if isinstance(step, dict)
    ]
    context = {
        "agent_id": agent.get("slug"),
        "module": agent.get("module"),
        "title": title,
        "purpose": summary,
        "sap_modules": agent.get("sapModules") or [],
        "workflow": workflow,
    }
    return (
        "The user selected this SAPBusinessAgents profile as advisory business context. "
        "It does not have a deterministic execution contract, so do not claim that the fixed "
        "Agent ran. Use the profile only to understand intent, and select only registered GET-only "
        "SAP tools or approved read-only Skills from the supplied catalogs.\n\n"
        f"Selected Agent context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"Original user question:\n{query}"
    )


def _collect_sap_entity_refs(plan: dict[str, Any]) -> set[tuple[str, str, str]]:
    refs: set[tuple[str, str, str]] = set()
    for harness_step in _normalize_free_steps(plan):
        if harness_step.get("tool") not in {"sap_read"}:
            continue
        sap_plan = harness_step.get("plan")
        if not isinstance(sap_plan, dict):
            continue
        candidates = [sap_plan]
        nested = sap_plan.get("steps")
        if isinstance(nested, list):
            candidates.extend(item for item in nested if isinstance(item, dict))
        for candidate in candidates:
            service_name = str(candidate.get("service_name") or "").strip()
            odata_version = str(candidate.get("odata_version") or "").strip()
            entity_set = str(candidate.get("entity_set") or "").strip()
            if service_name and odata_version and entity_set:
                refs.add((service_name, odata_version, entity_set))
    return refs


def _sap_plan_trace_fields(plan: dict[str, Any]) -> dict[str, Any]:
    """Return only versioned public identities; never return paths or transport data."""

    refs: list[tuple[str, str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            service = str(value.get("service_name") or "")
            version = str(value.get("odata_version") or "")
            entity = str(value.get("entity_set") or "")
            if service and version and entity and (service, version, entity) not in refs:
                refs.append((service, version, entity))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(plan)
    if not refs:
        return {}
    result: dict[str, Any] = {
        "odata_versions": sorted({version for _service, version, _entity in refs}),
        "odata_refs": [
            {"service_name": service, "odata_version": version, "entity_set": entity}
            for service, version, entity in refs
        ],
    }
    if len(refs) == 1:
        service, version, entity = refs[0]
        result.update(
            {
                "service_name": service,
                "odata_version": version,
                "entity_set": entity,
            }
        )
    return result


def _count_free_query_top_bounds(plan: dict[str, Any]) -> int:
    """Count explicit result-size bounds in SAP read portions of a free-query plan."""

    def visit(value: Any) -> int:
        if isinstance(value, dict):
            count = sum(
                1
                for key, child in value.items()
                if key in {"top", "$top"} and child is not None
            )
            return count + sum(visit(child) for child in value.values())
        if isinstance(value, list):
            return sum(visit(child) for child in value)
        return 0

    # An inconclusive Harness run can legitimately reach its time/turn limit
    # before any SAP execute plan succeeds.  Its audit plan then has an empty
    # ``steps`` array.  Completion must preserve that controlled INCONCLUSIVE
    # outcome instead of reclassifying it as an invalid Codex plan.
    if plan.get("kind") == "sap_business_agents_harness" and plan.get("steps") == []:
        return 0
    count = 0
    for harness_step in _normalize_free_steps(plan):
        if harness_step.get("tool") not in {"sap_read"}:
            continue
        sap_plan = harness_step.get("plan")
        if isinstance(sap_plan, dict):
            count += visit(sap_plan)
    return count


def _require_grounded_decision(
    decision: PlannerDecision,
    allowed_refs: set[tuple[str, str, str]],
) -> PlannerDecision:
    if decision.needs_clarification or not isinstance(decision.plan, dict):
        raise RunExecutionError(
            "Codex could not ground the candidate plan in the live SAP schemas.",
            code="codex_grounded_plan_missing",
        )
    grounded_refs = _collect_sap_entity_refs(decision.plan)
    unexpected = sorted(grounded_refs.difference(allowed_refs))
    if unexpected:
        raise RunExecutionError(
            "Codex schema grounding introduced an unapproved service or entity.",
            code="codex_grounding_scope_expanded",
            detail={"unexpected_entities": unexpected},
        )
    return decision


def _canonicalize_plan_order_by(
    decision: PlannerDecision,
) -> tuple[PlannerDecision, int]:
    if not isinstance(decision.plan, dict):
        return decision, 0
    count = 0

    def visit(value: Any) -> Any:
        nonlocal count
        if isinstance(value, dict):
            normalized = {key: visit(child) for key, child in value.items()}
            order_by = normalized.get("order_by")
            if isinstance(order_by, list):
                fields: list[str] = []
                for item in order_by:
                    text = str(item or "").strip()
                    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+(asc|desc)", text, re.I)
                    if match and match.group(2).lower() == "desc":
                        raise RunExecutionError(
                            "Descending order expressions are not supported by the guarded SAP read plan contract.",
                            code="unsupported_order_direction",
                            detail={"order_by": text},
                        )
                    if match:
                        text = match.group(1)
                        count += 1
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
                        raise RunExecutionError(
                            "SAP read order_by entries must be bare field names.",
                            code="invalid_order_by_expression",
                        )
                    if text not in fields:
                        fields.append(text)
                normalized["order_by"] = fields
            return normalized
        if isinstance(value, list):
            return [visit(child) for child in value]
        return value

    return decision.model_copy(update={"plan": visit(decision.plan)}), count


def _remove_unsupported_order_by(
    decision: PlannerDecision,
    schemas: list[dict[str, Any]],
) -> tuple[PlannerDecision, int]:
    """Remove only order fields that live metadata explicitly marks unsupported.

    Sorting is a transport/paging concern, so dropping an unsupported optional order
    expression does not change the business filter or expand the query scope. The
    Provider will report an inconclusive result if it cannot prove stable pagination.
    """

    if not isinstance(decision.plan, dict):
        return decision, 0
    field_sortability: dict[tuple[str, str, str, str], bool] = {}
    for response in schemas:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            continue
        default_service = str((data.get("service") or {}).get("service_name") or "")
        default_version = str((data.get("service") or {}).get("odata_version") or "")
        for field in data.get("fields") or []:
            if not isinstance(field, dict):
                continue
            service = str(field.get("service_name") or default_service)
            version = str(field.get("odata_version") or default_version)
            entity = str(field.get("entity_set") or "")
            name = str(field.get("field_name") or "")
            if service and version and entity and name:
                field_sortability[(service, version, entity, name)] = field.get("sortable") is not False

    copied = copy.deepcopy(decision.plan)
    removed = 0
    for harness_step in _normalize_free_steps(copied):
        if harness_step.get("tool") not in {"sap_read"}:
            continue
        sap_plan = harness_step.get("plan")
        if not isinstance(sap_plan, dict):
            continue
        candidates = [sap_plan]
        nested = sap_plan.get("steps")
        if isinstance(nested, list):
            candidates.extend(item for item in nested if isinstance(item, dict))
        for candidate in candidates:
            service = str(candidate.get("service_name") or sap_plan.get("service_name") or "")
            version = str(candidate.get("odata_version") or sap_plan.get("odata_version") or "")
            entity = str(candidate.get("entity_set") or "")
            order_by = candidate.get("order_by")
            if not isinstance(order_by, list):
                continue
            kept: list[str] = []
            for field in order_by:
                name = str(field)
                if field_sortability.get((service, version, entity, name)) is False:
                    removed += 1
                else:
                    kept.append(name)
            candidate["order_by"] = kept
    return decision.model_copy(update={"plan": copied}), removed


def _validate_free_plan_limits(plan: dict[str, Any], max_tool_calls: int | None) -> None:
    call_count = 0
    for step in _normalize_free_steps(plan):
        tool = step.get("tool")
        if tool in {"sap_read"}:
            sap_plan = step.get("plan")
            if not isinstance(sap_plan, dict):
                raise RunExecutionError("SAP read harness step has no plan object.", code="invalid_codex_plan")
            _reject_non_get(sap_plan)
            nested_steps = sap_plan.get("steps") or []
            if not isinstance(nested_steps, list):
                raise RunExecutionError("SAP read plan steps must be an array.", code="invalid_codex_plan")
            call_count += max(1, len(nested_steps))
        elif tool == "skill":
            if not str(step.get("skill_id") or ""):
                raise RunExecutionError("Skill harness step has no skill_id.", code="invalid_codex_plan")
            call_count += 1
        else:
            raise RunExecutionError(
                f"Codex selected an unsupported tool: {tool}", code="unregistered_tool_rejected"
            )
    if max_tool_calls is not None and call_count > max_tool_calls:
        raise RunExecutionError(
            f"Codex plan exceeds the {max_tool_calls}-call prototype limit.",
            code="tool_call_limit_exceeded",
            detail={"planned_call_count": call_count, "max_tool_calls": max_tool_calls},
        )


def _completeness_evidence_scope(
    result: RunResult,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select evidence that can affect the final report's source completeness.

    Harness investigations intentionally retain superseded and failed diagnostic
    reads for audit.  Once a presentation has passed the run-scoped final-report
    validator, only evidence cited by that presentation or its final evidence
    contract should determine the public report's source range.  Unknown final
    references fail closed.
    """

    scope = {
        "final_report_scoped": False,
        "referenced_count": 0,
        "audit_only_count": 0,
        "missing_reference_count": 0,
    }
    presentation = result.presentation
    if (
        result.mode != RunMode.free_query
        or result.harness is None
        or presentation is None
        or not presentation.validation_ref
    ):
        return result.evidence, scope

    references = _collect_final_evidence_refs(presentation.model_dump(mode="json"))
    for rule_result in result.rule_results:
        if not isinstance(rule_result, dict):
            continue
        if rule_result.get("rule_id") != "harness_evidence_contract":
            continue
        references.update(
            str(item)
            for item in rule_result.get("evidence_refs") or []
            if str(item)
        )
    if not references:
        return result.evidence, scope

    selected = [
        item
        for item in result.evidence
        if isinstance(item, dict) and str(item.get("evidence_ref") or "") in references
    ]
    available = {
        str(item.get("evidence_ref"))
        for item in selected
        if item.get("evidence_ref")
    }
    scope.update(
        {
            "final_report_scoped": True,
            "referenced_count": len(references),
            "audit_only_count": sum(
                1
                for item in result.evidence
                if not isinstance(item, dict)
                or str(item.get("evidence_ref") or "") not in references
            ),
            "missing_reference_count": len(references - available),
        }
    )
    return selected, scope


def _collect_final_evidence_refs(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_refs" and isinstance(child, list):
                references.update(str(item) for item in child if str(item))
            else:
                references.update(_collect_final_evidence_refs(child))
    elif isinstance(value, list):
        for child in value:
            references.update(_collect_final_evidence_refs(child))
    return references


def _reject_non_get(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"http_method", "httpMethod", "method"} and str(child).upper() != "GET":
                raise RunExecutionError(
                    "Codex plan contains a non-GET operation.", code="write_operation_rejected"
                )
            _reject_non_get(child)
    elif isinstance(value, list):
        for child in value:
            _reject_non_get(child)


def _find_business_report(rule_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rule_result in rule_results:
        report = rule_result.get("business_report") if isinstance(rule_result, dict) else None
        if isinstance(report, dict):
            return report
    return None


def _text_pair(value: Any, fallback: str = "") -> LocalizedText:
    if isinstance(value, LocalizedText):
        return value
    if isinstance(value, dict):
        zh = str(value.get("zh") or value.get("en") or fallback)
        en = str(value.get("en") or value.get("zh") or fallback)
        return LocalizedText(zh=zh, en=en)
    text = str(fallback if value is None or value == "" else value)
    return LocalizedText(zh=text, en=text)


def _presentation_value(
    value: Any,
    value_format: str = "text",
    value_schema: dict[str, Any] | None = None,
) -> LocalizedText:
    schema = value_schema if isinstance(value_schema, dict) else {}
    display = schema.get("x-sapba-display")
    display = display if isinstance(display, dict) else {}
    labels = display.get("labels")
    labels = labels if isinstance(labels, dict) else {}

    def enum_label(item: Any) -> LocalizedText:
        configured = labels.get(str(item))
        return _text_pair(configured, str(item))

    if isinstance(value, list):
        localized = [enum_label(item) for item in value]
        return LocalizedText(
            zh="、".join(item.zh for item in localized),
            en=", ".join(item.en for item in localized),
        )
    if str(value) in labels:
        return enum_label(value)
    if isinstance(value, bool):
        return LocalizedText(zh="是" if value else "否", en="Yes" if value else "No")
    value_format = str(display.get("format") or value_format)
    if value_format == "status" and not isinstance(value, dict):
        labels = {
            "confirmed": LocalizedText(zh="已确认", en="Confirmed"),
            "matched": LocalizedText(zh="已匹配", en="Matched"),
            "partial": LocalizedText(zh="部分确认", en="Partially confirmed"),
            "open": LocalizedText(zh="存在差额", en="Difference remains"),
            "not_confirmed": LocalizedText(zh="未确认", en="Not confirmed"),
            "not_found": LocalizedText(zh="未找到", en="Not found"),
            "unknown": LocalizedText(zh="无法确认", en="Unknown"),
            "not_assessed": LocalizedText(zh="未单独核验", en="Not independently verified"),
            "not_requested": LocalizedText(zh="未启用", en="Not enabled"),
            "candidate": LocalizedText(zh="风险候选", en="Risk candidate"),
            "not_candidate": LocalizedText(zh="未发现风险", en="No risk found"),
            "snapshot_only": LocalizedText(zh="仅库存快照", en="Stock snapshot only"),
            "no_stock": LocalizedText(zh="无当前库存", en="No current stock"),
            "inconclusive": LocalizedText(zh="无法确认", en="Inconclusive"),
            "normal": LocalizedText(zh="正常", en="Normal"),
            "attention": LocalizedText(zh="需要关注", en="Attention required"),
            "complete": LocalizedText(zh="已完成", en="Complete"),
            "partial_complete": LocalizedText(zh="部分完成", en="Partially complete"),
        }
        normalized = str(value or "").strip().lower()
        if normalized in labels:
            return labels[normalized]
    return _text_pair(value)


def _output_property_schema(
    output_schema: dict[str, Any] | None, key: str
) -> dict[str, Any]:
    if not isinstance(output_schema, dict):
        return {}
    properties = output_schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    value = properties.get(key)
    return value if isinstance(value, dict) else {}


def _schema_visible(schema: dict[str, Any]) -> bool:
    display = schema.get("x-sapba-display")
    return not (isinstance(display, dict) and display.get("visible") is False)


def _schema_label(schema: dict[str, Any], fallback: Any) -> LocalizedText:
    return _text_pair(schema.get("title"), str(fallback or ""))


def _finding_text(value: Any) -> LocalizedText:
    if not isinstance(value, dict):
        return _text_pair(value)
    explicit = value.get("detail") or value.get("text") or value.get("label")
    if explicit:
        return _text_pair(explicit)
    code = str(value.get("code") or "Finding")
    raw = str(value.get("value") or "").strip()
    object_id = str(value.get("object") or "").strip()
    value_text = _text_pair(value.get("value_text"))

    def build(localized: str) -> str:
        parts = [code]
        if raw:
            parts.append(raw)
        text = " — ".join(parts)
        if localized:
            text += f" — {localized}"
        if object_id:
            text += f" ({object_id})"
        return text

    return LocalizedText(zh=build(value_text.zh), en=build(value_text.en))


def _default_presentation(
    result: RunResult,
    *,
    output_schema: dict[str, Any] | None = None,
) -> RunPresentation:
    report = _find_business_report(result.rule_results)
    summary = _text_pair(result.summary, "查询已经结束。")
    title = summary
    blocks: list[PresentationBlock] = []
    if report:
        title = _text_pair(report.get("headline"), summary.zh)
        overview = _text_pair(report.get("overview"))
        if overview.zh or overview.en:
            blocks.append(
                PresentationBlock(
                    type="text",
                    text=overview,
                    claim_scope="customer_business_fact",
                    tone=str(report.get("tone") or "neutral"),
                )
            )
        stages = [item for item in report.get("stages") or [] if isinstance(item, dict)]
        if stages:
            columns = [
                PresentationColumn(
                    key="stage", label=LocalizedText(zh="业务阶段", en="Business stage")
                ),
                PresentationColumn(
                    key="status", label=LocalizedText(zh="状态", en="Status"), format="status"
                ),
                PresentationColumn(
                    key="detail", label=LocalizedText(zh="说明", en="Explanation")
                ),
            ]
            rows = [
                PresentationRow(
                    values=[
                        _text_pair(item.get("label") or item.get("id")),
                        _text_pair(item.get("state_label") or item.get("state")),
                        _text_pair(item.get("detail")),
                    ]
                )
                for item in stages
            ]
            blocks.append(
                PresentationBlock(
                    type="table",
                    title=LocalizedText(zh="各阶段结果", en="Results by stage"),
                    claim_scope="customer_business_fact",
                    columns=columns,
                    rows=rows,
                    total_rows=len(rows),
                )
            )
        metrics = [item for item in report.get("metrics") or [] if isinstance(item, dict)]
        if metrics:
            presentation_metrics: list[PresentationMetric] = []
            for index, item in enumerate(metrics):
                metric_id = str(item.get("id") or item.get("name") or f"metric_{index + 1}")
                metric_schema = _output_property_schema(output_schema, metric_id)
                metric_value = _presentation_value(
                    item.get("value"),
                    str(item.get("format") or "text"),
                    metric_schema,
                )
                metric_unit = str(item.get("unit") or "").strip()
                if metric_unit and (metric_value.zh or metric_value.en):
                    metric_value = LocalizedText(
                        zh=f"{metric_value.zh} {metric_unit}",
                        en=f"{metric_value.en} {metric_unit}",
                    )
                presentation_metrics.append(
                    PresentationMetric(
                        id=metric_id,
                        label=(
                            _schema_label(metric_schema, metric_id)
                            if metric_schema
                            else _text_pair(item.get("label") or metric_id)
                        ),
                        value=metric_value,
                    )
                )
            blocks.append(
                PresentationBlock(
                    type="metrics",
                    title=LocalizedText(zh="关键业务指标", en="Key business metrics"),
                    claim_scope="customer_business_fact",
                    metrics=presentation_metrics,
                )
            )
        action_tables = [
            item for item in report.get("action_tables") or [] if isinstance(item, dict)
        ]
        for table_index, table in enumerate(action_tables):
            table_columns = [
                item for item in table.get("columns") or [] if isinstance(item, dict)
            ]
            columns: list[PresentationColumn] = []
            for index, item in enumerate(table_columns):
                column_format = str(item.get("format") or "text")
                if column_format not in {
                    "text", "date", "datetime", "integer", "decimal", "currency", "status"
                }:
                    column_format = "text"
                columns.append(
                    PresentationColumn(
                        key=str(item.get("key") or f"column_{index + 1}"),
                        label=_text_pair(item.get("label") or item.get("key")),
                        format=column_format,
                    )
                )
            table_records = [
                item for item in table.get("rows") or [] if isinstance(item, dict)
            ]
            source_complete = table.get("source_complete")
            source_complete = source_complete if isinstance(source_complete, bool) else None
            if columns and table_records:
                rows = [
                    PresentationRow(
                        values=[
                            _presentation_value(record.get(column.key), column.format)
                            for column in columns
                        ],
                        evidence_refs=[
                            str(ref)
                            for ref in record.get("_evidence_refs") or []
                            if str(ref)
                        ],
                    )
                    for record in table_records[:200]
                ]
                blocks.append(
                    PresentationBlock(
                        type="table",
                        title=_text_pair(
                            table.get("title"),
                            str(table.get("id") or f"Action table {table_index + 1}"),
                        ),
                        claim_scope="customer_business_fact",
                        columns=columns,
                        rows=rows,
                        total_rows=int(table.get("total_rows") or len(table_records)),
                        source_complete=source_complete,
                    )
                )
            elif columns:
                empty_state = _text_pair(table.get("empty_state"))
                if empty_state.zh or empty_state.en:
                    blocks.append(
                        PresentationBlock(
                            type="notice",
                            title=_text_pair(
                                table.get("title"),
                                str(table.get("id") or f"Action table {table_index + 1}"),
                            ),
                            tone="success" if source_complete is True else "warning",
                            claim_scope="customer_business_fact",
                            text=empty_state,
                            source_complete=source_complete,
                        )
                    )
        record_columns = [
            item for item in report.get("record_columns") or [] if isinstance(item, dict)
        ]
        records = [item for item in report.get("records") or [] if isinstance(item, dict)]
        if record_columns and records:
            columns: list[PresentationColumn] = []
            column_schemas: list[dict[str, Any]] = []
            for index, item in enumerate(record_columns):
                key = str(item.get("key") or f"column_{index + 1}")
                property_schema = _output_property_schema(output_schema, key)
                if property_schema and not _schema_visible(property_schema):
                    continue
                display = property_schema.get("x-sapba-display") if property_schema else None
                display = display if isinstance(display, dict) else {}
                column_format = str(display.get("format") or item.get("format") or "text")
                if column_format not in {
                    "text", "date", "datetime", "integer", "decimal", "currency", "status"
                }:
                    column_format = "text"
                columns.append(
                    PresentationColumn(
                        key=key,
                        label=(
                            _schema_label(property_schema, key)
                            if property_schema
                            else _text_pair(item.get("label") or key)
                        ),
                        format=column_format,
                    )
                )
                column_schemas.append(property_schema)
            rows = [
                PresentationRow(
                    values=[
                        _presentation_value(
                            record.get(column.key), column.format, column_schemas[index]
                        )
                        for index, column in enumerate(columns)
                    ]
                )
                for record in records[:200]
            ]
            if len(records) == 1:
                blocks.append(
                    PresentationBlock(
                        type="key_value",
                        title=LocalizedText(zh="业务记录", en="Business record"),
                        claim_scope="customer_business_fact",
                        entries=[
                            PresentationEntry(label=column.label, value=rows[0].values[index])
                            for index, column in enumerate(columns)
                        ],
                    )
                )
            else:
                blocks.append(
                    PresentationBlock(
                        type="table",
                        title=LocalizedText(zh="业务记录", en="Business records"),
                        claim_scope="customer_business_fact",
                        columns=columns,
                        rows=rows,
                        total_rows=len(records),
                        source_complete=result.completeness.source_complete,
                    )
                )
        evidence_tables = [
            item for item in report.get("evidence_tables") or [] if isinstance(item, dict)
        ]
        for table_index, table in enumerate(evidence_tables):
            table_columns = [
                item for item in table.get("columns") or [] if isinstance(item, dict)
            ]
            table_records = [
                item for item in table.get("rows") or [] if isinstance(item, dict)
            ]
            if not table_columns or not table_records:
                continue
            columns = [
                PresentationColumn(
                    key=str(item.get("key") or f"column_{index + 1}"),
                    label=_text_pair(item.get("label") or item.get("key")),
                    format=str(item.get("format") or "text"),
                )
                for index, item in enumerate(table_columns)
            ]
            rows = [
                PresentationRow(
                    values=[
                        _presentation_value(record.get(column.key), column.format)
                        for column in columns
                    ]
                )
                for record in table_records[:200]
            ]
            blocks.append(
                PresentationBlock(
                    type="table",
                    title=_text_pair(
                        table.get("title"),
                        str(table.get("id") or f"Evidence table {table_index + 1}"),
                    ),
                    claim_scope="customer_business_fact",
                    columns=columns,
                    rows=rows,
                    total_rows=len(table_records),
                    source_complete=result.completeness.source_complete,
                )
            )
        findings = [_finding_text(item) for item in report.get("findings") or []]
        if findings:
            blocks.append(
                PresentationBlock(
                    type="bullet_list",
                    title=LocalizedText(zh="业务发现", en="Business findings"),
                    claim_scope="customer_business_fact",
                    items=findings,
                )
            )
        gaps = [_text_pair(item) for item in report.get("missing_evidence") or []]
        if gaps:
            blocks.append(
                PresentationBlock(
                    type="bullet_list",
                    title=LocalizedText(zh="尚缺少的证据或能力", en="Missing evidence or capability"),
                    tone="warning",
                    claim_scope="diagnostic",
                    items=gaps,
                )
            )
        actions_value = report.get("next_actions") or []
        if isinstance(actions_value, dict):
            zh_actions = actions_value.get("zh") or []
            en_actions = actions_value.get("en") or []
            action_count = max(len(zh_actions), len(en_actions))
            actions = [
                _text_pair(
                    {
                        "zh": zh_actions[index] if index < len(zh_actions) else en_actions[index],
                        "en": en_actions[index] if index < len(en_actions) else zh_actions[index],
                    }
                )
                for index in range(action_count)
            ]
        else:
            actions = [_text_pair(item) for item in actions_value]
        if actions:
            blocks.append(
                PresentationBlock(
                    type="bullet_list",
                    title=LocalizedText(zh="建议下一步", en="Recommended next steps"),
                    claim_scope="diagnostic",
                    items=actions,
                )
            )
    elif result.mode == RunMode.workflow and result.node_results:
        rows: list[PresentationRow] = []
        for node in result.node_results:
            if not isinstance(node, dict):
                continue
            node_summary = node.get("summary") or node.get("result", {}).get("summary") or ""
            rows.append(
                PresentationRow(
                    values=[
                        _text_pair(node.get("node_id") or node.get("agent_id")),
                        _text_pair(node.get("status")),
                        _text_pair(node_summary),
                    ]
                )
            )
        if rows:
            blocks.append(
                PresentationBlock(
                    type="table",
                    title=LocalizedText(zh="工作流节点结果", en="Workflow node results"),
                    columns=[
                        PresentationColumn(
                            key="node", label=LocalizedText(zh="节点", en="Node")
                        ),
                        PresentationColumn(
                            key="status", label=LocalizedText(zh="状态", en="Status"), format="status"
                        ),
                        PresentationColumn(
                            key="conclusion", label=LocalizedText(zh="结论", en="Conclusion")
                        ),
                    ],
                    rows=rows,
                    total_rows=len(rows),
                )
            )
    if not blocks:
        blocks.append(
            PresentationBlock(
                type="text",
                text=summary,
                tone="error" if result.errors else "neutral",
                claim_scope="diagnostic" if result.errors else "customer_business_fact",
            )
        )
    return RunPresentation(schema_version="1.0", title=title, blocks=blocks)


def _localized_text(value: Any, locale: str = "zh") -> str:
    if isinstance(value, dict):
        return str(value.get(locale) or value.get("zh") or value.get("en") or "")
    return "" if value is None else str(value)


def _markdown_cell(value: Any) -> str:
    return _localized_text(value, "zh").replace("|", "\\|").replace("\n", " ").strip()


def _markdown_business_value(value: Any, value_format: str = "text") -> str:
    if value_format == "status" and not isinstance(value, dict):
        labels = {
            "confirmed": "已确认",
            "matched": "已匹配",
            "partial": "部分确认",
            "open": "存在差额",
            "not_confirmed": "未确认",
            "not_found": "未找到",
            "unknown": "无法确认",
            "not_assessed": "未单独核验",
            "not_requested": "未启用",
            "candidate": "风险候选",
            "not_candidate": "未发现风险",
            "snapshot_only": "仅库存快照",
            "no_stock": "无当前库存",
            "inconclusive": "无法确认",
            "normal": "正常",
            "attention": "需要关注",
        }
        normalized = str(value or "").strip().lower()
        if normalized in labels:
            return labels[normalized]
    return _markdown_cell(value)


def _business_markdown_report(
    result: RunResult,
    business_report: dict[str, Any] | None,
) -> str:
    summary = result.summary.get("zh") or result.summary.get("en") or ""
    lines = ["# SAP 业务查询结果", ""]
    if business_report:
        headline = _localized_text(business_report.get("headline"), "zh")
        overview = _localized_text(business_report.get("overview"), "zh")
        lines.extend(["## 业务结论", "", f"**{headline}**", "", overview, ""])
        stages = [
            stage
            for stage in business_report.get("stages") or []
            if isinstance(stage, dict)
        ]
        if stages:
            lines.extend(
                [
                    "## 各阶段结果",
                    "",
                    "| 业务阶段 | 状态 | 说明 |",
                    "| --- | --- | --- |",
                ]
            )
            for stage in stages:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_cell(stage.get("label")),
                            _markdown_cell(stage.get("state_label")),
                            _markdown_cell(stage.get("detail")),
                        ]
                    )
                    + " |"
                )
            lines.append("")
        metrics = [
            metric
            for metric in business_report.get("metrics") or []
            if isinstance(metric, dict)
        ]
        if metrics:
            lines.extend(
                [
                    "## 关键业务指标",
                    "",
                    "| 指标 | 结果 |",
                    "| --- | --- |",
                ]
            )
            for metric in metrics:
                lines.append(
                    f"| {_markdown_cell(metric.get('label'))} | "
                    f"{_markdown_cell(metric.get('value'))} |"
                )
            lines.append("")
        action_tables = [
            table
            for table in business_report.get("action_tables") or []
            if isinstance(table, dict)
        ]
        for table in action_tables:
            columns = [
                column
                for column in table.get("columns") or []
                if isinstance(column, dict) and str(column.get("key") or "").strip()
            ]
            rows = [row for row in table.get("rows") or [] if isinstance(row, dict)]
            if not columns:
                continue
            lines.extend([f"## {_markdown_cell(table.get('title'))}", ""])
            if not rows:
                lines.extend([_markdown_cell(table.get("empty_state")), ""])
                continue
            lines.extend(
                [
                    "| "
                    + " | ".join(_markdown_cell(column.get("label")) for column in columns)
                    + " |",
                    "| " + " | ".join("---" for _ in columns) + " |",
                ]
            )
            for row in rows[:200]:
                lines.append(
                    "| "
                    + " | ".join(
                        _markdown_business_value(
                            row.get(str(column.get("key"))),
                            str(column.get("format") or "text"),
                        )
                        for column in columns
                    )
                    + " |"
                )
            if len(rows) > 200:
                lines.extend(
                    [
                        "",
                        f"> 页面报告展示前 200 条；完整 {len(rows)} 条请下载 `{table.get('artifact_name')}`。",
                    ]
                )
            lines.append("")
        evidence_tables = [
            table
            for table in business_report.get("evidence_tables") or []
            if isinstance(table, dict)
        ]
        for table in evidence_tables:
            columns = [
                column
                for column in table.get("columns") or []
                if isinstance(column, dict) and str(column.get("key") or "").strip()
            ]
            rows = [row for row in table.get("rows") or [] if isinstance(row, dict)]
            if not columns or not rows:
                continue
            lines.extend(
                [
                    f"## {_markdown_cell(table.get('title'))}",
                    "",
                    "| " + " | ".join(_markdown_cell(column.get("label")) for column in columns) + " |",
                    "| " + " | ".join("---" for _ in columns) + " |",
                ]
            )
            for row in rows:
                lines.append(
                    "| "
                    + " | ".join(
                        _markdown_business_value(
                            row.get(str(column.get("key"))),
                            str(column.get("format") or "text"),
                        )
                        for column in columns
                    )
                    + " |"
                )
            lines.append("")
        findings = [
            finding
            for finding in business_report.get("findings") or []
            if isinstance(finding, dict)
        ]
        if findings:
            lines.extend(["## 业务发现", ""])
            for finding in findings:
                lines.append(f"- {_markdown_cell(_finding_text(finding).model_dump())}")
            lines.append("")
        missing_evidence = business_report.get("missing_evidence") or []
        if isinstance(missing_evidence, list) and missing_evidence:
            lines.extend(["## 尚缺少的证据或能力", ""])
            lines.extend(f"- {_markdown_cell(item)}" for item in missing_evidence)
            lines.append("")
        actions = business_report.get("next_actions")
        localized_actions = actions.get("zh") if isinstance(actions, dict) else actions
        if isinstance(localized_actions, list) and localized_actions:
            lines.extend(["## 建议下一步", ""])
            lines.extend(f"{index}. {action}" for index, action in enumerate(localized_actions, 1))
            lines.append("")
    else:
        lines.extend(["## 查询结论", "", summary or "当前没有可展示的业务结论。", ""])
    completeness_label = (
        "本次查询范围已完整返回"
        if result.completeness.source_complete
        else "当前查询结果存在范围限制"
    )
    lines.extend(
        [
            "## 数据范围说明",
            "",
            f"**{completeness_label}**",
            "",
            result.completeness.reason,
            "",
            "## 运行信息",
            "",
            f"- 运行编号：`{result.run_id}`",
            f"- 运行方式：{'固定 Agent' if result.mode == RunMode.agent else '自由 SAP 查询'}",
            f"- Agent：`{result.agent_id or 'N/A'}`",
            f"- 完成时间：{result.completed_at or 'N/A'}",
            "",
        ]
    )
    return "\n".join(lines)


def _safe_message(response: dict[str, Any], locale: str, fallback: str) -> str:
    presentation = response.get("presentation")
    if isinstance(presentation, dict) and presentation.get("text"):
        text = presentation["text"]
        if isinstance(text, dict):
            return str(text.get(locale) or text.get("en") or text.get("zh") or fallback)
        return str(text)
    snapshot = response.get("result_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("final_message"):
        message = snapshot["final_message"]
        if isinstance(message, dict):
            return str(message.get(locale) or message.get("en") or message.get("zh") or fallback)
        return str(message)
    return fallback


_SENSITIVE_KEYS = {
    "password",
    "sap_password",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
}


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redact_sensitive(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(child) for child in value]
    return value


def _public_step_output(step: dict[str, Any], output: Any) -> Any:
    """Keep raw ADT rows and connection details in transient execution context only."""

    if step.get("executor") != "skill" or step.get("skillId") != "sap-adt-table-export":
        return output
    if not isinstance(output, dict):
        return output
    public = copy.deepcopy(output)
    rows = public.pop("rows", [])
    public["rows_redacted"] = True
    public["returned_row_count"] = len(rows) if isinstance(rows, list) else public.get("row_count", 0)
    source = public.get("source")
    if isinstance(source, dict):
        for key in ("client", "endpoint", "metadata_endpoint", "system_alias"):
            source.pop(key, None)
    return public
