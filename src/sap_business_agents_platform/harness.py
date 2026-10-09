from __future__ import annotations
from .runtime_harness_contract import _developer_instructions, _turn_prompt, _HARNESS_OUTPUT_SCHEMA, _PRESENTATION_SCHEMA, _LOCALIZED_TEXT_SCHEMA

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from pydantic import ValidationError

from .config import Settings
from .assistant_tools import CATALOG_VERSION, AssistantToolContext, catalog as assistant_catalog, inspect as assistant_inspect, permitted as assistant_permitted, contract_digest as assistant_contract_digest, contract_snapshot as assistant_contract_snapshot
from .assistant_ddic import DdicLabelError, field_labels
from .agent_rules import evaluate_business_agent
from .acceptance_projection import output_schema, validate_projection, visible_projection_issues
from .database import RunStore
from .models import RunPresentation, RunStatus, TERMINAL_STATUSES
from .month_end import assess_current_month_end_status_evidence
from .normalization import SapInputNormalizationError, SapValueNormalizer
from .restricted_artifacts import RestrictedArtifactStore
from .sap_read.base import SapReadError
from .tool_gateway import ToolAdmissionError, ToolAdmissionGateway




def _strip_argument_strings(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_strip_argument_strings(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _strip_argument_strings(item) for key, item in value.items()}
    return value



def _public_skill_contract(skill: dict[str, Any]) -> dict[str, Any]:
    restricted = skill.get("output_policy") == "restricted_artifact"
    return {
        "skill_id": skill.get("skill_id"),
        "read_only": skill.get("read_only"), "validated": skill.get("validated"),
        "available": skill.get("available"), "output_policy": skill.get("output_policy"),
        "input_schema": skill.get("input_schema"),
        # A missing public schema is an explicit limitation, not permission to
        # expose a restricted output schema or an on-disk artifact contract.
        "output_schema": skill.get("public_output_schema") if restricted else skill.get("output_schema"),
    }


@dataclass(slots=True)
class HarnessOutcome:
    thread_id: str | None
    turn_count: int
    status: str
    stop_reason: str
    summary: dict[str, str]
    source_complete: bool = False
    business_complete: bool = False
    missing_evidence: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    executed_plans: list[dict[str, Any]] = field(default_factory=list)
    clarification_question: str = ""
    input_kind: str | None = None
    input_field: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    web_search_count: int = 0
    discovered_tool_count: int = 0
    activated_tool_count: int = 0
    presentation: RunPresentation | None = None
    acceptance_projection: dict[str, Any] | None = None
    verified_rule_results: list[dict[str, Any]] = field(default_factory=list)
    budgeted_tool_call_count: int = 0
    elapsed_seconds: int = 0
    limit_kind: str | None = None
    hard_limit_seconds: int = 0
    query_seconds_granted: int = 0
    finalization_seconds_reserved: int = 0
    extension_count: int = 0
    extension_reasons: list[str] = field(default_factory=list)
    deadline_phase: str = "querying"


class AcceptanceReportValidationError(RuntimeError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("Acceptance report validation failed without material progress.")
        self.code = "acceptance_report_validation_failed"
        self.detail = {"validation_issues": _safe_public(issues)}


class HarnessToolBroker:
    """Platform-owned capability broker used by the run-scoped MCP processes."""

    def __init__(self, settings: Settings, store: RunStore, sap_read: Any, skills: Any) -> None:
        self.settings = settings
        self.store = store
        self.sap_read = sap_read
        self.skills = skills
        self.normalizer = SapValueNormalizer(
            settings.repository_root / "config" / "sap-value-normalization.json"
        )
        self.tool_gateway = ToolAdmissionGateway()
        self.restricted_artifacts = RestrictedArtifactStore(
            settings.data_root,
            store,
            retention_days=settings.restricted_artifact_retention_days,
        )
        self._tokens: dict[str, str] = {}
        self._gap_tokens: dict[str, dict[str, Any]] = {}
        # Run-purpose constraints are separate from the platform-wide approved
        # Skill catalogue. Ordinary free queries keep their existing behavior.
        self._sample_contexts: dict[str, Any] = {}
        self._read_scopes: dict[str, Any] = {}
        self._run_clocks: dict[str, tuple[str, float]] = {}
        self._tool_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._closed_runs: set[str] = set()
        self._authoring_sessions: dict[str, tuple[str, str, AssistantToolContext, dict[str, str]]] = {}
        self._authoring_tool_tasks: dict[str, set[asyncio.Task[Any]]] = {}

    def open_authoring_session(
        self, draft_id: str, operation_id: str, revision: int,
        request_digest: str, expires_at: datetime,
        allowed_ddic_fields: tuple[tuple[str, str], ...] = (),
    ) -> str:
        if not self.store.assert_agent_operation(draft_id, operation_id, revision):
            raise ToolAdmissionError("The authoring operation is no longer current.", code="tool_session_stale")
        token = secrets.token_urlsafe(32)
        self._authoring_sessions[operation_id] = (
            draft_id, token, AssistantToolContext("agent_authoring", request_digest,
                                                  draft_id, revision, expires_at,
                                                  allowed_ddic_fields),
            assistant_contract_snapshot("agent_authoring"),
        )
        return token

    def close_authoring_session(self, operation_id: str) -> None:
        self._authoring_sessions.pop(operation_id, None)
        for task in self._authoring_tool_tasks.pop(operation_id, set()):
            if not task.done():
                task.cancel()

    async def _handle_authoring(
        self, operation_id: str, token: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        record = self._authoring_sessions.get(operation_id)
        if record is None or not hmac.compare_digest(record[1], token):
            return {"ok": False, "code": "harness_capability_denied"}
        draft_id, _secret, context, contracts = record
        allowed, reason = assistant_permitted(context, tool_name)
        if allowed and not self.store.assert_agent_operation(draft_id, operation_id, int(context.revision or 0)):
            allowed, reason = False, "tool_session_stale"
        if allowed and (contracts.get(tool_name) != assistant_contract_digest(tool_name)):
            allowed, reason = False, "tool_contract_changed"
        audit = {"tool": tool_name[:80], "at": datetime.now(timezone.utc).isoformat(),
                 "status": "denied", "code": reason}
        if not allowed:
            self.store.append_agent_operation_tool_event(draft_id, operation_id, audit)
            return {"ok": False, "code": reason}
        if tool_name == "sap_ddic_field_labels_get":
            pair = (str(arguments.get("table") or "").strip().upper(),
                    str(arguments.get("field") or "").strip().upper())
            if pair not in context.allowed_ddic_fields:
                self.store.append_agent_operation_tool_event(draft_id, operation_id,
                    {**audit, "code": "tool_parameter_outside_task_scope"})
                return {"ok": False, "code": "tool_parameter_outside_task_scope"}
        try:
            if tool_name == "tool_catalog_search":
                output = {"ok": True, "catalog_version": CATALOG_VERSION, "tools": self._assistant_catalog(context,
                    query=str(arguments.get("query") or "")[:120],
                    limit=min(max(int(arguments.get("limit") or 30), 1), 50))}
            elif tool_name == "tool_catalog_inspect":
                item = assistant_inspect(context, str(arguments.get("tool_id") or ""),
                    approved_skills=self.skills.list_all_approved_skills())
                output = {"ok": item is not None, "catalog_version": CATALOG_VERSION, "tool": item,
                          "code": None if item is not None else "tool_not_found"}
            elif tool_name == "sap_ddic_field_labels_get":
                remaining = max(0.001, (context.expires_at - datetime.now(timezone.utc)).total_seconds())
                task = asyncio.current_task()
                self._authoring_tool_tasks.setdefault(operation_id, set()).add(task)
                try:
                    async with asyncio.timeout(remaining):
                        output = await field_labels(self.skills, arguments)
                finally:
                    self._authoring_tool_tasks.get(operation_id, set()).discard(task)
            else:
                output = {"ok": False, "code": "tool_not_enabled_for_task"}
        except DdicLabelError as exc:
            output = {"ok": False, "code": exc.code}
        except Exception:
            output = {"ok": False, "code": "tool_execution_failed"}
        # A cancelled or revised operation cannot receive a late SAP-derived result.
        if not context.valid() or not self.store.assert_agent_operation(draft_id, operation_id, int(context.revision or 0)):
            output = {"ok": False, "code": "tool_session_stale"}
        audit.update(status="completed" if output.get("ok") else "denied",
                     code=output.get("code"), evidence_sha256=output.get("evidence_sha256"))
        self.store.append_agent_operation_tool_event(draft_id, operation_id, audit)
        return output

    def _assistant_catalog(self, context: AssistantToolContext, *, query: str = "", limit: int = 30) -> list[dict[str, Any]]:
        return assistant_catalog(context, approved_skills=self.skills.list_all_approved_skills(),
                                 query=query, limit=limit)

    def open_session(self, run_id: str) -> str:
        self._closed_runs.discard(run_id)
        self.tool_gateway.restore(
            run_id, self.store.list_harness_tool_candidates(run_id)
        )
        self._restore_gap_tokens(run_id)
        token = secrets.token_urlsafe(32)
        self._tokens[run_id] = token
        state = self.store.get_harness_state(run_id)
        if not isinstance(state.get("time_budget"), dict):
            self.store.update_harness_state(
                run_id,
                {
                    "time_budget": {
                        "hard_limit_seconds": self.settings.free_query_run_seconds,
                        "query_seconds_granted": self.settings.free_query_initial_budget_seconds,
                        "finalization_seconds_reserved": (
                            self.settings.free_query_finalization_budget_seconds
                        ),
                        "extension_count": 0,
                        "extension_reasons": [],
                        "deadline_phase": "querying",
                        "progress_marker": self._progress_marker(run_id),
                    }
                },
            )
        frozen = state.get("assistant_tool_authorization")
        if (not isinstance(frozen, dict)
                or frozen.get("request_digest") != self._run_request_digest(run_id, state)):
            context = self._run_assistant_context(run_id, fresh=True)
            self.store.update_harness_state(run_id, {"assistant_tool_authorization": {
                "kind": context.kind, "request_digest": context.request_digest,
                "target_id": context.target_id, "revision": context.revision,
                "expires_at": context.expires_at.isoformat(),
                "tool_contracts": assistant_contract_snapshot(context.kind),
            }})
        elif not isinstance(frozen.get("tool_contracts"), dict):
            updated = dict(frozen)
            updated["tool_contracts"] = assistant_contract_snapshot(str(updated.get("kind") or ""))
            self.store.update_harness_state(run_id, {"assistant_tool_authorization": updated})
        return token

    def _run_request_digest(self, run_id: str, state: dict[str, Any]) -> str:
        run = self.store.get_run(run_id)
        request_data = {"query": str(run.query or ""), "acceptance_spec": state.get("acceptance_spec")}
        return hashlib.sha256(json.dumps(request_data, sort_keys=True, default=str).encode()).hexdigest()

    def _run_assistant_context(self, run_id: str, *, fresh: bool = False) -> AssistantToolContext:
        run = self.store.get_run(run_id)
        state = self.store.get_harness_state(run_id)
        snapshot = state.get("assistant_tool_authorization")
        if isinstance(snapshot, dict) and not fresh:
            try:
                return AssistantToolContext(
                    str(snapshot["kind"]), str(snapshot["request_digest"]),
                    str(snapshot["target_id"]), snapshot.get("revision"),
                    datetime.fromisoformat(str(snapshot["expires_at"]).replace("Z", "+00:00")),
                )
            except (KeyError, ValueError, TypeError):
                # A malformed persisted binding cannot silently broaden access.
                return AssistantToolContext("invalid", "", run_id, None,
                                            datetime.fromtimestamp(0, timezone.utc))
        kind = ("sample_discovery" if run_id in self._sample_contexts else
                "acceptance_baseline" if state.get("acceptance_direct_baseline") is True else
                "acceptance_free_query" if state.get("acceptance_spec") else "free_query")
        started = datetime.fromisoformat(str(run.started_at or run.created_at).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        from datetime import timedelta
        return AssistantToolContext(
            kind=kind,
            request_digest=self._run_request_digest(run_id, state),
            target_id=run_id, revision=state.get("draft_revision"),
            expires_at=started + timedelta(seconds=self.budget_snapshot(run_id)["hard_limit_seconds"]),
        )

    def budget_snapshot(self, run_id: str) -> dict[str, Any]:
        budget = self.store.get_harness_state(run_id).get("time_budget") or {}
        return {
            "hard_limit_seconds": int(
                budget.get("hard_limit_seconds") or self.settings.free_query_run_seconds
            ),
            "query_seconds_granted": int(
                budget.get("query_seconds_granted")
                or self.settings.free_query_initial_budget_seconds
            ),
            "finalization_seconds_reserved": int(
                budget.get("finalization_seconds_reserved")
                or self.settings.free_query_finalization_budget_seconds
            ),
            "extension_count": int(budget.get("extension_count") or 0),
            "extension_reasons": list(budget.get("extension_reasons") or []),
            "deadline_phase": str(budget.get("deadline_phase") or "querying"),
            "progress_marker": int(budget.get("progress_marker") or 0),
        }

    def review_deadline(self, run_id: str) -> dict[str, Any]:
        budget = self.budget_snapshot(run_id)
        elapsed = self._elapsed_seconds(run_id)
        hard_limit = budget["hard_limit_seconds"]
        max_query = max(
            1, hard_limit - budget["finalization_seconds_reserved"]
        )
        phase = budget["deadline_phase"]
        changed = False
        while (
            phase == "querying"
            and elapsed >= budget["query_seconds_granted"]
            and budget["query_seconds_granted"] < max_query
        ):
            marker = self._progress_marker(run_id)
            if marker <= budget["progress_marker"]:
                phase = "finalizing"
                changed = True
                break
            extension = min(
                self.settings.free_query_extension_budget_seconds,
                max_query - budget["query_seconds_granted"],
            )
            if extension <= 0:
                phase = "finalizing"
                changed = True
                break
            budget["query_seconds_granted"] += extension
            budget["extension_count"] += 1
            reason = "validated_sap_evidence_or_plan_progress"
            budget["extension_reasons"].append(reason)
            budget["progress_marker"] = marker
            changed = True
            self.store.append_event(
                run_id,
                "harness_time_extended",
                {
                    "extension_seconds": extension,
                    "query_seconds_granted": budget["query_seconds_granted"],
                    "reason": reason,
                },
            )
        if phase == "querying" and elapsed >= max_query:
            phase = "finalizing"
            changed = True
        if elapsed >= hard_limit:
            phase = "deadline_exceeded"
            changed = True
        if phase != budget["deadline_phase"]:
            budget["deadline_phase"] = phase
            if phase == "finalizing":
                self.store.append_event(
                    run_id,
                    "harness_finalization_started",
                    {
                        "elapsed_seconds": elapsed,
                        "finalization_seconds_reserved": budget[
                            "finalization_seconds_reserved"
                        ],
                    },
                )
        if changed:
            state = self.store.get_harness_state(run_id)
            state["time_budget"] = budget
            self.store.save_harness_state(run_id, state)
        current_progress = self.store.get_run(run_id).progress
        self.store.set_progress(
            run_id,
            current_step_id=current_progress.current_step_id,
            current_node_id=current_progress.current_node_id,
            current_tool=current_progress.current_tool,
            elapsed_seconds=elapsed,
            hard_limit_seconds=hard_limit,
            deadline_phase=(
                "finalizing" if phase in {"finalizing", "deadline_exceeded"} else "querying"
            ),
            extension_count=budget["extension_count"],
            next_deadline_at=self._next_deadline_at(run_id, budget),
        )
        return {**budget, "elapsed_seconds": elapsed}

    def _elapsed_seconds(self, run_id: str) -> int:
        record = self.store.get_run(run_id)
        # Queue time is not execution time. Anchor once, then use a monotonic
        # clock so wall-clock corrections cannot extend an active task.
        anchor = self.store.get_harness_state(run_id).get("budget_start") or {}
        value = anchor.get("execution_started_at") if anchor.get("source_started_at") == record.started_at else record.started_at
        if not value:
            return 0
        cached = self._run_clocks.get(run_id)
        if cached and cached[0] == str(value):
            return max(0, int(time.monotonic() - cached[1]))
        try:
            started = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = max(0, (datetime.now(timezone.utc) - started).total_seconds())
            self._run_clocks[run_id] = (str(value), time.monotonic() - elapsed)
            return int(elapsed)
        except (TypeError, ValueError):
            return 0

    def _next_deadline_at(self, run_id: str, budget: dict[str, Any]) -> str | None:
        record = self.store.get_run(run_id)
        anchor = self.store.get_harness_state(run_id).get("budget_start") or {}
        execution_start = anchor.get("execution_started_at") if anchor.get("source_started_at") == record.started_at else record.started_at
        try:
            started = datetime.fromisoformat(
                str(execution_start or record.created_at).replace("Z", "+00:00")
            )
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            seconds = (
                budget["hard_limit_seconds"]
                if budget["deadline_phase"] != "querying"
                else budget["query_seconds_granted"]
            )
            return datetime.fromtimestamp(
                started.timestamp() + seconds, tz=timezone.utc
            ).isoformat()
        except (TypeError, ValueError):
            return None

    def _progress_marker(self, run_id: str) -> int:
        marker = 0
        previous_gaps: set[str] | None = None
        for call in self.store.list_harness_tool_calls(run_id):
            if call.get("status") != "completed":
                continue
            output = call.get("output") or {}
            if call.get("tool_name") == "sap_query_execute" and output.get(
                "evidence_ref"
            ):
                marker += 10
            elif call.get("tool_name") == "sap_query_validate" and output.get("ok"):
                marker += 3
            elif call.get("tool_name") == "sap_evidence_assess" and output.get("ok"):
                gaps = {
                    str(item)
                    for item in output.get("missing_evidence") or []
                    if str(item)
                }
                if previous_gaps is not None:
                    marker += len(previous_gaps - gaps)
                previous_gaps = gaps
        return marker

    def bind_read_scope(self, run_id: str, scope: Any) -> None:
        scope.normalizer = self.normalizer
        self._read_scopes[run_id] = scope

    def close_session(self, run_id: str) -> None:
        self._closed_runs.add(run_id)
        self._tokens.pop(run_id, None)
        self._read_scopes.pop(run_id, None)
        clear_scope = getattr(self.sap_read, "clear_schema_scope", None)
        if callable(clear_scope):
            clear_scope(run_id)
        for token, record in list(self._gap_tokens.items()):
            if record.get("run_id") == run_id:
                self._gap_tokens.pop(token, None)

    async def cancel_tools(self, run_id: str, *, timeout: float = 10) -> bool:
        self.close_session(run_id)
        tasks = [task for task in self._tool_tasks.get(run_id, set()) if not task.done()]
        for task in tasks:
            task.cancel()
        if not tasks:
            return True
        _done, pending = await asyncio.wait(tasks, timeout=max(0, timeout))
        return not pending

    def _ensure_writable(self, run_id: str) -> None:
        record = self.store.get_run(run_id)
        if run_id in self._closed_runs or record.cancel_requested or record.status in TERMINAL_STATUSES:
            raise ToolAdmissionError("This run has ended; late results cannot be applied.", code="harness_run_closed")
        if self._elapsed_seconds(run_id) >= self.budget_snapshot(run_id)["hard_limit_seconds"]:
            raise ToolAdmissionError("The run deadline has been reached.", code="harness_deadline_exceeded")

    def _restore_gap_tokens(self, run_id: str) -> None:
        calls = self.store.list_harness_tool_calls(run_id)
        used_fingerprints = {
            str((call.get("safe_input") or {}).get("gap_token") or "")
            for call in calls
            if call.get("tool_name") == "sap_skill_execute"
            and not (
                isinstance(call.get("output"), dict)
                and call["output"].get("code")
                in {
                    "gap_token_invalid",
                    "gap_token_expired",
                    "gap_token_input_mismatch",
                    "skill_input_invalid",
                    "skill_not_approved",
                }
            )
        }
        for call in calls:
            output = call.get("output")
            if (
                call.get("tool_name") != "sap_evidence_assess"
                or call.get("status") != "completed"
                or not isinstance(output, dict)
                or not (
                    output.get("skill_eligible") is True
                    or output.get("adt_eligible") is True
                )
            ):
                continue
            token = str(output.get("gap_token") or "")
            if not token:
                continue
            token_fingerprint = (
                token if token.startswith("sha256:") else _capability_fingerprint(token)
            )
            self._gap_tokens[token_fingerprint] = {
                "run_id": run_id,
                "skill_id": str(output.get("skill_id") or "sap-adt-table-export"),
                "skill_input_hash": str(output.get("skill_input_hash") or ""),
                "missing_evidence": output.get("missing_evidence") or ["source_completeness"],
                "used": _capability_fingerprint(token) in used_fingerprints,
                "expires_at_epoch": int(output.get("expires_at_epoch") or 0),
            }

    def authenticate(self, run_id: str, token: str) -> bool:
        expected = self._tokens.get(run_id, "")
        return bool(expected) and hmac.compare_digest(expected, token)

    async def handle(
        self, run_id: str, token: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if run_id in self._authoring_sessions:
            return await self._handle_authoring(run_id, token, tool_name, arguments)
        if not self.authenticate(run_id, token):
            return {"ok": False, "code": "harness_capability_denied", "message": "Invalid capability."}
        run = self.store.get_run(run_id)
        context = self._run_assistant_context(run_id)
        allowed, reason = assistant_permitted(context, tool_name)
        if allowed and context.request_digest != self._run_request_digest(run_id, self.store.get_harness_state(run_id)):
            allowed, reason = False, "tool_session_stale"
        frozen = (self.store.get_harness_state(run_id).get("assistant_tool_authorization") or {}).get("tool_contracts") or {}
        if allowed and frozen.get(tool_name) != assistant_contract_digest(tool_name):
            allowed, reason = False, "tool_contract_changed"
        if (not allowed or run.cancel_requested or run.status in TERMINAL_STATUSES
                or run.status == RunStatus.waiting_input or run_id in self._closed_runs):
            code = reason if not allowed else "harness_run_closed"
            self.store.append_event(run_id, "tool_denied", {"tool": tool_name[:80], "code": code})
            return {"ok": False, "code": code}
        sample_context = self._sample_contexts.get(run_id)
        if sample_context is not None:
            try:
                sample_context.allow_tool(tool_name, arguments)
            except ValueError as exc:
                if getattr(exc, "code", "") == "invalid_order_by_expression":
                    sample_context.validation_issues = [{"code": "invalid_order_by_expression"}]
                    sample_context.progress("validating_query")
                    return {"ok": False, "code": "invalid_order_by_expression",
                            "validation_issues": sample_context.validation_issues,
                            "message": "order_by must contain bare field names, e.g. [\"PurchasingDocument\"]. Do not append asc or desc."}
                return {"ok": False, "code": getattr(exc, "code", "sample_request_denied"),
                        "message": "The sample request is outside the verified draft scope or budget."}
        budget = ({"deadline_phase": "querying" if sample_context.remaining(external=True) else "finalizing"}
                  if sample_context is not None else self.review_deadline(run_id))
        if budget["deadline_phase"] == "deadline_exceeded":
            return {
                "ok": False,
                "code": "harness_deadline_exceeded",
                "message": "The free-query hard deadline has been reached.",
            }
        if budget["deadline_phase"] == "finalizing" and tool_name not in {
            "sap_evidence_read",
            "sap_evidence_assess",
            "sap_inventory_fifo_assess",
            "sap_month_end_status_assess",
            "sap_final_report_validate",
            "safe_compute",
            "tool_catalog_search",
            "tool_catalog_inspect",
        }:
            return {
                "ok": False,
                "code": "harness_finalization_only",
                "message": (
                    "The query phase is closed. Complete the report from already "
                    "validated evidence; no new external reads are allowed."
                ),
            }
        arguments = _strip_argument_strings(arguments)
        normalization_diagnostics = []
        adapter_error = None
        if tool_name in {"sap_query_validate", "sap_query_execute"} and isinstance(
            arguments.get("plan"), dict
        ):
            try:
                from .runtime_query_contract import plans
                from .harness_query_adapter import normalize_ascending_order
                arguments["plan"], normalization_diagnostics = normalize_ascending_order(arguments["plan"])
                plans(arguments["plan"])
                arguments["plan"] = self.normalizer.normalize_plan(arguments["plan"])
                if run_id in self._read_scopes:
                    self._read_scopes[run_id].check(arguments["plan"])
            except (SapInputNormalizationError, SapReadError, ValueError) as exc:
                adapter_error = exc
        if tool_name == "sap_skill_execute" and run_id in self._read_scopes:
            try:
                self._read_scopes[run_id].check_skill(arguments)
            except ValueError as exc:
                adapter_error = exc
        calls = self.store.list_harness_tool_calls(run_id)
        call_id = str(arguments.pop("tool_call_id", "") or f"call_{uuid.uuid4().hex[:16]}")
        safe_input = _safe_tool_input(arguments)
        request_hash = hashlib.sha256(
            json.dumps(
                {"tool": tool_name, "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        existing = next(
            (
                call
                for call in calls
                if call.get("tool_name") == tool_name
                and call.get("request_hash") == request_hash
            ),
            None,
        )
        # A fresh final-report call must be revalidated against the current
        # evidence/assessment state even when its payload is byte-identical.
        # Network replay with the same tool_call_id remains idempotent below.
        previous_output = (existing or {}).get("output") or {}
        previous_issues = previous_output.get("validation_issues") or (previous_output.get("validation") or {}).get("validation_issues") or []
        previous_codes = {previous_output.get("code"), *(item.get("code") for item in previous_issues if isinstance(item, dict))}
        transient_failure = bool(previous_codes.intersection({
            "sap_read_timeout", "sap_schema_timeout", "sap_read_connection_error", "sap_metadata_invalid"
        }))
        if (
            existing is not None
            and (tool_name == "sap_final_report_validate" or transient_failure)
            and existing.get("status") == "failed"
            and existing.get("call_id") != call_id
        ):
            # A new SDK tool invocation is another repair attempt even when
            # the payload is byte-identical.  Give it a distinct persistence
            # key; replaying the original tool_call_id remains idempotent.
            request_hash = hashlib.sha256(
                f"{request_hash}:{call_id}".encode("utf-8")
            ).hexdigest()
            existing = None
        if existing is not None:
            if existing.get("status") in {"completed", "failed"} and isinstance(
                existing.get("output"), dict
            ):
                return self._completed_tool_replay(run_id, tool_name, existing["output"])
            return _unknown_recovered_call(existing)
        budgeted_calls = [
            call for call in calls if call.get("tool_name") != "sap_final_report_validate"
        ]
        if (
            tool_name != "sap_final_report_validate"
            and self.settings.max_tool_calls is not None
            and len(budgeted_calls) >= self.settings.max_tool_calls
        ):
            return {
                "ok": False,
                "code": "harness_tool_limit_reached",
                "message": "The run-scoped tool-call limit has been reached.",
            }
        self._set_run_phase(run_id, tool_name)
        existing = self.store.begin_harness_tool_call(
            call_id=call_id,
            run_id=run_id,
            tool_name=tool_name,
            request_hash=request_hash,
            safe_input=safe_input,
        )
        if existing is not None:
            if existing.get("status") in {"completed", "failed"} and isinstance(
                existing.get("output"), dict
            ):
                return self._completed_tool_replay(run_id, tool_name, existing["output"])
            return _unknown_recovered_call(existing)

        self.store.append_event(
            run_id, "tool_requested", {"call_id": call_id, "tool": tool_name, "input": safe_input}
        )
        if tool_name == "sap_query_execute" and any(
            call.get("tool_name") == "sap_query_execute" for call in calls
        ):
            plan = arguments.get("plan") if isinstance(arguments.get("plan"), dict) else {}
            self.store.append_event(
                run_id,
                "query_revised",
                {
                    "service_name": plan.get("service_name"),
                    "odata_version": plan.get("odata_version"),
                    "entity_set": plan.get("entity_set"),
                    "reason": str(arguments.get("query") or "")[:500],
                },
            )
        sample_read_started = False
        task = asyncio.current_task()
        self._tool_tasks.setdefault(run_id, set()).add(task)
        try:
            if adapter_error is not None:
                raise adapter_error
            if sample_context is not None:
                sample_context.tool_tasks.add(asyncio.current_task())
                sample_context.progress({"sap_catalog_search": "preparing", "sap_schema_get": "reading_metadata",
                    "sap_query_validate": "validating_query", "sap_query_execute": "reading_candidates",
                    "sap_skill_execute": "reading_candidates"}.get(tool_name, "checking_sample"))
            if sample_context is not None and tool_name in {"sap_query_execute", "sap_skill_execute"}:
                sample_context.begin_read()
                sample_read_started = True
            if sample_context is not None:
                async with asyncio.timeout(sample_context.remaining(external=tool_name not in {
                    "sap_evidence_read", "sap_evidence_assess", "tool_catalog_search", "tool_catalog_inspect"})):
                    output = await self._dispatch(run_id, tool_name, arguments)
                if sample_context.closed:
                    raise ValueError("sample_discovery_closed")
            else:
                remaining = max(0, budget["hard_limit_seconds"] - self._elapsed_seconds(run_id))
                async with asyncio.timeout(remaining):
                    scope = getattr(self.sap_read, "metadata_scope", None)
                    with scope(run_id) if callable(scope) else nullcontext():
                        output = await self._dispatch(run_id, tool_name, arguments)
                self._ensure_writable(run_id)
            if normalization_diagnostics:
                output = {**output, "normalization_diagnostics": normalization_diagnostics}
            output = _safe_public(output, preserve_rows=True)
            status = "completed" if output.get("ok") is not False else "failed"
        except asyncio.CancelledError:
            self.store.complete_harness_tool_call(call_id, status="failed", output={"ok": False, "code": "harness_run_cancelled"})
            raise
        except Exception as exc:  # tool errors are observations for the same Codex turn
            output = {
                "ok": False,
                "code": getattr(exc, "code", "harness_deadline_exceeded" if isinstance(exc, TimeoutError) else "tool_execution_failed"),
                "message": "Sample evidence read failed." if sample_context is not None else str(exc),
            }
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict) and sample_context is None:
                output["detail"] = _safe_public(detail)
            status = "failed"
        finally:
            self._tool_tasks.get(run_id, set()).discard(task)
            if sample_context is not None:
                sample_context.tool_tasks.discard(asyncio.current_task())
            if sample_read_started:
                sample_context.calls_in_flight = max(0, sample_context.calls_in_flight - 1)
        evidence_ref = str(output.get("evidence_ref") or "") or None
        self.store.complete_harness_tool_call(
            call_id,
            status=status,
            output=_stored_tool_output(tool_name, output),
            evidence_ref=evidence_ref,
        )
        self.store.append_event(
            run_id,
            "tool_completed" if status == "completed" else "tool_failed",
            {
                "call_id": call_id,
                "tool": tool_name,
                "status": status,
                "evidence_ref": evidence_ref,
                "source_complete": output.get("source_complete"),
                "code": output.get("code"),
                "validation_issues": _safe_public(output.get("validation_issues") or []),
                "normalization_diagnostics": normalization_diagnostics,
                "terminal_validation_failure": output.get("terminal_validation_failure") is True,
            },
        )
        if sample_context is not None:
            sample_context.tool_completed(tool_name, output, lambda ref: self._read_evidence(run_id, ref))
        if tool_name == "sap_schema_get" and run_id in self._read_scopes:
            self._read_scopes[run_id].record_schema(output)
        return _client_tool_output(output)

    def _completed_tool_replay(
        self, run_id: str, tool_name: str, output: dict[str, Any]
    ) -> dict[str, Any]:
        replay = _client_tool_output(output)
        if (
            tool_name in {"sap_query_validate", "sap_query_execute"}
            and output.get("code") == "sap_read_http_error"
            and int((output.get("detail") or {}).get("http_status") or 0) == 400
        ):
            replay = {
                **replay,
                "code": "sap_query_plan_revision_required",
                "previous_code": "sap_read_http_error",
                "message": (
                    "This exact plan already returned HTTP 400. Revise the plan so its "
                    "digest changes before another attempt."
                ),
            }
        stored_token = str(output.get("gap_token") or "")
        if tool_name == "sap_evidence_assess" and self.review_deadline(run_id)["deadline_phase"] != "querying":
            return {**replay, "gap_token": None, "skill_eligible": False, "adt_eligible": False,
                    "code": "harness_finalization_only", "idempotent_replay": True}
        if (
            tool_name == "sap_evidence_assess"
            and output.get("skill_eligible") is True
            and stored_token.startswith("sha256:")
        ):
            gap_token = secrets.token_urlsafe(24)
            expires_at_epoch = int(time.time()) + max(0, self.budget_snapshot(run_id)["query_seconds_granted"] - self._elapsed_seconds(run_id))
            self._gap_tokens[_capability_fingerprint(gap_token)] = {
                "run_id": run_id,
                "skill_id": str(output.get("skill_id") or "sap-adt-table-export"),
                "skill_input_hash": str(output.get("skill_input_hash") or ""),
                "missing_evidence": output.get("missing_evidence") or ["source_completeness"],
                "used": False,
                "expires_at_epoch": expires_at_epoch,
            }
            replay = {
                **replay,
                "gap_token": gap_token,
                "expires_at_epoch": expires_at_epoch,
            }
        return {**replay, "idempotent_replay": True}

    def _set_run_phase(self, run_id: str, tool_name: str) -> None:
        record = self.store.get_run(run_id)
        if record.status in TERMINAL_STATUSES or record.status == RunStatus.waiting_input:
            return
        validation_tools = {"sap_catalog_search", "sap_schema_get", "sap_query_validate", "list_all_approved_skills", "tool_catalog_search", "tool_catalog_inspect"}
        next_status = RunStatus.validating if tool_name in validation_tools else RunStatus.running
        # Do not move the public progress indicator backwards after live reads begin.
        if record.status == RunStatus.running and next_status == RunStatus.validating:
            return
        if record.status != next_status:
            self.store.update_run(run_id, status=next_status)
            self.store.append_event(
                run_id,
                "run_phase_changed",
                {"status": next_status.value, "tool": tool_name},
            )
        if tool_name in {"sap_query_execute", "sap_skill_execute"}:
            phase = "reading_sap"
            current_tool = "sap_read" if tool_name == "sap_query_execute" else "skill"
        elif tool_name in {
            "sap_evidence_read", "sap_evidence_assess", "sap_inventory_fifo_assess",
            "sap_month_end_status_assess",
            "sap_final_report_validate", "safe_compute", "external_tool_execute",
        }:
            phase = "validating_evidence"
            current_tool = tool_name
        else:
            phase = "preparing"
            current_tool = tool_name
        self.store.set_progress(
            run_id,
            phase=phase,
            state="active",
            current_tool=current_tool,
            determinate=False,
        )

    async def _dispatch(
        self, run_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name in {"tool_catalog_search", "tool_catalog_inspect"}:
            context = self._run_assistant_context(run_id)
            if tool_name == "tool_catalog_search":
                return {"ok": True, "catalog_version": CATALOG_VERSION, "tools": self._assistant_catalog(context,
                    query=str(arguments.get("query") or "")[:120],
                    limit=min(max(int(arguments.get("limit") or 30), 1), 50))}
            item = assistant_inspect(context, str(arguments.get("tool_id") or ""),
                approved_skills=self.skills.list_all_approved_skills())
            return {"ok": item is not None, "catalog_version": CATALOG_VERSION, "tool": item,
                    "code": None if item is not None else "tool_not_found"}
        if tool_name == "list_all_approved_skills":
            catalog = self.skills.list_all_approved_skills()
            selected = str(arguments.get("skill_id") or "")
            return {"ok": True, "total_approved": len(catalog), "complete": True,
                    "skills": [_public_skill_contract(item) for item in catalog
                               if not selected or item.get("skill_id") == selected]}
        if tool_name == "sap_catalog_search":
            result = await self.sap_read.catalog(
                query=str(arguments.get("query") or ""),
                limit=min(max(int(arguments.get("limit") or 20), 1), 100),
            )
            return _compact_catalog_result(result)
        if tool_name == "sap_schema_get":
            options = {}
            if callable(getattr(self.sap_read, "clear_schema_scope", None)):
                options = {"cache_scope": run_id, "mode": str(arguments.get("mode") or "fields"),
                           "offset": max(int(arguments.get("offset") or 0), 0),
                           "limit": min(max(int(arguments.get("limit") or 100), 1), 100)}
            elif arguments.get("mode") == "entities":
                return {"ok": False, "code": "schema_entity_discovery_unavailable"}
            result = await self.sap_read.schema(
                str(arguments.get("service_name") or ""),
                arguments.get("entity_sets") or [],
                str(arguments.get("query") or ""),
                odata_version=str(arguments.get("odata_version") or ""),
                include_fields=True,
                max_fields=min(max(int(arguments.get("max_fields") or 5000), 1), 5000),
                **options,
            )
            sample_context = self._sample_contexts.get(run_id)
            if sample_context is not None:
                sample_context.record_schema(result)
                result = {**result, "sample_query_contract": {"order_by": "bare field names only; ascending is implicit",
                    "stable_keys": [{"service_name": key[0], "odata_version": key[1], "entity_set": key[2],
                                     "order_by": fields} for key, fields in sample_context.live_keys.items()],
                    "sortable_projection_fallback": (
                        "When an entity key is not sortable, order by every sortable source field "
                        "bound to a requested missing input."
                    )}}
            return result
        if tool_name == "sap_query_validate":
            plan = self.normalizer.normalize_plan(
                _require_object(arguments.get("plan"), "plan")
            )
            business_advisories = _plan_business_contract_advisories(
                str(self.store.get_run(run_id).query or ""), plan
            )
            result = await self.sap_read.validate_plan(plan, str(arguments.get("query") or ""))
            normalized_plan = result.get("normalized_plan")
            if result.get("ok") and isinstance(normalized_plan, dict):
                from .runtime_query_contract import preserve_grounding
                preserve_grounding(plan, normalized_plan)
                if run_id in self._read_scopes:
                    self._read_scopes[run_id].check(normalized_plan)
            response = {
                **result,
                "validated_plan": (
                    normalized_plan if isinstance(normalized_plan, dict) else plan
                ) if result.get("ok") else None,
            }
            if business_advisories:
                response["advisories"] = business_advisories
            return response
        if tool_name == "sap_query_execute":
            plan = self.normalizer.normalize_plan(
                _require_object(arguments.get("plan"), "plan")
            )
            business_advisories = _plan_business_contract_advisories(
                str(self.store.get_run(run_id).query or ""), plan
            )
            validation = await self.sap_read.validate_plan(plan, str(arguments.get("query") or ""))
            if validation.get("ok") is not True:
                return {"ok": False, "code": "free_query_plan_rejected", "validation": validation}
            normalized_plan = validation.get("normalized_plan")
            if isinstance(normalized_plan, dict):
                from .runtime_query_contract import preserve_grounding
                preserve_grounding(plan, normalized_plan)
                plan = normalized_plan
            if run_id in self._read_scopes:
                self._read_scopes[run_id].check(plan)
            sample_context = self._sample_contexts.get(run_id)
            if sample_context is not None:
                # Recheck the Provider-normalized plan, not only Runtime input.
                sample_context.check_plan(plan)
                sample_context.check_stable_keys(plan)
            raw = await self.sap_read.execute_plan(
                plan,
                str(arguments.get("query") or ""),
                conversation_id=self.store.get_run(run_id).thread_id,
            )
            if sample_context is not None:
                raw = sample_context.project_evidence(raw, plan=plan)
            if run_id in self._read_scopes and not raw.get("rows_redacted") and not raw.get("restricted_artifact_ref"):
                self._read_scopes[run_id].remember(_extract_rows(raw))
            evidence_ref = self._save_evidence(run_id, "sap_live", raw)
            if sample_context is not None:
                sample_context.remember(evidence_ref, plan=plan)
            response = {
                "ok": raw.get("ok", True),
                "source_type": "sap_live",
                "claim_scope": "customer_business_fact",
                "evidence_ref": evidence_ref,
                "source_complete": _source_complete(raw),
                "row_count": _row_count(raw),
                "preview": _bounded_preview(raw),
                "odata_version": plan.get("odata_version"),
            }
            if business_advisories:
                response["advisories"] = business_advisories
            return response
        if tool_name == "sap_evidence_read":
            raw, meta = self._read_evidence(run_id, str(arguments.get("evidence_ref") or ""))
            if raw.get("rows_redacted") is True or raw.get("restricted_artifact_ref"):
                return {**meta, "ok": False, "code": "evidence_rows_restricted", "restricted": True,
                        "evidence_ref": str(arguments.get("evidence_ref") or ""),
                        "row_count": _row_count(raw),
                        "message": "These evidence rows are restricted. This is not an empty SAP result."}
            offset = max(int(arguments.get("offset") or 0), 0)
            limit = min(max(int(arguments.get("limit") or 100), 1), 200)
            rows = _extract_rows(raw)
            fields = [str(item) for item in arguments.get("fields") or []]
            page = rows[offset : offset + limit]
            if fields:
                page = [
                    {name: row.get(name) for name in fields if name in row}
                    for row in page
                    if isinstance(row, dict)
                ]
            return {
                "ok": True,
                **meta,
                "offset": offset,
                "limit": limit,
                "rows": _safe_public(page, preserve_rows=True),
                "has_more": offset + limit < len(rows),
                "row_count": len(rows),
            }
        if tool_name == "sap_evidence_assess":
            return self._assess_evidence(run_id, arguments)
        if tool_name == "sap_inventory_fifo_assess":
            return self._assess_inventory_fifo(run_id, arguments)
        if tool_name == "sap_month_end_status_assess":
            return self._assess_month_end_status(run_id, arguments)
        if tool_name == "sap_skill_execute":
            return await self._execute_skill(run_id, arguments)
        if tool_name == "sap_final_report_validate":
            return self._validate_report(run_id, arguments)
        if tool_name == "tool_discovery_search":
            self.store.append_event(run_id, "tool_discovery_started", {"query": arguments.get("query")})
            result = await self.tool_gateway.search(
                run_id,
                query=str(arguments.get("query") or ""),
                capability=str(arguments.get("capability") or ""),
                manifest_url=str(arguments.get("manifest_url") or "") or None,
            )
            self.store.save_harness_tool_candidates(
                run_id, self.tool_gateway.snapshot(run_id)
            )
            self.store.append_event(
                run_id, "tool_discovery_completed", {"count": len(result.get("candidates") or [])}
            )
            return result
        if tool_name == "tool_discovery_inspect":
            return self.tool_gateway.inspect(run_id, str(arguments.get("candidate_id") or ""))
        if tool_name == "tool_discovery_activate":
            candidate_id = str(arguments.get("candidate_id") or "")
            try:
                result = self.tool_gateway.activate(run_id, candidate_id)
            except ToolAdmissionError as exc:
                self.store.append_event(
                    run_id,
                    "tool_admission_rejected",
                    {"candidate_id": candidate_id, "code": exc.code, "reason": str(exc)},
                )
                raise
            self.store.append_event(
                run_id, "tool_admission_passed", {"candidate_id": candidate_id}
            )
            self.store.save_harness_tool_candidates(
                run_id, self.tool_gateway.snapshot(run_id)
            )
            return result
        if tool_name in {"external_tool_execute", "safe_compute"}:
            candidate_id = (
                "builtin.safe-compute.v1"
                if tool_name == "safe_compute"
                else str(arguments.get("candidate_id") or "")
            )
            if tool_name == "safe_compute":
                try:
                    self.tool_gateway.activate(run_id, candidate_id)
                except ToolAdmissionError:
                    await self.tool_gateway.search(run_id, query="safe compute")
                    self.tool_gateway.activate(run_id, candidate_id)
                self.store.save_harness_tool_candidates(
                    run_id, self.tool_gateway.snapshot(run_id)
                )
                operation_id = "evaluate"
                parameters = {
                    "language": arguments.get("language"),
                    "code": arguments.get("code"),
                    "inputs": arguments.get("inputs") or {},
                }
            else:
                operation_id = str(arguments.get("operation_id") or "")
                parameters = _require_object(arguments.get("parameters") or {}, "parameters")
            self.store.append_event(
                run_id,
                "external_tool_started",
                {"candidate_id": candidate_id, "operation_id": operation_id},
            )
            result = await self.tool_gateway.execute(
                run_id,
                candidate_id=candidate_id,
                operation_id=operation_id,
                parameters=parameters,
            )
            self.store.append_event(
                run_id,
                "external_tool_completed",
                {"candidate_id": candidate_id, "operation_id": operation_id},
            )
            return result
        raise ToolAdmissionError("The requested tool is not registered.", code="unregistered_tool_rejected")

    def snapshot(self, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        calls = self.store.list_harness_tool_calls(run_id)
        public_calls = [
            {
                "call_id": call["call_id"],
                "tool": call["tool_name"],
                "status": call["status"],
                "input": call["safe_input"],
                "evidence_ref": call["evidence_ref"],
                "created_at": call["created_at"],
                "completed_at": call["completed_at"],
            }
            for call in calls
        ]
        evidence: list[dict[str, Any]] = []
        for call in calls:
            output = call.get("output") or {}
            if output.get("evidence_ref") and call.get("tool_name") != "sap_evidence_read":
                evidence.append(
                    {
                        "evidence_ref": output["evidence_ref"],
                        "source_type": output.get("source_type"),
                        "claim_scope": output.get("claim_scope"),
                        "source_complete": output.get("source_complete", False),
                        "row_count": output.get("row_count"),
                        "call_id": call["call_id"],
                    }
                )
        return public_calls, evidence

    def _save_evidence(self, run_id: str, source_type: str, raw: dict[str, Any]) -> str:
        self._ensure_writable(run_id)
        encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        evidence_ref = f"ev_{hashlib.sha256(encoded).hexdigest()[:24]}"
        directory = self.settings.data_root / "harness" / run_id / "evidence"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{evidence_ref}.json"
        if not path.exists():
            path.write_bytes(encoded)
        meta = {
            "source_type": source_type,
            "claim_scope": (
                "customer_business_fact" if source_type in {"sap_live", "sap_skill"} else "diagnostic"
            ),
            "source_complete": _source_complete(raw),
        }
        (directory / f"{evidence_ref}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        return evidence_ref

    def _read_evidence(self, run_id: str, evidence_ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not re.fullmatch(r"ev_[0-9a-f]{24}", evidence_ref):
            raise ToolAdmissionError("Invalid evidence reference.", code="evidence_ref_invalid")
        directory = self.settings.data_root / "harness" / run_id / "evidence"
        path = directory / f"{evidence_ref}.json"
        meta_path = directory / f"{evidence_ref}.meta.json"
        if not path.is_file() or not meta_path.is_file():
            raise ToolAdmissionError("Evidence reference is not available for this run.")
        return json.loads(path.read_text(encoding="utf-8")), json.loads(
            meta_path.read_text(encoding="utf-8")
        )

    def _assess_evidence(self, run_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        references = [str(item) for item in arguments.get("evidence_refs") or []]
        missing = [str(item) for item in arguments.get("missing_evidence") or [] if str(item)]
        skill_id = str(arguments.get("skill_id") or "sap-adt-table-export").strip()
        skill_input = arguments.get("skill_input")
        if skill_input is not None and not isinstance(skill_input, dict):
            raise ToolAdmissionError(
                "skill_input must be an object.", code="skill_input_invalid"
            )
        metadata: list[dict[str, Any]] = []
        for reference in references:
            _raw, meta = self._read_evidence(run_id, reference)
            metadata.append(meta)
        calls = self.store.list_harness_tool_calls(run_id)
        attempted = {call["tool_name"] for call in calls}
        prerequisite = {"sap_catalog_search", "sap_schema_get", "sap_query_validate"}
        complete = bool(metadata) and all(item.get("source_complete") is True for item in metadata)
        needs_skill = bool(missing) or not complete
        gap_token: str | None = None
        skill_input_hash = ""
        expires_at_epoch = 0
        query_open = self.review_deadline(run_id)["deadline_phase"] == "querying"
        if needs_skill and prerequisite.issubset(attempted) and query_open:
            skill_contract = self._approved_skill(skill_id)
            if skill_id != "sap-adt-table-export" and skill_input is None:
                raise ToolAdmissionError(
                    "The exact Skill input is required before issuing a non-ADT gap token.",
                    code="skill_input_required",
                )
            if skill_input is not None:
                try:
                    self.skills.validate_input(skill_id, skill_input)
                except Exception as exc:
                    return {"ok": False, "code": "skill_input_invalid",
                            "message": "Use the exact approved input schema; no Skill was executed.",
                            "skill_contract": _public_skill_contract(skill_contract)}
                skill_input_hash = _json_fingerprint(skill_input)
            gap_token = secrets.token_urlsafe(24)
            expires_at_epoch = int(time.time()) + max(0, self.budget_snapshot(run_id)["query_seconds_granted"] - self._elapsed_seconds(run_id))
            self._gap_tokens[_capability_fingerprint(gap_token)] = {
                "run_id": run_id,
                "skill_id": skill_id,
                "skill_input_hash": skill_input_hash,
                "missing_evidence": missing or ["source_completeness"],
                "used": False,
                "expires_at_epoch": expires_at_epoch,
            }
        result = {
            "ok": True,
            "source_complete": complete and not missing,
            "missing_evidence": missing,
            "skill_eligible": gap_token is not None,
            "adt_eligible": gap_token is not None and skill_id == "sap-adt-table-export",
            "skill_id": skill_id,
            "skill_input_hash": skill_input_hash or None,
            "gap_token": gap_token,
            "expires_at_epoch": expires_at_epoch or None,
            "reason": (
                f"Required OData evidence is incomplete and the deterministic prerequisite gate "
                f"approved the read-only Skill {skill_id}."
                if gap_token
                else "No new external reads are allowed during finalization." if not query_open
                else "A Skill is not required or the OData-first prerequisite gate has not passed."
            ),
        }
        self.store.append_event(
            run_id,
            "evidence_gap_assessed",
            {key: value for key, value in result.items() if key != "gap_token"},
        )
        return result

    def _approved_skill(self, skill_id: str) -> dict[str, Any]:
        try:
            skill = self.skills.get(skill_id)
        except Exception as exc:
            raise ToolAdmissionError(
                "The requested Skill is not in the platform-approved catalog.",
                code="skill_not_approved",
            ) from exc
        if (
            skill.get("read_only") is not True
            or skill.get("validated") is not True
            or skill.get("available") is not True
        ):
            raise ToolAdmissionError(
                "The requested Skill is not approved and available for read-only automation.",
                code="skill_not_approved",
            )
        return skill

    def _assess_month_end_status(
        self, run_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        company_code = str(arguments.get("company_code") or "").strip()
        try:
            fiscal_year = int(arguments.get("fiscal_year"))
            period = int(arguments.get("period"))
            as_of = date.fromisoformat(str(arguments.get("as_of") or ""))
        except (TypeError, ValueError):
            return {"ok": False, "code": "month_end_status_input_invalid"}

        reference_names = {
            "company": "company_evidence_ref",
            "T001": "t001_evidence_ref",
            "T001B": "t001b_evidence_ref",
            "TABA": "taba_evidence_ref",
            "MARV": "marv_evidence_ref",
        }
        raw_by_source: dict[str, dict[str, Any]] = {}
        rows_by_source: dict[str, list[dict[str, Any]]] = {}
        evidence_refs: list[str] = []
        try:
            for source, argument_name in reference_names.items():
                reference = str(arguments.get(argument_name) or "")
                raw, meta = self._read_evidence(run_id, reference)
                if meta.get("source_complete") is not True or _source_complete(raw) is not True:
                    return {
                        "ok": False,
                        "code": "month_end_status_source_incomplete",
                        "source": source,
                    }
                raw_by_source[source] = raw
                rows_by_source[source] = self._internal_evidence_rows(run_id, raw)
                evidence_refs.append(reference)
        except ToolAdmissionError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}

        company_rows = [
            row
            for row in rows_by_source["company"]
            if str(row.get("CompanyCode") or "").strip() == company_code
        ]
        variants = {
            str(row.get("FiscalYearVariant") or "").strip().upper()
            for row in company_rows
            if str(row.get("FiscalYearVariant") or "").strip()
        }
        if len(company_rows) != 1 or len(variants) != 1:
            return {"ok": False, "code": "month_end_company_metadata_unresolved"}
        fiscal_year_variant = next(iter(variants))

        for object_name in ("T001", "T001B", "TABA", "MARV"):
            payload = raw_by_source[object_name]
            if (
                payload.get("skill_id") != "sap-adt-table-export"
                or payload.get("read_only") is not True
                or payload.get("validated") is not True
                or str(payload.get("status") or "") != "complete"
                or str((payload.get("scope") or {}).get("object") or "").upper()
                != object_name
            ):
                return {
                    "ok": False,
                    "code": "month_end_status_adt_contract_invalid",
                    "source": object_name,
                }
        if not all(
            _adt_scope_has_exact_value(raw_by_source[name], "BUKRS", company_code)
            for name in ("T001", "TABA", "MARV")
        ):
            return {"ok": False, "code": "month_end_status_company_scope_invalid"}

        try:
            assessed = assess_current_month_end_status_evidence(
                company_code=company_code,
                fiscal_year=fiscal_year,
                period=period,
                as_of=as_of,
                fiscal_year_variant=fiscal_year_variant,
                t001_rows=rows_by_source["T001"],
                t001b_rows=rows_by_source["T001B"],
                taba_rows=rows_by_source["TABA"],
                marv_rows=rows_by_source["MARV"],
            )
        except ValueError as exc:
            return {"ok": False, "code": str(exc)}
        period_variant = str(assessed.get("posting_period_variant") or "")
        if not _adt_scope_has_exact_value(
            raw_by_source["T001B"], "BUKRS", period_variant
        ):
            return {"ok": False, "code": "month_end_status_period_scope_invalid"}
        return {
            **assessed,
            "evidence_refs": evidence_refs,
            "privacy_projection": "derived_status_only",
        }

    def _internal_evidence_rows(
        self, run_id: str, raw: dict[str, Any]
    ) -> list[dict[str, Any]]:
        explicit_rows = raw.get("rows")
        if isinstance(explicit_rows, list):
            return [dict(row) for row in explicit_rows if isinstance(row, dict)]
        restricted = raw.get("restricted_artifact_ref")
        if isinstance(restricted, dict) and restricted.get("artifact_id"):
            return self.restricted_artifacts.rows(
                run_id, str(restricted["artifact_id"])
            )
        rows = _extract_rows(raw)
        if rows:
            return [dict(row) for row in rows if isinstance(row, dict)]
        if _row_count(raw) == 0:
            return []
        raise ToolAdmissionError(
            "Restricted ADT rows are unavailable for deterministic assessment.",
            code="month_end_status_restricted_rows_unavailable",
        )

    def _assess_inventory_fifo(
        self, run_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        identifiers = {
            name: str(arguments.get(name) or "").strip()
            for name in ("material", "plant", "storage_location")
        }
        if any(not re.fullmatch(r"[0-9A-Za-z_-]+", value) for value in identifiers.values()):
            return {"ok": False, "code": "inventory_fifo_input_invalid"}
        try:
            snapshot = date.fromisoformat(str(arguments.get("snapshot_date") or ""))
            slow_days = int(arguments.get("slow_moving_days"))
            obsolete_days = int(arguments.get("obsolete_days"))
            expiry_days = int(arguments.get("expiry_days"))
        except (TypeError, ValueError):
            return {"ok": False, "code": "inventory_fifo_input_invalid"}
        if slow_days < 1 or obsolete_days <= slow_days or expiry_days < 1:
            return {"ok": False, "code": "inventory_fifo_threshold_invalid"}

        reference_names = (
            "stock_initial_evidence_ref",
            "stock_confirmation_evidence_ref",
            "movement_item_evidence_ref",
            "movement_header_evidence_ref",
        )
        references = {name: str(arguments.get(name) or "") for name in reference_names}
        batch_reference = str(arguments.get("batch_evidence_ref") or "")
        raw: dict[str, dict[str, Any]] = {}
        metadata: dict[str, dict[str, Any]] = {}
        try:
            for name, reference in references.items():
                raw[name], metadata[name] = self._read_evidence(run_id, reference)
            if batch_reference:
                raw["batch_evidence_ref"], metadata["batch_evidence_ref"] = self._read_evidence(
                    run_id, batch_reference
                )
        except ToolAdmissionError as exc:
            return {"ok": False, "code": exc.code, "message": str(exc)}
        if any(item.get("source_complete") is not True for item in metadata.values()):
            return {"ok": False, "code": "inventory_fifo_source_incomplete"}

        calls = [
            call
            for call in self.store.list_harness_tool_calls(run_id)
            if call.get("tool_name") == "sap_query_execute"
            and call.get("status") == "completed"
        ]
        calls_by_reference: dict[str, list[dict[str, Any]]] = {}
        for call in calls:
            reference = str(call.get("evidence_ref") or "")
            if reference:
                calls_by_reference.setdefault(reference, []).append(call)
        stock_references = {
            references["stock_initial_evidence_ref"],
            references["stock_confirmation_evidence_ref"],
        }
        stock_calls = [
            call
            for reference in stock_references
            for call in calls_by_reference.get(reference, [])
            if _plan_contains_entity(
                (call.get("safe_input") or {}).get("plan"), "A_MatlStkInAcctMod"
            )
        ]
        if len({str(call.get("call_id") or "") for call in stock_calls}) < 2:
            return {"ok": False, "code": "stock_confirmation_evidence"}
        for call in stock_calls:
            plan = (call.get("safe_input") or {}).get("plan")
            issue = _inventory_plan_scope_issue(
                plan, require_movement=False, identifiers=identifiers
            )
            if issue:
                return {"ok": False, **issue}
        item_calls = calls_by_reference.get(references["movement_item_evidence_ref"], [])
        header_calls = calls_by_reference.get(references["movement_header_evidence_ref"], [])
        valid_item_call = next(
            (
                call
                for call in item_calls
                if _plan_contains_entity(
                    (call.get("safe_input") or {}).get("plan"),
                    "A_MaterialDocumentItem",
                )
                and _inventory_plan_scope_issue(
                    (call.get("safe_input") or {}).get("plan"),
                    require_movement=True,
                    identifiers=identifiers,
                )
                is None
            ),
            None,
        )
        valid_header_call = next(
            (
                call
                for call in header_calls
                if _inventory_header_plan_issue(
                    (call.get("safe_input") or {}).get("plan")
                )
                is None
            ),
            None,
        )
        if valid_item_call is None or valid_header_call is None:
            return {"ok": False, "code": "movement_evidence"}
        if batch_reference:
            valid_batch_call = next(
                (
                    call
                    for call in calls_by_reference.get(batch_reference, [])
                    if _plan_contains_entity(
                        (call.get("safe_input") or {}).get("plan"), "Batch"
                    )
                    and _inventory_batch_plan_issue(
                        (call.get("safe_input") or {}).get("plan"),
                        identifiers=identifiers,
                    )
                    is None
                ),
                None,
            )
            if valid_batch_call is None:
                return {
                    "ok": False,
                    "code": "inventory_batch_material_scope_required",
                }

        initial_rows = _rows_with_fields(
            raw["stock_initial_evidence_ref"],
            {"Material", "Plant", "StorageLocation", "MatlWrhsStkQtyInMatlBaseUnit"},
        )
        confirmation_rows = _rows_with_fields(
            raw["stock_confirmation_evidence_ref"],
            {"Material", "Plant", "StorageLocation", "MatlWrhsStkQtyInMatlBaseUnit"},
        )
        movement_items = _rows_with_fields(
            raw["movement_item_evidence_ref"],
            {
                "MaterialDocumentYear",
                "MaterialDocument",
                "MaterialDocumentItem",
                "QuantityInBaseUnit",
                "DebitCreditCode",
            },
        )
        movement_headers = _rows_with_fields(
            raw["movement_header_evidence_ref"],
            {"MaterialDocumentYear", "MaterialDocument", "PostingDate", "CreationTime"},
        )
        batch_rows = (
            _rows_with_fields(
                raw["batch_evidence_ref"],
                {"Material", "Batch"},
            )
            if batch_reference
            else []
        )
        evidence = {
            "stock_initial": _complete_payload(initial_rows),
            "stock_confirmation": _complete_payload(confirmation_rows),
            "movement": _complete_payload(
                movement_items,
                step_results={
                    "movement_date_items": {
                        "results": movement_items,
                        "source_complete": True,
                    },
                    "movement_date_headers": {
                        "results": movement_headers,
                        "source_complete": True,
                    },
                },
            ),
            "batch_expiry": _complete_payload(batch_rows),
        }
        rule_result = evaluate_business_agent(
            {
                "agent_id": "inventory-health-balancing",
                "run_input": {
                    **identifiers,
                    "slow_moving_days": slow_days,
                    "obsolete_days": obsolete_days,
                    "expiry_days": expiry_days,
                },
                "window": {
                    "snapshot_date": snapshot.isoformat(),
                    "check_slow_moving": True,
                    "check_obsolete": True,
                    "check_expiry": True,
                    "movement_check_requested": True,
                    "selected_checks": ["slow_moving", "obsolete", "expiry"],
                },
                "assessment": {
                    "api_complete": {
                        "stock": True,
                        "movement": True,
                        "batch_expiry": True,
                    },
                    "needs_adt": {
                        "stock": False,
                        "movement": False,
                        "batch_expiry": False,
                    },
                },
                "evidence": evidence,
                "fallbacks": {},
                "requested": {
                    "stock": True,
                    "movement": True,
                    "batch_expiry": True,
                },
                "known_gaps": [],
            }
        )
        output = rule_result.get("workflow_output") or {}
        public_result = {
            key: output.get(key)
            for key in (
                "snapshot_date",
                "material",
                "plant",
                "storage_location",
                "current_unrestricted_stock",
                "unit",
                "aging_method",
                "aging_complete",
                "last_movement_activity_date",
                "days_since_last_movement_activity",
                "oldest_remaining_layer_date",
                "oldest_remaining_layer_age_days",
                "classified_stock_quantity",
                "unclassified_stock_quantity",
                "below_threshold_stock_quantity",
                "slow_moving_only_stock_quantity",
                "obsolete_stock_quantity",
                "slow_moving_status",
                "obsolete_status",
                "expiry_status",
                "expiry_candidate_count",
                "expired_batch_count",
                "expiring_batch_count",
                "missing_expiry_date_batch_count",
                "expiry_evidence_complete",
                "batch_expiry_details",
                "business_status",
                "source_complete",
                "evidence_complete",
                "aging_buckets",
                "remaining_fifo_layers",
            )
        }
        record_fields = (
            "snapshot_date",
            "material",
            "plant",
            "storage_location",
            "current_unrestricted_stock",
            "unit",
            "aging_method",
            "aging_complete",
            "last_movement_activity_date",
            "oldest_remaining_layer_date",
            "slow_moving_status",
            "obsolete_status",
            "expiry_status",
            "expiry_evidence_complete",
            "business_status",
            "source_complete",
            "evidence_complete",
        )
        metric_fields = (
            "current_unrestricted_stock",
            "days_since_last_movement_activity",
            "oldest_remaining_layer_age_days",
            "classified_stock_quantity",
            "unclassified_stock_quantity",
            "below_threshold_stock_quantity",
            "slow_moving_only_stock_quantity",
            "obsolete_stock_quantity",
            "expiry_candidate_count",
            "expired_batch_count",
            "expiring_batch_count",
            "missing_expiry_date_batch_count",
        )
        assessment_valid = output.get("aging_complete") is True
        public_rule_result = {
            "rule_id": "inventory_health_fifo_harness_v1",
            "status": rule_result.get("status"),
            "assessment_valid": assessment_valid,
            "business_status": output.get("business_status"),
            "business_complete": rule_result.get("business_complete") is True,
            "missing_evidence": list(rule_result.get("missing_evidence") or []),
            "business_report": {
                "records": [
                    {field: public_result.get(field) for field in record_fields}
                ],
                "metrics": [
                    {"id": field, "value": public_result.get(field)}
                    for field in metric_fields
                ],
                "limitations": list(rule_result.get("missing_evidence") or []),
                "missing_evidence": list(rule_result.get("missing_evidence") or []),
                "source_complete": output.get("source_complete") is True,
                "evidence_complete": output.get("evidence_complete") is True,
            },
            "workflow_output": public_result,
        }
        return {
            # A deterministic FIFO assessment can be valid even when the
            # customer evidence is incomplete (for example, one positive-stock
            # batch has no expiration date).  `ok` describes execution and
            # contract validity; `business_complete` remains the separate,
            # fail-closed evidence-completeness result.
            "ok": assessment_valid,
            "assessment_valid": assessment_valid,
            "aging_complete": output.get("aging_complete") is True,
            "status": rule_result.get("status"),
            "source_complete": rule_result.get("business_report", {}).get("source_complete"),
            "business_complete": rule_result.get("business_complete"),
            "missing_evidence": rule_result.get("missing_evidence") or [],
            "evidence_refs": list(
                dict.fromkeys(
                    reference
                    for reference in [*references.values(), batch_reference]
                    if reference
                )
            ),
            "result": public_result,
            "rule_result": public_rule_result,
        }

    async def _execute_skill(self, run_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.review_deadline(run_id)["deadline_phase"] != "querying":
            raise ToolAdmissionError("External reads are closed for this run.", code="harness_finalization_only")
        skill_id = str(arguments.get("skill_id") or "")
        token = str(arguments.get("gap_token") or "")
        gap = self._gap_tokens.get(_capability_fingerprint(token))
        if (
            not gap
            or gap["run_id"] != run_id
            or gap["skill_id"] != skill_id
            or gap["used"]
        ):
            raise ToolAdmissionError("A valid single-use evidence gap token is required.", code="gap_token_invalid")
        expires_at_epoch = int(gap.get("expires_at_epoch") or 0)
        if expires_at_epoch and int(time.time()) > expires_at_epoch:
            raise ToolAdmissionError(
                "The single-use evidence gap token has expired.", code="gap_token_expired"
            )
        self._approved_skill(skill_id)
        payload = _require_object(arguments.get("input") or {}, "input")
        expected_input_hash = str(gap.get("skill_input_hash") or "")
        if expected_input_hash and not hmac.compare_digest(
            expected_input_hash, _json_fingerprint(payload)
        ):
            raise ToolAdmissionError(
                "The Skill input does not match the input authorized by the gap token.",
                code="gap_token_input_mismatch",
            )
        execution_payload = dict(payload)
        if skill_id == "sap-bank-receipt-evidence":
            try:
                binding = self.store.get_run_secret(run_id, "receipt_reference")
            except KeyError:
                binding = None
            if binding is not None:
                execution_payload["receipt_reference"] = (
                    self.restricted_artifacts.protector.reveal_run_secret(
                        run_id=run_id,
                        field="receipt_reference",
                        protected_value=binding["protected_value"],
                    )
                )
        # Contract errors happen before SAP is contacted and therefore do not
        # consume the single-use execution capability. Once validation passes,
        # the token is burned before starting the Skill subprocess.
        try:
            self.skills.validate_input(skill_id, execution_payload)
        except Exception as exc:
            raise ToolAdmissionError(
                "The requested Skill input does not match its approved contract.",
                code="skill_input_invalid",
            ) from exc
        gap["used"] = True
        output = await self.skills.execute(skill_id, execution_payload)
        self._ensure_writable(run_id)
        public_output, _private_refs = self.restricted_artifacts.materialize_skill_output(
            run_id=run_id,
            skill_id=skill_id,
            output=output,
            skill_contract=self.skills.get(skill_id),
        )
        sample_context = self._sample_contexts.get(run_id)
        if sample_context is not None:
            public_output = sample_context.project_evidence(public_output)
        evidence_ref = self._save_evidence(run_id, "sap_skill", public_output)
        if sample_context is not None:
            sample_context.remember(evidence_ref, skill_id=skill_id)
        return {
            "ok": public_output.get("ok", public_output.get("status") == "complete"),
            "source_type": "sap_skill",
            "claim_scope": "customer_business_fact",
            "evidence_ref": evidence_ref,
            "source_complete": _source_complete(public_output),
            "row_count": _row_count(public_output),
            "preview": _bounded_preview(public_output),
            "status": public_output.get("status"),
        }

    def _validate_report(self, run_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_writable(run_id)
        report = _require_object(arguments.get("report"), "report")
        presentation: RunPresentation | None = None
        issues: list[dict[str, Any]] = []
        try:
            presentation = RunPresentation.model_validate(report)
        except ValidationError as exc:
            for item in exc.errors(include_url=False, include_input=False)[:50]:
                path = "/report" + "".join(f"/{part}" for part in item.get("loc") or [])
                issues.append({
                    "code": "acceptance_report_schema_invalid",
                    "path": path,
                    "constraint": str(item.get("type") or "schema"),
                    "message": "The report field does not satisfy the declared presentation schema.",
                })
        known = {
            item["evidence_ref"]: item
            for item in self.snapshot(run_id)[1]
            if item.get("evidence_ref")
        }
        state = self.store.get_harness_state(run_id)
        spec = state.get("acceptance_spec")
        projection = arguments.get("acceptance_projection")
        if spec is not None:
            issues.extend(validate_projection(spec, projection, known))
            if not issues:
                issues.extend(visible_projection_issues(spec, projection, report))
        elif projection is not None:
            issues.append({"code": "acceptance_projection_not_requested"})
        if "inventory_fifo" in set(state.get("required_assessments") or []):
            fifo_calls = [
                call
                for call in self.store.list_harness_tool_calls(run_id)
                if call.get("tool_name") == "sap_inventory_fifo_assess"
                and call.get("status") == "completed"
                and isinstance(call.get("output"), dict)
                and call["output"].get("ok") is True
                and call["output"].get("assessment_valid") is True
            ]
            if not fifo_calls:
                issues.append({"code": "inventory_fifo_assessment_required"})
        for block_index, block in enumerate(presentation.blocks if presentation else []):
            references = list(block.evidence_refs)
            references.extend(ref for entry in block.entries for ref in entry.evidence_refs)
            references.extend(ref for metric in block.metrics for ref in metric.evidence_refs)
            references.extend(ref for row in block.rows for ref in row.evidence_refs)
            references = list(dict.fromkeys(references))
            for reference in references:
                if reference not in known:
                    issues.append(
                        {
                            "code": "unknown_evidence_ref",
                            "block_index": block_index,
                            "evidence_ref": reference,
                        }
                    )
            if block.claim_scope == "customer_business_fact":
                refs = [known.get(reference) for reference in references]
                if not refs or any(
                    item is None
                    or item.get("source_type") not in {"sap_live", "sap_skill"}
                    or (
                        item.get("source_type") == "sap_skill"
                        and item.get("source_complete") is not True
                    )
                    for item in refs
                ):
                    issues.append(
                        {"code": "customer_fact_requires_sap_evidence", "block_index": block_index}
                    )
        report_hash = (
            _presentation_hash(presentation)
            if presentation is not None
            else _json_fingerprint({"report": report})
        )
        output: dict[str, Any] = {
            "ok": not issues,
            "validation_issues": issues,
            "report_hash": report_hash,
            "validation_ref": f"validation_{report_hash.removeprefix('sha256:')[:24]}",
            **({"code": "acceptance_report_validation_failed"} if issues else {}),
            # Persist the exact validated object with the tool call. It is
            # stripped from the response returned to Codex below.
            "_validated_report": presentation.model_dump(mode="json") if presentation else None,
            "_validated_acceptance_projection": projection if spec is not None and not issues else None,
        }
        if issues and spec is not None:
            progress = _acceptance_validation_progress(
                report=report,
                projection=projection,
                known=known,
                assessment_calls=self.store.list_harness_tool_calls(run_id),
            )
            issue_digest = _json_fingerprint({"issues": issues})
            progress_digest = _json_fingerprint(progress)
            previous = state.get("acceptance_validation_failure") or {}
            count = (
                int(previous.get("consecutive") or 0) + 1
                if previous.get("issue_digest") == issue_digest
                and previous.get("progress_digest") == progress_digest
                else 1
            )
            failure = {
                "issue_digest": issue_digest,
                "progress_digest": progress_digest,
                "consecutive": count,
                "validation_issues": _safe_public(issues),
            }
            if count >= 2:
                failure["terminal"] = True
                output["terminal_validation_failure"] = True
            self.store.update_harness_state(
                run_id, {"acceptance_validation_failure": failure}
            )
        elif not issues and spec is not None:
            self.store.update_harness_state(
                run_id, {"acceptance_validation_failure": None}
            )
        return output


class CodexHarnessController:
    def __init__(self, settings: Settings, store: RunStore, broker: HarnessToolBroker) -> None:
        self.settings = settings
        self.store = store
        self.broker = broker
        self._active_turns: dict[str, Any] = {}

    async def steer(self, run_id: str, value: str) -> bool:
        turn = self._active_turns.get(run_id)
        if turn is None:
            return False
        await turn.steer(value)
        self.store.append_event(run_id, "input_received", {"input": value, "mode": "steer"})
        return True

    async def interrupt(self, run_id: str) -> bool:
        turn = self._active_turns.get(run_id)
        if turn is None:
            return False
        await turn.interrupt()
        self.store.append_event(run_id, "harness_interrupted", {})
        return True

    async def _monitor_deadline(self, run_id: str, turn: Any) -> None:
        from .runtime_harness import monitor_deadline
        await monitor_deadline(self, run_id, turn)

    async def run(self, run_id, query, thread_id, model=None, reasoning_effort=None):
        from .runtime_harness import run
        from .codex_driver import CodexHarnessDriver
        return await run(self, CodexHarnessDriver(self), run_id, query, thread_id, model, reasoning_effort)




def _unknown_recovered_call(existing: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "tool_call_recovery_unknown",
        "message": (
            "A previous process recorded this call without a durable completed result. "
            "The platform will not repeat a possibly executed SAP request automatically."
        ),
        "call_id": existing["call_id"],
    }


def _persistent_harness_counts(store: RunStore, run_id: str) -> tuple[int, int, int]:
    events = store.events_after(run_id)
    web_search_count = sum(item.type == "web_search_completed" for item in events)
    discovered_tool_count = sum(
        max(0, int(item.data.get("count") or 0))
        for item in events
        if item.type == "tool_discovery_completed"
    )
    activated_tool_count = sum(item.type == "tool_admission_passed" for item in events)
    return web_search_count, discovered_tool_count, activated_tool_count


def _budgeted_tool_call_count(calls: list[dict[str, Any]]) -> int:
    return sum(
        1
        for call in calls
        if (call.get("tool") or call.get("tool_name")) != "sap_final_report_validate"
    )


def _safe_codex(
    settings: Settings, run_id: str, capability: str, workspace: Path, *, allow_web: bool = True, full_access: bool = False
) -> Any:
    from codex_cli_bin import bundled_codex_path
    from openai_codex import AsyncCodex
    from openai_codex.client import CodexConfig

    _validate_internal_api_url(settings.internal_api_url)
    python = sys.executable
    args = [str(bundled_codex_path())]
    if allow_web:
        args.append("--search")
    else:
        args.extend(["--config", 'web_search="disabled"'])
    for feature in (
        "shell_tool",
        "apply_patch_streaming_events",
        "browser_use",
        "computer_use",
        "image_generation",
        "multi_agent",
        "plugins",
        "apps",
        "hooks",
    ):
        args.extend(["--disable", feature])
    overrides = _mcp_overrides(settings, run_id, capability, python, allow_discovery=allow_web)
    for item in overrides:
        args.extend(["--config", item])
    args.extend(["app-server", "--listen", "stdio://"])
    sanitized_env = _sanitized_codex_env()
    config = CodexConfig(
        launch_args_override=tuple(args),
        cwd=str(workspace),
        env=sanitized_env,
        client_name="sap_business_agents_harness",
        client_title="SAPBusinessAgents Harness",
    )
    codex = AsyncCodex(config=config)
    # The pinned SDK high-level wrapper does not yet surface an approval handler.
    # Install the deny handler before initialization on its wrapped low-level client.
    codex._client._sync._approval_handler = _deny_approval  # type: ignore[attr-defined]
    if full_access:
        from .runtime_execution import configure_client
        return configure_client(codex)
    return codex


def _mcp_overrides(
    settings: Settings, run_id: str, capability: str, python: str, *, allow_discovery: bool = True
) -> list[str]:
    overrides: list[str] = []
    config_path = Path.home() / ".codex" / "config.toml"
    if config_path.is_file():
        try:
            import tomllib

            payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
            for name in (payload.get("mcp_servers") or {}):
                if re.fullmatch(r"[A-Za-z0-9_-]+", str(name)):
                    overrides.append(f"mcp_servers.{name}.enabled=false")
                else:
                    raise RuntimeError("capability_isolation_failed:invalid_mcp_name")
        except (OSError, tomllib.TOMLDecodeError):
            raise RuntimeError("capability_isolation_failed:codex_config_unreadable")
    common_env = {
        "SAPBA_INTERNAL_API_URL": settings.internal_api_url,
        "SAPBA_HARNESS_RUN_ID": run_id,
        "SAPBA_HARNESS_CAPABILITY": capability,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for server_name, mode in (
        ("sap_business_agents", "sap"),
        ("sap_tool_discovery", "tools"),
    ):
        if mode == "tools" and not allow_discovery:
            continue
        overrides.extend(
            [
                f"mcp_servers.{server_name}.command={json.dumps(python)}",
                (
                    f"mcp_servers.{server_name}.args="
                    + json.dumps(
                        ["-m", "sap_business_agents_platform.mcp_server", "--mode", mode]
                    )
                ),
                f"mcp_servers.{server_name}.enabled=true",
            ]
        )
        for key, value in common_env.items():
            overrides.append(
                f"mcp_servers.{server_name}.env.{key}={json.dumps(value)}"
            )
    return overrides


def _validate_internal_api_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("capability_isolation_failed:internal_api_not_loopback")


def _sanitized_codex_env() -> dict[str, str]:
    sanitized: dict[str, str] = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    for key in os.environ:
        upper = key.upper()
        if (
            upper.startswith("SAP_")
            or upper.startswith("SAPBA_SAP")
            or upper.startswith("SAP_ADT_")
            or any(part in upper for part in ("PASSWORD", "SECRET", "API_KEY"))
        ):
            sanitized[key] = ""
    return sanitized


def _deny_approval(method: str, _params: dict[str, Any] | None) -> dict[str, Any]:
    if "requestApproval" in method:
        return {"decision": "decline"}
    return {}


def _approval_mode() -> Any:
    from openai_codex import ApprovalMode

    return ApprovalMode.deny_all


def _sandbox(*, full_access: bool = False) -> Any:
    from openai_codex import Sandbox

    return Sandbox.full_access if full_access else Sandbox.read_only






def _event_item(event: Any) -> tuple[str, dict[str, Any]]:
    if event.method not in {"item/started", "item/completed"}:
        return "", {}
    payload = getattr(event, "payload", None)
    item = getattr(payload, "item", None)
    root = getattr(item, "root", item)
    if root is None or not hasattr(root, "model_dump"):
        return "", {}
    data = root.model_dump(mode="json", by_alias=True)
    return str(data.get("type") or ""), data


def _custom_tool_kind(item: dict[str, Any], *, full_access: bool = False) -> tuple[str, str]:
    if str(item.get("type") or "") != "customToolCall":
        return "", ""
    source = str(item.get("input") or "")
    if full_access and any(marker in source for marker in ("tools.exec_command", "tools.apply_patch", "tools.write_stdin", "tools.view_image")):
        return "engineering", ""
    if any(
        marker in source
        for marker in (
            "tools.exec_command",
            "tools.apply_patch",
            "tools.write_stdin",
            "tools.view_image",
            "tools.image_gen",
            "tools.computer",
            "tools.browser",
        )
    ):
        return "forbidden", ""
    if "tools.web__run" in source:
        topics = re.findall(r"\bq\s*:\s*[\"']([^\"']{1,500})[\"']", source)
        return "web_search", "; ".join(topics[:4])[:1000]
    # Native Web Search is the only allowed App Server custom tool. Every
    # other capability must enter through one of the two run-scoped MCPs.
    return "forbidden", ""


def _public_https_citations(value: Any) -> list[str]:
    candidates: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            candidates.extend(re.findall(r"https://[^\s\"'<>]+", item))

    visit(value)
    accepted: list[str] = []
    for candidate in candidates:
        parsed = urlsplit(candidate.rstrip(".,);]"))
        host = str(parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or host in {"localhost", "host.docker.internal"}
            or host.endswith((".local", ".internal", ".localhost"))
        ):
            continue
        try:
            if not ipaddress.ip_address(host).is_global:
                continue
        except ValueError:
            pass
        public = f"https://{parsed.netloc}{parsed.path or '/'}"
        if public not in accepted:
            accepted.append(public)
    return accepted[:20]


async def _stream_with_timeout(stream: Any, timeout_seconds: int) -> Any:
    """Apply one wall-clock budget to an asynchronous App Server turn stream."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout_seconds)
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError
        try:
            event = await asyncio.wait_for(anext(stream), timeout=remaining)
        except StopAsyncIteration:
            return
        yield event


async def _best_effort_interrupt(turn: Any) -> str | None:
    """Never let cleanup replace the original Harness failure or timeout."""

    try:
        await asyncio.wait_for(turn.interrupt(), timeout=5)
    except TimeoutError:
        return "interrupt_timeout"
    except Exception as exc:  # App Server may already have discarded the turn.
        code = str(getattr(exc, "code", "") or exc.__class__.__name__)
        return code[:100]
    return None


def _completed_turn_error(event: Any) -> tuple[str, str] | None:
    payload = getattr(event, "payload", None)
    turn = getattr(payload, "turn", None)
    status = getattr(turn, "status", None)
    status_value = str(getattr(status, "value", status) or "")
    if status_value.casefold() != "failed":
        return None
    error = getattr(turn, "error", None)
    message = str(getattr(error, "message", "") or "Codex turn failed.")
    code = str(getattr(error, "code", "") or "codex_turn_failed")
    # The upstream error body may contain a JSON envelope. Preserve only the
    # public error code/message, never request data or App Server internals.
    try:
        envelope = json.loads(message)
        detail = envelope.get("error") if isinstance(envelope, dict) else None
        if isinstance(detail, dict):
            code = str(detail.get("code") or detail.get("type") or code)
            message = str(detail.get("message") or message)
    except json.JSONDecodeError:
        pass
    return code[:100], message[:1000]


def _last_agent_message(payload: dict[str, Any]) -> str:
    messages: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "agentMessage" and value.get("text"):
                messages.append(str(value["text"]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return messages[-1] if messages else ""


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolAdmissionError(f"{label} must be an object.", code="tool_input_invalid")
    return value


_SECRET_KEYS = {
    "password",
    "authorization",
    "token",
    "secret",
    "api_key",
    "apikey",
    "sap_base_url",
    "base_url",
    "url",
    "connection_profile",
}


def _capability_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _acceptance_validation_progress(
    *,
    report: dict[str, Any],
    projection: Any,
    known: dict[str, Any],
    assessment_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Hash only validation-relevant output, evidence and assessment progress."""

    blocks = []
    for block in report.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        blocks.append({
            "type": block.get("type"),
            "claim_scope": block.get("claim_scope"),
            "evidence_refs": block.get("evidence_refs") or [],
            "entries": [
                {"value": item.get("value"), "evidence_refs": item.get("evidence_refs") or []}
                for item in block.get("entries") or []
                if isinstance(item, dict)
            ],
            "metrics": [
                {
                    "id": item.get("id"),
                    "value": item.get("value"),
                    "evidence_refs": item.get("evidence_refs") or [],
                }
                for item in block.get("metrics") or []
                if isinstance(item, dict)
            ],
            "columns": [
                item.get("key")
                for item in block.get("columns") or []
                if isinstance(item, dict)
            ],
            "rows": [
                {"values": item.get("values") or [], "evidence_refs": item.get("evidence_refs") or []}
                for item in block.get("rows") or []
                if isinstance(item, dict)
            ],
        })
    assessments = [
        {
            "call_id": call.get("call_id"),
            "status": call.get("status"),
            "ok": (call.get("output") or {}).get("ok"),
            "assessment_valid": (call.get("output") or {}).get("assessment_valid"),
            "evidence_ref": call.get("evidence_ref"),
        }
        for call in assessment_calls
        if call.get("tool_name") == "sap_inventory_fifo_assess"
    ]
    evidence = [
        {
            "evidence_ref": ref,
            "source_type": item.get("source_type"),
            "source_complete": item.get("source_complete"),
        }
        for ref, item in sorted(known.items())
    ]
    return {
        "projection": projection,
        "blocks": blocks,
        "evidence": evidence,
        "assessments": assessments,
    }


def _presentation_hash(value: RunPresentation | dict[str, Any]) -> str:
    presentation = (
        value if isinstance(value, RunPresentation) else RunPresentation.model_validate(value)
    )
    payload = presentation.model_dump(mode="json", exclude={"validation_ref"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _client_tool_output(value: dict[str, Any]) -> dict[str, Any]:
    """Remove control-plane-only values before returning a tool response."""

    return {key: item for key, item in value.items() if not str(key).startswith("_")}


def _stored_tool_output(tool_name: str, value: dict[str, Any]) -> dict[str, Any]:
    """Persist capability tokens only as fingerprints while returning them once to Codex."""

    stored = dict(value)
    token = stored.get("gap_token")
    if tool_name == "sap_evidence_assess" and isinstance(token, str) and token:
        stored["gap_token"] = _capability_fingerprint(token)
    return stored


def _validated_presentation_snapshot(
    validation_ref: str, raw_calls: list[dict[str, Any]]
) -> RunPresentation | None:
    """Resolve an opaque validation reference to its immutable verified report."""

    for call in reversed(raw_calls):
        output = call.get("output")
        if (
            call.get("tool_name") != "sap_final_report_validate"
            or call.get("status") != "completed"
            or not isinstance(output, dict)
            or output.get("ok") is not True
            or output.get("validation_ref") != validation_ref
        ):
            continue
        report = output.get("_validated_report")
        if not isinstance(report, dict):
            # Compatibility with calls written before immutable snapshots were
            # introduced. Small reports remain recoverable from safe_input.
            safe_input = call.get("safe_input")
            report = safe_input.get("report") if isinstance(safe_input, dict) else None
        if not isinstance(report, dict):
            continue
        try:
            presentation = RunPresentation.model_validate(report)
        except Exception:
            continue
        report_hash = _presentation_hash(presentation)
        if (
            output.get("report_hash") == report_hash
            and output.get("validation_ref")
            == f"validation_{report_hash.removeprefix('sha256:')[:24]}"
        ):
            presentation.validation_ref = validation_ref
            return presentation
    return None


def _latest_validated_presentation(raw_calls: list[dict[str, Any]]) -> RunPresentation | None:
    for call in reversed(raw_calls):
        output = call.get("output")
        validation_ref = output.get("validation_ref") if isinstance(output, dict) else None
        if validation_ref:
            recovered = _validated_presentation_snapshot(str(validation_ref), raw_calls)
            if recovered is not None:
                return recovered
    return None


def _latest_assessed_missing_evidence(raw_calls: list[dict[str, Any]]) -> list[str]:
    for call in reversed(raw_calls):
        if call.get("tool_name") != "sap_evidence_assess" or call.get("status") != "completed":
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        values = output.get("missing_evidence")
        if isinstance(values, list):
            return list(dict.fromkeys(str(item) for item in values if str(item)))
    return []


def _effective_missing_evidence(
    payload_missing: Any,
    raw_calls: list[dict[str, Any]],
) -> list[str]:
    """Resolve business gaps using the latest completed control-plane assessment.

    A successful refinement after a gap assessment can supersede the earlier data read,
    but it cannot silently supersede the gap decision itself.  Requiring one final
    assessment prevents both stale false gaps and optimistic model-only gap closure.
    """

    payload_values = [str(item) for item in payload_missing or [] if str(item)]
    latest_assessment_index = -1
    latest_data_index = -1
    assessed_values: list[str] = []
    for index, call in enumerate(raw_calls):
        if call.get("status") != "completed":
            continue
        tool_name = str(call.get("tool_name") or call.get("tool") or "")
        if tool_name in {"sap_query_execute", "sap_skill_execute"}:
            latest_data_index = index
        elif tool_name == "sap_evidence_assess":
            latest_assessment_index = index
            output = call.get("output") if isinstance(call.get("output"), dict) else {}
            assessed_values = [
                str(item) for item in output.get("missing_evidence") or [] if str(item)
            ]
    if latest_assessment_index < 0:
        return list(dict.fromkeys(payload_values))
    if latest_data_index > latest_assessment_index:
        return list(
            dict.fromkeys(
                [
                    *payload_values,
                    *assessed_values,
                    "evidence_reassessment_required",
                ]
            )
        )
    return list(dict.fromkeys(assessed_values))


def _presentation_evidence_refs(presentation: RunPresentation) -> list[str]:
    references: list[str] = []
    for block in presentation.blocks:
        references.extend(block.evidence_refs)
        for entry in block.entries:
            references.extend(entry.evidence_refs)
        for metric in block.metrics:
            references.extend(metric.evidence_refs)
        for row in block.rows:
            references.extend(row.evidence_refs)
    return list(dict.fromkeys(str(item) for item in references if str(item)))


def _executed_plans_from_calls(raw_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for call in raw_calls:
        if call.get("tool_name") != "sap_query_execute" or call.get("status") != "completed":
            continue
        safe_input = call.get("safe_input") if isinstance(call.get("safe_input"), dict) else {}
        plan = safe_input.get("plan") if isinstance(safe_input.get("plan"), dict) else {}
        evidence_ref = str(call.get("evidence_ref") or "")
        identity = (
            str(plan.get("service_name") or ""),
            str(plan.get("odata_version") or ""),
            str(plan.get("entity_set") or ""),
            evidence_ref,
        )
        if (
            not all(identity)
            or str(plan.get("http_method") or "GET").upper() != "GET"
            or identity in seen
        ):
            continue
        seen.add(identity)
        plans.append(
            {
                "service_name": identity[0],
                "odata_version": identity[1],
                "entity_set": identity[2],
                "http_method": "GET",
                "evidence_ref": identity[3],
            }
        )
    return plans


def _validated_payload_from_store(
    store: RunStore,
    run_id: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    raw_calls = store.list_harness_tool_calls(run_id)
    presentation = _latest_validated_presentation(raw_calls)
    if presentation is None:
        return None
    projection = None
    if store.get_harness_state(run_id).get("acceptance_spec"):
        validated = next((call.get("output") or {} for call in reversed(raw_calls)
                          if call.get("tool_name") == "sap_final_report_validate"
                          and call.get("status") == "completed"
                          and (call.get("output") or {}).get("ok") is True), {})
        projection = validated.get("_validated_acceptance_projection")
        if projection is None:
            return None
    missing = _effective_missing_evidence([], raw_calls)
    first_text = next(
        (
            block.text
            for block in presentation.blocks
            if block.type == "text" and block.text is not None
        ),
        presentation.title,
    )
    return {
        "status": "inconclusive" if missing or (projection and not projection["business_complete"]) else "completed",
        "intent": "validated_live_sap_result",
        "summary": {"zh": first_text.zh, "en": first_text.en},
        "source_complete": _evidence_sources_complete(evidence),
        "business_complete": not missing and (projection is None or projection["business_complete"]),
        "missing_evidence": list(dict.fromkeys(missing + (projection["evidence_gap_codes"] if projection else []))),
        "evidence_refs": _presentation_evidence_refs(presentation),
        "executed_plans": _executed_plans_from_calls(raw_calls),
        "clarification_question": "",
        "presentation": presentation.model_dump(mode="json"),
        **({"acceptance_projection": projection} if projection is not None else {}),
    }


def _evidence_sources_complete(evidence: list[dict[str, Any]]) -> bool:
    sources = [
        item
        for item in evidence
        if item.get("source_type") in {"sap_live", "sap_skill"}
    ]
    return bool(sources) and all(item.get("source_complete") is True for item in sources)


def _plan_candidates(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    candidates = [plan]
    candidates.extend(
        item for item in plan.get("steps") or [] if isinstance(item, dict)
    )
    return candidates


def _plan_contains_entity(plan: Any, entity_set: str) -> bool:
    return any(
        str(item.get("entity_set") or "") == entity_set
        for item in _plan_candidates(plan)
    )


def _has_exact_filter(candidate: dict[str, Any], field: str, value: str) -> bool:
    return any(
        str(item.get("field") or "") == field
        and str(item.get("operator") or "eq").casefold() == "eq"
        and str(item.get("value") if item.get("value") is not None else "") == value
        for item in candidate.get("filters") or []
        if isinstance(item, dict)
    )


def _inventory_plan_scope_issue(
    plan: Any,
    *,
    require_movement: bool,
    identifiers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    expected_identifiers = identifiers or {}
    for candidate in _plan_candidates(plan):
        entity = str(candidate.get("entity_set") or "")
        if entity not in {"A_MatlStkInAcctMod", "A_MaterialDocumentItem"}:
            continue
        required_filters = {
            "InventoryStockType": "01",
            "InventorySpecialStockType": "",
        }
        if expected_identifiers:
            required_filters.update(
                {
                    "Material": expected_identifiers["material"],
                    "Plant": expected_identifiers["plant"],
                    "StorageLocation": expected_identifiers["storage_location"],
                }
            )
        missing_filters = [
            field
            for field, value in required_filters.items()
            if not _has_exact_filter(candidate, field, value)
        ]
        if missing_filters:
            return {
                "code": "inventory_unrestricted_scope_required",
                "message": (
                    "Inventory-health aging must be scoped to InventoryStockType='01', blank "
                    "InventorySpecialStockType, and the exact material/plant/storage location."
                ),
                "missing_filters": missing_filters,
            }
        if entity == "A_MaterialDocumentItem" and require_movement:
            selected = {str(item) for item in candidate.get("select_fields") or []}
            required_fields = {
                "MaterialDocumentYear",
                "MaterialDocument",
                "MaterialDocumentItem",
                "Batch",
                "DebitCreditCode",
                "QuantityInBaseUnit",
                "MaterialBaseUnit",
                "InventoryStockType",
                "InventorySpecialStockType",
            }
            missing_fields = sorted(required_fields - selected)
            if missing_fields:
                return {
                    "code": "inventory_fifo_movement_fields_required",
                    "message": (
                        "FIFO inventory aging requires complete quantity, direction, unit, "
                        "batch, and stable-key fields."
                    ),
                    "missing_fields": missing_fields,
                }
            if candidate.get("top") is not None or any(
                str(item.get("field") or "") == "MaterialDocumentYear"
                for item in candidate.get("filters") or []
                if isinstance(item, dict)
            ):
                return {
                    "code": "inventory_fifo_full_history_required",
                    "message": (
                        "FIFO inventory aging requires the exact complete movement history "
                        "without an explicit top or fiscal-year/date lower bound."
                    ),
                }
    return None


def _inventory_header_plan_issue(plan: Any) -> dict[str, Any] | None:
    for candidate in _plan_candidates(plan):
        if str(candidate.get("entity_set") or "") != "A_MaterialDocumentHeader":
            continue
        selected = {str(item) for item in candidate.get("select_fields") or []}
        required = {
            "MaterialDocumentYear",
            "MaterialDocument",
            "PostingDate",
            "CreationDate",
            "CreationTime",
        }
        bindings = {
            (
                str(item.get("field") or ""),
                str(item.get("source_field") or ""),
                str(item.get("source_step_id") or ""),
            )
            for item in candidate.get("filter_from_previous") or []
            if isinstance(item, dict)
        }
        source_ids = {item[2] for item in bindings if item[2]}
        composite_bound = (
            len(source_ids) == 1
            and any(field == source == "MaterialDocumentYear" for field, source, _ in bindings)
            and any(field == source == "MaterialDocument" for field, source, _ in bindings)
        )
        if not required.issubset(selected) or not composite_bound or candidate.get("top") is not None:
            return {
                "code": "inventory_fifo_header_evidence_required",
                "message": (
                    "FIFO inventory aging requires composite-bound material-document headers "
                    "with posting date, creation date, and creation time."
                ),
            }
        return None
    return {"code": "inventory_fifo_header_evidence_required"}


def _inventory_batch_plan_issue(
    plan: Any, *, identifiers: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Require material-wide Batch API evidence for inventory-health expiry checks."""

    expected_material = str((identifiers or {}).get("material") or "")
    for candidate in _plan_candidates(plan):
        if str(candidate.get("entity_set") or "") != "Batch":
            continue
        filters = [item for item in candidate.get("filters") or [] if isinstance(item, dict)]
        material_filters = [
            item
            for item in filters
            if str(item.get("field") or "") == "Material"
            and str(item.get("operator") or "eq").casefold() == "eq"
            and str(item.get("value") or "").strip()
        ]
        has_expected_material = bool(material_filters) and (
            not expected_material
            or any(str(item.get("value") or "") == expected_material for item in material_filters)
        )
        plant_filtered = any(
            str(item.get("field") or "") == "BatchIdentifyingPlant" for item in filters
        )
        selected = {str(item) for item in candidate.get("select_fields") or []}
        required = {
            "Material",
            "BatchIdentifyingPlant",
            "Batch",
            "ShelfLifeExpirationDate",
        }
        if (
            not has_expected_material
            or plant_filtered
            or not required.issubset(selected)
            or candidate.get("top") is not None
        ):
            return {
                "code": "inventory_batch_material_scope_required",
                "message": (
                    "Inventory-health expiry evidence must query the complete Batch entity by "
                    "Material only. BatchIdentifyingPlant is an association attribute, not a "
                    "source filter, because SAP can store the authoritative batch at material level."
                ),
            }
        return None
    return None


def _rows_with_fields(value: Any, required_fields: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if required_fields.issubset(item):
                found.append(item)
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    unique: dict[str, dict[str, Any]] = {}
    for row in found:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        unique.setdefault(key, row)
    return list(unique.values())


def _complete_payload(
    rows: list[dict[str, Any]],
    *,
    step_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "completed",
        "source_complete": True,
        "source_truncated": False,
        "data": {"results": rows, "source_complete": True},
        "step_results": step_results
        or {"step_1": {"results": rows, "source_complete": True}},
    }


def _deadline_presentation(evidence: list[dict[str, Any]]) -> RunPresentation:
    complete_count = sum(item.get("source_complete") is True for item in evidence)
    return RunPresentation.model_validate(
        {
            "schema_version": "1.0",
            "title": {
                "zh": "本轮查询已按时间预算结束",
                "en": "This query ended at its time budget",
            },
            "validation_ref": None,
            "blocks": [
                {
                    "type": "notice",
                    "tone": "warning",
                    "claim_scope": "diagnostic",
                    "title": {
                        "zh": "当前结论无法完全确认",
                        "en": "The current conclusion is inconclusive",
                    },
                    "text": {
                        "zh": (
                            f"系统已保留 {len(evidence)} 份SAP证据，其中 "
                            f"{complete_count} 份来源查询完整；最后整理阶段没有发起新的SAP读取。"
                        ),
                        "en": (
                            f"The platform preserved {len(evidence)} SAP evidence sets; "
                            f"{complete_count} have complete source reads. No new SAP read "
                            "was started during finalization."
                        ),
                    },
                    "source_complete": False,
                }
            ],
        }
    )


def _plan_business_contract_issue(query: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Describe a question-derived business concern without choosing a SAP API."""

    lowered = query.casefold()
    needs_transaction_amount = any(
        token in lowered
        for token in (
            "交易币金额",
            "transaction-currency amount",
            "transaction currency amount",
        )
    )
    customer_or_supplier_scope = any(
        token in lowered for token in ("客户", "供应商", "customer", "supplier")
    )
    mentions_ledger = any(token in lowered for token in ("ledger", "分类账", "账本"))
    candidates = _plan_candidates(plan)
    inventory_health_scope = _inventory_health_requested(query)
    if inventory_health_scope:
        inventory_issue = _inventory_plan_scope_issue(plan, require_movement=True)
        if inventory_issue:
            return inventory_issue
        batch_issue = _inventory_batch_plan_issue(plan)
        if batch_issue:
            return batch_issue
    for candidate in candidates:
        entity = str(candidate.get("entity_set") or "")
        selected = {str(item) for item in candidate.get("select_fields") or []}
        filters = {
            str(item.get("field") or "")
            for item in candidate.get("filters") or []
            if isinstance(item, dict)
        }
        if needs_transaction_amount and entity == "A_OperationalAcctgDocItemCube":
            required = {"AmountInTransactionCurrency", "TransactionCurrency"}
            missing = sorted(required - selected)
            if missing:
                return {
                    "code": "paired_transaction_amount_required",
                    "message": (
                        "The question explicitly requests transaction-currency amount. "
                        "Select AmountInTransactionCurrency and TransactionCurrency together "
                        "in the complete account-item query."
                    ),
                    "missing_fields": missing,
                }
        if (
            needs_transaction_amount
            and customer_or_supplier_scope
            and not mentions_ledger
            and entity == "GLAccountLineItem"
            and "Ledger" in filters
        ):
            return {
                "code": "unverified_ledger_scope_rejected",
                "message": (
                    "The question does not request a ledger restriction. Do not add Ledger to a "
                    "customer or supplier subledger amount lookup unless equivalent item coverage "
                    "has been proved from live evidence."
                ),
            }
    return None


def _plan_business_contract_advisories(query: str, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate legacy question heuristics into non-blocking planning guidance."""

    issue = _plan_business_contract_issue(query, plan)
    if not issue:
        return []
    code = str(issue.get("code") or "business_semantic_check_suggested")
    english = str(issue.get("message") or "Review the inferred business scope against live evidence.")
    chinese = {
        "paired_transaction_amount_required": "问题涉及交易币金额，建议同时读取交易币金额及币种字段并核对完整行项目。",
        "unverified_ledger_scope_rejected": "问题未指定台账，建议确认额外台账过滤不会遗漏客户或供应商子分类账项目。",
    }.get(code, "当前计划可能需要额外的业务范围核对；该提示不会阻止查询继续执行。")
    related_steps = [
        str(item.get("step_id") or item.get("id") or "")
        for item in _plan_candidates(plan)
        if isinstance(item, dict) and (item.get("step_id") or item.get("id"))
    ]
    detail = {
        key: value
        for key, value in issue.items()
        if key not in {"code", "message"}
    }
    return [
        {
            "code": code,
            "message": {"zh": chinese, "en": english},
            "related_steps": list(dict.fromkeys(related_steps)),
            "knowledge_refs": ["runtime-question-business-heuristics"],
            "suggested_checks": [
                {
                    "zh": "结合实时Schema、返回记录及完整业务键复核该提示。",
                    "en": "Review this guidance against live schemas, returned records, and complete business keys.",
                }
            ],
            **({"detail": detail} if detail else {}),
        }
    ]


def _inventory_health_requested(query: str) -> bool:
    return classify_required_assessments(query)["intent"] == "required"


_INVENTORY_FIFO_TERMS = (
    "fifo",
    "库存健康",
    "库存账龄",
    "慢动",
    "呆滞",
    "inventory health",
    "inventory aging",
    "slow-moving",
    "obsolete stock",
    "stagnant stock",
)


def classify_required_assessments(query: str) -> dict[str, Any]:
    """Classify specialized assessment intent from user-authored text only.

    This intentionally ignores quoted examples and treats a requirement as
    positive only at sentence scope. Platform prompts and tool output must
    never be passed to this helper.
    """

    text = str(query or "")
    # Quoted examples or copied contract text are references, not requests.
    for pattern in (
        r'`[^`]*`',
        r'"[^"\r\n]*"',
        r"'[^'\r\n]*'",
        r"“[^”\r\n]*”",
        r"‘[^’\r\n]*’",
    ):
        text = re.sub(pattern, " ", text)
    sentences = [
        item.strip().casefold()
        for item in re.split(r"(?<=[。！？.!?;；])|[\r\n]+", text)
        if item.strip()
    ]
    positive = 0
    negative = 0
    term_pattern = "(?:" + "|".join(
        re.escape(term) for term in sorted(_INVENTORY_FIFO_TERMS, key=len, reverse=True)
    ) + ")"
    negative_patterns = (
        rf"(?:不|无需|不需要|不执行|不进行|不做|排除|不要).{{0,24}}{term_pattern}",
        rf"(?:no|not|without|exclude|excluding|do\s+not|does\s+not|need\s+not|isn't|is\s+not).{{0,64}}{term_pattern}",
        rf"{term_pattern}.{{0,32}}(?:不需要|不执行|不进行|不做|排除|无需)",
    )
    for sentence in sentences:
        # Contrast markers usually separate a requested assessment from an
        # explicit exclusion.  Do not split ordinary commas: lists such as
        # "No stock allocation, FIFO, date cutoff" are one negative scope.
        clauses = [
            item.strip()
            for item in re.split(
                r"(?:，?\s*(?:但|但是|不过)|,?\s*\b(?:but|however)\b)",
                sentence,
                flags=re.IGNORECASE,
            )
            if item.strip()
        ]
        for clause in clauses:
            if not any(term in clause for term in _INVENTORY_FIFO_TERMS):
                continue
            if any(re.search(pattern, clause, flags=re.IGNORECASE) for pattern in negative_patterns):
                negative += 1
            else:
                positive += 1
    if positive and negative:
        intent = "ambiguous"
        required: list[str] = []
    elif positive:
        intent = "required"
        required = ["inventory_fifo"]
    elif negative:
        intent = "not_required"
        required = []
    else:
        intent = "none"
        required = []
    return {
        "intent": intent,
        "required_assessments": required,
        "source": "user_query",
        "positive_sentence_count": positive,
        "negative_sentence_count": negative,
    }


def _safe_tool_input(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name.casefold().endswith("_token") and isinstance(child, str):
                result[name] = _capability_fingerprint(child)
            elif name.casefold() in {"receipt_reference", "payer_name", "bank_reference"}:
                result[name] = "[REDACTED]"
            else:
                result[name] = _safe_tool_input(child, depth=depth + 1)
        return _safe_public(result)
    if isinstance(value, list):
        return [_safe_tool_input(item, depth=depth + 1) for item in value[:50]]
    return _safe_public(value, depth=depth)


def _safe_public(value: Any, *, preserve_rows: bool = False, depth: int = 0) -> Any:
    if depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name.casefold() == "__metadata":
                continue
            if name.casefold() in _SECRET_KEYS or name.casefold().endswith("_path"):
                result[name] = "[REDACTED]"
            else:
                result[name] = _safe_public(child, preserve_rows=preserve_rows, depth=depth + 1)
        return result
    if isinstance(value, list):
        limit = 200 if preserve_rows else 50
        items = [
            _safe_public(item, preserve_rows=preserve_rows, depth=depth + 1)
            for item in value[:limit]
        ]
        if len(value) > limit:
            items.append({"_truncated": len(value) - limit})
        return items
    if isinstance(value, str):
        if len(value) > 20_000:
            return value[:20_000] + "…[TRUNCATED]"
        return value
    return value


def _compact_catalog_result(value: dict[str, Any]) -> dict[str, Any]:
    """Keep Catalog search useful to the model without flooding a turn."""

    data = value.get("data") if isinstance(value, dict) else None
    if not isinstance(data, dict):
        return value
    compact: list[dict[str, Any]] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        terms = [
            str(item)
            for item in raw.get("business_terms") or []
            if str(item) and not _looks_public_mojibake(str(item))
        ]
        curated_fields = [str(item) for item in raw.get("curated_fields") or [] if str(item)]
        compact.append(
            {
                "service_name": raw.get("service_name"),
                "odata_version": raw.get("odata_version"),
                "artifact_id": raw.get("artifact_id"),
                "artifact_version": raw.get("artifact_version"),
                "status": raw.get("status"),
                "entity_set": raw.get("entity_set"),
                "description": raw.get("description"),
                "business_aliases": list(raw.get("business_aliases") or [])[:10],
                "business_terms": terms[-24:],
                "curated_topics": list(raw.get("curated_topics") or []),
                "search_purpose": raw.get("search_purpose") or {},
                "candidate_fields": curated_fields[:100],
                "supported_operations": ["GET"],
                "schema_authority": "live_metadata_required_before_execution",
            }
        )
    return {
        "ok": value.get("ok", True),
        "data": {
            "items": compact,
            "service_candidates": [
                {key: candidate.get(key) for key in (
                    "service_name", "odata_version", "discovery_source", "metadata_status", "business_aliases"
                )}
                for candidate in data.get("service_candidates") or [] if isinstance(candidate, dict)
            ],
            "service_candidate_count": data.get("service_candidate_count", 0),
            "total_count": data.get("total_count", len(compact)),
            "provider_id": data.get("provider_id"),
            "catalog_scope": data.get("catalog_scope"),
            "source_complete_policy": (
                "Explicit top bounds are incomplete; live metadata is authoritative."
            ),
        },
    }


def _looks_public_mojibake(value: str) -> bool:
    if "\ufffd" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        return True
    return any(marker in value for marker in ("Ã", "Â", "â€", "ï¿½"))


def _adt_scope_has_exact_value(
    payload: dict[str, Any], field: str, expected: str
) -> bool:
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    for item in scope.get("filters") or []:
        if not isinstance(item, dict) or str(item.get("field") or "").upper() != field.upper():
            continue
        if str(item.get("sign") or "I").upper() != "I":
            continue
        operator = str(item.get("operator") or item.get("option") or "").upper()
        if operator == "EQ" and str(item.get("value") or "").strip() == expected:
            return True
        if operator == "IN":
            values = [str(value).strip() for value in item.get("values") or []]
            if values == [expected]:
                return True
    return False


def _source_complete(value: Any) -> bool:
    flags: list[bool] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {"source_complete", "paging_complete"} and isinstance(child, bool):
                    flags.append(child)
                else:
                    walk(child)
            if item.get("status") in {"partial", "failed", "row_limit_reached"}:
                flags.append(False)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return bool(flags) and all(flags)


def _extract_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("rows", "results", "value", "items", "records"):
            candidate = value.get(key)
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                return candidate
        # Provider envelopes also contain lists of transport diagnostics such as
        # chunk_results, requests, artifacts, and filters.  Those are not SAP
        # business rows.  Recurse only through object containers; named row
        # arrays above remain the sole list-to-row boundary.
        preferred = [value.get(key) for key in ("data", "step_results", "payload", "output")]
        remaining = [
            child
            for key, child in value.items()
            if key not in {"data", "step_results", "payload", "output"}
            and isinstance(child, dict)
        ]
        for child in [*preferred, *remaining]:
            if not isinstance(child, dict):
                continue
            rows = _extract_rows(child)
            if rows:
                return rows
    elif isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return value
        for child in value:
            rows = _extract_rows(child)
            if rows:
                return rows
    return []


def _row_count(value: Any) -> int:
    if isinstance(value, dict):
        for key in ("result_count", "row_count", "returned_row_count", "count"):
            if isinstance(value.get(key), int):
                return int(value[key])
    rows = _extract_rows(value)
    if rows:
        return len(rows)
    return 0


def _bounded_preview(value: Any) -> dict[str, Any]:
    rows = _extract_rows(value)
    if rows:
        return {"rows": _safe_public(rows[:200], preserve_rows=True), "preview_count": min(len(rows), 200)}
    return _safe_public(value)
