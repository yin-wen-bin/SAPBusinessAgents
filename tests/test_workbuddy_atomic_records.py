"""Bounded record replacement under Windows sharing errors; no SDK/SAP."""
import asyncio
import json
import time

import pytest

from sap_business_agents_platform import workbuddy_environment as storage
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
from tests.test_workbuddy_isolation import supervisor


def windows_error(code):
    error = PermissionError("private path and credentials")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [32, 33])
def test_transient_sharing_retries_same_bytes_and_atomic_target(tmp_path, monkeypatch, code):
    target = tmp_path / "job.json"
    storage.atomic_json(target, {"status": "old"})
    replace = storage.os.replace
    attempts, waits = [], []
    def replace_once(source, destination):
        attempts.append(source.read_bytes())
        assert destination == target
        assert json.loads(target.read_text())["status"] == "old"
        if len(attempts) < 3:
            raise windows_error(code)
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", replace_once)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    storage.atomic_json(target, {"status": "new"}, deadline=time.monotonic() + 1)
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert waits == [.01, .02]
    assert json.loads(target.read_text()) == {"status": "new"}
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("code", [5, 32, 33, 112])
def test_persistent_errors_keep_old_record_and_remove_only_owned_temp(tmp_path, monkeypatch, code):
    target = tmp_path / "job.json"
    storage.atomic_json(target, {"status": "old"})
    historical = tmp_path / "job.json.historical.tmp"
    historical.write_text("original failed attempt; preserve")
    attempts, waits = [], []
    def reject(source, _):
        attempts.append(source)
        raise windows_error(code)
    monkeypatch.setattr(storage.os, "replace", reject)
    monkeypatch.setattr(storage.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        storage.atomic_json(target, {"status": "new"})
    assert len(attempts) == (3 if code in {32, 33} else 1)
    assert len(waits) == (2 if code in {32, 33} else 0)
    assert json.loads(target.read_text()) == {"status": "old"}
    assert list(tmp_path.glob("*.tmp")) == [historical]
    assert historical.read_text() == "original failed attempt; preserve"


def test_no_retry_past_signed_deadline(tmp_path, monkeypatch):
    attempts = []
    def reject(*_):
        attempts.append(1)
        raise windows_error(32)
    monkeypatch.setattr(storage.os, "replace", reject)
    monkeypatch.setattr(storage.time, "sleep", lambda _: pytest.fail("Expired budget cannot acquire retry time"))
    with pytest.raises(PermissionError):
        storage.atomic_json(tmp_path / "job.json", {}, deadline=time.monotonic() - 1)
    assert attempts == [1] and list(tmp_path.glob("*.tmp")) == []


def test_temp_cleanup_error_cannot_replace_primary_write_error(tmp_path, monkeypatch):
    def reject(*_): raise windows_error(112)
    monkeypatch.setattr(storage.os, "replace", reject)
    # Actual filesystem cleanup produces OSError, not an arbitrary callback error.
    monkeypatch.setattr(type(tmp_path), "unlink", lambda *_, **__: (_ for _ in ()).throw(OSError("private cleanup error")))
    with pytest.raises(PermissionError) as caught:
        storage.atomic_json(tmp_path / "job.json", {})
    assert caught.value.winerror == 112


def test_job_progress_replace_collision_does_not_abort_valid_terminal(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    replace = storage.os.replace
    collisions = []
    def replace_once(source, destination):
        value = json.loads(source.read_text())
        if value.get("last_output_check") and not collisions:
            collisions.append(1)
            raise windows_error(32)
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", replace_once)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    result = asyncio.run(owner.run(task_id="collision-success", snapshot=binding, operation="analyze_role_matching",
        payload={"scenario": "schema_check_native_terminal", "output_schema": schema}, seconds=10))
    saved = json.loads((owner.environment.root / "jobs" / (digest("collision-success") + ".json")).read_text())
    assert collisions == [1] and result["output_format"] == "native_json_schema"
    assert saved["status"] == "completed" and saved["cleanup_complete"]
    assert not owner.pending() and not owner.environment.state().get("qualification_records")


def test_permission_failure_in_progress_has_safe_windows_code_and_no_terminal(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    replace = storage.os.replace
    def fail_progress(source, destination):
        value = json.loads(source.read_text())
        if value.get("status") == "running" and value.get("last_output_check"):
            raise windows_error(5)
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", fail_progress)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="permission-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "schema_check", "output_schema": schema}, seconds=10))
    saved = json.loads((owner.environment.root / "jobs" / (digest("permission-failure") + ".json")).read_text())
    assert caught.value.code == "workbuddy_host_io_failed" and caught.value.detail["windows_error_code"] == 5
    assert saved["cleanup_complete"] and saved["status"] == "failed"
    assert "result_metadata" not in saved and "private" not in json.dumps(saved)
    assert list((owner.environment.root / "jobs").glob("*.tmp")) == []


def test_initial_record_failure_still_cleans_owned_process(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    def reject(*_): raise windows_error(5)
    monkeypatch.setattr(storage.os, "replace", reject)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="initial-write-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "hang"}, seconds=10))
    assert caught.value.code == "workbuddy_host_io_failed"
    assert caught.value.detail["protocol_phase"] == "job_record_write"
    assert caught.value.detail["record_persistence_failed"] is True
    assert not owner.active and "initial-write-failure" in owner.closed and not owner.pending()
    assert "private" not in str(caught.value) + json.dumps(caught.value.detail)


def test_final_record_failure_refuses_received_terminal_without_raw_os_error(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    replace = storage.os.replace
    def reject_final(source, destination):
        value = json.loads(source.read_text())
        if value.get("cleanup_complete"):
            raise windows_error(112)
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", reject_final)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="final-write-failure", snapshot=binding, operation="analyze_role_matching",
            payload={}, seconds=10))
    assert caught.value.code == "workbuddy_host_io_failed"
    assert caught.value.detail["protocol_phase"] == "cleanup_record_write"
    assert caught.value.detail["record_persistence_failed"] is True
    assert not owner.active and "private" not in str(caught.value) + json.dumps(caught.value.detail)
    saved = json.loads((owner.environment.root / "jobs" / (digest("final-write-failure") + ".json")).read_text())
    assert saved["status"] == "running" and not saved["cleanup_complete"]
    assert not owner.environment.state().get("qualification_records")


def test_final_record_failure_cannot_mask_primary_native_error(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    replace = storage.os.replace
    def reject_final(source, destination):
        value = json.loads(source.read_text())
        if value.get("cleanup_complete"):
            raise windows_error(112)
        return replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", reject_final)
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id="native-error-final-write-failure", snapshot=binding, operation="analyze_role_matching",
            payload={"scenario": "error_close_hang"}, seconds=10))
    assert caught.value.code == "workbuddy_upstream_empty_stream"
    assert caught.value.detail["record_persistence_failed"] is True
    assert not owner.active and "private" not in str(caught.value) + json.dumps(caught.value.detail)
