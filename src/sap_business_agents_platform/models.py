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
    updated_at: str = Field(default_factory=utc_now)


TERMINAL_STATUSES = {
    RunStatus.completed,
    RunStatus.inconclusive,
    RunStatus.failed,
    RunStatus.cancelled,
}


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RunMode
    agent_id: str | None = Field(default=None, alias="agentId")
    workflow_id: str | None = Field(default=None, alias="workflowId")
    query: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)

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
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1)

    @model_validator(mode="after")
    def strip_input(self) -> "RunInput":
        self.input = self.input.strip()
        if not self.input:
            raise ValueError("input must not be blank")
        return self


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


class WorkflowDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    workflow: dict[str, Any]


class WorkflowValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_discover: bool = Field(default=True, alias="autoDiscover")
    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_inconclusive: bool = Field(default=False, alias="acknowledgeInconclusive")


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
