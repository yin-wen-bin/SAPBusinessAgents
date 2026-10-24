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


def schema_definition(schema, path):
    """Resolve declared paths through refs/unions, never through input keys.

    Ambiguous branch definitions yield no metadata, not a guessed constraint.
    """
    def resolve(node, parts, depth=0):
        if depth > 32 or not isinstance(node, dict): return None
        ref = node.get('$ref')
        if isinstance(ref, str) and ref.startswith('#/$defs/'):
            node = schema.get('$defs', {}).get(ref[8:], {})
        if not parts: return node
        part, rest = parts[0], parts[1:]
        candidates = []
        if part in node.get('properties', {}):
            candidates.append(resolve(node['properties'][part], rest, depth + 1))
        elif str(part).isdecimal() and isinstance(node.get('items'), dict):
            candidates.append(resolve(node['items'], rest, depth + 1))
        for keyword in ('anyOf', 'oneOf'):
            candidates.extend(resolve(branch, parts, depth + 1) for branch in node.get(keyword, []))
        candidates = [value for value in candidates if value is not None]
        return candidates[0] if candidates and all(value == candidates[0] for value in candidates) else None
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 256: return None
    parts = [] if path == '/' else [p.replace('~1', '/').replace('~0', '~') for p in path[1:].split('/')]
    return resolve(schema, parts)


def schema_issues(raw, schema):
    from jsonschema import Draft202012Validator
    issues = {}
    def diagnostics(error):
        yield error  # Retain the original union error and its historical signature.
        if error.validator not in {'anyOf', 'oneOf'} or not error.context: return
        branches = error.schema.get(error.validator, [])
        matching = []
        for index, branch in enumerate(branches):
            types = branch.get('type')
            types = [types] if isinstance(types, str) else types
            if types and any(Draft202012Validator.TYPE_CHECKER.is_type(error.instance, kind) for kind in types):
                matching.append(index)
        if len(matching) == 1:
            for child in error.context:
                if child.schema_path and child.schema_path[0] == matching[0]:
                    yield from diagnostics(child)
    errors = (nested for error in Draft202012Validator(schema).iter_errors(raw) for nested in diagnostics(error))
    for error in errors:
        parts = []
        for part in error.absolute_path:
            candidate = parts + [str(part).replace('~', '~0').replace('/', '~1')]
            if schema_definition(schema, '/' + '/'.join(candidate)) is None: break
            parts = candidate
        path = "/" + "/".join(parts)
        missing = [name for name in error.schema.get("required", []) if name in error.schema.get("properties", {})
                   and isinstance(error.instance, dict) and name not in error.instance]
        if error.validator == "required" and missing:
            entries = [{"code": "runtime_report_schema_invalid", "path": path.rstrip("/") + "/" + name,
                           "constraint": "required"} for name in missing]
        else:
            entries = [{"code": "runtime_report_schema_invalid", "path": path, "constraint": str(error.validator)}]
        for item in entries:
            if len(issues) >= 50: break
            issues[tuple(item.values())] = item
        if len(issues) >= 50:
            break
    # A validator emits one `required` error per absent property. The safe
    # expansion above must not repeat the same field for every such error.
    return list(issues.values())


_STAGE_CODES = frozenset({
    "workbuddy_authentication_required", "workbuddy_authentication_refresh_failed",
    "runtime_deadline_exceeded", "workbuddy_deadline_exceeded", "runtime_cleanup_incomplete",
    "runtime_report_validation_failed", "runtime_report_json_invalid", "runtime_context_too_large",
    "runtime_result_not_complete", "runtime_cancelled", "workbuddy_structured_output_invalid",
    "workbuddy_structured_output_missing", "workbuddy_worker_exited", "workbuddy_execution_failed",
    "workbuddy_terminal_correction_stalled", "workbuddy_terminal_protocol_invalid",
    "workbuddy_protocol_invalid", "workbuddy_message_limit", "workbuddy_output_limit",
    "workbuddy_cancelled", "role_matching_consolidation_failed",
    "workbuddy_upstream_empty_stream", "workbuddy_upstream_error", "workbuddy_result_missing",
    "workbuddy_event_delivery_failed", "workbuddy_worker_transport_failed",
    "workbuddy_host_io_failed", "workbuddy_host_execution_failed",
    "runtime_native_schema_rejected",
    "runtime_budget_binding_missing", "runtime_operation_retired", "runtime_platform_context_missing",
    "runtime_session_binding_changed", "runtime_native_schema_required",
    "runtime_native_rpc_error", "runtime_native_transport_closed", "runtime_native_turn_failed",
    "runtime_native_execution_failed",
    "role_matching_evidence_ref_invalid",
})
NATIVE_ERROR_CODES = frozenset({"contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
    "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "cyberPolicy", "misalignmentPolicyViolation",
    "tooManyDenials", "internalServerError", "unauthorized", "badRequest", "threadRollbackFailed",
    "sandboxError", "other", "httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts", "activeTurnNotSteerable"})
NATIVE_ERROR_CLASSES = frozenset({"CodexError", "JsonRpcError", "CodexRpcError", "ParseError",
    "InvalidRequestError", "MethodNotFoundError", "InvalidParamsError", "InternalRpcError",
    "ServerBusyError", "RetryLimitExceededError", "TransportClosedError", "RuntimeError", "TimeoutError", "unknown"})
_CONSTRAINTS = frozenset({"json", "required", "type", "enum", "additionalProperties", "minItems",
    "maxItems", "minLength", "maxLength", "minimum", "maximum", "pattern", "anyOf", "oneOf", "uniqueItems",
    "known_document_reference"})


def _declared_path(path, schema):
    if not isinstance(path, str) or len(path) > 256 or not path.startswith("/"):
        return False
    if path == "/":
        return True
    return schema_definition(schema, path) is not None


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
    # The legacy SDK envelope encodes analysis_json as a string. Inner field
    # errors belong to its existing business schema, not that wire envelope.
    # Keep the explicit known contract through subsequent service projections;
    # never accept a caller-supplied schema or arbitrary paths.
    issue_schema = schema
    from .runtime_role_contract import DIAGNOSTIC_CONTRACT, SCHEMA as role_schema
    if (phase in {"role_understanding", "role_catalog_page"}
            and raw.get("output_contract_id") == DIAGNOSTIC_CONTRACT):
        issue_schema = role_schema
        result["output_contract_id"] = DIAGNOSTIC_CONTRACT
    for name in ("primary_failure_code", "cleanup_failure_code"):
        value = raw.get(name)
        if isinstance(value, str) and value in _STAGE_CODES:
            result[name] = value
    if isinstance(raw.get("failure_source"), str) and raw["failure_source"] in {"error_message", "error_result", "message_stream_end", "sdk_exception", "turn_completed", "unknown", "owned_cli_trace", "owned_worker_stderr"}:
        result["failure_source"] = raw["failure_source"]
    if code == 'workbuddy_authentication_required' and raw.get('http_status') == 401:
        result['http_status'] = 401
    if code == 'workbuddy_authentication_required' and raw.get('authentication_reason') == 'login_required':
        result['authentication_reason'] = 'login_required'
    if code == 'workbuddy_authentication_required' and raw.get('authentication_sync_failed') is True:
        result['authentication_sync_failed'] = True
    for name, allowed in (("native_error_code", NATIVE_ERROR_CODES), ("native_error_class", NATIVE_ERROR_CLASSES),
                          ("native_terminal_status", {"completed", "failed", "interrupted"}),
                          ("native_phase", {"sdk_initialize", "thread_start", "thread_resume", "turn_start", "message_stream"}),
                          ("error_class", NATIVE_ERROR_CLASSES | {"TypeError", "ValueError", "KeyError", "AttributeError", "JSONDecodeError", "RuntimeContractError", "WorkBuddyError"})):
        if isinstance(raw.get(name), str) and raw[name] in allowed:
            result[name] = raw[name]
    # Additive WorkBuddy host diagnostics only. Do not reinterpret Codex/native
    # errors or expose arbitrary exception names, paths or callback content.
    if isinstance(code, str) and code.startswith("workbuddy_"):
        from .workbuddy_diagnostics import HOST_PHASES, HOST_ERROR_CLASSES
        if raw.get("failure_source") == "supervisor_host":
            result["failure_source"] = "supervisor_host"
        for name, allowed in (("protocol_phase", HOST_PHASES),
                ("last_frame_kind", {"tool_call", "schema_check", "event", "result", "error"}),
                ("schema_reply_state", {"prepared", "written", "drained"}),
                ("schema_check_phase", {"native_candidate", "native_terminal"}),
                ("host_error_class", HOST_ERROR_CLASSES), ("delivery_error_class", HOST_ERROR_CLASSES)):
            if isinstance(raw.get(name), str) and raw[name] in allowed:
                result[name] = raw[name]
        if type(raw.get("schema_check_count")) is int and 0 <= raw["schema_check_count"] <= 2**53:
            result["schema_check_count"] = raw["schema_check_count"]
        if type(raw.get("windows_error_code")) is int and raw["windows_error_code"] in {2, 3, 5, 32, 33, 112}:
            result["windows_error_code"] = raw["windows_error_code"]
        for name in ("last_schema_valid", "failure_event_delivery_failed", "record_persistence_failed"):
            if type(raw.get(name)) is bool:
                result[name] = raw[name]
    if type(raw.get("native_rpc_code")) is int and -(2**31) <= raw["native_rpc_code"] < 2**31:
        result["native_rpc_code"] = raw["native_rpc_code"]
    for name in ("message_count", "assistant_message_count", "stream_event_count"):
        value = raw.get(name)
        if type(value) is int and 0 <= value <= 2**53:
            result[name] = value
    if type(raw.get("terminal_result_received")) is bool:
        result["terminal_result_received"] = raw["terminal_result_received"]
    for name in ("sdk_connected", "query_submitted", "sdk_closing", "sdk_closed"):
        if type(raw.get(name)) is bool:
            result[name] = raw[name]
    result["failure_category"] = (
        "authentication" if code in {"workbuddy_authentication_required", "workbuddy_authentication_refresh_failed"}
        else "timeout" if code in {"runtime_deadline_exceeded", "workbuddy_deadline_exceeded"}
        else "cleanup" if code == "runtime_cleanup_incomplete"
        else "contract" if code in {"runtime_report_validation_failed", "runtime_report_json_invalid",
            "workbuddy_structured_output_invalid", "workbuddy_structured_output_missing", "runtime_result_not_complete", "runtime_native_schema_rejected", "role_matching_evidence_ref_invalid"}
        else "runtime"
    )
    for name in ("input_length", "output_length", "budget_seconds", "elapsed_ms", "first_response_ms", "first_assistant_ms", "page_index"):
        item = raw.get(name)
        if type(item) is int and 0 <= item <= 2**53:
            result[name] = item
    for name in ("input_sha256", "output_sha256", "schema_sha256"):
        item = raw.get(name)
        if isinstance(item, str) and re.fullmatch("[a-f0-9]{64}", item):
            result[name] = item
    issues = []
    for item in raw.get("validation_issues", []) if isinstance(raw.get("validation_issues"), list) else []:
        if (isinstance(item, dict) and isinstance(item.get("code"), str)
                and item["code"] in {"runtime_report_schema_invalid", "runtime_report_json_invalid", "role_matching_evidence_ref_invalid"}
                and isinstance(item.get("constraint"), str) and item["constraint"] in _CONSTRAINTS
                and _declared_path(item.get("path"), issue_schema)):
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
    if not isinstance(code, str) or code not in _STAGE_CODES:
        code = detail.get("failure_code", code)
    raw = {**detail, "failure_code": code, "budget_seconds": budget_seconds, "elapsed_ms": elapsed_ms}
    raw["error_class"] = type(error).__name__
    raw["schema_sha256"] = hashlib.sha256(json.dumps(schema, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
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

