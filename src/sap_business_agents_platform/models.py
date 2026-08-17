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


class Completeness(BaseModel):
    source_complete: bool = False
    business_complete: bool = False
    reason: str = "No SAP evidence has been collected."
    missing_evidence: list[str] = Field(default_factory=list)


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
    errors: list[dict[str, Any]] = Field(default_factory=list)
    thread_id: str | None = None
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
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


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


class DraftAuthoringCreate(DraftCreate):
    run_id: str


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1)


class DraftRecord(BaseModel):
    draft_id: str
    run_id: str
    status: Literal["generated", "validated", "invalid", "applied"]
    path: str
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class WorkflowDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: dict[str, str] = Field(
        default_factory=lambda: {"zh": "未命名工作流", "en": "Untitled workflow"}
    )
    description: dict[str, str] = Field(default_factory=lambda: {"zh": "", "en": ""})
    workflow: dict[str, Any] | None = None


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
        "draft", "invalid", "validated", "inconclusive", "needs_review", "published"
    ]
    revision: int
    workflow: dict[str, Any]
    path: str
    thread_id: str | None = None
    validation_run_id: str | None = None
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
