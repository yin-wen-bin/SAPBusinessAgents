"""Business operation policies, independent of native SDK timeout defaults."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ORCHESTRATION_VERSION = "2.0"
RETIRED_OPERATIONS = frozenset({"plan", "ground_plan", "summarize", "request_additional_input", "resume_free_query_session"})
OPERATIONS = (
    "author_draft", "review_agent_feedback",
    "compose_workflow", "review_workflow", "repair_workflow", "review_workflow_feedback",
    "review_free_query_feedback", "revise_free_query_presentation", "analyze_role_matching",
    "review_role_matching_feedback", "workflow_authoring.v2", "free_query",
    "sample_discovery", "acceptance_baseline", "acceptance_free_query",
)
ROLE_MATCHING_RUNTIME_TURN_SECONDS = 300
WORKFLOW_AUTHORING_SECONDS = 3600
# Compatibility import; policy belongs to the business operation, not the SDK.
WORKBUDDY_WORKFLOW_SECONDS = WORKFLOW_AUTHORING_SECONDS
OPERATION_BUDGETS = {
    "author_draft": 3600, "review_agent_feedback": 3600,
    "compose_workflow": 3600, "review_workflow_feedback": 3600,
    "workflow_authoring.v2": 3600, "review_workflow": 180, "repair_workflow": 180,
    "review_free_query_feedback": 1800, "revise_free_query_presentation": 1800,
    "free_query": 1800, "sample_discovery": 600,
    "acceptance_baseline": 600, "acceptance_free_query": 1800,
    "analyze_role_matching": 300, "review_role_matching_feedback": 300,
}
OPERATION_METHODS = {name: name for name in OPERATIONS}
OPERATION_METHODS["workflow_authoring.v2"] = "author_workflow_v2"
METHOD_OPERATIONS = {method: name for name, method in OPERATION_METHODS.items()}


def require_operation(operation):
    from .runtime_contract import RuntimeContractError
    if operation in RETIRED_OPERATIONS:
        raise RuntimeContractError("runtime_operation_retired")
    if operation not in OPERATIONS:
        raise RuntimeContractError("runtime_operation_unavailable")
    return operation


def operation_seconds(provider_id: str, operation: str, *, existing: float | None = None) -> float | None:
    require_operation(operation)
    if operation in {"review_agent_feedback", "free_query", "review_free_query_feedback",
                     "revise_free_query_presentation", "sample_discovery", "acceptance_baseline", "acceptance_free_query"}:
        return existing if existing is not None else OPERATION_BUDGETS[operation]
    return min(existing, OPERATION_BUDGETS[operation]) if existing is not None else OPERATION_BUDGETS[operation]


def orchestration_digest(operation: str) -> str:
    if operation not in OPERATIONS:
        raise ValueError("runtime_operation_unavailable")
    root = Path(__file__).parent
    names = ("runtime_contract.py", "runtime_policy.py", "runtime_prompts.py", "shared_planner.py",
             "runtime_query_contract.py", "runtime_workflow_contract.py", "runtime_agent_authoring.py",
             "runtime_workflow_authoring.py", "runtime_harness_contract.py", "runtime_harness_result.py",
             "runtime_role_contract.py", "runtime_role_consolidation.py", "role_matching.py", "runtime_diagnostics.py", "runtime_sample.py", "runtime_harness.py",
             "codex_driver.py", "workbuddy_driver.py", "workflow_authoring_runtime.py", "sample_discovery.py", "harness.py",
             "codex_planner.py", "workbuddy_planner.py", "workbuddy_harness.py", "workbuddy_authoring.py",
             "workbuddy_sample.py", "workbuddy_diagnostics.py", "workbuddy_supervisor.py",
             "runtime_execution.py", "runtime_query_history.py", "runtime.py", "engine.py",
             "workflow_factory.py", "workflow_assistant.py", "factory.py")
    # Compatibility facades still own native startup/formatting helpers. Their
    # behavior cannot change underneath an earlier operation qualification.
    # A checkout's CRLF policy must not change business qualification.
    values = {name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for name in names}
    return hashlib.sha256(json.dumps({"version": ORCHESTRATION_VERSION, "operation": operation,
                                      "files": values}, sort_keys=True).encode()).hexdigest()
