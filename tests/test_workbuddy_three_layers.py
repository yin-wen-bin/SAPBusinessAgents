"""Offline policy, protocol and migration tests. No SDK/model/SAP calls."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform import workbuddy_integration as integration
from sap_business_agents_platform import workbuddy_model_protocol as protocol
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest, atomic_json, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_site import make_configuration
from tests.test_workbuddy_isolation import release


from tests.workbuddy_integration_fakes import certificate


def fixture(tmp_path, monkeypatch, operations=None):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    monkeypatch.setattr(manager.environment, "release", lambda *_: value)
    check = {"compatible": True, "check_digest": "legacy-check", "environment_digest": value["environment_digest"],
        "actual_model": "new-model", "model_identity_source": "assistant_message", "identity_known": True,
        "authentication_revision": 0, **make_configuration("china", 1)}
    cert = certificate(manager, value, operations)
    manager.environment.mutate(lambda state: state.update(authenticated=True, enabled=False,
        default_model_id="new-model", environment_digest=value["environment_digest"],
        authentication_environment=value["environment_digest"], models={"new-model": check},
        runtime_admission_policy=integration.POLICY, sdk_integration_records={cert["id"]: cert}))
    return manager, value


def test_zero_business_passes_enable_select_dispatch_and_formal_identity(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "capabilities", lambda *a, **kw: pytest.fail("business projection used in admission"))
    monkeypatch.setattr(manager, "qualification_plan", lambda *a, **kw: pytest.fail("Campaign history scanned"))
    item = manager.snapshot()
    assert item["can_enable"] and not item["enabled"]
    assert item["business_evaluation"]["required_operation_count"] == 15
    manager.set_enabled(True)
    snapshot = manager.runtime_snapshot(formal=True)
    manager.environment.assert_operation(snapshot, "acceptance_baseline")
    manager.environment.assert_operation(snapshot, "acceptance_free_query")
    asyncio.run(manager.set_default_model("new-model"))
    assert not manager.environment.state().get("qualification_records")
    assert not manager.environment.state().get("validation_ledger")
    assert "probe_contract" not in manager._model_check(manager.environment.state(), manager.environment.release(), "new-model")


def test_business_updates_do_not_change_runtime_binding_or_model_check(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    manager.set_enabled(True)
    original = manager.runtime_snapshot()
    status = manager.snapshot()["configuration_digest"]
    catalog_digest = manager.models()["catalog_digest"]
    manager.environment.mutate(lambda state: state.update(business_evaluation_projections={"new-model": {"status": "failed"}}))
    assert manager.runtime_snapshot()["configuration_digest"] == original["configuration_digest"]
    assert manager.snapshot()["configuration_digest"] == status
    assert manager.models()["catalog_digest"] == catalog_digest
    assert manager.snapshot()["control_status"]["revision"] == manager.environment.state()["revision"]
    assert manager.snapshot()["runtime_configuration_revision"] == original["runtime_configuration_revision"]
    assert manager.snapshot()["can_enable"]
    manager.environment.assert_operation(original, "review_workflow")


def test_admission_never_projects_quality_or_other_models(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "business_evaluation", lambda *_: pytest.fail("quality evaluated in admission"))
    monkeypatch.setattr(manager, "models", lambda **_: pytest.fail("all models scanned in admission"))
    assert manager.snapshot(admission=True)["can_enable"]
    manager.set_enabled(True)
    assert manager.runtime_snapshot()["model"] == "new-model"


def test_sdk_registry_zero_quality_can_select_workbuddy_without_changing_codex(tmp_path, monkeypatch):
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManager
    path = tmp_path / "sdks.json"
    _write_runtime_registry(path)
    registry = SDKManager(path, tmp_path, adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()}, selection_path=tmp_path / "default.json")
    manager, _ = fixture(tmp_path, monkeypatch)
    registry.workbuddy = manager
    manager.set_enabled(True)
    assert registry.admission_status("workbuddy")["selectable"]
    assert registry.set_default("workbuddy")["selected"]
    assert registry.runtime_snapshot("workbuddy")["model"] == "new-model"
    assert not manager.environment.state().get("qualification_records")


@pytest.mark.parametrize("change,code", [
    ("certificate", "workbuddy_sdk_integration_required"),
    ("authentication", "workbuddy_authentication_required"),
    ("model", "runtime_model_check_required"),
    ("mode", "workbuddy_sdk_operation_not_certified"),
    ("operation", "workbuddy_sdk_operation_not_certified"),
    ("restricted", "workbuddy_windows_restricted_unverified"),
    ("auth_revision", "workbuddy_capability_binding_mismatch")])
def test_hard_gates_remain(tmp_path, monkeypatch, change, code):
    manager, _ = fixture(tmp_path, monkeypatch)
    manager.set_enabled(True)
    snapshot = manager.runtime_snapshot()
    if change == "certificate":
        manager.environment.mutate(lambda state: state.update(sdk_integration_records={}))
    if change == "authentication":
        manager.environment.mutate(lambda state: state.update(authenticated=False))
    if change == "model":
        manager.environment.mutate(lambda state: state["models"]["new-model"].update(compatible=False))
    if change == "auth_revision":
        snapshot["authentication_revision"] += 1
    with pytest.raises(WorkBuddyError, match=code):
        manager.environment.assert_operation(snapshot, "free_query" if change == "operation" else "review_workflow",
            mode="trusted_local" if change == "mode" else "restricted" if change == "restricted" else "bounded")


def test_sdk_certificate_independent_of_model_and_quality(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch)
    before = integration.projection(manager.environment, manager.environment.state(), value)
    manager.environment.mutate(lambda state: state.update(default_model_id="model-v2", models={}, capabilities={}))
    assert integration.projection(manager.environment, manager.environment.state(), value) == before
    cert = next(iter(manager.environment.state()["sdk_integration_records"].values()))
    path = manager.environment.root / cert["evidence"]["core"]["deadline"]["path"]
    path.write_text("{}", encoding="utf-8")
    assert integration.projection(manager.environment, manager.environment.state(), value)["status"] == "not_certified"


def test_boolean_assertions_without_concrete_test_artifact_cannot_certify(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch)
    cert = next(iter(manager.environment.state()["sdk_integration_records"].values()))
    evidence = copy.deepcopy(cert["evidence"])
    path = manager.environment.root / evidence["core"]["deadline"]["path"]
    report = json.loads(path.read_text(encoding="utf-8"))
    report.pop("test_report")
    atomic_json(path, report)
    for item in evidence["core"].values(): item["sha256"] = file_hash(path)
    with pytest.raises(WorkBuddyError, match="evidence_invalid"):
        integration.audit(manager.environment, value, evidence)


def test_operation_certificates_merge_without_recertifying_other_modes(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch, {"review_agent_feedback": ["bounded"]})
    assert manager.snapshot()["operation_availability"]["review_agent_feedback"]["status"] == "partially_certified"
    extra = certificate(manager, value, {"review_agent_feedback": ["trusted_local"]})
    manager.environment.mutate(lambda state: state["sdk_integration_records"].update({extra["id"]: extra}))
    status = integration.projection(manager.environment, manager.environment.state(), value)
    assert set(status["operations"]["review_agent_feedback"]) == {"bounded", "trusted_local"}
    assert len(status["certificate_ids"]) == 2


def test_probe_scope_allows_only_memory_tool_and_no_other_tools():
    challenge = protocol.Challenge()
    payload = challenge.payload("offline-workspace")
    assert protocol.probe_scope("model_check", payload, challenge.handle)
    assert not protocol.probe_scope("model_check", payload, lambda *_: {})
    assert not protocol.probe_scope("authentication", payload, challenge.handle)
    assert not protocol.probe_scope("model_check", {**payload, "tools": [*protocol.TOOLS, {"name": "sap_read"}]}, challenge.handle)


@pytest.mark.parametrize("failure", [None, "no_tool", "forged", "schema", "no_terminal", "cleanup", "timeout"])
def test_versioned_probe_idempotent_and_strict(tmp_path, monkeypatch, failure):
    manager, _ = fixture(tmp_path, monkeypatch)
    calls = []
    async def run(**kwargs):
        calls.append(kwargs)
        assert kwargs["seconds"] == 90 and kwargs["payload"]["timeout_ms"] == 90000
        assert kwargs["probe"] and protocol.probe_scope("model_check", kwargs["payload"], kwargs["tool_handler"])
        assert not kwargs.get("mode") or kwargs["mode"] == "bounded"
        challenge = kwargs["tool_handler"].__self__
        if failure == "timeout":
            raise WorkBuddyError("workbuddy_deadline_exceeded")
        echo = await kwargs["tool_handler"](protocol.TOOL, {"challenge": challenge.challenge}) if failure != "no_tool" else {"receipt": "guessed"}
        output = {"status": "ok", "challenge": challenge.challenge, "receipt": echo["receipt"],
                  "optional": None, "count": 1, "nested": {"kind": "echo"}}
        if failure == "forged": output["receipt"] = "not-the-tool-result"
        if failure == "schema": output["count"] = "1"
        atomic_json(manager.environment.root / "jobs" / (digest(kwargs["task_id"]) + ".json"),
                    {"status": "completed", "cleanup_complete": failure != "cleanup", "operation": "model_check", "runtime_model": "next-model",
                     "result_metadata": {"structured_terminal": True, "actual_model": "next-model", "identity_source": "assistant_message", "output_sha256": digest(json.dumps(output))}})
        return {"text": json.dumps(output), "output_format": None if failure == "no_terminal" else "native_json_schema",
                "actual_model": "next-model", "model_identity_source": "assistant_message"}
    monkeypatch.setattr(manager.supervisor, "run", run)
    result = asyncio.run(manager.check_model("next-model", request_id="check-1"))
    second = asyncio.run(manager.check_model("next-model", request_id="check-1"))
    assert second["check"] == result["check"] and len(calls) == 1
    assert result["check"]["compatible"] is (failure is None)
    assert result["check"]["probe_contract"] == protocol.CONTRACT
    with pytest.raises(WorkBuddyError, match="request_conflict"):
        asyncio.run(manager.check_model("another-model", request_id="check-1"))


def test_no_automatic_migration_and_no_cross_model_business_pass(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch)
    manager.environment.mutate(lambda state: state.pop("runtime_admission_policy"))
    before = manager.environment.state_path.read_bytes()
    assert not manager.snapshot()["can_enable"]
    assert manager.environment.state_path.read_bytes() == before
    assert integration.projection(manager.environment, manager.environment.state(), value)["reason"] == "workbuddy_admission_migration_required"


def test_protocol_dependencies_exclude_ui_quality_and_admission():
    assert not any(word in name for name in integration.PROTOCOL_FILES for word in ("manager", "qualification", "admission", "supervisor"))
    assert "workbuddy_model_protocol.py" in integration.PROTOCOL_FILES
    check = {"probe_contract": protocol.CONTRACT, "model_protocol_digest": "old"}
    assert not protocol.current(check)
    assert protocol.current({"compatible": True, "probe_contract": "sapba.workbuddy.model-check/2"})


def test_model_probe_contract_changes_do_not_invalidate_sdk_core(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch)
    before = integration.projection(manager.environment, manager.environment.state(), value)
    monkeypatch.setattr(integration, "protocol_digest", lambda *_: "new-probe-contract")
    assert integration.projection(manager.environment, manager.environment.state(), value) == before


def test_duplicate_inflight_request_is_read_not_redispatched(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    from tests.workbuddy_integration_fakes import probe_result
    calls = []
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def run(**kwargs):
            calls.append(kwargs)
            started.set()
            await finish.wait()
            return await probe_result(manager, kwargs, "other-model")
        monkeypatch.setattr(manager.supervisor, "run", run)
        task = asyncio.create_task(manager.check_model("other-model", request_id="inflight"))
        await started.wait()
        duplicate = await manager.check_model("other-model", request_id="inflight")
        assert duplicate["request"]["status"] == "running" and duplicate["check"] is None
        finish.set()
        assert (await task)["check"]["compatible"]
    asyncio.run(scenario())
    assert len(calls) == 1


def test_memory_probe_can_bootstrap_but_never_enable_without_sdk_certificate(tmp_path, monkeypatch):
    from tests.workbuddy_integration_fakes import probe_result
    manager, _ = fixture(tmp_path, monkeypatch)
    manager.environment.mutate(lambda state: state.update(sdk_integration_records={}))
    async def run(**kwargs):
        assert kwargs["probe"] and protocol.probe_scope("model_check", kwargs["payload"], kwargs["tool_handler"])
        return await probe_result(manager, kwargs, "other-model")
    monkeypatch.setattr(manager.supervisor, "run", run)
    result = asyncio.run(manager.check_model("other-model", request_id="bootstrap"))
    assert result["check"]["compatible"]
    assert not result["item"]["selectable"]
    assert not manager.snapshot()["can_enable"]
    with pytest.raises(WorkBuddyError, match="sdk_integration_required"):
        manager.runtime_snapshot("other-model", require_enabled=False)


@pytest.mark.parametrize("change", ["authentication_revision", "environment_digest", "service_site_revision"])
def test_changed_binding_rejects_late_probe_result(tmp_path, monkeypatch, change):
    manager, _ = fixture(tmp_path, monkeypatch)
    from tests.workbuddy_integration_fakes import probe_result
    old = copy.deepcopy(manager.environment.state()["models"])
    async def run(**kwargs):
        result = await probe_result(manager, kwargs, "other-model")
        manager.environment.mutate(lambda state: state.update({change: 42 if change != "environment_digest" else "f" * 64}))
        return result
    monkeypatch.setattr(manager.supervisor, "run", run)
    with pytest.raises(WorkBuddyError):
        asyncio.run(manager.check_model("other-model", request_id="late"))
    assert manager.environment.state()["models"] == old


def test_cleanup_and_installation_block_without_business_projection(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(manager.supervisor, "pending", lambda: [{"status": "cleanup_pending"}])
    assert not manager.snapshot()["can_enable"]
    with pytest.raises(WorkBuddyError, match="cleanup_pending"):
        manager.runtime_snapshot(require_enabled=False)
    monkeypatch.setattr(manager.supervisor, "pending", lambda: [])
    def broken(*_):
        raise WorkBuddyError("workbuddy_environment_digest_mismatch")
    monkeypatch.setattr(manager.environment, "release", broken)
    assert not manager.snapshot()["can_enable"]
    with pytest.raises(WorkBuddyError, match="digest_mismatch"):
        manager.runtime_snapshot(require_enabled=False)


def test_explicit_activation_requires_writer_clearance_before_mutation(tmp_path, monkeypatch):
    manager, value = fixture(tmp_path, monkeypatch)
    cert = certificate(manager, value)
    original = manager.environment.state_path.read_bytes()
    with pytest.raises(WorkBuddyError, match="writer_clearance_required"):
        integration.activate(manager.environment, cert, request_id="activate", clearance=lambda: {})
    assert manager.environment.state_path.read_bytes() == original


def test_new_model_switch_keeps_old_snapshot_and_never_runs_probe(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    manager.set_enabled(True)
    original = manager.runtime_snapshot()
    state = manager.environment.state()
    other = {**state["models"]["new-model"], "actual_model": "other-model", "check_digest": "other-legacy"}
    manager.environment.mutate(lambda value: value["models"].update({"other-model": other}))
    monkeypatch.setattr(manager.supervisor, "run", lambda **_: pytest.fail("model switch invoked a probe"))
    asyncio.run(manager.set_default_model("other-model"))
    assert manager.bound_snapshot(original) == original
    manager.environment.assert_operation(original, "review_workflow")
    assert manager.runtime_snapshot()["model"] == "other-model"


def test_isolated_migration_appends_and_rollbacks_can_read_without_rewriting_pass(tmp_path, monkeypatch):
    import time
    from sap_business_agents_platform.runtime_validation_qualification import activate as activate_ledger
    manager, value = fixture(tmp_path, monkeypatch)
    def clearance():
        return {"all_writers_checked": True, "old_writers": [], "active_jobs": [], "cleanup_pending": [],
            "state_digest": digest(manager.environment.state()), "process_inventory_digest": "offline-test-inventory", "checked_at": time.time()}
    activate_ledger(manager.environment, clearance)
    legacy = {"id": "old-business-pass", "status": "PASS"}
    manager.environment.mutate(lambda state: state.update(business_evaluation_projections={"old-model": {"qualification": legacy}}))
    before = manager.environment.state()
    cert = certificate(manager, value)
    first = integration.activate(manager.environment, cert, request_id="migration", clearance=clearance)
    after = manager.environment.state()
    assert first["status"] == "applied"
    assert after["models"] == before["models"] and after["default_model_id"] == before["default_model_id"]
    assert after["validation_ledger"] == before["validation_ledger"]
    assert after["qualification_records"] == before["qualification_records"]
    assert after["business_evaluation_projections"] == before["business_evaluation_projections"]
    assert integration.activate(manager.environment, cert, request_id="migration", clearance=lambda: {})["status"] == "already_applied"
    assert manager.environment.state() == after
