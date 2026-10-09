"""Read-only execution-flow classification; never infer it from an SDK ID."""


def query_flow(record, events=(), snapshot=None):
    runtime = snapshot or (record.runtime.model_dump(mode="json") if record.runtime else {})
    declared = runtime.get("execution_flow")
    findings = set()
    if declared in {"harness", "planner_legacy"}:
        findings.add(declared)
    if record.result is not None and record.result.harness is not None:
        findings.add("harness")
    for event in events:
        kind = event.type if hasattr(event, "type") else event.get("type")
        data = event.data if hasattr(event, "data") else event.get("data", {})
        if kind == "harness_completed" or (kind == "planning_started" and data.get("runtime") in {"codex_app_server", "workbuddy_worker"}):
            findings.add("harness")
        if kind == "validation_started" and data.get("phase") == "live_schema_grounding":
            findings.add("planner_legacy")
    return next(iter(findings)) if len(findings) == 1 else "unknown"
