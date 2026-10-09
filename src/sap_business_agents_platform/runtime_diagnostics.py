"""SDK-free, allowlisted native progress projection; no command/content logging."""
import re
import hashlib
import json


def output_diagnostic(text, *, operation, output_format=None):
    """Keep enough to identify a terminal failure without saving business rows."""
    value = text.encode("utf-8") if isinstance(text, str) else None
    return {"operation": operation, "phase": "terminal_output", "output_format": output_format,
            "output_length": len(value) if value is not None else None,
            "output_sha256": hashlib.sha256(value).hexdigest() if value is not None else None}


def schema_issues(raw, schema):
    from jsonschema import Draft202012Validator
    issues = []
    for error in Draft202012Validator(schema).iter_errors(raw):
        parts, node = [], schema
        for part in error.absolute_path:
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                node = schema.get("$defs", {}).get(node["$ref"].split("/")[-1], {})
            if isinstance(part, int):
                node = node.get("items", {})
            elif part in node.get("properties", {}):
                node = node["properties"][part]
            else:
                break
            parts.append(str(part).replace("~", "~0").replace("/", "~1"))
        path = "/" + "/".join(parts)
        missing = [name for name in error.schema.get("required", []) if name in error.schema.get("properties", {})
                   and isinstance(error.instance, dict) and name not in error.instance]
        if error.validator == "required" and missing:
            issues.extend({"code": "runtime_report_schema_invalid", "path": path.rstrip("/") + "/" + name,
                           "constraint": "required"} for name in missing)
        else:
            issues.append({"code": "runtime_report_schema_invalid", "path": path, "constraint": str(error.validator)})
        if len(issues) >= 50:
            break
    # A validator emits one `required` error per absent property. The safe
    # expansion above must not repeat the same field for every such error.
    unique = {tuple(item.values()): item for item in issues}
    return list(unique.values())[:50]


_STAGE_CODES = frozenset({
    "runtime_deadline_exceeded", "workbuddy_deadline_exceeded", "runtime_cleanup_incomplete",
    "runtime_report_validation_failed", "runtime_report_json_invalid", "runtime_context_too_large",
    "runtime_result_not_complete", "runtime_cancelled", "workbuddy_structured_output_invalid",
    "workbuddy_structured_output_missing", "workbuddy_worker_exited", "workbuddy_execution_failed",
    "workbuddy_protocol_invalid", "workbuddy_message_limit", "workbuddy_output_limit",
    "workbuddy_cancelled", "role_matching_consolidation_failed",
    "workbuddy_upstream_empty_stream", "workbuddy_upstream_error", "workbuddy_result_missing",
    "runtime_native_schema_rejected",
    "runtime_budget_binding_missing", "runtime_operation_retired", "runtime_platform_context_missing",
    "runtime_session_binding_changed", "runtime_native_schema_required",
})
_CONSTRAINTS = frozenset({"json", "required", "type", "enum", "additionalProperties", "minItems",
    "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern", "anyOf", "oneOf", "uniqueItems"})


def _declared_path(path, schema):
    if not isinstance(path, str) or len(path) > 256 or not path.startswith("/"):
        return False
    if path == "/":
        return True
    node = schema
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if "$ref" in node:
            node = schema.get("$defs", {}).get(node["$ref"].split("/")[-1], {})
        if part in node.get("properties", {}):
            node = node["properties"][part]
        elif part.isdigit() and "items" in node:
            node = node["items"]
        else:
            return False
    return True


def safe_stage_diagnostic(value, *, schema, operation=None, phase=None, contract_id=None):
    """Project platform errors, never messages, SDK logs or business values."""
    raw = value if isinstance(value, dict) else {}
    code = raw.get("failure_code")
    from .runtime_role_consolidation import SCHEMA as final_schema, VERSION as final_version
    fallback = "role_matching_consolidation_failed" if phase == "role_consolidation" or schema == final_schema else "runtime_execution_failed"
    code = code if isinstance(code, str) and code in _STAGE_CODES else fallback
    result = {"failure_code": code, "phase": phase or "execution"}
    if operation:
        result["operation"] = operation
    if contract_id:
        result["contract_id"] = contract_id
    if schema == final_schema:
        result["contract_id"] = final_version
        if phase is None:
            result["phase"] = "role_consolidation"
    for name in ("primary_failure_code", "cleanup_failure_code"):
        value = raw.get(name)
        if isinstance(value, str) and value in _STAGE_CODES:
            result[name] = value
    if isinstance(raw.get("failure_source"), str) and raw["failure_source"] in {"error_message", "error_result", "message_stream_end", "sdk_exception", "unknown"}:
        result["failure_source"] = raw["failure_source"]
    for name in ("message_count", "assistant_message_count", "stream_event_count"):
        value = raw.get(name)
        if type(value) is int and 0 <= value <= 2**53:
            result[name] = value
    if type(raw.get("terminal_result_received")) is bool:
        result["terminal_result_received"] = raw["terminal_result_received"]
    result["failure_category"] = (
        "timeout" if code in {"runtime_deadline_exceeded", "workbuddy_deadline_exceeded"}
        else "cleanup" if code == "runtime_cleanup_incomplete"
        else "contract" if code in {"runtime_report_validation_failed", "runtime_report_json_invalid",
            "workbuddy_structured_output_invalid", "workbuddy_structured_output_missing", "runtime_result_not_complete", "runtime_native_schema_rejected"}
        else "runtime"
    )
    for name in ("input_length", "output_length", "budget_seconds", "elapsed_ms"):
        item = raw.get(name)
        if type(item) is int and 0 <= item <= 2**53:
            result[name] = item
    for name in ("input_sha256", "output_sha256"):
        item = raw.get(name)
        if isinstance(item, str) and re.fullmatch("[a-f0-9]{64}", item):
            result[name] = item
    issues = []
    for item in raw.get("validation_issues", []) if isinstance(raw.get("validation_issues"), list) else []:
        if (isinstance(item, dict) and isinstance(item.get("code"), str)
                and item["code"] in {"runtime_report_schema_invalid", "runtime_report_json_invalid"}
                and isinstance(item.get("constraint"), str) and item["constraint"] in _CONSTRAINTS
                and _declared_path(item.get("path"), schema)):
            issue = {key: item[key] for key in ("code", "path", "constraint")}
            if issue not in issues:
                issues.append(issue)
        if len(issues) >= 50:
            break
    if issues:
        result["validation_issues"] = issues
    return result


def stage_failure(error, *, schema, prompt=None, budget_seconds=None, elapsed_ms=None, operation=None, phase=None):
    code = getattr(error, "code", None)
    if isinstance(error, TimeoutError):
        code = "runtime_deadline_exceeded"
    elif isinstance(error, json.JSONDecodeError):
        code = "runtime_report_json_invalid"
    detail = getattr(error, "detail", {})
    detail = detail if isinstance(detail, dict) else {}
    raw = {**detail, "failure_code": code, "budget_seconds": budget_seconds, "elapsed_ms": elapsed_ms}
    if isinstance(prompt, str):
        encoded = prompt.encode("utf-8")
        raw.update(input_length=len(encoded), input_sha256=hashlib.sha256(encoded).hexdigest())
    return safe_stage_diagnostic(raw, schema=schema, operation=operation, phase=phase)


def checked_output(text, schema, *, operation, output_format=None):
    from .runtime_contract import RuntimeContractError
    detail = output_diagnostic(text, operation=operation, output_format=output_format)
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        issues = [{"code": "runtime_report_json_invalid", "path": "/", "constraint": "json"}]
    else:
        issues = schema_issues(raw, schema)
    if issues:
        error = RuntimeContractError("runtime_report_validation_failed")
        error.detail = {**detail, "validation_issues": issues}
        raise error
    return raw

