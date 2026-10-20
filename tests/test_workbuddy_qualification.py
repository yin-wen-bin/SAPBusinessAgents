"""Offline ledger/provenance checks. No SDK, model, login or SAP is invoked."""
import copy
import json
from pathlib import Path

import pytest

from sap_business_agents_platform import workbuddy_qualification as q
from sap_business_agents_platform.runtime_policy import ORCHESTRATION_VERSION, orchestration_digest, OPERATIONS
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_site import make_configuration


def make_release(environment, label="first", worker=b"worker", cfg_command="first"):
    directory = environment.root / "building" / label
    directory.mkdir(parents=True)
    for name, content in {"python.exe": b"python", "cli.exe": b"cli", "worker.py": worker,
            "pyvenv.cfg": ("home = C:/Python\ninclude-system-site-packages = false\nversion = 3.13\ncommand = " + cfg_command).encode()}.items():
        (directory / name).write_bytes(content)
    data = {"sdk_version": "0.3.247", "cli_version": "2.141.0", "python": "python.exe", "cli": "cli.exe", "worker": "worker.py",
        "sdk_wheel_digest": "wheel", "dependency_lock_digest": "lock", "python_bootstrap_digest": "base",
        "service_site_contract": "1", "files": {p.name: file_hash(p) for p in directory.iterdir()}, "build_label": label}
    key = digest(data)
    target = environment.root / "releases" / key
    target.parent.mkdir(exist_ok=True)
    directory.rename(target)
    atomic_json(target / "environment.json", {**data, "environment_digest": key})
    return environment.release(key)


def manager_fixture(tmp_path):
    manager = WorkBuddyManager(tmp_path)
    release = make_release(manager.environment)
    current = make_configuration("china", 1)
    check = {"compatible": True, "environment_digest": release["environment_digest"], "check_digest": "initial-check",
        "actual_model": "deepseek-v4.1-flash", "identity_known": True, "model_identity_source": "assistant_message",
        "execution_digest": q.execution_components(release)["digest"], "authentication_revision": 4, **current}
    check["management_digest"] = q.management_digest()
    checks = {"deepseek-v4.1-flash": check, "hy4-preview": {**check, "actual_model": "hy4-preview"}}
    for model, value in checks.items():
        value["model_id"] = model
        job_id = "probe-" + model
        job = {"operation": "model_check", "runtime_model": model, "status": "completed", "cleanup_complete": True,
            "environment_digest": release["environment_digest"], "authentication_revision": 4, "management_digest": q.management_digest(), **current,
            "result_metadata": {"structured_terminal": True, "actual_model": model, "identity_source": "assistant_message"}}
        atomic_json(manager.environment.root / "jobs" / (digest(job_id) + ".json"), job)
        value.update(source_job_id=job_id, source_job_digest=digest(job))
    manager.environment.mutate(lambda s: s.update(environment_digest=release["environment_digest"],
        authenticated=True, authentication_environment=release["environment_digest"], authentication_revision=4,
        authentication_site=current, default_model_id="deepseek-v4.1-flash", **current,
        models=checks))
    from tests.workbuddy_integration_fakes import install_certificate
    install_certificate(manager, release)
    return manager, release


def live_pass(manager, release, operation="sample_discovery", mode="bounded", model="deepseek-v4.1-flash", job_id="live-one"):
    snapshot = manager.runtime_snapshot(model, require_enabled=False)
    target = q.binding(snapshot, release, operation, mode)
    job = {"task_id": job_id, "status": "completed", "cleanup_complete": True, "validation_job": True,
        "environment_digest": release["environment_digest"], "runtime_digest": digest(snapshot), "permission_mode": mode,
        "operation": operation, "orchestration_version": ORCHESTRATION_VERSION,
        "orchestration_digest": orchestration_digest(operation), "qualification_binding": target,
        "request_metadata": {"schema_sha256": "schema", "input_sha256": "input"},
        "result_metadata": {"structured_terminal": True, "actual_model": model, "identity_source": "assistant_message", "output_sha256": "output"}}
    path = manager.environment.root / "jobs" / (digest(job_id) + ".json")
    atomic_json(path, job)
    manager.record_operation_validation(snapshot, operation, job_id=job_id, permission_modes=[mode],
        evidence={"platform_contract_passed": True, "kind": "live_validation"})
    record = next(r for r in manager.environment.state()["qualification_records"].values() if r.get("job_id") == job_id)
    return snapshot, record, path


def target_receipt(manager, release, job_id="target-startup"):
    state = manager.environment.state()
    context = make_configuration("china", 1)
    job = {"task_id": job_id, "operation": "authentication", "environment_digest": release["environment_digest"],
        "authentication_revision": state["authentication_revision"], "status": "completed", "cleanup_complete": True,
        "management_digest": q.management_digest(),
        "result_metadata": {"authenticated": True}, **context}
    atomic_json(manager.environment.root / "jobs" / (digest(job_id) + ".json"), job)
    manager.environment.mutate(lambda s: s.update(environment_digest=release["environment_digest"],
        authentication_environment=release["environment_digest"]))
    manager._record_environment_check(release, context, job_id)


def test_models_are_isolated_and_forged_snapshot_cannot_grant_dispatch(tmp_path):
    manager, release = manager_fixture(tmp_path)
    original, record, _ = live_pass(manager, release)
    assert manager.capabilities(release["environment_digest"], model="deepseek-v4.1-flash")["sample_discovery"]["status"] == "validated"
    assert manager.capabilities(release["environment_digest"], model="hy4-preview")["sample_discovery"]["status"] == "unverified"
    forged = {**manager.runtime_snapshot("hy4-preview", require_enabled=False), "operation_capabilities": {"sample_discovery": {"status": "validated"}}}
    # Quality projections neither grant nor deny independently certified SDK support.
    manager.environment.assert_operation(forged, "sample_discovery")
    manager.environment.mutate(lambda s: s.update(default_model_id="hy4-preview"))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "unverified"
    manager.environment.mutate(lambda s: s.update(default_model_id="deepseek-v4.1-flash"))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "validated"
    assert manager.environment.state()["qualification_records"][record["id"]] == record


def test_refresh_of_same_model_check_does_not_destroy_business_pass(tmp_path):
    manager, release = manager_fixture(tmp_path)
    snapshot, _, _ = live_pass(manager, release)
    def refresh(s):
        s.setdefault("model_check_history", []).append(copy.deepcopy(s["models"]["deepseek-v4.1-flash"]))
        s["models"]["deepseek-v4.1-flash"].update(check_digest="new-timestamp-check")
    manager.environment.mutate(refresh)
    manager.environment.assert_operation(snapshot, "sample_discovery")
    manager.environment.mutate(lambda s: s["models"]["deepseek-v4.1-flash"].update(actual_model="changed-identity"))
    with pytest.raises(WorkBuddyError):
        manager.environment.assert_operation(snapshot, "sample_discovery")


def test_same_output_model_does_not_merge_distinct_requested_routes(tmp_path):
    manager, release = manager_fixture(tmp_path)
    live_pass(manager, release)
    state = manager.environment.state()
    manager.environment.mutate(lambda s: s["models"].update(alias=copy.deepcopy(state["models"]["deepseek-v4.1-flash"])))
    assert manager.capabilities(release["environment_digest"], model="alias")["sample_discovery"]["status"] == "unverified"


def test_permission_modes_are_separate_and_registration_is_idempotent(tmp_path):
    manager, release = manager_fixture(tmp_path)
    snapshot, record, _ = live_pass(manager, release, "review_agent_feedback")
    args = dict(job_id="live-one", permission_modes=["bounded"], evidence={"platform_contract_passed": True, "kind": "live_validation"})
    manager.record_operation_validation(snapshot, "review_agent_feedback", **args)
    assert len(manager.environment.state()["qualification_records"]) == 1
    capability = manager.capabilities(release["environment_digest"])["review_agent_feedback"]
    assert capability["permission_modes"] == ["bounded"]
    live_pass(manager, release, "review_agent_feedback", "trusted_local", job_id="trusted")
    assert manager.capabilities(release["environment_digest"])["review_agent_feedback"]["permission_modes"] == ["bounded", "trusted_local"]
    assert manager.environment.state()["qualification_records"][record["id"]] == record


@pytest.mark.parametrize("name,affected", [("sample_scope_graph.py", {"sample_discovery"}),
    ("role_matching.py", {"analyze_role_matching", "review_role_matching_feedback"}),
    ("assistant_tools.py", set(OPERATIONS)), ("workbuddy_supervisor.py", set(OPERATIONS)),
    ("codex_driver.py", set())])
def test_per_operation_transitive_dependencies_and_codex_exclusion(monkeypatch, name, affected):
    baseline = q.dependency_manifests()
    read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: read(p) + b"\n# changed" if p.name == name else read(p))
    current = q.dependency_manifests()
    assert {op for op in OPERATIONS if baseline[op]["digest"] != current[op]["digest"]} == affected


def test_unknown_or_cyclic_component_registry_fails_closed(monkeypatch):
    monkeypatch.setitem(q.COMPONENTS, "sample", (("sample",), ()))
    with pytest.raises(WorkBuddyError, match="registry_invalid"):
        q.dependency_manifests()


def test_unrelated_change_retains_role_pass_but_invalidates_sample(tmp_path, monkeypatch):
    manager, release = manager_fixture(tmp_path)
    live_pass(manager, release, "sample_discovery")
    live_pass(manager, release, "analyze_role_matching", job_id="role")
    read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: read(p) + b"\n# changed" if p.name == "sample_scope_graph.py" else read(p))
    values = manager.capabilities(release["environment_digest"])
    assert values["sample_discovery"]["status"] == "unverified"
    assert "dependency:sample_scope_graph.py" in values["sample_discovery"]["change_reasons"]
    assert values["analyze_role_matching"]["status"] == "validated"


def test_new_release_requires_owned_startup_and_audited_reuse(tmp_path):
    manager, old = manager_fixture(tmp_path)
    _, record, _ = live_pass(manager, old)
    old_bytes = manager.environment.state()["qualification_records"][record["id"]]
    new = make_release(manager.environment, "second", cfg_command="second")
    assert new["environment_digest"] != old["environment_digest"]
    assert q.execution_components(new)["digest"] == q.execution_components(old)["digest"]
    manager.environment.mutate(lambda s: s.update(environment_digest=new["environment_digest"]))
    assert manager.qualification_plan()["reuse_candidates"] == []
    with pytest.raises(Exception) as failure:
        manager.apply_qualification_reuse("deepseek-v4.1-flash", record["id"])
    assert failure.value.code == "workbuddy_authentication_required"
    target_receipt(manager, new)
    manager.apply_model_check_reuse("deepseek-v4.1-flash")
    assert manager.runtime_snapshot(require_enabled=False)["environment_digest"] == new["environment_digest"]
    plan = manager.qualification_plan()
    assert len(plan["reuse_candidates"]) == 1
    assert next(c for c in plan["cases"] if c["operation"] == "sample_discovery")["action"] == "auditable_reuse"
    identifier = manager.apply_qualification_reuse("deepseek-v4.1-flash", record["id"])
    assert manager.apply_qualification_reuse("deepseek-v4.1-flash", record["id"]) == identifier
    capability = manager.capabilities(new["environment_digest"])["sample_discovery"]
    assert capability["status"] == "validated"
    assert capability["validation_origins"]["bounded"]["kind"] == "reuse"
    assert manager.environment.state()["qualification_records"][record["id"]] == old_bytes
    assert manager.environment.state()["models"]["deepseek-v4.1-flash"]["environment_digest"] == old["environment_digest"]


@pytest.mark.parametrize("change", ["worker", "sdk", "scope", "system_packages", "unknown_cfg"])
def test_environment_changes_cannot_be_hidden_by_same_version(tmp_path, change):
    manager, old = manager_fixture(tmp_path)
    new = make_release(manager.environment, "second", worker=b"different" if change == "worker" else b"worker")
    if change in {"sdk", "scope"}:
        new = copy.deepcopy(new)
        new["sdk_wheel_digest" if change == "sdk" else "dependency_lock_digest"] = "different"
    if change in {"system_packages", "unknown_cfg"}:
        path = Path(new["directory"]) / "pyvenv.cfg"
        path.write_text(path.read_text().replace("false", "true") if change == "system_packages" else path.read_text() + "\ncustom = unknown")
    if change == "unknown_cfg":
        with pytest.raises(WorkBuddyError, match="equivalence_unknown"):
            q.execution_components(new)
    else:
        assert q.execution_components(new)["digest"] != q.execution_components(old)["digest"]


@pytest.mark.parametrize("field,value", [("status", "cancelled"), ("cleanup_complete", False),
    ("validation_job", False), ("runtime_digest", "forged")])
def test_bad_owned_job_cannot_register_or_replace_pass(tmp_path, field, value):
    manager, release = manager_fixture(tmp_path)
    snapshot, record, path = live_pass(manager, release)
    original = manager.environment.state()["qualification_records"][record["id"]]
    job = json.loads(path.read_text()); job[field] = value; atomic_json(path, job)
    with pytest.raises(Exception):
        manager.record_operation_validation(snapshot, "sample_discovery", job_id="live-one", permission_modes=["bounded"],
            evidence={"platform_contract_passed": True, "kind": "live_validation"})
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "unverified"
    assert manager.environment.state()["qualification_records"][record["id"]] == original


def test_newer_failure_blocks_fallback_and_different_model_failure_does_not(tmp_path):
    manager, release = manager_fixture(tmp_path)
    _, record, _ = live_pass(manager, release)
    manager.environment.mutate(lambda s: q.add_record(s, {"kind": "failure", "binding": record["binding"],
        "environment_digest": release["environment_digest"], "job_id": "failed", "failure_code": "contract"}))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["reason"] == "workbuddy_qualification_latest_failure"
    live_pass(manager, release, job_id="repaired")
    other = q.binding(manager.runtime_snapshot("hy4-preview", require_enabled=False), release, "sample_discovery", "bounded")
    manager.environment.mutate(lambda s: q.add_record(s, {"kind": "failure", "binding": other, "job_id": "other"}))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "validated"


def test_legacy_migration_never_infers_model_or_rewrites_pass(tmp_path):
    manager, release = manager_fixture(tmp_path)
    key = release["environment_digest"] + ":" + make_configuration("china", 1)["service_site_digest"] + ":sample_discovery:1.0"
    original = {"status": "validated", "permission_modes": ["bounded"], "job_id": "old"}
    manager.environment.mutate(lambda s: s.update(capabilities={key: original}))
    before = manager.environment.state_path.read_bytes()
    plan = manager.qualification_plan()
    assert plan["effective_pass_count"] == 0 and plan["legacy_pass_count"] == 1
    assert manager.environment.state_path.read_bytes() == before
    first = manager.migrate_qualification_history()
    second = manager.migrate_qualification_history()
    assert first == second and next(iter(first.values()))["model"] is None
    assert manager.environment.state()["capabilities"][key] == original
    assert manager.qualification_plan()["effective_pass_count"] == 0


def test_tampered_or_missing_startup_receipt_revokes_reuse(tmp_path):
    manager, old = manager_fixture(tmp_path)
    _, record, _ = live_pass(manager, old)
    new = make_release(manager.environment, "second")
    target_receipt(manager, new)
    manager.apply_model_check_reuse("deepseek-v4.1-flash")
    manager.apply_qualification_reuse("deepseek-v4.1-flash", record["id"])
    path = manager.environment.root / "jobs" / (digest("target-startup") + ".json")
    path.unlink()
    assert manager.capabilities(new["environment_digest"])["sample_discovery"]["status"] == "unverified"
    with pytest.raises(Exception):
        manager.runtime_snapshot(require_enabled=False)


def test_snapshot_plan_and_enable_gate_agree(tmp_path):
    manager, release = manager_fixture(tmp_path)
    live_pass(manager, release)
    snapshot = manager.snapshot()
    assert manager.qualification_plan()["effective_pass_count"] == 1
    assert snapshot["can_enable"] is True and snapshot["sdk_integration"]["status"] == "certified"
    assert snapshot["enabled"] is False


def test_owned_job_metadata_has_no_sensitive_original_values(tmp_path):
    manager, release = manager_fixture(tmp_path)
    _, record, _ = live_pass(manager, release)
    assert "text" not in record["result_metadata"]
    assert set(record["input_metadata"]) == {"schema_sha256", "input_sha256"}


def test_model_probe_job_missing_prevents_cross_release_reuse(tmp_path):
    manager, old = manager_fixture(tmp_path)
    new = make_release(manager.environment, "second")
    target_receipt(manager, new)
    check = manager.environment.state()["models"]["deepseek-v4.1-flash"]
    (manager.environment.root / "jobs" / (digest(check["source_job_id"]) + ".json")).unlink()
    with pytest.raises(WorkBuddyError, match="reuse_not_proven"):
        manager.apply_model_check_reuse("deepseek-v4.1-flash")


def test_reuse_always_anchors_original_live_job_not_derived_receipt(tmp_path):
    manager, old = manager_fixture(tmp_path)
    _, original, _ = live_pass(manager, old)
    second = make_release(manager.environment, "second")
    target_receipt(manager, second)
    manager.apply_model_check_reuse("deepseek-v4.1-flash")
    reused = manager.apply_qualification_reuse("deepseek-v4.1-flash", original["id"])
    third = make_release(manager.environment, "third")
    target_receipt(manager, third, "third-startup")
    manager.apply_model_check_reuse("deepseek-v4.1-flash")
    with pytest.raises(WorkBuddyError, match="reuse_not_proven"):
        manager.apply_qualification_reuse("deepseek-v4.1-flash", reused)
    identifier = manager.apply_qualification_reuse("deepseek-v4.1-flash", original["id"])
    assert manager.environment.state()["qualification_records"][identifier]["source_record_id"] == original["id"]


def test_authenticated_site_or_account_revision_changes_block_old_eligibility(tmp_path):
    manager, release = manager_fixture(tmp_path)
    live_pass(manager, release)
    manager.environment.mutate(lambda s: s.update(authentication_revision=5))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "unverified"
    manager.environment.mutate(lambda s: s.update(**make_configuration("international", 2)))
    assert manager.capabilities(release["environment_digest"])["sample_discovery"]["status"] == "unverified"


def test_enabled_runtime_can_switch_to_compatible_unevaluated_model(tmp_path):
    import asyncio
    manager, release = manager_fixture(tmp_path)
    manager.environment.mutate(lambda s: s.update(enabled=True))
    before = manager.environment.state_path.read_bytes()
    asyncio.run(manager.set_default_model("hy4-preview"))
    assert manager.environment.state()["default_model_id"] == "hy4-preview"
    assert manager.qualification_plan("hy4-preview")["effective_pass_count"] == 0


def test_legacy_snapshot_attribution_is_exact_and_never_grants_eligibility(tmp_path):
    manager, release = manager_fixture(tmp_path)
    snapshot, _, path = live_pass(manager, release)
    key = release["environment_digest"] + ":" + snapshot["service_site_digest"] + ":sample_discovery:1.0"
    legacy = {"status": "validated", "environment_digest": release["environment_digest"],
        "authentication_revision": 4, "job_id": "live-one", **make_configuration("china", 1)}
    manager.environment.mutate(lambda s: s.update(capabilities={key: legacy}, qualification_records={}))
    history = manager.migrate_qualification_history([{**snapshot, "model": "hy4-preview"}])
    assert next(iter(history.values()))["model"] is None
    history = manager.migrate_qualification_history([snapshot])
    attributed = [v for v in history.values() if v.get("model")]
    assert len(attributed) == 1 and attributed[0]["model"]["requested_model"] == "deepseek-v4.1-flash"
    assert manager.qualification_plan()["historical_pass_count"] == 1
    assert manager.qualification_plan()["effective_pass_count"] == 0
    assert manager.environment.state()["capabilities"][key] == legacy


def test_private_cli_inspection_is_read_only(tmp_path, capsys):
    import importlib.util
    manager, release = manager_fixture(tmp_path)
    path = Path(__file__).resolve().parents[1] / "scripts/inspect-workbuddy-qualification.py"
    spec = importlib.util.spec_from_file_location("qualification_cli", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    before = manager.environment.state_path.read_bytes()
    assert module.main(["--root", str(tmp_path), "--model", "deepseek-v4.1-flash"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["cases"]) == 16 and report["effective_pass_count"] == 0
    assert manager.environment.state_path.read_bytes() == before


def test_validation_worker_failure_is_recorded_without_changing_wire_protocol(tmp_path, monkeypatch):
    import asyncio
    from tests.test_workbuddy_isolation import supervisor, verified_binding
    owner, snapshot = supervisor(tmp_path, monkeypatch)
    snapshot = verified_binding(owner, snapshot)
    async def run():
        with owner.verification(snapshot, {"review_workflow"}):
            with pytest.raises(WorkBuddyError, match="handshake_mismatch"):
                await owner.run(task_id="failed-validation", snapshot=snapshot, operation="review_workflow",
                    payload={"scenario": "bad_handshake"}, seconds=5)
    asyncio.run(run())
    values = owner.environment.state()["qualification_records"].values()
    assert {v["kind"] for v in values} == {"failure"}
    assert any(v["failure_code"] == "workbuddy_qualification_validation_pending" for v in values)
    assert any(v["failure_code"] == "workbuddy_qualification_execution_failed" for v in values)
    job = json.loads((owner.environment.root / "jobs" / (digest("failed-validation") + ".json")).read_text())
    assert job["cleanup_complete"] and job["status"] != "completed"
    assert job["protocol"] == "sapba.workbuddy.worker/1"


def test_management_actions_never_reap_another_api_worker(tmp_path, monkeypatch):
    manager, release = manager_fixture(tmp_path)
    atomic_json(manager.environment.root / "jobs/other-controller.json", {"status": "running", "pid": 123})
    monkeypatch.setattr(manager.supervisor, "reconcile", lambda: pytest.fail("Must not terminate another controller's worker"))
    before = manager.environment.state_path.read_bytes()
    for action in (manager.migrate_qualification_history, lambda: manager.apply_model_check_reuse("deepseek-v4.1-flash"),
            lambda: manager.apply_qualification_reuse("deepseek-v4.1-flash", "unknown")):
        with pytest.raises(WorkBuddyError, match="qualification_busy"):
            action()
    assert manager.environment.state_path.read_bytes() == before
