"""Business operation policies, independent of native SDK timeout defaults."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ORCHESTRATION_VERSION = "1.0"
OPERATIONS = (
    "plan", "ground_plan", "summarize", "author_draft", "review_agent_feedback",
    "compose_workflow", "review_workflow", "repair_workflow", "review_workflow_feedback",
    "review_free_query_feedback", "revise_free_query_presentation", "analyze_role_matching",
    "review_role_matching_feedback", "workflow_authoring.v2", "free_query",
    "sample_discovery", "acceptance_baseline", "acceptance_free_query",
)
ROLE_MATCHING_RUNTIME_TURN_SECONDS = 300
WORKBUDDY_WORKFLOW_SECONDS = 3600


def operation_seconds(provider_id: str, operation: str, *, existing: float | None = None) -> float | None:
    if provider_id == "workbuddy":
        if operation in {"compose_workflow", "review_workflow_feedback", "review_agent_feedback", "workflow_authoring.v2"}:
            return WORKBUDDY_WORKFLOW_SECONDS
        if operation in {"analyze_role_matching", "review_role_matching_feedback"}:
            return ROLE_MATCHING_RUNTIME_TURN_SECONDS
    # Codex's existing per-entry SDK and outer budgets are not extended here.
    return existing


def orchestration_digest(operation: str) -> str:
    if operation not in OPERATIONS:
        raise ValueError("runtime_operation_unavailable")
    root = Path(__file__).parent
    names = ("runtime_contract.py", "runtime_policy.py", "runtime_prompts.py", "shared_planner.py",
             "runtime_query_contract.py", "runtime_workflow_contract.py", "runtime_agent_authoring.py",
             "runtime_workflow_authoring.py", "runtime_harness_contract.py", "runtime_harness_result.py",
             "runtime_role_contract.py", "runtime_diagnostics.py", "runtime_sample.py", "runtime_harness.py",
             "codex_driver.py", "workbuddy_driver.py", "workflow_authoring_runtime.py", "sample_discovery.py", "harness.py",
             "codex_planner.py", "workbuddy_planner.py", "workbuddy_harness.py", "workbuddy_authoring.py",
             "workbuddy_sample.py", "workbuddy_diagnostics.py")
    # Compatibility facades still own native startup/formatting helpers. Their
    # behavior cannot change underneath an earlier operation qualification.
    # A checkout's CRLF policy must not change business qualification.
    values = {name: hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for name in names}
    return hashlib.sha256(json.dumps({"version": ORCHESTRATION_VERSION, "operation": operation,
                                      "files": values}, sort_keys=True).encode()).hexdigest()
