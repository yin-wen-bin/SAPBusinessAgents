"""Formatter rejection/correction uses real platform checks, no model or SAP."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker as worker
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic, safe_progress
from sap_business_agents_platform.workbuddy_site import make_configuration, routing
from tests.test_workbuddy_harness_nullable import schema, envelope


def exercise(monkeypatch, candidates, *, terminal=True, bypass_hook=False, wire_schema=None):
    frozen = copy.deepcopy(wire_schema if wire_schema is not None else schema())
    schema_before = copy.deepcopy(frozen)
    before = copy.deepcopy(candidates)
    observed, decisions, checks, public, pending, captured = [], [], [], [], [], []
    class ToolUse:
        id, name = "local-call", "StructuredOutput"
        def __init__(self, value): self.input = value
    class Assistant:
        model = "hy4-preview"
        def __init__(self, blocks): self.content = blocks
    class Result:
        is_error, session_id, result = False, "local-session", "not a constrained terminal"
        def __init__(self): self.structured_output = copy.deepcopy(captured[-1]) if captured and terminal else None
    class Client:
        def __init__(self, *, options): self.options = options; observed.append(options)
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self):
            callback = self.options.hooks["PreToolUse"][0].hooks[0]
            for value in candidates:
                # Simulate the pinned SDK's pre-tool callback, before capture.
                decision = {} if bypass_hook else await callback(
                    {"tool_name": "StructuredOutput", "tool_input": value}, "local-call", {})
                decisions.append(decision)
                if decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                    yield Assistant([])
                    continue
                captured.append(copy.deepcopy(value))
                yield Assistant([ToolUse(value)])
            yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        ResultMessage=Result, AssistantMessage=Assistant, ToolUseBlock=ToolUse,
        TextBlock=type("Text", (), {}), ToolResultBlock=type("ToolResult", (), {}),
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace, HookMatcher=SimpleNamespace))
    def send(kind, **data):
        if kind == "schema_check":
            diagnosis = native_output_diagnostic(data["value"], frozen, operation="free_query", phase=data["phase"])
            checks.append(diagnosis)
            public.append(safe_progress("workbuddy_native_output_checked", diagnosis))
            pending.append({"kind": "schema_check_result", "call_id": data["call_id"], "task_id": "task",
                "attempt_id": "attempt", "schema_sha256": worker.fingerprint(frozen),
                "schema_valid": diagnosis["schema_valid"], "validation_issues": diagnosis["validation_issues"]})
    monkeypatch.setattr(worker, "read_frame", lambda: pending.pop(0))
    request = {"operation": "free_query", "mode": "bounded", "task_id": "task", "attempt_id": "attempt",
        "cli_path": "fixture-cli", "snapshot": {"model": "hy4-preview", **make_configuration("china", 1)},
        "service_site_config": routing(make_configuration("china", 1)),
        "native_output_diagnostics": worker.NATIVE_DIAGNOSTICS,
        "payload": {"cwd": "fixture", "prompt": "unchanged business task", "output_schema": frozen,
                    "max_turns": 50, "timeout_ms": 1800000}}
    async def run():
        try:
            return await worker.execute(request, send)
        finally:
            assert frozen == schema_before and candidates == before
    return run, observed, decisions, checks, public, captured


@pytest.mark.parametrize("bad", ["null", "", "None", "undefined", "private-wrong-PO", 7, False])
def test_invalid_enum_is_denied_then_model_supplied_json_null_succeeds(monkeypatch, bad):
    raw = {**envelope(), "input_kind": bad, "input_field": bad}
    run, observed, decisions, checks, public, captured = exercise(monkeypatch, [raw, envelope()])
    result = asyncio.run(run())
    assert json.loads(result["text"]) == envelope() and result["output_format"] == "native_json_schema"
    assert captured == [envelope()]  # No platform repair of the rejected object.
    reason = decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    assert decisions[0]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "/input_kind" in reason and "/input_field" in reason and "literal JSON null" in reason
    assert "private-wrong-PO" not in reason and "private-wrong-PO" not in json.dumps(public)
    assert decisions[1] == {} and not checks[0]["schema_valid"] and checks[-1]["schema_valid"]
    options = observed[0]
    assert options.hooks["PreToolUse"][0].matcher == "^StructuredOutput$"
    assert json.loads(options.extra_args["json-schema"]) == schema()
    assert options.permission_mode == "plan" and options.tools == options.allowed_tools == ["StructuredOutput"]
    assert options.max_turns == 50 and options.request_timeout_ms == 1800000
    assert options.setting_sources == [] and options.resume is None and options.persist_session is False
    assert options.env["CODEBUDDY_INTERNET_ENVIRONMENT"] == "internal"
    assert "Bash" not in options.allowed_tools and not options.mcp_servers


def test_missing_fields_are_not_filled_and_secure_enum_values_are_not_replaced(monkeypatch):
    invalid = envelope(); invalid.pop("input_kind"); invalid.pop("input_field")
    valid = {**envelope(), "status": "waiting_input", "input_kind": "secure_business_reference", "input_field": "receipt_reference"}
    run, _, decisions, _, _, captured = exercise(monkeypatch, [invalid, valid])
    assert json.loads(asyncio.run(run())["text"]) == valid
    assert "required" in decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    assert "input_kind" not in invalid and captured == [valid]


def test_guard_has_no_permission_effect_on_other_tools(monkeypatch):
    run, observed, _, checks, _, _ = exercise(monkeypatch, [envelope()])
    asyncio.run(run())
    size = len(checks)
    guard = observed[0].hooks["PreToolUse"][0].hooks[0]
    assert asyncio.run(guard({"tool_name": "Bash", "tool_input": {"private": "secret"}}, None, {})) == {}
    assert asyncio.run(guard({}, None, {})) == {} and len(checks) == size
    deny = asyncio.run(observed[0].can_use_tool("Bash", {}, None))
    assert deny.message == "tool_not_enabled_for_task"


def test_repeated_bad_candidates_or_missing_terminal_cannot_succeed(monkeypatch):
    invalid = {**envelope(), "input_kind": "null", "input_field": "null"}
    run, _, _, _, _, captured = exercise(monkeypatch, [invalid, invalid])
    with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
        asyncio.run(run())
    assert not captured
    run, _, _, _, _, _ = exercise(monkeypatch, [envelope()], terminal=False)
    with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
        asyncio.run(run())


def test_invalid_terminal_is_rejected_even_if_sdk_skips_pre_tool_hook(monkeypatch):
    invalid = {**envelope(), "input_kind": "null", "input_field": "null"}
    run, _, _, _, _, _ = exercise(monkeypatch, [invalid], bypass_hook=True)
    with pytest.raises(ValueError, match="workbuddy_structured_output_invalid"):
        asyncio.run(run())


def test_feedback_rejects_undeclared_paths_and_never_echoes_values():
    response = {"schema_valid": False, "message": "private-secret", "validation_issues": [
        {"code": "runtime_report_schema_invalid", "path": "/input_kind", "constraint": "enum", "value": "private-secret"},
        {"code": "runtime_report_schema_invalid", "path": "/private-secret", "constraint": "enum"},
        {"code": "unknown", "path": "/input_field", "constraint": "enum"}]}
    message = worker.formatter_feedback(response, schema())
    assert "/input_kind" in message and "private-secret" not in message and "unknown" not in message
    unrelated = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    assert "input_kind" not in worker.formatter_feedback(response, unrelated)


def test_clarification_diagnostics_store_shape_not_rejected_value():
    raw = {**envelope(), "input_kind": "private-secret"}; raw.pop("input_field")
    diag = native_output_diagnostic(raw, schema(), operation="free_query", phase="native_candidate")
    expected = {"input_kind": {"present": True, "json_type": "string", "enum_member": False},
                "input_field": {"present": False, "json_type": "missing", "enum_member": False}}
    assert diag["clarification_fields"] == expected
    assert safe_progress("workbuddy_native_output_checked", diag)["clarification_fields"] == expected
    assert "private-secret" not in json.dumps(diag)
    diag["clarification_fields"]["private-secret"] = expected["input_kind"]
    diag["clarification_fields"]["input_kind"]["value"] = "private-secret"
    assert "private-secret" not in json.dumps(safe_progress("workbuddy_native_output_checked", diag))


@pytest.mark.parametrize("bad", ["null", "", "None", "undefined", "private-wrong-PO", 7, False])
def test_equivalent_schema_has_identical_feedback_without_value_repair(monkeypatch, bad):
    from scripts.workbuddy_schema_contrast import equivalent_union_schema
    canonical = schema()
    wire = equivalent_union_schema(canonical)
    raw = {**envelope(), "input_kind": bad, "input_field": bad}
    run, _, canonical_decisions, _, _, _ = exercise(monkeypatch, [raw, envelope()])
    canonical_result = asyncio.run(run())
    run, observed, wire_decisions, checks, public, captured = exercise(monkeypatch, [raw, envelope()], wire_schema=wire)
    wire_result = asyncio.run(run())
    canonical_reason = canonical_decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    wire_reason = wire_decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    if isinstance(bad, str):
        assert wire_decisions == canonical_decisions
    else:
        # Different Schema keywords report type/anyOf respectively. Preserve
        # the actual validator diagnostics; the corrective guidance is equal.
        assert '"constraint":"type"' in canonical_reason
        assert '"constraint":"anyOf"' in wire_reason
        assert canonical_reason.split(" These are secure-reference", 1)[1] == wire_reason.split(" These are secure-reference", 1)[1]
    assert wire_decisions[0]["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "literal JSON null" in wire_decisions[0]["hookSpecificOutput"]["permissionDecisionReason"]
    assert json.loads(wire_result["text"]) == json.loads(canonical_result["text"]) == envelope()
    assert captured == [envelope()] and raw["input_kind"] == bad and raw["input_field"] == bad
    assert not checks[0]["schema_valid"] and checks[-1]["schema_valid"]
    assert "private-wrong-PO" not in json.dumps(public)
    assert json.loads(observed[0].extra_args["json-schema"]) == wire


@pytest.mark.parametrize("patch", [
    {"type": ["string"]}, {"enum": ["secure_business_reference"]},
    {"not": {"type": "null"}}, {"allOf": [{"type": "string"}]},
    {"oneOf": [{"type": "null"}, {"type": "null"}]}, {"$ref": "#/$defs/unknown"},
    {"anyOf": [{"type": "string"}, {"type": "null", "not": {"const": None}}]},
    {"anyOf": [{"type": "string"}]}, {"type": "string", "anyOf": [{"type": "null"}]},
    {"const": "secure_business_reference"},
    {"type": [["string"], "null"]}, {"type": None}, {"anyOf": None},
    {"anyOf": [{"type": ["null"]}]},
])
def test_feedback_does_not_claim_null_for_nonnullable_or_complex_constraints(patch):
    definition = {"type": ["string", "null"], "enum": ["secure_business_reference", None], **patch}
    assert not worker.declared_nullable_enum(definition)
    frozen = schema(); frozen["properties"]["input_kind"] = definition
    response = {"validation_issues": [{"code": "runtime_report_schema_invalid", "path": "/input_kind", "constraint": "enum"}]}
    assert "literal JSON null" not in worker.formatter_feedback(response, frozen)


def test_nullable_feedback_recognizes_order_and_annotations_without_changing_schema():
    variants = [
        {"type": ["null", "string"], "enum": [None, "secure_business_reference"]},
        {"anyOf": [{"type": "null"}, {"type": "string"}], "enum": [None, "secure_business_reference"]},
        {"type": ["string", "null"], "anyOf": [{"type": "null"}, {"type": "string"}],
         "enum": ["secure_business_reference", None], "description": "Not a SAP column"},
    ]
    for definition in variants:
        before = copy.deepcopy(definition)
        assert worker.declared_nullable_enum(definition) and definition == before


@pytest.mark.parametrize("wire", [False, True])
def test_both_encodings_keep_native_terminal_missing_and_invalid_gates(monkeypatch, wire):
    from scripts.workbuddy_schema_contrast import equivalent_union_schema
    frozen = equivalent_union_schema(schema()) if wire else schema()
    invalid = {**envelope(), "input_kind": "null", "input_field": "null"}
    run, _, _, _, _, captured = exercise(monkeypatch, [invalid, invalid], wire_schema=frozen)
    with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
        asyncio.run(run())
    assert not captured
    run, _, _, _, _, _ = exercise(monkeypatch, [invalid], wire_schema=frozen, bypass_hook=True)
    with pytest.raises(ValueError, match="workbuddy_structured_output_invalid"):
        asyncio.run(run())
