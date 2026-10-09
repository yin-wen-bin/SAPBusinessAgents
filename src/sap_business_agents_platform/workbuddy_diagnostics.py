"""Legacy WorkBuddy error/event encoding; checks come from the shared contract."""
from .runtime_diagnostics import output_diagnostic, schema_issues as _schema_issues, checked_output as _checked_output
from .workbuddy_compat import legacy
import re
import json

NATIVE_DIAGNOSTICS = "sapba.workbuddy.native-output/1"
NATIVE_FAILURE_CODES = frozenset({
    "workbuddy_execution_failed", "workbuddy_upstream_empty_stream", "workbuddy_upstream_error",
    "workbuddy_result_missing", "workbuddy_structured_output_missing", "workbuddy_structured_output_invalid",
    "workbuddy_schema_check_response_invalid", "workbuddy_tool_response_invalid",
})


def safe_native_failure(value):
    raw = value if isinstance(value, dict) else {}
    code = raw.get("failure_code")
    result = {"failure_code": code if isinstance(code, str) and code in NATIVE_FAILURE_CODES
              else "workbuddy_execution_failed"}
    source = raw.get("failure_source")
    result["failure_source"] = source if isinstance(source, str) and source in {
        "error_message", "error_result", "message_stream_end", "sdk_exception"} else "unknown"
    for name in ("elapsed_ms", "message_count", "assistant_message_count", "stream_event_count"):
        value = raw.get(name)
        if type(value) is int and 0 <= value <= 2**53:
            result[name] = value
    if type(raw.get("terminal_result_received")) is bool:
        result["terminal_result_received"] = raw["terminal_result_received"]
    return result


def native_output_diagnostic(value, schema, *, operation, phase):
    """Validate against the caller's frozen Schema; never persist the value.

    This is diagnostic only. It cannot promote a tool argument to a final reply
    or satisfy evidence, operation qualification or the native-terminal gate.
    """
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    issues = _schema_issues(value, schema)
    from .runtime_role_consolidation import SCHEMA as final_schema, VERSION as final_version
    return {"provider_id": "workbuddy",
            **({"contract_id": final_version} if schema == final_schema else {}),
            **output_diagnostic(encoded, operation=operation, output_format="native_json_schema"),
            "phase": phase, "schema_valid": not issues, "validation_issues": issues,
            "output_type": "object" if isinstance(value, dict) else "array" if isinstance(value, list)
            else "null" if value is None else "boolean" if isinstance(value, bool)
            else "string" if isinstance(value, str) else "number"}

def schema_issues(raw, schema):
    return [{**issue, "code": issue["code"].replace("runtime_", "workbuddy_", 1)}
            for issue in _schema_issues(raw, schema)]

checked_output = legacy(_checked_output)

_EVENTS = {
    "agent_runtime_tool_started", "agent_runtime_tool_finished", "agent_runtime_result_received",
    "agent_runtime_sdk_closing", "agent_runtime_sdk_closed",
    "workbuddy_native_output_checked",
    "workbuddy_native_failure",
}
_TOOLS = {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "MultiEdit", "TodoWrite", "StructuredOutput", "unrecognized"}


def safe_progress(kind: str, data: dict) -> dict:
    if kind not in _EVENTS or data.get("provider_id") != "workbuddy":
        return {}
    if kind == "workbuddy_native_failure":
        return safe_native_failure(data)
    public = {}
    name = data.get("tool")
    if isinstance(name, str) and name in _TOOLS:
        public["tool"] = name
    elif isinstance(name, str) and re.fullmatch(r"mcp__sapba__[a-z][a-z0-9_]{0,60}", name):
        public["tool"] = name
    for field in ("call_index", "elapsed_ms", "pending_tool_count", "output_length", "content_length",
                  "structured_call_count", "structured_result_count"):
        value = data.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3_600_000:
            public[field] = value
    for field in ("matched_call", "is_error", "platform_tool", "terminal_result_received",
                  "structured_output_present", "schema_valid", "native_capture_ack"):
        value = data.get(field)
        if isinstance(value, bool) or field == "is_error" and value is None and field in data:
            public[field] = value
    for field in ("schema_sha256", "output_sha256", "content_sha256"):
        value = data.get(field)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            public[field] = value
    for field, allowed in {
        "phase": {"native_candidate", "native_terminal"},
        "output_type": {"object", "array", "null", "boolean", "string", "number"},
        "result_subtype": {"success", "error_max_turns", "error_during_execution",
                           "error_max_budget_usd", "error_max_structured_output_retries", "unknown"},
        "stop_reason": {"end_turn", "max_tokens", "stop_sequence", "tool_use", "cancelled", "unknown"},
    }.items():
        if isinstance(data.get(field), str) and data[field] in allowed:
            public[field] = data[field]
    # The supervisor creates this event using the frozen schema, not SDK text.
    if kind == "workbuddy_native_output_checked":
        from .runtime_role_consolidation import VERSION as final_version
        if data.get("contract_id") == final_version:
            public["contract_id"] = final_version
        issues = data.get("validation_issues")
        public["validation_issues"] = [
            {"code": "runtime_report_schema_invalid", "path": issue["path"], "constraint": issue["constraint"]}
            for issue in (issues[:50] if isinstance(issues, list) else [])
            if isinstance(issue, dict) and issue.get("code") == "runtime_report_schema_invalid"
            and isinstance(issue.get("path"), str) and len(issue["path"]) <= 512
            and isinstance(issue.get("constraint"), str)
            and issue.get("constraint") in {"required", "additionalProperties", "type", "enum", "anyOf",
                "oneOf", "allOf", "minimum", "maximum", "minItems", "maxItems", "pattern", "const",
                "minLength", "maxLength", "uniqueItems", "format", "not", "multipleOf"}
        ]
    return public
