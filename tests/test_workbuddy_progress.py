"""Safe native progress without the SDK, network, shell commands or SAP."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress


def test_saved_diagnostics_allowlist_is_workbuddy_only():
    payload = {"provider_id": "workbuddy", "tool": "Edit", "call_index": 1, "elapsed_ms": 12,
               "is_error": False, "matched_call": True, "command": "secret", "content": "private"}
    assert safe_progress("agent_runtime_tool_finished", payload) == {
        "tool": "Edit", "call_index": 1, "elapsed_ms": 12, "is_error": False, "matched_call": True}
    assert safe_progress("agent_runtime_tool_finished", {**payload, "provider_id": "codex"}) == {}
    assert safe_progress("unknown_event", payload) == {}
    assert safe_progress("agent_runtime_tool_finished", {"provider_id": "workbuddy",
        "tool": "private-tool", "call_index": True, "elapsed_ms": -1, "pending_tool_count": 3_600_001,
        "is_error": "secret"}) == {}


@pytest.mark.parametrize("scenario", ["success", "tool_error", "unknown_tool", "closing_timeout", "missing_terminal"])
def test_native_progress_separates_tool_result_and_sdk_close(monkeypatch, scenario):
    class ToolUse:
        id = "sensitive-sdk-id"
        name = "private-tool-name" if scenario == "unknown_tool" else "Edit"
        input = {"command": "password=sensitive-command", "path": "private-path"}
    class ToolResult:
        tool_use_id = "sensitive-sdk-id"
        content = "restricted-business-rows"
        is_error = scenario == "tool_error"
    class Assistant:
        model = "concrete-model"
        def __init__(self, content):
            self.content = content
    class Result:
        is_error, structured_output, result, session_id = False, None, '{"status":"ok"}', "private-session"
    events = []
    class Client:
        def __init__(self, *, options):
            assert options.tools is None and options.setting_sources == []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            if scenario == "closing_timeout":
                raise TimeoutError("private-error-text")
            # Completion must not be announced before SDK shutdown succeeds.
            assert "agent_runtime_turn_completed" not in [item[0] for item in events]
        async def query(self, prompt):
            assert prompt == "probe"
        async def receive_response(self):
            yield Assistant([ToolUse()])
            yield SimpleNamespace(content=[ToolResult()])
            if scenario != "missing_terminal":
                yield Result()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=ToolUse, ToolResultBlock=ToolResult,
        PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace))
    def send(kind, **kwargs):
        assert kind == "event"
        events.append((kwargs["event"], kwargs["data"]))
    run = workbuddy_worker.execute({"operation": "review_agent_feedback", "mode": "trusted_local",
        "cli_path": "fixture-cli", "snapshot": {"model": "concrete-model"},
        "payload": {"cwd": "fixture", "prompt": "probe"}}, send)
    if scenario == "closing_timeout":
        with pytest.raises(TimeoutError):
            asyncio.run(run)
    elif scenario == "missing_terminal":
        with pytest.raises(ValueError, match="workbuddy_result_missing"):
            asyncio.run(run)
    else:
        assert asyncio.run(run)["text"] == '{"status":"ok"}'
    names = [item[0] for item in events]
    start = next(data for name, data in events if name == "agent_runtime_tool_started")
    end = next(data for name, data in events if name == "agent_runtime_tool_finished")
    assert start["call_index"] == end["call_index"] == 1
    assert start["tool"] == end["tool"] == ("unrecognized" if scenario == "unknown_tool" else "Edit")
    assert end["elapsed_ms"] >= 0 and end["is_error"] is (scenario == "tool_error")
    assert ("agent_runtime_result_received" in names) is (scenario != "missing_terminal")
    assert ("agent_runtime_sdk_closed" in names) is (scenario != "closing_timeout")
    assert ("agent_runtime_turn_completed" in names) is (scenario not in {"closing_timeout", "missing_terminal"})
    assert names.index("agent_runtime_tool_started") < names.index("agent_runtime_tool_finished")
    encoded = json.dumps(events)
    for secret in ("sensitive-sdk-id", "private-tool-name", "password", "private-path", "restricted-business-rows", "private-session"):
        assert secret not in encoded
