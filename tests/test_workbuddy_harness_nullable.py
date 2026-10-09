"""Nullable native-envelope regressions. No model, SDK installation or SAP I/O."""
import asyncio
import copy
import json
import sys
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.acceptance_projection import output_schema
from sap_business_agents_platform.models import RuntimeSnapshot
from sap_business_agents_platform.runtime_contract import RuntimeRequest
from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA, _turn_prompt
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic
from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions, parse_output
from sap_business_agents_platform import workbuddy_worker


def schema():
    return output_schema(_HARNESS_OUTPUT_SCHEMA, None)


def envelope():
    return {"status": "inconclusive", "intent": "offline fixture", "clarification_question": "",
            "input_kind": None, "input_field": None, "summary": {"zh": "离线样例", "en": "Offline fixture"},
            "source_complete": False, "business_complete": False, "missing_evidence": ["offline_fixture"],
            "evidence_refs": [], "executed_plans": [], "presentation": None}


def test_nullable_guidance_is_provider_only_and_does_not_change_schema():
    frozen = schema()
    before = copy.deepcopy(frozen)
    prompt = _turn_prompt("offline", continuing=False)
    guidance = native_terminal_instructions(frozen)
    assert frozen == before and schema() == before
    assert '{"input_kind": null, "input_field": null}' in guidance
    assert "NOT SAP query parameters" in guidance
    assert "Do not omit" in guidance and "not as a standalone response" in guidance
    assert "sap_final_report_validate validates the presentation" in guidance
    assert _turn_prompt("offline", continuing=False) == prompt
    assert "Otherwise set both fields to null" in prompt  # Existing shared meaning, not a new rule.
    unrelated = {"type": "object", "required": ["answer"], "properties": {"answer": {"type": "string"}}}
    assert native_terminal_instructions(unrelated) == (
        "\nSDK terminal encoding: use StructuredOutput with the entire frozen output object, not a fragment.")


@pytest.mark.parametrize("field", ["input_kind", "input_field"])
@pytest.mark.parametrize("bad", ["null", "None", "", "undefined", "PurchaseOrder", "__omit__"])
def test_nullable_field_errors_still_have_precise_paths_without_value_repair(field, bad):
    raw = envelope()
    if bad == "__omit__":
        raw.pop(field)
    else:
        raw[field] = bad
    original = copy.deepcopy(raw)
    diagnosis = native_output_diagnostic(raw, schema(), operation="free_query", phase="native_candidate")
    assert not diagnosis["schema_valid"]
    expected = "required" if bad == "__omit__" else "enum"
    assert any(item["path"] == "/" + field and item["constraint"] == expected
               for item in diagnosis["validation_issues"])
    with pytest.raises(WorkBuddyError) as error:
        parse_output(json.dumps(raw), schema())
    assert any(item["path"] == "/" + field and item["constraint"] == expected
               for item in error.value.detail["validation_issues"])
    assert raw == original
    assert "PurchaseOrder" not in json.dumps(diagnosis)  # Diagnostics never echo the supplied value.


@pytest.mark.parametrize("status,kind,field", [
    ("completed", None, None), ("inconclusive", None, None), ("waiting_input", None, None),
    ("waiting_input", "secure_business_reference", "receipt_reference"),
])
def test_valid_null_and_secure_clarification_values_remain_lossless(status, kind, field):
    raw = {**envelope(), "status": status, "input_kind": kind, "input_field": field}
    assert native_output_diagnostic(raw, schema(), operation="free_query", phase="native_terminal")["schema_valid"]
    assert parse_output(json.dumps(raw), schema()) == raw


@pytest.mark.parametrize("terminal", [True, False])
def test_full_nullable_envelope_crosses_pinned_worker_codec_but_needs_real_terminal(monkeypatch, terminal):
    frozen, raw, observed = schema(), envelope(), []
    class ToolUse:
        id, name, input = "fixture-call", "StructuredOutput", raw
    class Assistant:
        model, content = "hy4-preview", [ToolUse()]
    class Result:
        is_error, session_id, result = False, "fixture-session", json.dumps(raw)
        structured_output = raw if terminal else None
    class Client:
        def __init__(self, *, options): observed.append(options)
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self):
            yield Assistant()
            yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=ToolUse, ToolResultBlock=type("ToolResult", (), {}),
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace))
    request = {"operation": "free_query", "mode": "bounded", "cli_path": "locked-fixture-cli",
        "snapshot": {"model": "hy4-preview"}, "payload": {"cwd": "fixture", "prompt": "offline",
        "output_schema": frozen, "system_prompt": native_terminal_instructions(frozen)}}
    events = []
    task = workbuddy_worker.execute(request, lambda kind, **data: events.append((kind, data)))
    if terminal:
        result = asyncio.run(task)
        assert result["output_format"] == "native_json_schema" and json.loads(result["text"]) == raw
    else:
        with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
            asyncio.run(task)
        assert not any(data.get("event") == "agent_runtime_turn_completed" for _, data in events)
    forwarded = json.loads(observed[0].extra_args["json-schema"])
    assert forwarded == frozen
    assert None in forwarded["properties"]["input_kind"]["enum"]
    assert None in forwarded["properties"]["input_field"]["enum"]
    assert observed[0].tools == observed[0].allowed_tools == ["StructuredOutput"]
    assert observed[0].setting_sources == [] and observed[0].persist_session is False


@pytest.mark.parametrize("output_format", ["native_json_schema", "text"])
def test_harness_driver_changes_only_native_format_guidance(tmp_path, output_format):
    binding = RuntimeSnapshot(provider_id="workbuddy", sdk_id="codebuddy-agent-sdk", model="hy4-preview",
        configuration_digest="fixture", environment_digest="fixture-env", actual_model="hy4-preview").model_dump(mode="json")
    observed, events = [], []
    raw = envelope()
    async def native(**kwargs):
        observed.append(kwargs)
        return {"text": json.dumps(raw), "output_format": output_format, "actual_model": "hy4-preview"}
    runtime = RuntimeSnapshot.model_validate(binding)
    store = SimpleNamespace(get_run=lambda _: SimpleNamespace(runtime=runtime),
        get_harness_state=lambda _: {}, append_event=lambda *args: events.append(args),
        update_run=lambda *args, **kwargs: None, update_harness_state=lambda *args, **kwargs: None)
    owner = SimpleNamespace(store=store, manager=SimpleNamespace(supervisor=SimpleNamespace(run=native)))
    frozen = schema()
    request = RuntimeRequest("workbuddy", "free_query", "turn", binding, "Original user query", frozen,
        cwd=str(tmp_path), instructions="Original shared policy.",
        permissions={"acceptance_direct_baseline": False}, deadline=time.monotonic() + 60)
    context = {"run_id": "fixture-run", "state": {}, "turn_count": 1}
    task = WorkBuddyHarnessDriver(owner).harness_turn(request, context)
    if output_format == "native_json_schema":
        assert asyncio.run(task).output == raw
    else:
        with pytest.raises(WorkBuddyError, match="workbuddy_structured_output_missing"):
            asyncio.run(task)
    assert len(observed) == 1  # No automatic second call or terminal fabrication.
    sent = observed[0]
    assert sent["snapshot"] == binding and sent["operation"] == "free_query"
    assert sent["payload"]["prompt"] == request.prompt
    assert sent["payload"]["cwd"] == str(tmp_path) and sent["payload"]["output_schema"] == frozen
    assert sent["payload"]["system_prompt"] == request.instructions + native_terminal_instructions(frozen)
    assert 0 < sent["seconds"] <= 60 and 0 < sent["payload"]["timeout_ms"] <= 60000
    from sap_business_agents_platform.mcp_server import _SAP_TOOLS, _TOOL_TOOLS
    from sap_business_agents_platform.runtime_query_contract import PLAN_SCHEMA
    expected_tools = copy.deepcopy(_SAP_TOOLS + _TOOL_TOOLS)
    for tool in expected_tools:
        if tool["name"] in {"sap_query_validate", "sap_query_execute"}:
            tool["inputSchema"]["properties"]["plan"] = PLAN_SCHEMA
    assert sent["payload"]["tools"] == expected_tools
    assert context.get("final_response") == (json.dumps(raw, ensure_ascii=False) if output_format == "native_json_schema" else None)
