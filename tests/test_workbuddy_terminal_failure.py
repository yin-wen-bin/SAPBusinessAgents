"""Native failure must reach the owner before a hanging SDK disconnect."""
import asyncio
import json
import sys
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_worker import execute, native_failure_code
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_diagnostics import safe_native_failure
from tests.test_workbuddy_isolation import supervisor


@pytest.mark.parametrize("message,code", [
    ("EmptyStreamError: secret-token", "workbuddy_upstream_empty_stream"),
    ("Error streaming response: private-row", "workbuddy_upstream_error"),
    ("credential=unknown-error", "workbuddy_execution_failed"),
])
def test_native_signatures_do_not_export_text(message, code):
    assert native_failure_code(message) == code
    safe = safe_native_failure({"failure_code": code, "failure_source": "sdk_exception", "raw": message})
    assert safe == {"failure_code": code, "failure_source": "sdk_exception"}


@pytest.mark.parametrize("scenario,code,source", [
    ("error", "workbuddy_upstream_empty_stream", "error_message"),
    ("result_error", "workbuddy_execution_failed", "error_result"),
    ("end", "workbuddy_result_missing", "message_stream_end"),
])
def test_worker_signals_before_sdk_close(monkeypatch, scenario, code, source):
    class Error:
        error = "EmptyStreamError: password=must-not-export"
    class Result:
        is_error, structured_output, errors = True, None, ["unknown private error"]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def query(self, prompt): pass
        async def receive_messages(self):
            if scenario == "error": yield Error()
            elif scenario == "result_error": yield Result()
        async def __aexit__(self, *args):
            await asyncio.Event().wait()
    monkeypatch.setitem(sys.modules, "codebuddy_agent_sdk", SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        ErrorMessage=Error, ResultMessage=Result, AssistantMessage=type("Assistant", (), {}),
        TextBlock=type("Text", (), {}), ToolUseBlock=type("ToolUse", (), {}),
        ToolResultBlock=type("ToolResult", (), {}), PermissionResultAllow=SimpleNamespace,
        PermissionResultDeny=SimpleNamespace))
    frames = []
    async def check():
        task = asyncio.create_task(execute({"operation": "plan", "mode": "bounded", "cli_path": "fixture",
            "snapshot": {"model": "fixture"}, "payload": {"cwd": "fixture", "prompt": "fixture"}},
            lambda kind, **data: frames.append({"kind": kind, **data})))
        for _ in range(20):
            await asyncio.sleep(0)
            if any(item["kind"] == "error" for item in frames): break
        assert not task.done()
        error = next(item for item in frames if item["kind"] == "error")
        assert error["code"] == code
        assert error["diagnostic"]["failure_source"] == source
        assert error["diagnostic"]["terminal_result_received"] is (scenario == "result_error")
        assert "password" not in json.dumps(frames) and "private" not in json.dumps(frames)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(check())


def test_owned_process_failure_does_not_wait_for_stage_deadline(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    events = []
    started = time.monotonic()
    with pytest.raises(WorkBuddyError, match="workbuddy_upstream_empty_stream") as caught:
        asyncio.run(owner.run(task_id="native-error", snapshot=binding, operation="review_workflow",
            payload={"scenario": "error_close_hang"}, seconds=60,
            emit=lambda kind, data: events.append({"event": kind, **data})))
    assert time.monotonic() - started < 10
    assert caught.value.detail["failure_source"] == "error_message"
    assert "private" not in json.dumps(events)
    record = next((owner.environment.root / "jobs").glob("*.json"))
    saved = json.loads(record.read_text())
    assert saved["failure_code"] == "workbuddy_upstream_empty_stream"
    assert saved["cleanup_complete"] and saved["status"] == "failed"
    assert not owner.pending()

def test_cleanup_exception_preserves_primary_failure_and_fences_retry(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    cancel = owner.cancel
    async def cleanup_error(task_id):
        await cancel(task_id)  # Fixture process is actually stopped.
        raise OSError("private path and credentials")
    monkeypatch.setattr(owner, "cancel", cleanup_error)
    with pytest.raises(WorkBuddyError, match="workbuddy_upstream_empty_stream") as caught:
        asyncio.run(owner.run(task_id="cleanup-error", snapshot=binding, operation="review_workflow",
            payload={"scenario": "error_close_hang"}, seconds=60))
    assert caught.value.detail["primary_failure_code"] == "workbuddy_upstream_empty_stream"
    assert caught.value.detail["cleanup_failure_code"] == "runtime_cleanup_incomplete"
    saved = next(json.loads(path.read_text()) for path in (owner.environment.root / "jobs").glob("*.json"))
    assert saved["status"] == "cleanup_pending" and not saved["cleanup_complete"]
    assert saved["failure_observed_at"] <= saved["cleanup_started_at"] <= saved["cleanup_completed_at"]
    assert "private" not in json.dumps(saved) and "credentials" not in json.dumps(caught.value.detail)

def test_untrusted_failure_shapes_are_safely_ignored():
    assert safe_native_failure({"failure_code": {}, "failure_source": {}, "elapsed_ms": True}) == {
        "failure_code": "workbuddy_execution_failed", "failure_source": "unknown"}
