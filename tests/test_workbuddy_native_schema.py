"""Pinned CLI Schema forwarding; never weakens platform workflow validation."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker


@pytest.mark.parametrize("scenario", ["structured", "missing", "legacy", "invalid_schema"])
def test_native_schema_options_and_terminal_output(monkeypatch, scenario):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    observed = []
    class Assistant:
        model, content = "hy4-preview", []
    class Result:
        is_error, session_id, result = False, "private-session", '{"answer":"unconstrained"}'
        structured_output = {"answer": "constrained"} if scenario == "structured" else None
    class Client:
        def __init__(self, *, options):
            observed.append(options)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def query(self, _):
            pass
        async def receive_response(self):
            yield Assistant()
            yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=type("ToolUse", (), {}), ToolResultBlock=type("ToolResult", (), {}),
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace))
    payload = {"cwd": "isolated", "prompt": "probe", "system_prompt": "Preserve the task's authorization."}
    if scenario != "legacy":
        payload["output_schema"] = [] if scenario == "invalid_schema" else schema
    events = []
    run = workbuddy_worker.execute({"operation": "workflow_authoring.v2", "mode": "bounded",
        "cli_path": "locked-cli", "snapshot": {"model": "hy4-preview"}, "payload": payload},
        lambda kind, **data: events.append((kind, data)))
    if scenario in {"missing", "invalid_schema"}:
        with pytest.raises(ValueError, match="workbuddy_structured_output_missing" if scenario == "missing"
                           else "workbuddy_output_schema_invalid"):
            asyncio.run(run)
        assert not any(data.get("event") == "agent_runtime_turn_completed" for _, data in events)
    else:
        result = asyncio.run(run)
        assert json.loads(result["text"]) == {"answer": "constrained" if scenario == "structured" else "unconstrained"}
        assert (result.get("output_format") == "native_json_schema") is (scenario == "structured")
    if scenario == "invalid_schema":
        assert not observed  # Reject before starting the SDK.
        return
    options = observed[0]
    expected = [] if scenario == "legacy" else ["StructuredOutput"]
    assert options.tools == options.allowed_tools == expected and options.setting_sources == []
    assert options.resume is None and options.persist_session is False
    assert options.extra_args["print"] is None and options.extra_args["strict-mcp-config"] is None
    if scenario == "legacy":
        assert "json-schema" not in options.extra_args
        assert options.system_prompt == payload["system_prompt"]
    else:
        assert json.loads(options.extra_args["json-schema"]) == schema
        assert options.system_prompt.startswith(payload["system_prompt"])
        assert "then call StructuredOutput exactly once" in options.system_prompt
    denied = asyncio.run(options.can_use_tool("unverified-native-tool", {}, None))
    assert denied.message == "tool_not_enabled_for_task"  # Schema grants no tool permission.
    bash = asyncio.run(options.can_use_tool("Bash", {}, None))
    assert bash.message == "tool_not_enabled_for_task"
    formatter = asyncio.run(options.can_use_tool("StructuredOutput", {"answer": "constrained"}, None))
    if scenario == "legacy":
        assert formatter.message == "tool_not_enabled_for_task"
    else:
        assert formatter.updated_input == {"answer": "constrained"}
