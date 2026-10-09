"""Native-capture diagnostics are not terminal results or tool authorization."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic, safe_progress
from tests.test_workbuddy_isolation import supervisor

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"answer": {"type": "string", "enum": ["private-business-value"]}}, "required": ["answer"]}


@pytest.mark.parametrize("terminal", [None, {"answer": "private-business-value"}])
def test_valid_tool_argument_does_not_replace_missing_terminal(monkeypatch, terminal):
    class ToolUse:
        id, name, input = "private-id", "StructuredOutput", {"answer": "private-business-value"}
    class ToolResult:
        tool_use_id, is_error, content = "private-id", False, "private-native-result"
    class Assistant:
        model, content = "hy4-preview", [ToolUse()]
    class Result:
        is_error, session_id, result = False, "private-session", '{"answer":"private-business-value"}'
        structured_output, subtype, stop_reason = terminal, "success", "end_turn"
    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self):
            yield Assistant()
            yield SimpleNamespace(content=[ToolResult()])
            yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=ToolUse, ToolResultBlock=ToolResult, PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace))
    public, pending = [], []
    def send(kind, **data):
        if kind == "schema_check":
            diagnosis = native_output_diagnostic(data["value"], SCHEMA, operation="free_query", phase=data["phase"])
            public.append(("workbuddy_native_output_checked", diagnosis))
            pending.append({"kind": "schema_check_result", "call_id": data["call_id"],
                "task_id": "task", "attempt_id": "attempt", "schema_sha256": workbuddy_worker.fingerprint(SCHEMA),
                "schema_valid": diagnosis["schema_valid"]})
        elif kind == "error":
            public.append(("workbuddy_native_failure", data["diagnostic"]))
        else:
            public.append((data["event"], data["data"]))
    monkeypatch.setattr(workbuddy_worker, "read_frame", lambda: pending.pop(0))
    request = {"operation": "free_query", "mode": "bounded", "cli_path": "fixture-cli",
        "task_id": "task", "attempt_id": "attempt", "native_output_diagnostics": workbuddy_worker.NATIVE_DIAGNOSTICS,
        "snapshot": {"model": "hy4-preview"}, "payload": {"cwd": "fixture", "prompt": "probe", "output_schema": SCHEMA}}
    if terminal is None:
        with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
            asyncio.run(workbuddy_worker.execute(request, send))
    else:
        assert json.loads(asyncio.run(workbuddy_worker.execute(request, send))["text"]) == terminal
    checks = [data for kind, data in public if kind == "workbuddy_native_output_checked"]
    assert checks[0]["schema_valid"] is True
    assert checks[1]["schema_valid"] is (terminal is not None)
    assert checks[1]["phase"] == "native_terminal"
    received = next(data for kind, data in public if kind == "agent_runtime_result_received")
    assert received["structured_call_count"] == received["structured_result_count"] == 1
    assert received["structured_output_present"] is (terminal is not None)
    assert received["result_subtype"] == "success" and received["stop_reason"] == "end_turn"
    encoded = json.dumps(public)
    for secret in ("private-business-value", "private-native-result", "private-id", "private-session"):
        assert secret not in encoded


@pytest.mark.parametrize("value,constraint,path", [({}, "required", "/answer"),
    ({"answer": "secret-invalid"}, "enum", "/answer"), ({"answer": 9}, "type", "/answer"),
    ({"answer": "private-business-value", "secret-key": "secret-extra"}, "additionalProperties", "/")])
def test_schema_issue_paths_have_no_values(value, constraint, path):
    diagnosis = native_output_diagnostic(value, SCHEMA, operation="free_query", phase="native_candidate")
    assert any(issue["constraint"] == constraint and issue["path"] == path for issue in diagnosis["validation_issues"])
    assert diagnosis["schema_valid"] is False
    assert "secret" not in json.dumps(diagnosis)
    assert safe_progress("workbuddy_native_output_checked", diagnosis)["validation_issues"] == diagnosis["validation_issues"]


@pytest.mark.parametrize("field", ["call_id", "task_id", "attempt_id", "schema_sha256", "schema_valid"])
def test_schema_reply_requires_matching_binding(monkeypatch, field):
    # A mismatch is rejected before any model task can be completed.
    class Result:
        is_error, session_id, result = False, "private-session", None
        structured_output = {"answer": "private-business-value"}
    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self): yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=type("Assistant", (), {}), ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=type("ToolUse", (), {}), ToolResultBlock=type("ToolResult", (), {}),
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace))
    response = {}
    def send(kind, **data):
        if kind == "schema_check":
            response.update(kind="schema_check_result", call_id=data["call_id"], task_id="task", attempt_id="attempt",
                schema_sha256=workbuddy_worker.fingerprint(SCHEMA), schema_valid=True)
            response[field] = "foreign"
    monkeypatch.setattr(workbuddy_worker, "read_frame", lambda: response)
    with pytest.raises(ValueError, match="workbuddy_schema_check_response_invalid"):
        asyncio.run(workbuddy_worker.execute({"operation": "free_query", "mode": "bounded", "cli_path": "fixture",
            "task_id": "task", "attempt_id": "attempt", "snapshot": {"model": "hy4-preview"},
            "native_output_diagnostics": workbuddy_worker.NATIVE_DIAGNOSTICS,
            "payload": {"cwd": "fixture", "prompt": "probe", "output_schema": SCHEMA}}, send))


@pytest.mark.parametrize("scenario,schema,error", [
    ("schema_check", {"type": "object", "properties": {"other": {"type": "string"}}, "required": ["other"]}, None),
    ("schema_check", None, "workbuddy_protocol_invalid"),
    ("schema_check_bad_phase", SCHEMA, "workbuddy_protocol_invalid"),
])
def test_owned_protocol_checks_use_frozen_schema_not_worker_schema(tmp_path, monkeypatch, scenario, schema, error):
    owner, binding = supervisor(tmp_path, monkeypatch)
    events, tool_calls = [], []
    async def tool(*args):
        tool_calls.append(args)
        pytest.fail("A schema check cannot become a Broker call")
    payload = {"scenario": scenario, **({"output_schema": schema} if schema else {})}
    run = owner.run(task_id="diagnosis", snapshot=binding, operation="free_query", payload=payload,
                    seconds=10, tool_handler=tool, emit=lambda kind, data: events.append((kind, data)))
    if error:
        with pytest.raises(Exception, match=error): asyncio.run(run)
    else:
        result = asyncio.run(run)
        assert json.loads(result["text"])["schema_valid"] is False
        assert "native_json_schema" != result.get("output_format")
        assert events[0][0] == "workbuddy_native_output_checked"
        assert any(item["path"] == "/other" for item in events[0][1]["validation_issues"])
        assert "private-business-value" not in json.dumps(events)
    assert not tool_calls and not owner.active and not owner.pending()


def test_untrusted_progress_shapes_are_ignored():
    assert safe_progress("agent_runtime_result_received", {"provider_id": "workbuddy",
        "result_subtype": ["secret"], "stop_reason": {}, "schema_sha256": "secret", "structured_output_present": "yes"}) == {}
    assert safe_progress("workbuddy_native_output_checked", {"provider_id": "workbuddy", "validation_issues": {}}) == {"validation_issues": []}


def test_full_harness_schema_uses_the_shared_checker_without_business_qualification():
    from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA
    value = {"status": "inconclusive", "intent": "synthetic", "clarification_question": "", "summary": {"zh": "测试", "en": "Test"},
        "source_complete": False, "business_complete": False, "missing_evidence": [], "evidence_refs": [], "executed_plans": [],
        "presentation": {"schema_version": "1.0", "title": {"zh": "测试", "en": "Test"}, "validation_ref": None, "blocks": []}}
    diagnosis = native_output_diagnostic(value, _HARNESS_OUTPUT_SCHEMA, operation="free_query", phase="native_candidate")
    assert diagnosis["schema_valid"] is True and "qualified" not in diagnosis
    value["status"] = "private-invalid-status"
    diagnosis = native_output_diagnostic(value, _HARNESS_OUTPUT_SCHEMA, operation="free_query", phase="native_terminal")
    assert any(issue["path"] == "/status" and issue["constraint"] == "enum" for issue in diagnosis["validation_issues"])
    assert "private" not in json.dumps(diagnosis)
