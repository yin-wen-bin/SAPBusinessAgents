"""Safe execution diagnostics shared by run details and acceptance reports."""
from __future__ import annotations

import re
from typing import Any


def run_diagnostics(store: Any, run_id: str) -> dict[str, Any]:
    calls = store.list_harness_tool_calls(run_id)
    failed = [call for call in calls if call.get("status") == "failed"]
    completed = [call for call in calls if call.get("status") == "completed"]
    last = failed[-1] if failed else {}
    output = last.get("output") if isinstance(last.get("output"), dict) else {}
    def safe_code(value: Any) -> str | None:
        return str(value) if re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", str(value or "")) else None
    detail = output.get("detail") if isinstance(output.get("detail"), dict) else {}
    validation = output.get("validation") if isinstance(output.get("validation"), dict) else {}
    issues = output.get("validation_issues") or detail.get("validation_issues") or validation.get("validation_issues") or []
    if not isinstance(issues, list):
        issues = []
    def scope(call: dict[str, Any]) -> Any:
        arguments = call.get("safe_input") or {}
        plan = arguments.get("plan") or arguments
        if not isinstance(plan, dict):
            return None
        if plan.get("steps"):
            return tuple(scope({"safe_input": step}) for step in plan["steps"] if isinstance(step, dict))
        if plan.get("service_name"):
            return (plan.get("service_name"), plan.get("odata_version"),
                    plan.get("entity_set") or tuple(plan.get("entity_sets") or []), plan.get("mode", "fields"))
        return arguments.get("evidence_ref") or arguments.get("skill_id") or call.get("request_hash")
    resolved = False
    if last:
        later = calls[calls.index(last) + 1:]
        resolved = any(call.get("status") == "completed" and (
            call.get("tool_name") == "sap_final_report_validate"
            or (call.get("tool_name") == last.get("tool_name") and scope(last) is not None and scope(call) == scope(last))
        ) for call in later)
    unresolved = [] if resolved else [
        {"code": safe_code(item.get("code"))} for item in issues
        if isinstance(item, dict) and safe_code(item.get("code"))
    ]
    if not resolved and not unresolved and safe_code(output.get("code")):
        unresolved = [{"code": safe_code(output["code"])}]
    return {
        "last_failed_tool": safe_code(last.get("tool_name")),
        "last_error_code": safe_code(output.get("code") or (issues[0].get("code") if issues and isinstance(issues[0], dict) else None)),
        "last_completed_tool": safe_code(completed[-1].get("tool_name")) if completed else None,
        "actual_phase": safe_code(store.get_run(run_id).progress.phase),
        "unresolved_issues": unresolved,
        "tool_call_count": len(calls),
    }
