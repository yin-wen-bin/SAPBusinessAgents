"""Correct timers, safe formatter milestones, unchanged native terminal gates."""
import asyncio
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_worker as worker
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic, safe_progress
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_site import make_configuration, routing
from tests.test_workbuddy_harness_nullable import schema, envelope
from tests.test_workbuddy_isolation import supervisor


def replay(monkeypatch, *, feedback="matched", terminal=True, callback_error=False):
    clock = SimpleNamespace(now=100.0)
    public, frames, options_seen, errors = [], [], [], []
    frozen = schema()
    invalid = {**envelope(), "input_kind": "private-invalid", "input_field": "private-invalid"}
    before = copy.deepcopy(invalid)
    class ToolUse:
        name, id = "StructuredOutput", "private-call-id"
        def __init__(self, value): self.input = value
    class ToolResult:
        tool_use_id, is_error = "private-call-id", False
        def __init__(self, value): self.content = value
    class Assistant:
        model = "hy4-preview"
        def __init__(self, content): self.content = content
    class User:
        def __init__(self, content): self.content = content
    class Result:
        is_error, session_id, result = False, "private-session-id", json.dumps(envelope())
        structured_output = envelope() if terminal else None
    class Client:
        def __init__(self, *, options): self.options = options; options_seen.append(options)
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self):
            guard = self.options.hooks["PreToolUse"][0].hooks[0]
            clock.now = 105
            try:
                denial = await guard({"tool_name": "StructuredOutput", "tool_input": invalid}, "private-call-id", {})
            except Exception:
                assert callback_error
                # Reproduce the SDK consuming a callback exception and waiting.
                await asyncio.Event().wait()
                return
            assert denial["hookSpecificOutput"]["permissionDecision"] == "deny"
            reason = denial["hookSpecificOutput"]["permissionDecisionReason"]
            yield Assistant([ToolUse(invalid)])
            clock.now = 125
            content = [{"type": "text", "text": "Error: " + reason}] if feedback == "matched" else "private-unexpected-content"
            yield User([ToolResult(content)])
            clock.now = 140; yield Assistant([])
            clock.now = 145
            assert await guard({"tool_name": "StructuredOutput", "tool_input": envelope()}, "private-call-id", {}) == {}
            yield Assistant([ToolUse(envelope())])
            clock.now = 170; yield User([ToolResult("capture acknowledgment")])
            clock.now = 180; yield Result()
            pytest.fail("Worker must stop consuming after its native terminal")
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}),
        ToolUseBlock=ToolUse, ToolResultBlock=ToolResult, PermissionResultAllow=SimpleNamespace,
        PermissionResultDeny=SimpleNamespace, HookMatcher=SimpleNamespace))
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock.now))
    error_signal = asyncio.Event()
    def send(kind, **data):
        if kind == "schema_check":
            diagnosis = native_output_diagnostic(data["value"], frozen, operation="free_query", phase=data["phase"])
            reply = {"kind": "schema_check_result", "call_id": data["call_id"], "task_id": "task", "attempt_id": "attempt",
                "schema_sha256": worker.fingerprint(frozen), "schema_valid": diagnosis["schema_valid"],
                "validation_issues": diagnosis["validation_issues"]}
            if callback_error: reply["schema_sha256"] = "foreign"
            frames.append(reply)
        elif kind == "event":
            safe = safe_progress(data["event"], data["data"])
            public.append({"event": data["event"], **safe})
        elif kind == "error":
            errors.append(data)
            error_signal.set()
    monkeypatch.setattr(worker, "read_frame", lambda: frames.pop(0))
    async def run():
        try:
            return await worker.execute({"operation": "free_query", "mode": "bounded", "task_id": "task", "attempt_id": "attempt",
                "cli_path": "fixture-cli", "native_output_diagnostics": worker.NATIVE_DIAGNOSTICS,
                "snapshot": {"model": "hy4-preview", **make_configuration("china", 1)},
                "service_site_config": routing(make_configuration("china", 1)),
                "payload": {"cwd": "fixture", "prompt": "unchanged synthetic input", "system_prompt": "unchanged policy",
                    "output_schema": frozen, "timeout_ms": 90000, "max_turns": 50}}, send)
        finally:
            assert frozen == schema() and invalid == before
    return run, public, options_seen, errors, error_signal


@pytest.mark.parametrize("feedback", ["matched", "different"])
def test_safe_feedback_correlation_and_clock_never_reset_run_start(monkeypatch, feedback):
    run, public, options, errors, _ = replay(monkeypatch, feedback=feedback)
    result = asyncio.run(run())
    assert json.loads(result["text"]) == envelope() and result["output_format"] == "native_json_schema"
    activities = [e["elapsed_ms"] for e in public if e["event"] == "agent_runtime_activity"]
    assert activities == sorted(activities) and activities[-1] == 80000
    tools = [e["elapsed_ms"] for e in public if e["event"] == "agent_runtime_tool_finished"]
    assert tools == [20000, 25000]
    formatter = [e for e in public if e["event"] == "workbuddy_formatter_progress"]
    matches = [e["feedback_match"] for e in formatter if e["formatter_stage"] == "tool_result_observed"]
    assert matches == [feedback, "unavailable"]  # No denial expected for the valid call.
    assert any(e["formatter_stage"] == "rejection_prepared" and "expected_feedback_sha256" in e for e in formatter)
    assert any(e["formatter_stage"] == "terminal_checked" and e["schema_valid"] for e in formatter)
    waits = [e["worker_wait_state"] for e in public if e["event"] == "workbuddy_native_wait"]
    assert waits[-2:] == ["sdk_closing", "sdk_closed"]
    assert options[0].request_timeout_ms == 90000 and options[0].max_turns == 50
    assert "one successful" in options[0].system_prompt and "A rejected or unsuccessful attempt is not a capture" in options[0].system_prompt
    assert "StructuredOutput exactly once" not in options[0].system_prompt
    assert "request-timeout-ms" not in options[0].extra_args and not errors
    assert options[0].tools == options[0].allowed_tools == ["StructuredOutput"]
    encoded = json.dumps(public)
    for secret in ("private-invalid", "private-call-id", "private-session-id", "private-unexpected-content"):
        assert secret not in encoded


def test_missing_native_terminal_still_fails_after_valid_model_candidate(monkeypatch):
    run, public, _, _, _ = replay(monkeypatch, terminal=False)
    with pytest.raises(ValueError, match="workbuddy_structured_output_missing"):
        asyncio.run(run())
    assert not any(e.get("schema_valid") for e in public if e.get("formatter_stage") == "terminal_checked")


def test_callback_failure_is_signaled_before_sdk_can_hide_it(monkeypatch):
    run, public, _, errors, signal = replay(monkeypatch, callback_error=True)
    async def check():
        task = asyncio.create_task(run())
        try:
            await asyncio.wait_for(signal.wait(), timeout=2)
            assert not task.done()
            assert errors[0]["code"] == "workbuddy_schema_check_response_invalid"
            assert errors[0]["diagnostic"]["failure_source"] == "formatter_hook"
            assert any(e.get("formatter_stage") == "hook_failed" for e in public)
            assert "foreign" not in json.dumps(errors)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_new_event_allowlist_does_not_guess_remote_state_or_echo_raw_fields():
    value = {"provider_id": "workbuddy", "formatter_stage": "hook_entered", "hook_index": 1,
        "expected_feedback_sha256": "a"*64, "raw": "private", "tool_input": {"secret": 1},
        "worker_wait_state": "remote_model_thinking", "native_timeout_propagation": "verified"}
    assert safe_progress("workbuddy_formatter_progress", value) == {
        "formatter_stage": "hook_entered", "hook_index": 1, "expected_feedback_sha256": "a"*64}
    budget = safe_progress("workbuddy_native_budget", {"provider_id": "workbuddy", "requested_timeout_ms": 90000,
        "budget_strategy": "supervisor_deadline", "native_timeout_propagation": "unverified", "endpoint": "private"})
    assert budget == {"requested_timeout_ms": 90000, "budget_strategy": "supervisor_deadline", "native_timeout_propagation": "unverified"}
    assert safe_progress("workbuddy_formatter_progress", {**value, "provider_id": "codex"}) == {}


def test_timeout_keeps_authoritative_last_schema_issue_and_safe_wait_state(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    frozen = {"type": "object", "additionalProperties": False,
              "properties": {"answer": {"type": "string", "enum": ["allowed"]}}, "required": ["answer"]}
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="schema-timeout", snapshot=binding, operation="free_query", seconds=1,
            payload={"scenario": "schema_check_hang", "output_schema": frozen}))
    assert caught.value.code == "workbuddy_deadline_exceeded"
    assert caught.value.detail["worker_wait_state"] == "waiting_sdk_message"
    last = caught.value.detail["last_output_check"]
    assert last["validation_issues"] == [{"code": "runtime_report_schema_invalid", "path": "/answer", "constraint": "enum"}]
    record = json.loads(next((owner.environment.root / "jobs").glob("*.json")).read_text())
    assert record["last_output_check"] == last and record["cleanup_complete"] and record["status"] == "timed_out"
    assert not owner.pending()
    for secret in ("private-business-value", "password", "raw_log"):
        assert secret not in json.dumps(record)


def test_worker_eof_keeps_last_issue_and_is_not_a_successful_terminal(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    frozen = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["allowed"]}}, "required": ["answer"]}
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="schema-eof", snapshot=binding, operation="free_query", seconds=10,
            payload={"scenario": "schema_check_eof", "output_schema": frozen}))
    assert caught.value.detail["last_output_check"]["schema_valid"] is False
    assert caught.value.detail["worker_wait_state"] == "waiting_sdk_message"
    record = json.loads(next((owner.environment.root / "jobs").glob("*.json")).read_text())
    assert record["status"] == "failed" and record["cleanup_complete"] and not owner.pending()
    assert "private-business-value" not in json.dumps(record)


def test_cancellation_preserves_last_check_without_late_result_or_qualification(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    frozen = {"type": "object", "properties": {"answer": {"enum": ["allowed"]}}, "required": ["answer"]}
    ready = asyncio.Event()
    async def check():
        task = asyncio.create_task(owner.run(task_id="schema-cancel", snapshot=binding, operation="free_query", seconds=60,
            payload={"scenario": "schema_check_hang", "output_schema": frozen},
            emit=lambda kind, data: ready.set() if kind == "workbuddy_native_wait" else None))
        await asyncio.wait_for(ready.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.detail["last_output_check"]["schema_valid"] is False
        assert caught.value.detail["worker_wait_state"] == "waiting_sdk_message"
    asyncio.run(check())
    record = json.loads(next((owner.environment.root / "jobs").glob("*.json")).read_text())
    assert record["status"] == "cancelled" and record["cleanup_complete"] and not record["validation_job"]
    assert not owner.pending()
