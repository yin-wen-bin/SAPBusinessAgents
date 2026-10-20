"""Observe pinned SDK semantics without live models, credentials or SAP."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from openai_codex.api import AsyncThread
from openai_codex.errors import JsonRpcError, TransportClosedError
from openai_codex.generated.v2_all import Turn, TurnCompletedNotification, ItemCompletedNotification

from sap_business_agents_platform.codex_driver import observed_role_run
from sap_business_agents_platform.runtime_diagnostics import stage_failure, safe_stage_diagnostic

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


class Native:
    def __init__(self, events=(), error=None):
        self.events, self.error, self.calls, self.closed = events, error, [], False

    async def turn(self, prompt, **options):
        self.calls.append((prompt, options))
        if self.error:
            raise self.error
        return SimpleNamespace(id="turn", stream=self.stream, run=self.collect)

    async def stream(self):
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True

    async def collect(self):
        from openai_codex._run import _collect_async_turn_result
        stream = self.stream()
        try:
            return await _collect_async_turn_result(stream, turn_id="turn")
        finally:
            await stream.aclose()


def terminal(status, info=None):
    turn = Turn(id="turn", status=status, items=[], error=None if status == "completed" else {
        "message": "private-password-and-business-data", "additionalDetails": "secret", "codexErrorInfo": info})
    return SimpleNamespace(method="turn/completed", payload=TurnCompletedNotification(thread_id="thread", turn=turn))


def test_observer_preserves_sdk_request_and_success_result():
    options = {"output_schema": SCHEMA, "effort": "max", "cwd": "fixture"}
    old, new = Native([terminal("completed")]), Native([terminal("completed")])
    expected = asyncio.run(AsyncThread.run(old, "same", **options))
    actual = asyncio.run(observed_role_run(new, "same", **options))
    # AsyncThread.run forwards explicit unset options too. Our driver omits
    # them, as before; compare the non-None values seen by SDK turn().
    assert {key: value for key, value in old.calls[0][1].items() if value is not None} == new.calls[0][1]
    assert actual == expected and old.closed and new.closed


def test_final_assistant_output_uses_the_same_sdk_collector():
    item = ItemCompletedNotification(thread_id="thread", turn_id="turn", completed_at_ms=1, item={"id": "item",
        "type": "agentMessage", "text": '{"ok":true}', "phase": "final_answer"})
    events = [SimpleNamespace(method="item/completed", payload=item), terminal("completed")]
    old, new = Native(events), Native(events)
    expected = asyncio.run(AsyncThread.run(old, "same", output_schema=SCHEMA))
    actual = asyncio.run(observed_role_run(new, "same", output_schema=SCHEMA))
    assert actual == expected and actual.final_response == '{"ok":true}'


def test_role_schema_signature_does_not_expose_the_error_message():
    from sap_business_agents_platform.codex_driver import role_schema_error
    from sap_business_agents_platform.runtime_stage import role_stage
    with role_stage("role_understanding", schema=SCHEMA, operation="analyze_role_matching"):
        failure = role_schema_error(RuntimeError("Invalid schema: 'uniqueItems' is not permitted; password=secret"), SCHEMA)
    detail = stage_failure(failure, schema=SCHEMA, phase="role_understanding")
    assert detail["failure_code"] == "runtime_native_schema_rejected"
    assert detail["validation_issues"][0]["constraint"] == "uniqueItems"
    assert "secret" not in json.dumps(detail)
    assert role_schema_error(RuntimeError("Invalid schema"), SCHEMA) is None


@pytest.mark.parametrize("info", ["unauthorized", "rateLimitExceeded", {"httpConnectionFailed": {"httpStatusCode": 503}}, "future-secret-code"])
def test_failed_terminal_retains_only_known_error_identity(info):
    native = Native([terminal("failed", info)])
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(observed_role_run(native, "private-input", output_schema=SCHEMA))
    diagnostic = stage_failure(caught.value, schema=SCHEMA, operation="analyze_role_matching", phase="role_understanding")
    assert native.closed and diagnostic["failure_code"] == "runtime_native_turn_failed"
    assert diagnostic["native_terminal_status"] == "failed"
    assert diagnostic["terminal_result_received"] and diagnostic["message_count"] == 1
    expected = next(iter(info)) if isinstance(info, dict) else info
    if expected != "future-secret-code":
        assert diagnostic["native_error_code"] == expected
    else:
        assert "native_error_code" not in diagnostic
    assert not any(secret in json.dumps(diagnostic) for secret in ("private-password", "business-data", "secret"))


@pytest.mark.parametrize("error,code", [(JsonRpcError(-32602, "secret", {"password": "secret"}), "runtime_native_rpc_error"),
    (TransportClosedError("secret"), "runtime_native_transport_closed")])
def test_native_exception_keeps_safe_class_and_numeric_rpc_code(error, code):
    with pytest.raises(type(error)) as caught:
        asyncio.run(observed_role_run(Native(error=error), "same", output_schema=SCHEMA))
    detail = stage_failure(caught.value, schema=SCHEMA, phase="role_understanding")
    assert detail["failure_code"] == code and detail["native_error_class"] == type(error).__name__
    if isinstance(error, JsonRpcError):
        assert detail["native_rpc_code"] == -32602
    assert "secret" not in json.dumps(detail)


def test_missing_terminal_and_interrupted_terminal_are_not_success():
    for events, code in (([], "runtime_result_not_complete"), ([terminal("interrupted")], "runtime_cancelled")):
        native = Native(events)
        with pytest.raises(Exception) as caught:
            asyncio.run(observed_role_run(native, "same", output_schema=SCHEMA))
        assert stage_failure(caught.value, schema=SCHEMA)["failure_code"] == code
        assert native.closed


def test_projection_rejects_unknown_native_strings_and_sensitive_data():
    detail = safe_stage_diagnostic({"native_error_class": "private-class", "native_error_code": "secret",
        "native_terminal_status": "secret", "native_rpc_code": True, "message": "secret", "data": {"password": "secret"}}, schema=SCHEMA)
    assert "secret" not in json.dumps(detail) and "native_rpc_code" not in detail


def test_thread_start_error_is_observed_without_changing_options():
    from sap_business_agents_platform.codex_driver import CodexClient
    from sap_business_agents_platform.runtime_stage import role_stage
    calls = []
    class Active:
        async def thread_start(self, **options):
            calls.append(options)
            raise JsonRpcError(-32602, "private", {"password": "private"})
    client = CodexClient(None, None)
    client.active = Active()
    with pytest.raises(JsonRpcError) as caught:
        with role_stage("role_understanding", schema=SCHEMA, operation="analyze_role_matching"):
            asyncio.run(client.thread_start(cwd="original", service_name="original", model="original"))
    assert calls == [{"cwd": "original", "service_name": "original", "model": "original"}]
    detail = caught.value.runtime_diagnostic
    assert detail["native_phase"] == "thread_start" and detail["native_rpc_code"] == -32602
    assert detail["failure_code"] == "runtime_native_rpc_error" and "private" not in json.dumps(detail)


def test_platform_decode_failure_keeps_successful_native_terminal_progress():
    from sap_business_agents_platform.runtime_stage import role_stage
    with pytest.raises(KeyError) as caught:
        with role_stage("role_understanding", schema=SCHEMA, operation="analyze_role_matching"):
            asyncio.run(observed_role_run(Native([terminal("completed")]), "same", output_schema=SCHEMA))
            raise KeyError("secret-business-key")
    detail = caught.value.runtime_diagnostic
    assert detail["native_terminal_status"] == "completed" and detail["terminal_result_received"]
    assert detail["error_class"] == "KeyError" and detail["native_phase"] == "message_stream"
    assert "secret-business-key" not in json.dumps(detail)


def test_platform_attribute_error_is_safe_and_distinct_from_native_failure():
    from sap_business_agents_platform.runtime_stage import role_stage
    with pytest.raises(AttributeError) as caught:
        with role_stage("role_understanding", schema=SCHEMA, operation="analyze_role_matching"):
            asyncio.run(observed_role_run(Native([terminal("completed")]), "same", output_schema=SCHEMA))
            raise AttributeError("secret-business-object")
    detail = caught.value.runtime_diagnostic
    assert detail["native_terminal_status"] == "completed" and detail["terminal_result_received"]
    assert detail["error_class"] == "AttributeError"
    assert "secret-business-object" not in json.dumps(detail)


def test_initialization_before_role_stage_has_safe_diagnostic():
    from sap_business_agents_platform.codex_driver import annotate_role_error
    error = TransportClosedError("private-connection-message")
    annotate_role_error(error, "sdk_initialize", "analyze_role_matching")
    assert error.runtime_diagnostic["phase"] == "role_initialization"
    assert error.runtime_diagnostic["native_phase"] == "sdk_initialize"
    assert error.runtime_diagnostic["failure_code"] == "runtime_native_transport_closed"
    assert "private-connection-message" not in json.dumps(error.runtime_diagnostic)
    other = TransportClosedError("unchanged")
    annotate_role_error(other, "sdk_initialize", "compose_workflow")
    assert not hasattr(other, "runtime_diagnostic") and not hasattr(other, "detail")
