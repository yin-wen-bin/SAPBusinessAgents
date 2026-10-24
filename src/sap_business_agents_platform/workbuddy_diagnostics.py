"""Legacy WorkBuddy error/event encoding; checks come from the shared contract."""
from .runtime_diagnostics import output_diagnostic, schema_issues as _schema_issues, checked_output as _checked_output
from .workbuddy_compat import legacy
import re
import json

NATIVE_DIAGNOSTICS = "sapba.workbuddy.native-output/1"
NATIVE_FAILURE_CODES = frozenset({
    "workbuddy_authentication_required", "workbuddy_authentication_refresh_failed",
    "workbuddy_execution_failed", "workbuddy_upstream_empty_stream", "workbuddy_upstream_error",
    "workbuddy_result_missing", "workbuddy_structured_output_missing", "workbuddy_structured_output_invalid",
    "workbuddy_schema_check_response_invalid", "workbuddy_tool_response_invalid",
    "workbuddy_terminal_correction_stalled", "workbuddy_terminal_protocol_invalid",
})

# Host-side failures are not SDK/upstream errors. Keep their vocabulary and
# protocol positions separate; never persist exception text, paths or traceback.
HOST_FAILURE_CODES = frozenset({"workbuddy_event_delivery_failed", "workbuddy_worker_transport_failed",
                               "workbuddy_host_io_failed", "workbuddy_host_execution_failed"})
HOST_PHASES = frozenset({"job_record_write", "event_record_write", "cleanup_record_write", "request_write", "request_drain", "handshake_read", "message_read",
    "tool_handler", "tool_reply_write", "tool_reply_drain", "schema_validation", "schema_check_event",
    "schema_reply_write", "schema_reply_drain", "event_dispatch", "result_validation", "failure_event"})
HOST_ERROR_CLASSES = frozenset({"BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError",
    "PermissionError", "FileNotFoundError", "OSError", "EOFError", "RuntimeError", "TypeError",
    "ValueError", "KeyError", "AttributeError", "ExceptionGroup", "BaseExceptionGroup", "unknown"})


def host_failure(error, phase):
    """Classify a trusted host exception without consulting its sensitive text.

    Do not infer upstream availability or promote a schema-valid candidate. A
    user-defined exception class is unknown even if it spoofs a builtin name.
    """
    import builtins
    error_class = next((name for name in HOST_ERROR_CLASSES if name != "unknown"
                        and type(error) is getattr(builtins, name, None)), "unknown")
    if phase in {"schema_check_event", "event_dispatch", "failure_event"}:
        code = "workbuddy_event_delivery_failed"
    elif isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, EOFError)):
        code = "workbuddy_worker_transport_failed"
    elif isinstance(error, OSError):
        code = "workbuddy_host_io_failed"
    else:
        code = "workbuddy_host_execution_failed"
    windows_error = getattr(error, "winerror", None)
    return {"failure_code": code, "failure_source": "supervisor_host", "error_class": error_class,
            "host_error_class": error_class,
            **({"windows_error_code": windows_error} if type(windows_error) is int
               and windows_error in {2, 3, 5, 32, 33, 112} else {}),
            **({"protocol_phase": phase} if phase in HOST_PHASES else {})}


def safe_native_failure(value):
    raw = value if isinstance(value, dict) else {}
    code = raw.get("failure_code")
    result = {"failure_code": code if isinstance(code, str) and code in NATIVE_FAILURE_CODES
              else "workbuddy_execution_failed"}
    source = raw.get("failure_source")
    result["failure_source"] = source if isinstance(source, str) and source in {
        "error_message", "error_result", "message_stream_end", "sdk_exception", "formatter_hook", "owned_cli_trace", "owned_worker_stderr"} else "unknown"
    if result['failure_code'] == 'workbuddy_authentication_required' and raw.get('http_status') == 401:
        result['http_status'] = 401
    if result['failure_code'] == 'workbuddy_authentication_required' and raw.get('authentication_reason') == 'login_required':
        result['authentication_reason'] = 'login_required'
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
    clarification_fields = {}
    if isinstance(value, dict):
        for name in ("input_kind", "input_field"):
            definition = schema.get("properties", {}).get(name)
            if not isinstance(definition, dict) or not isinstance(definition.get("enum"), list):
                continue
            member = value.get(name)
            clarification_fields[name] = {"present": name in value,
                "json_type": "missing" if name not in value else "null" if member is None
                else "boolean" if isinstance(member, bool) else "string" if isinstance(member, str)
                else "array" if isinstance(member, list) else "object" if isinstance(member, dict) else "number",
                "enum_member": name in value and any(type(member) is type(allowed) and member == allowed
                                                      for allowed in definition["enum"])}
    return {"provider_id": "workbuddy",
            **({"clarification_fields": clarification_fields} if clarification_fields else {}),
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
    "agent_runtime_turn_started", "agent_runtime_connected", "agent_runtime_query_submitted", "agent_runtime_activity",
    "agent_runtime_tool_started", "agent_runtime_tool_finished", "agent_runtime_result_received",
    "agent_runtime_sdk_closing", "agent_runtime_sdk_closed",
    "workbuddy_native_output_checked",
    "workbuddy_native_failure",
    "workbuddy_formatter_progress", "workbuddy_native_wait", "workbuddy_native_budget",
}
_TOOLS = {"Bash", "Read", "Write", "Edit", "Glob", "Grep", "MultiEdit", "TodoWrite", "StructuredOutput", "unrecognized"}


def safe_progress(kind: str, data: dict) -> dict:
    if kind not in _EVENTS or data.get("provider_id") != "workbuddy":
        return {}
    if kind == "workbuddy_native_failure":
        return safe_native_failure(data)
    public = {}
    milestone = {"agent_runtime_connected": "sdk_connected", "agent_runtime_query_submitted": "query_submitted",
        "agent_runtime_sdk_closing": "sdk_closing", "agent_runtime_sdk_closed": "sdk_closed"}.get(kind)
    if milestone:
        public[milestone] = True
    name = data.get("tool")
    if isinstance(name, str) and name in _TOOLS:
        public["tool"] = name
    elif isinstance(name, str) and re.fullmatch(r"mcp__sapba__[a-z][a-z0-9_]{0,60}", name):
        public["tool"] = name
    for field in ("call_index", "elapsed_ms", "pending_tool_count", "output_length", "content_length",
                  "structured_call_count", "structured_result_count", "message_count", "assistant_message_count",
                  "stream_event_count", "first_response_ms", "first_assistant_ms", "hook_index", "requested_timeout_ms"):
        value = data.get(field)
        limit = 2**53 if field in {"message_count", "assistant_message_count", "stream_event_count", "first_response_ms", "first_assistant_ms"} else 3_600_000
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit:
            public[field] = value
    for field in ("matched_call", "is_error", "platform_tool", "terminal_result_received",
                  "structured_output_present", "schema_valid", "native_capture_ack", "rejection_prepared"):
        value = data.get(field)
        if isinstance(value, bool) or field == "is_error" and value is None and field in data:
            public[field] = value
    for field in ("schema_sha256", "output_sha256", "content_sha256", "expected_feedback_sha256"):
        value = data.get(field)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            public[field] = value
    for field, allowed in {
        "phase": {"native_candidate", "native_terminal"},
        "output_type": {"object", "array", "null", "boolean", "string", "number"},
        "result_subtype": {"success", "error_max_turns", "error_during_execution",
                           "error_max_budget_usd", "error_max_structured_output_retries", "unknown"},
        "stop_reason": {"end_turn", "max_tokens", "stop_sequence", "tool_use", "cancelled", "unknown"},
        "formatter_stage": {"hook_entered", "check_completed", "rejection_prepared", "hook_returned", "hook_failed",
                            "tool_result_observed", "terminal_checked"},
        "feedback_match": {"matched", "different", "unavailable"},
        "worker_wait_state": {"sdk_connecting", "query_submitting", "waiting_sdk_message", "waiting_platform_schema_check",
                              "native_terminal_received", "sdk_closing", "sdk_closed"},
        "wait_state_source": {"worker_observation"},
        "budget_strategy": {"supervisor_deadline"},
        "native_timeout_propagation": {"unverified"},
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
        shapes = data.get("clarification_fields")
        if isinstance(shapes, dict):
            public["clarification_fields"] = {name: {"present": shape["present"],
                "json_type": shape["json_type"], "enum_member": shape["enum_member"]}
                for name, shape in shapes.items() if name in {"input_kind", "input_field"}
                and isinstance(shape, dict) and type(shape.get("present")) is bool
                and type(shape.get("enum_member")) is bool and isinstance(shape.get("json_type"), str)
                and shape["json_type"] in {"missing", "null", "string", "boolean", "number", "object", "array"}}
    return public
