"""WorkBuddy workflow v2 implementation; no Codex planner/SDK/private helpers."""
import json
import uuid
from pathlib import Path

from jsonschema import ValidationError, validate

from .authoring_workspace import AuthoringWorkspace
from .workbuddy_environment import WorkBuddyError

from .runtime_workflow_authoring import OUTPUT_SCHEMA, instructions as authoring_instructions, prompt as authoring_prompt, decode as decode_authoring


def capabilities(mode):
    return {"contract": "workflow_authoring.v2", "mode": mode, "file_edit": "operation_validation_required",
        "shell_tests": "operation_validation_required", "web": "not_verified", "mcp": "platform_authorized_only",
        "subagents": "not_verified", "resume": "platform_history", "steer": "queue_only",
        "stream_events": "worker_events", "image_input": "not_verified", "os_isolation": False if mode == "full_access" else "unverified"}


async def run(planner, **kwargs):
    """Compatibility entry; never applies or recompiles a candidate here."""
    from .runtime_workflow_authoring import run as shared_run
    from .runtime_contract import RuntimeContractError
    try:
        return await shared_run(planner, **kwargs)
    except (RuntimeContractError, ValueError) as exc:
        code = exc.code if isinstance(exc, RuntimeContractError) else str(exc)
        failure = WorkBuddyError("workbuddy_structured_output_invalid" if isinstance(exc, RuntimeContractError) else code)
        failure.detail = getattr(exc, "detail", {})
        issue = next(iter(failure.detail.get("validation_issues", [])), {})
        kwargs["emit"]("validation_failed", {"code": failure.code, **failure.detail,
            "constraint": "json_object" if issue.get("constraint") in {"json", "type"} else issue.get("constraint"),
            "path": issue.get("path", "/")})
        raise failure from None


def _safe_progress(kind, data):
    from .workbuddy_diagnostics import safe_progress
    return safe_progress(kind, data)
