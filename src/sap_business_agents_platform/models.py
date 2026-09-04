from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunMode(StrEnum):
    agent = "agent"
    free_query = "free_query"
    workflow = "workflow"


class RunStatus(StrEnum):
    queued = "queued"
    planning = "planning"
    waiting_input = "waiting_input"
    validating = "validating"
    running = "running"
    completed = "completed"
    inconclusive = "inconclusive"
    failed = "failed"
    cancelled = "cancelled"


class RunProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal[
        "received", "preparing", "reading_sap", "validating_evidence", "preparing_result"
    ] = "received"
    state: Literal[
        "active", "waiting_input", "completed", "inconclusive", "failed", "cancelled"
    ] = "active"
    current_step_id: str | None = None
    current_node_id: str | None = None
    current_tool: str | None = None
    completed_units: int = Field(default=0, ge=0)
    total_units: int | None = Field(default=None, ge=0)
    determinate: bool = False
    event_sequence: int = Field(default=0, ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)
    hard_limit_seconds: int | None = Field(default=None, ge=1)
    deadline_phase: Literal["querying", "finalizing", "completed"] | None = None
    next_deadline_at: str | None = None
    extension_count: int = Field(default=0, ge=0)
    updated_at: str = Field(default_factory=utc_now)


TERMINAL_STATUSES = {
    RunStatus.completed,
    RunStatus.inconclusive,
    RunStatus.failed,
    RunStatus.cancelled,
}


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: RunMode
    agent_id: str | None = Field(default=None, alias="agentId")
    workflow_id: str | None = Field(default=None, alias="workflowId")
    query: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    sensitive_inputs: dict[str, str] = Field(
        default_factory=dict, alias="sensitiveInputs"
    )

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "RunCreate":
        if self.query is not None:
            self.query = self.query.strip()
        if self.mode == RunMode.agent and not self.agent_id:
            raise ValueError("agentId is required for agent mode")
        if self.mode == RunMode.free_query and not str(self.query or "").strip():
            raise ValueError("query is required for free_query mode")
        if self.mode == RunMode.workflow and not self.workflow_id:
            raise ValueError("workflowId is required for workflow mode")
        normalized_sensitive: dict[str, str] = {}
        for name, value in self.sensitive_inputs.items():
            if not isinstance(value, str):
                raise ValueError(f"sensitiveInputs.{name} must be a string")
            trimmed = value.strip()
            if not trimmed:
                raise ValueError(f"sensitiveInputs.{name} must not be blank")
            normalized_sensitive[str(name)] = trimmed
        self.sensitive_inputs = normalized_sensitive
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    input: str | None = Field(default=None, min_length=1)
    sensitive_inputs: dict[str, str] = Field(
        default_factory=dict, alias="sensitiveInputs"
    )

    @model_validator(mode="after")
    def strip_input(self) -> "RunInput":
        if self.input is not None:
            self.input = self.input.strip()
            if not self.input:
                self.input = None
        self.sensitive_inputs = {
            str(name): value.strip()
            for name, value in self.sensitive_inputs.items()
            if isinstance(value, str) and value.strip()
        }
        if self.input is None and not self.sensitive_inputs:
            raise ValueError("input or sensitiveInputs is required")
        return self


class FreeQueryFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_iteration: int = Field(alias="baseIteration", ge=1)
    feedback: str = Field(min_length=1, max_length=12_000)
    locale: Literal["zh", "en"] = "zh"
    feedback_type_hint: Literal[
        "scope_or_filter",
        "relationship",
        "missing_evidence",
        "business_rule",
        "presentation",
    ] | None = Field(default=None, alias="feedbackTypeHint")

    @model_validator(mode="after")
    def strip_feedback(self) -> "FreeQueryFeedback":
        self.feedback = self.feedback.strip()
        if not self.feedback:
            raise ValueError("feedback must not be blank")
        return self


class FreeQueryFeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_iteration: int = Field(alias="baseIteration", ge=1)
    input: str = Field(min_length=1, max_length=12_000)
    sensitive_inputs: dict[str, str] = Field(
        default_factory=dict, alias="sensitiveInputs"
    )

    @model_validator(mode="after")
    def strip_input(self) -> "FreeQueryFeedbackInput":
        self.input = self.input.strip()
        if not self.input:
            raise ValueError("input must not be blank")
        self.sensitive_inputs = {
            str(name): value.strip()
            for name, value in self.sensitive_inputs.items()
            if isinstance(value, str) and value.strip()
        }
        return self


class ArtifactRevealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["rows", "download"] = "rows"


class ArtifactDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Keep the tombstone reason platform-defined. Free text here could itself
    # contain a bank reference or another business identifier.
    reason: Literal["user_requested"] = "user_requested"


class FreeQueryFeedbackCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def strip_reason(self) -> "FreeQueryFeedbackCancel":
        self.reason = self.reason.strip()
        return self


class FreeQueryAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    iteration: int = Field(ge=1)
    expected_result_digest: str = Field(alias="expectedResultDigest", min_length=8)


class FreeQuerySessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_run_id: str = Field(alias="sourceRunId", min_length=1)


class Completeness(BaseModel):
    source_complete: bool = False
    business_complete: bool = False
    reason: str = "No SAP evidence has been collected."
    missing_evidence: list[str] = Field(default_factory=list)


class LocalizedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zh: str = ""
    en: str = ""


class PresentationColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    label: LocalizedText
    format: Literal["text", "date", "datetime", "integer", "decimal", "currency", "status"] = "text"


class PresentationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: LocalizedText
    value: LocalizedText
    evidence_refs: list[str] = Field(default_factory=list)


class PresentationMetric(PresentationEntry):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    tone: Literal["neutral", "success", "warning", "error"] = "neutral"


class PresentationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[LocalizedText]
    evidence_refs: list[str] = Field(default_factory=list)


class PresentationBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "key_value", "metrics", "table", "bullet_list", "notice"]
    title: LocalizedText | None = None
    tone: Literal["neutral", "success", "warning", "error", "info"] = "neutral"
    claim_scope: Literal[
        "customer_business_fact", "product_documentation", "business_semantics", "diagnostic"
    ] = "diagnostic"
    evidence_refs: list[str] = Field(default_factory=list)
    text: LocalizedText | None = None
    entries: list[PresentationEntry] = Field(default_factory=list)
    metrics: list[PresentationMetric] = Field(default_factory=list)
    columns: list[PresentationColumn] = Field(default_factory=list)
    rows: list[PresentationRow] = Field(default_factory=list, max_length=200)
    items: list[LocalizedText] = Field(default_factory=list)
    total_rows: int | None = Field(default=None, ge=0)
    display_truncated: bool = False
    source_complete: bool | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "PresentationBlock":
        populated = {
            "text": self.text is not None,
            "key_value": bool(self.entries),
            "metrics": bool(self.metrics),
            "table": bool(self.columns),
            "bullet_list": bool(self.items),
            "notice": self.text is not None,
        }
        if not populated[self.type]:
            raise ValueError(f"presentation block {self.type} has no display content")
        if self.type == "table":
            if any(len(row.values) != len(self.columns) for row in self.rows):
                raise ValueError("presentation table rows must match the declared column count")
            if self.total_rows is None:
                self.total_rows = len(self.rows)
            if self.total_rows < len(self.rows):
                raise ValueError("presentation table total_rows cannot be smaller than displayed rows")
            if self.total_rows > len(self.rows):
                self.display_truncated = True
        return self


class RunPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    title: LocalizedText
    blocks: list[PresentationBlock]
    validation_ref: str | None = None


class HarnessLimitUsage(BaseModel):
    limit: int | None = None
    used: int = 0
    reached: bool = False


class HarnessLimits(BaseModel):
    tool_calls: HarnessLimitUsage = Field(default_factory=HarnessLimitUsage)
    turns: HarnessLimitUsage = Field(default_factory=HarnessLimitUsage)
    runtime_seconds: HarnessLimitUsage = Field(default_factory=HarnessLimitUsage)
    reached_kind: Literal["tool_calls", "turns", "runtime_seconds"] | None = None
    hard_limit_seconds: int | None = Field(default=None, ge=1)
    query_seconds_granted: int = Field(default=0, ge=0)
    finalization_seconds_reserved: int = Field(default=0, ge=0)
    extension_count: int = Field(default=0, ge=0)
    extension_reasons: list[str] = Field(default_factory=list)
    deadline_phase: Literal["querying", "finalizing", "completed"] | None = None
    elapsed_seconds: int = Field(default=0, ge=0)


class RuntimeSnapshot(BaseModel):
    provider_id: str
    sdk_id: str
    version: str | None = None
    configuration_digest: str
    capabilities: list[str] = Field(default_factory=list)
    selected_at: str | None = None


class HarnessResult(BaseModel):
    runtime: Literal["codex_app_server"] = "codex_app_server"
    protocol: Literal["agent_runtime.v2"] = "agent_runtime.v2"
    thread_id: str | None = None
    turn_count: int = 0
    tool_call_count: int = 0
    budgeted_tool_call_count: int = 0
    web_search_count: int = 0
    discovered_tool_count: int = 0
    activated_tool_count: int = 0
    limits: HarnessLimits = Field(default_factory=HarnessLimits)
    stop_reason: Literal[
        "completed",
        "waiting_input",
        "interrupted",
        "limit_reached",
        "capability_unavailable",
    ] = "capability_unavailable"


class RunResult(BaseModel):
    run_id: str
    mode: RunMode
    agent_id: str | None = None
    workflow_id: str | None = None
    workflow_revision: str | None = None
    query: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    rule_results: list[dict[str, Any]] = Field(default_factory=list)
    node_results: list[dict[str, Any]] = Field(default_factory=list)
    workflow_output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    completeness: Completeness = Field(default_factory=Completeness)
    summary: dict[str, str] = Field(default_factory=dict)
    presentation: RunPresentation | None = None
    workflow_presentation: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)
    thread_id: str | None = None
    runtime: RuntimeSnapshot | None = None
    harness: HarnessResult | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunRecord(BaseModel):
    run_id: str
    mode: RunMode
    status: RunStatus
    agent_id: str | None = None
    workflow_id: str | None = None
    parent_run_id: str | None = None
    node_id: str | None = None
    query: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] | None = None
    result: RunResult | None = None
    thread_id: str | None = None
    runtime: RuntimeSnapshot | None = None
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    progress: RunProgress = Field(default_factory=RunProgress)


class RunEvent(BaseModel):
    sequence: int
    run_id: str
    type: str
    data: dict[str, Any]
    created_at: str


class PlannerDecision(BaseModel):
    intent: str
    needs_clarification: bool = False
    clarification_question: str = ""
    plan: dict[str, Any] | None = None
    thread_id: str | None = None


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correction: str = ""
    workflow_draft_id: str | None = Field(default=None, alias="workflowDraftId")
    gap_id: str | None = Field(default=None, alias="gapId")


class DraftAuthoringCreate(DraftCreate):
    run_id: str


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1)


class DraftRecord(BaseModel):
    draft_id: str
    run_id: str
    status: Literal["generated", "needs_review", "validated", "invalid", "applied"]
    path: str
    origin: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentAuthoringCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: Literal["blank", "clone", "free_query", "workflow_gap"] = "blank"
    agent_id: str | None = Field(default=None, alias="agentId", pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    module: Literal["CO", "Common", "FI", "MM", "PP", "SD"] | None = None
    title: dict[str, str] | None = None
    source_agent_id: str | None = Field(
        default=None, alias="sourceAgentId", pattern=r"^[a-z0-9][a-z0-9-]{2,79}$"
    )
    run_id: str | None = Field(default=None, alias="runId")
    workflow_draft_id: str | None = Field(default=None, alias="workflowDraftId")
    gap_id: str | None = Field(default=None, alias="gapId")
    bump: Literal["patch", "minor", "major"] = "patch"
    locale: Literal["zh", "en"] = "zh"

    @model_validator(mode="after")
    def validate_source(self) -> "AgentAuthoringCreate":
        if self.source == "blank" and (not self.agent_id or not self.module):
            raise ValueError("blank Agent creation requires agentId and module")
        if self.source == "clone" and not self.source_agent_id:
            raise ValueError("clone Agent creation requires sourceAgentId")
        if self.source in {"free_query", "workflow_gap"} and not self.run_id:
            raise ValueError(f"{self.source} Agent creation requires runId")
        if self.source == "workflow_gap" and (not self.workflow_draft_id or not self.gap_id):
            raise ValueError("workflow_gap creation requires workflowDraftId and gapId")
        return self


class AgentDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    manifest: dict[str, Any]
    readme: str = Field(default="", max_length=200_000)
    rules: str = Field(default="", max_length=500_000)


class AgentFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_turn: int = Field(alias="baseTurn", ge=1)
    base_revision: int = Field(alias="baseRevision", ge=1)
    feedback: str = Field(min_length=1, max_length=12_000)
    locale: Literal["zh", "en"] = "zh"

    @model_validator(mode="after")
    def strip_feedback(self) -> "AgentFeedbackRequest":
        self.feedback = self.feedback.strip()
        if not self.feedback:
            raise ValueError("feedback must not be blank")
        return self


class AgentUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_revision: int = Field(alias="baseRevision", ge=1)
    target_revision: int = Field(alias="targetRevision", ge=1)


class AgentLiveValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input: dict[str, Any] = Field(default_factory=dict)
    auto_discover: bool = Field(default=False, alias="autoDiscover")


class AgentPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    target_version: str = Field(alias="targetVersion", pattern=r"^\d+\.\d+\.\d+$")
    activate: bool = False
    validation_report_digest: str | None = Field(
        default=None, alias="validationReportDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )


class AgentVersionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    bump: Literal["patch", "minor", "major"] = "patch"
    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_agent_hash: str = Field(
        alias="expectedAgentHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )


class AgentLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_agent_hash: str = Field(
        alias="expectedAgentHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    reason: str | None = Field(default=None, max_length=500)


class AgentActivateRequest(AgentLifecycleRequest):
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")


class AgentDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_agent_hash: str = Field(
        alias="expectedAgentHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    confirm_agent_id: str = Field(alias="confirmAgentId", min_length=1, max_length=80)


class WorkflowDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: dict[str, str] = Field(
        default_factory=lambda: {"zh": "未命名工作流", "en": "Untitled workflow"}
    )
    description: dict[str, str] = Field(default_factory=lambda: {"zh": "", "en": ""})
    workflow: dict[str, Any] | None = None


class WorkflowCompositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1, max_length=12_000)
    locale: Literal["zh", "en"] = "zh"

    @model_validator(mode="after")
    def strip_requirement(self) -> "WorkflowCompositionCreate":
        self.requirement = self.requirement.strip()
        if not self.requirement:
            raise ValueError("requirement must not be blank")
        return self


class WorkflowCompositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def strip_input(self) -> "WorkflowCompositionInput":
        self.input = self.input.strip()
        if not self.input:
            raise ValueError("input must not be blank")
        return self


class WorkflowFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_turn: int = Field(alias="baseTurn", ge=1)
    base_revision: int = Field(alias="baseRevision", ge=1)
    feedback: str = Field(min_length=1, max_length=12_000)
    feedback_type_hint: Literal[
        "goal_scope",
        "stage_or_agent",
        "mapping",
        "condition",
        "output_or_completeness",
        "validation_input",
        "validation_expectation",
        "agent_capability",
        "presentation",
        "new_intent",
    ] | None = Field(default=None, alias="feedbackTypeHint")
    locale: Literal["zh", "en"] = "zh"
    validation_run_id: str | None = Field(default=None, alias="validationRunId")

    @model_validator(mode="after")
    def strip_feedback(self) -> "WorkflowFeedbackRequest":
        self.feedback = self.feedback.strip()
        if not self.feedback:
            raise ValueError("feedback must not be blank")
        return self


class WorkflowFeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_turn: int = Field(alias="baseTurn", ge=1)
    input: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def strip_input(self) -> "WorkflowFeedbackInput":
        self.input = self.input.strip()
        if not self.input:
            raise ValueError("input must not be blank")
        return self


class WorkflowDesignAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_turn: int = Field(alias="baseTurn", ge=1)
    revision: int = Field(ge=1)
    workflow_hash: str = Field(alias="workflowHash", pattern=r"^sha256:[0-9a-f]{64}$")


class WorkflowValidationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    validation_run_id: str = Field(alias="validationRunId", min_length=1)
    validation_report_digest: str = Field(
        alias="validationReportDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    accepted_gap_codes: list[str] = Field(default_factory=list, alias="acceptedGapCodes")


class WorkflowUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_turn: int = Field(alias="baseTurn", ge=1)
    base_revision: int = Field(alias="baseRevision", ge=1)
    target_revision: int = Field(alias="targetRevision", ge=1)


class WorkflowDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    workflow: dict[str, Any]


class WorkflowValidationExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    operator: Literal["equals", "one_of", "exists", "non_empty", "decimal_within"]
    expected: Any = None
    tolerance: str | int | float | None = None

    @model_validator(mode="after")
    def validate_expectation_shape(self) -> "WorkflowValidationExpectation":
        if self.operator in {"equals", "decimal_within"} and "expected" not in self.model_fields_set:
            raise ValueError(f"expected is required for {self.operator}")
        if self.operator == "one_of" and (
            "expected" not in self.model_fields_set
            or not isinstance(self.expected, list)
            or not self.expected
        ):
            raise ValueError("one_of requires a non-empty expected array")
        if self.operator == "decimal_within" and self.tolerance is None:
            raise ValueError("decimal_within requires tolerance")
        if self.operator != "decimal_within" and self.tolerance is not None:
            raise ValueError("tolerance is only valid for decimal_within")
        if self.operator in {"exists", "non_empty"} and "expected" in self.model_fields_set:
            raise ValueError(f"expected is not valid for {self.operator}")
        return self


class WorkflowValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_discover: bool = Field(default=True, alias="autoDiscover")
    input: dict[str, Any] = Field(default_factory=dict)
    expectations: list[WorkflowValidationExpectation] = Field(
        default_factory=list, max_length=20
    )


class WorkflowPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_inconclusive: bool = Field(default=False, alias="acknowledgeInconclusive")
    validation_run_id: str | None = Field(default=None, alias="validationRunId")
    validation_report_digest: str | None = Field(
        default=None,
        alias="validationReportDigest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    accepted_gap_codes: list[str] = Field(default_factory=list, alias="acceptedGapCodes")


class WorkflowVersionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bump: Literal["patch", "minor", "major"] = "patch"
    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_workflow_hash: str = Field(
        alias="expectedWorkflowHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )


class WorkflowLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_workflow_hash: str = Field(
        alias="expectedWorkflowHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def strip_reason(self) -> "WorkflowLifecycleRequest":
        self.reason = self.reason.strip() if self.reason else None
        return self


class WorkflowDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(alias="expectedVersion", pattern=r"^\d+\.\d+\.\d+$")
    expected_workflow_hash: str = Field(
        alias="expectedWorkflowHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    confirm_workflow_id: str = Field(alias="confirmWorkflowId", min_length=1, max_length=128)


class WorkflowDraftRecord(BaseModel):
    draft_id: str
    status: Literal[
        "planning",
        "waiting_input",
        "needs_agents",
        "draft",
        "invalid",
        "validated",
        "inconclusive",
        "needs_review",
        "published",
    ]
    revision: int
    workflow: dict[str, Any]
    path: str
    thread_id: str | None = None
    validation_run_id: str | None = None
    composition: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class RoleMatchingSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    paths: list[str] = Field(default_factory=list, max_length=100)
    role_description: str | None = Field(
        default=None, alias="roleDescription", max_length=12_000
    )
    locale: Literal["zh", "en"] = "zh"
    consent_to_runtime: bool = Field(alias="consentToRuntime")

    @model_validator(mode="after")
    def normalize_paths(self) -> "RoleMatchingSessionCreate":
        self.paths = [value.strip() for value in self.paths]
        self.role_description = (
            self.role_description.strip() if self.role_description is not None else None
        ) or None
        if any(not value for value in self.paths):
            raise ValueError("paths must not contain blank entries")
        if not self.paths and not self.role_description:
            raise ValueError("paths or roleDescription must be provided")
        if not self.consent_to_runtime:
            raise ValueError("consentToRuntime must be true")
        return self


class RoleMatchingPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    paths: list[str] = Field(default_factory=list, max_length=100)
    role_description: str | None = Field(
        default=None, alias="roleDescription", max_length=12_000
    )

    @model_validator(mode="after")
    def normalize_paths(self) -> "RoleMatchingPreflightRequest":
        self.paths = [value.strip() for value in self.paths]
        self.role_description = (
            self.role_description.strip() if self.role_description is not None else None
        ) or None
        if any(not value for value in self.paths):
            raise ValueError("paths must not contain blank entries")
        if not self.paths and not self.role_description:
            raise ValueError("paths or roleDescription must be provided")
        return self


class RoleMatchingFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    base_revision: int = Field(alias="baseRevision", ge=1)
    message: str = Field(min_length=1, max_length=12_000)
    rematch_mode: Literal["incremental", "full"] = Field(
        default="incremental", alias="rematchMode"
    )
    added_paths: list[str] = Field(default_factory=list, alias="addedPaths", max_length=100)
    added_role_description: str | None = Field(
        default=None, alias="addedRoleDescription", max_length=12_000
    )
    excluded_document_ids: list[str] = Field(
        default_factory=list, alias="excludedDocumentIds", max_length=500
    )

    @model_validator(mode="after")
    def normalize_values(self) -> "RoleMatchingFeedback":
        self.message = self.message.strip()
        self.added_paths = [value.strip() for value in self.added_paths]
        self.added_role_description = (
            self.added_role_description.strip()
            if self.added_role_description is not None
            else None
        ) or None
        if not self.message or any(not value for value in self.added_paths):
            raise ValueError("feedback values must not be blank")
        return self


class RoleMatchingWorkflowDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    revision: int = Field(ge=1)
    expected_catalog_digest: str = Field(alias="expectedCatalogDigest", min_length=1)
