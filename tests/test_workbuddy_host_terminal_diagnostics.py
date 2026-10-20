"""Replay host failures after a valid candidate, without SDK/model/SAP calls."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
from sap_business_agents_platform.workbuddy_diagnostics import host_failure, HOST_FAILURE_CODES
from sap_business_agents_platform.runtime_diagnostics import stage_failure, safe_stage_diagnostic
from sap_business_agents_platform.runtime_role_consolidation import SCHEMA as FINAL_SCHEMA
from tests.test_workbuddy_isolation import supervisor


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}
PRIVATE = "password=private-row/private-path"


def saved_job(owner, task_id):
    return json.loads((owner.environment.root / "jobs" / (digest(task_id) + ".json")).read_text())


@pytest.mark.parametrize("failure", [PermissionError(PRIVATE), RuntimeError(PRIVATE),
                                   ExceptionGroup(PRIVATE, [OSError(PRIVATE)])])
def test_event_callback_failure_after_valid_candidate_is_persisted(tmp_path, monkeypatch, failure):
    owner, binding = supervisor(tmp_path, monkeypatch)
    calls = []
    def emit(kind, data):
        calls.append(kind)
        assert kind == "workbuddy_native_output_checked" and data["schema_valid"] is True
        raise failure
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="callback-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check", "output_schema": SCHEMA}, seconds=10, emit=emit))
    assert caught.value.code == "workbuddy_event_delivery_failed"
    saved = saved_job(owner, "callback-failure")
    detail = saved["failure_diagnostic"]
    assert saved["failure_code"] == caught.value.code
    assert detail["failure_source"] == "supervisor_host"
    assert detail["error_class"] == type(failure).__name__
    assert detail["protocol_phase"] == "schema_check_event"
    assert detail["schema_reply_state"] == "prepared" and detail["last_schema_valid"] is True
    assert detail["schema_check_count"] == 1 and detail["schema_check_phase"] == "native_candidate"
    assert saved["cleanup_complete"] and saved["status"] == "failed"
    assert "result_metadata" not in saved and not owner.active and not owner.pending()
    assert calls == ["workbuddy_native_output_checked"]  # Do not retry the broken sink.
    projected = stage_failure(caught.value, schema=FINAL_SCHEMA, phase="role_consolidation")
    for name in ("failure_code", "failure_source", "protocol_phase", "schema_reply_state", "last_schema_valid", "host_error_class"):
        assert projected[name] == detail[name]
    assert "private" not in json.dumps(saved) + json.dumps(projected) + str(caught.value)


@pytest.mark.parametrize("point,reply_state", [("write", "prepared"), ("drain", "written")])
def test_schema_reply_transport_failure_preserves_candidate_and_phase(tmp_path, monkeypatch, point, reply_state):
    owner, binding = supervisor(tmp_path, monkeypatch)
    spawn = asyncio.create_subprocess_exec
    async def replace_stdin(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        stdin = process.stdin
        class Transport:
            writes = 0
            def write(self, data):
                self.writes += 1
                if self.writes == 2 and point == "write":
                    raise BrokenPipeError(PRIVATE)
                return stdin.write(data)
            async def drain(self):
                if self.writes == 2 and point == "drain":
                    raise ConnectionResetError(PRIVATE)
                return await stdin.drain()
        process.stdin = Transport()
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", replace_stdin)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="pipe-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check_hang", "output_schema": SCHEMA}, seconds=10))
    assert caught.value.code == "workbuddy_worker_transport_failed"
    saved = saved_job(owner, "pipe-failure")
    assert saved["failure_diagnostic"]["protocol_phase"] == "schema_reply_" + point
    assert saved["protocol_progress"]["schema_reply_state"] == reply_state
    assert saved["last_output_check"]["schema_valid"] is True
    assert saved["status"] == "failed" and saved["cleanup_complete"]
    assert "result_metadata" not in saved and "private" not in json.dumps(saved)


def test_drained_schema_reply_and_eof_do_not_grant_a_terminal(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    with pytest.raises(WorkBuddyError, match="workbuddy_worker_exited") as caught:
        asyncio.run(owner.run(task_id="candidate-eof", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check_eof", "output_schema": SCHEMA}, seconds=10))
    saved = saved_job(owner, "candidate-eof")
    assert saved["protocol_progress"]["schema_reply_state"] == "drained"
    assert caught.value.detail["schema_reply_state"] == "drained"
    assert saved["last_output_check"]["schema_valid"] is True
    assert saved["cleanup_complete"] and saved["status"] == "failed"
    assert "result_metadata" not in saved and not owner.pending()


def test_valid_candidate_and_native_terminal_complete_without_qualification(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    result = asyncio.run(owner.run(task_id="native-success", snapshot=binding, operation="analyze_role_matching",
        payload={"scenario": "schema_check_native_terminal", "output_schema": SCHEMA}, seconds=10))
    saved = saved_job(owner, "native-success")
    assert saved["protocol_progress"]["schema_check_count"] == 2
    assert saved["protocol_progress"]["schema_check_phase"] == "native_terminal"
    assert saved["protocol_progress"]["schema_reply_state"] == "drained"
    assert saved["last_output_check"]["phase"] == "native_terminal"
    assert saved["result_metadata"]["structured_terminal"] is True
    assert result["output_format"] == "native_json_schema" and json.loads(result["text"])["answer"] == "private-business-value"
    assert saved["status"] == "completed" and saved["cleanup_complete"]
    assert not owner.environment.state().get("qualification_records")  # Synthetic process is never a live PASS.


def test_event_record_io_failure_is_not_misreported_as_callback_failure(tmp_path, monkeypatch):
    from sap_business_agents_platform import workbuddy_environment as storage
    owner, binding = supervisor(tmp_path, monkeypatch)
    replace = storage.os.replace
    delivered = []
    def reject_progress(source, destination):
        value = json.loads(source.read_text(encoding='utf-8'))
        if value.get('status') == 'running' and value.get('last_progress', {}).get('message_count') == 4:
            error = PermissionError(PRIVATE)
            error.winerror = 5
            raise error
        return replace(source, destination)
    monkeypatch.setattr(storage.os, 'replace', reject_progress)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id='event-journal-write-failure', snapshot=binding,
            operation='analyze_role_matching', payload={'scenario': 'progress_hang'}, seconds=10,
            emit=lambda *args: delivered.append(args)))
    assert caught.value.code == 'workbuddy_host_io_failed'
    assert caught.value.detail['protocol_phase'] == 'event_record_write'
    assert caught.value.detail['windows_error_code'] == 5
    assert not delivered
    saved = saved_job(owner, 'event-journal-write-failure')
    assert saved['status'] == 'failed' and saved['cleanup_complete']
    assert 'result_metadata' not in saved and PRIVATE not in json.dumps(saved)


def test_timeout_after_drained_reply_keeps_milestones_without_success(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    with pytest.raises(WorkBuddyError, match="workbuddy_deadline_exceeded") as caught:
        asyncio.run(owner.run(task_id="after-reply-timeout", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check_hang", "output_schema": SCHEMA}, seconds=2))
    saved = saved_job(owner, "after-reply-timeout")
    assert caught.value.detail["schema_reply_state"] == "drained"
    assert caught.value.detail["last_schema_valid"] is True
    assert saved["status"] == "timed_out" and saved["cleanup_complete"]
    assert "result_metadata" not in saved and not owner.pending()


def test_cancel_after_drained_reply_cannot_turn_candidate_into_success(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    async def check():
        observed = asyncio.Event()
        def emit(kind, _):
            if kind == "workbuddy_native_wait":
                observed.set()  # Fixture only emits this after receiving the reply.
        task = asyncio.create_task(owner.run(task_id="after-reply-cancel", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check_hang", "output_schema": SCHEMA}, seconds=10, emit=emit))
        await asyncio.wait_for(observed.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.detail["schema_reply_state"] == "drained"
    asyncio.run(check())
    saved = saved_job(owner, "after-reply-cancel")
    assert saved["status"] == "cancelled" and saved["cleanup_complete"]
    assert "result_metadata" not in saved and not owner.pending()


def test_native_error_not_replaced_by_failure_event_sink_or_cleanup(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    cancel = owner.cancel
    async def cleanup_error(task_id):
        await cancel(task_id)
        raise OSError(PRIVATE)
    monkeypatch.setattr(owner, "cancel", cleanup_error)
    def emit(*_): raise PermissionError(PRIVATE)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="double-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "error_close_hang"}, seconds=10, emit=emit))
    assert caught.value.code == "workbuddy_upstream_empty_stream"
    saved = saved_job(owner, "double-failure")
    detail = saved["failure_diagnostic"]
    assert detail["failure_event_delivery_failed"] is True
    assert detail["failure_source"] == "error_message" and detail["delivery_error_class"] == "PermissionError"
    assert caught.value.detail["primary_failure_code"] == "workbuddy_upstream_empty_stream"
    assert caught.value.detail["cleanup_failure_code"] == "runtime_cleanup_incomplete"
    assert saved["status"] == "cleanup_pending" and not saved["cleanup_complete"]
    assert "private" not in json.dumps(saved) + json.dumps(caught.value.detail)


@pytest.mark.parametrize("failure,code", [(PermissionError(PRIVATE), "workbuddy_host_io_failed"),
    (RuntimeError(PRIVATE), "workbuddy_host_execution_failed"),
    (EOFError(PRIVATE), "workbuddy_worker_transport_failed")])
def test_classification_uses_types_not_sensitive_exception_text(failure, code):
    assert host_failure(failure, "message_read")["failure_code"] == code
    assert "private" not in json.dumps(host_failure(failure, "message_read"))


def test_arbitrary_class_names_and_protocol_positions_are_not_exposed():
    Spoof = type("PermissionError", (Exception,), {})
    safe = host_failure(Spoof(PRIVATE), PRIVATE)
    assert safe["error_class"] == "unknown" and "protocol_phase" not in safe
    assert safe["failure_code"] in HOST_FAILURE_CODES
    forged = safe_stage_diagnostic({"failure_code": "workbuddy_host_execution_failed", "protocol_phase": PRIVATE,
        "error_class": PRIVATE, "host_error_class": PRIVATE, "last_frame_kind": PRIVATE, "schema_reply_state": PRIVATE,
        "schema_check_count": True, "last_schema_valid": "true"}, schema=FINAL_SCHEMA, phase="role_consolidation")
    assert "private" not in json.dumps(forged) and "schema_check_count" not in forged
    codex = safe_stage_diagnostic({"failure_code": "runtime_native_execution_failed", "protocol_phase": "schema_reply_drain",
        "failure_source": "supervisor_host", "schema_reply_state": "drained", "last_schema_valid": True}, schema=FINAL_SCHEMA)
    assert not {"protocol_phase", "schema_reply_state", "last_schema_valid", "failure_source"} & codex.keys()


def test_planner_failure_event_cannot_mask_the_saved_host_error(tmp_path):
    from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner, WorkBuddyRuntimeError
    from sap_business_agents_platform.runtime_contract import deadline_scope
    async def fail(**_):
        error = WorkBuddyError("workbuddy_worker_transport_failed")
        error.detail = {"failure_code": error.code, "protocol_phase": "schema_reply_drain", "last_schema_valid": True}
        raise error
    planner = WorkBuddyPlanner(tmp_path, model="offline", supervisor=SimpleNamespace(run=fail),
                              runtime_snapshot={"provider_id": "workbuddy", "model": "offline"})
    def sink(*_): raise PermissionError(PRIVATE)
    async def check():
        with planner.bind_events(sink), deadline_scope(time.monotonic() + 10):
            with pytest.raises(WorkBuddyRuntimeError) as caught:
                await planner._query("offline", thread_id=None, system_prompt=None, output_schema=SCHEMA)
        assert caught.value.code == "workbuddy_worker_transport_failed"
        assert caught.value.detail["failure_event_delivery_failed"] is True
        assert caught.value.detail["protocol_phase"] == "schema_reply_drain"
        assert "private" not in str(caught.value) + json.dumps(caught.value.detail)
    asyncio.run(check())
