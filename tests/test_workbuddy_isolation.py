"""Codex isolation, immutable environments and real owned offline process checks."""
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import pytest

from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, WorkBuddyError, digest, file_hash, atomic_json, OPERATIONS
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_job import process_identity

ROOT = Path(__file__).resolve().parents[1]


def release(environment):
    key = "a" * 64
    directory = environment.root / "releases" / key
    directory.mkdir(parents=True)
    worker = directory / "worker.py"
    worker.write_bytes((ROOT / "tests/fixtures/workbuddy_protocol_worker.py").read_bytes())
    return {"environment_digest": key, "directory": str(directory), "worker": "worker.py",
        "python_path": str(getattr(sys, "_base_executable", sys.executable)), "cli_path": "fixture-cli",
        "files": {"worker.py": file_hash(worker)}, "sdk_version": "fixture", "cli_version": "fixture"}


def supervisor(tmp_path, monkeypatch):
    environment = WorkBuddyEnvironment(tmp_path)
    value = release(environment)
    monkeypatch.setattr(environment, "release", lambda *_: value)
    monkeypatch.setattr(environment, "assert_operation", lambda *_args, **_kwargs: value)
    return WorkBuddySupervisor(environment), {"provider_id": "workbuddy", "environment_digest": value["environment_digest"], "reasoning_effort": None}


def test_platform_dependency_and_import_boundaries():
    import ast
    assert '"codebuddy-agent-sdk' not in (ROOT / "pyproject.toml").read_text()
    for name in ("workbuddy_planner.py", "workbuddy_manager.py", "workbuddy_environment.py", "workbuddy_supervisor.py", "workbuddy_authoring.py"):
        for item in ast.walk(ast.parse((ROOT / "src/sap_business_agents_platform" / name).read_text(encoding="utf-8"))):
            if isinstance(item, ast.ImportFrom):
                assert not any(word in (item.module or "") for word in ("codebuddy_agent_sdk", "codex_planner", "openai_codex"))
    for item in ast.walk(ast.parse((ROOT / "src/sap_business_agents_platform/workbuddy_worker.py").read_text())):
        if isinstance(item, ast.ImportFrom):
            assert not any(word in (item.module or "") for word in ("sap_business_agents_platform", "codex", "workflow_authoring_runtime"))


def test_release_content_digest_and_path_escape(tmp_path):
    environment = WorkBuddyEnvironment(tmp_path)
    data = {"python": "python.exe", "cli": "cli.exe", "worker": "worker.py",
            "files": {name: __import__('hashlib').sha256(b"safe").hexdigest() for name in ("python.exe", "cli.exe", "worker.py")}}
    key = digest(data)
    directory = environment.root / "releases" / key
    directory.mkdir(parents=True)
    for name in data["files"]:
        (directory / name).write_bytes(b"safe")
    atomic_json(directory / "environment.json", {**data, "environment_digest": key})
    assert environment.release(key)["environment_digest"] == key
    (directory / "worker.py").write_bytes(b"evil")
    with pytest.raises(WorkBuddyError, match="digest_mismatch"):
        environment.release(key)
    with pytest.raises(WorkBuddyError):
        environment.release("../other")


def test_worker_rewrite_with_identical_stat_identity_cannot_reuse_release_cache(tmp_path, monkeypatch):
    import hashlib
    environment = WorkBuddyEnvironment(tmp_path)
    data = {"python": "python.exe", "cli": "cli.exe", "worker": "worker.py",
        "files": {name: hashlib.sha256(b"safe").hexdigest() for name in ("python.exe", "cli.exe", "worker.py")}}
    key = digest(data)
    directory = environment.root / "releases" / key
    directory.mkdir(parents=True)
    for name in data["files"]:
        (directory / name).write_bytes(b"safe")
    atomic_json(directory / "environment.json", {**data, "environment_digest": key})
    worker = (directory / "worker.py").resolve()
    identity = worker.stat()
    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda path, *args, **kwargs: identity if path == worker
        else original_stat(path, *args, **kwargs))
    environment.release(key)
    worker.write_bytes(b"evil")
    with pytest.raises(WorkBuddyError, match="digest_mismatch"):
        environment.release(key)


@pytest.mark.parametrize("scenario,code", [("bad_handshake", "workbuddy_handshake_mismatch"),
    ("bad_attempt", "workbuddy_attempt_mismatch"), ("large_frame", "workbuddy_message_limit"),
    ("large_output", "workbuddy_output_limit")])
def test_worker_boundaries_and_cleanup(tmp_path, monkeypatch, scenario, code):
    owner, binding = supervisor(tmp_path, monkeypatch)
    with pytest.raises(WorkBuddyError) as failure:
        asyncio.run(owner.run(task_id="owned", snapshot=binding, operation="review_workflow", payload={"scenario": scenario}, seconds=10))
    assert failure.value.code == code
    assert not owner.active and not owner.pending()
    record = json.loads((owner.environment.root / "jobs" / (digest("owned") + ".json")).read_text())
    assert record["cleanup_complete"] and record["status"] != "completed"


def test_tool_relay_and_no_connection_credentials(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    monkeypatch.setenv("SAP_PASSWORD", "not-for-worker")
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-worker")
    calls = []
    async def scenario():
        async def tool(name, arguments):
            calls.append((name, arguments))
            return {"ok": True}
        result = await owner.run(task_id="one", snapshot=binding, operation="review_workflow", payload={"scenario": "tool"}, seconds=10, tool_handler=tool)
        assert json.loads(result["text"]) == {"ok": True}
        result = await owner.run(task_id="two", snapshot=binding, operation="review_workflow", payload={}, seconds=10)
        assert "SAP_PASSWORD" not in result["env"] and "OPENAI_API_KEY" not in result["env"]
    asyncio.run(scenario())
    assert calls == [("approved_fixture", {"value": 1})]


def test_timeout_and_owned_child_tree(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    observed = []
    async def scenario():
        with pytest.raises(WorkBuddyError, match="deadline_exceeded"):
            await owner.run(task_id="timeout", snapshot=binding, operation="review_workflow", payload={"scenario": "hang"}, seconds=0.2)
        task = asyncio.create_task(owner.run(task_id="child", snapshot=binding, operation="review_workflow",
            payload={"scenario": "child"}, seconds=10, emit=lambda kind, data: observed.append(data["pid"])))
        for _ in range(200):
            if observed:
                break
            await asyncio.sleep(0.01)
        assert observed
        assert not await owner.cancel("foreign")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(100):
            if process_identity(observed[0]) is None:
                break
            await asyncio.sleep(0.01)
        assert process_identity(observed[0]) is None
        assert not owner.pending()
    asyncio.run(scenario())


def test_reconcile_does_not_kill_reused_or_uncertain_pid(tmp_path, monkeypatch):
    owner, _ = supervisor(tmp_path, monkeypatch)
    path = owner.environment.root / "jobs/old.json"
    atomic_json(path, {"task_id": "old", "status": "running", "pid": 123, "process_identity": "old", "environment_digest": "a" * 64})
    monkeypatch.setattr("sap_business_agents_platform.workbuddy_supervisor.process_identity", lambda *_: "new")
    assert owner.reconcile() == []
    assert json.loads(path.read_text())["status"] == "interrupted"
    atomic_json(path, {"task_id": "old", "status": "running", "pid": 123, "process_identity": "old"})
    monkeypatch.setattr("sap_business_agents_platform.workbuddy_supervisor.process_identity", lambda *_: "unknown")
    assert owner.reconcile()[0]["status"] == "cleanup_pending"


def test_operations_modes_model_identity_and_old_environment_binding(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    monkeypatch.setattr(manager.environment, "release", lambda key=None: {**value, "environment_digest": key or value["environment_digest"]})
    binding = {"provider_id": "workbuddy", "environment_digest": "a" * 64, "reasoning_effort": None, "model": "route", "operation_capabilities": {}}
    with pytest.raises(WorkBuddyError, match="not validated"):
        manager.environment.assert_operation(binding, "review_workflow")
    with pytest.raises(WorkBuddyError, match="restricted_unverified"):
        manager.environment.assert_operation(binding, "review_workflow", mode="restricted")
    manager.environment.mutate(lambda state: state.update(environment_digest="b" * 64, default_model_id="new"))
    assert manager.bound_snapshot(binding)["environment_digest"] == "a" * 64
    assert manager.bound_snapshot(binding)["model"] == "route"
    with pytest.raises(Exception, match="Unknown model identity"):
        manager.bound_snapshot(binding, formal=True)


def test_unverified_operations_cannot_enable_even_with_checked_model(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    monkeypatch.setattr(manager.environment, "release", lambda *_: value)
    manager.environment.mutate(lambda state: state.update(environment_digest="a" * 64, authenticated=True,
        authentication_environment="a" * 64, default_model_id="model", models={"model": {"compatible": True, "environment_digest": "a" * 64}}))
    assert "workbuddy_operation_validation_required" in manager.snapshot()["blockers"]
    with pytest.raises(Exception, match="operation validation"):
        manager.set_enabled(True)


def test_probe_cannot_bypass_operation_or_tool_scope(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    for operation, payload, mode in (("review_workflow", {}, "bounded"), ("model_check", {"tools": [{"name": "unsafe"}]}, "bounded"),
                                    ("authentication", {}, "trusted_local")):
        with pytest.raises(WorkBuddyError, match="probe_scope_invalid"):
            asyncio.run(owner.run(task_id="bad", snapshot=binding, operation=operation, payload=payload,
                                 seconds=1, mode=mode, probe=True))
    assert not owner.active


def test_queue_capacity_is_acquired_before_execution_clock(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr("sap_business_agents_platform.workbuddy_supervisor.time.time", lambda: clock["now"])
    async def scenario():
        entered = asyncio.Event()
        release_queue = asyncio.Event()
        async def held():
            async with owner.reserve():
                entered.set()
                await release_queue.wait()
        first = asyncio.create_task(held())
        await entered.wait()
        next_job = asyncio.create_task(owner.run(task_id="queued", snapshot=binding,
            operation="review_workflow", payload={}, seconds=2))
        await asyncio.sleep(0)
        assert not owner.active
        # Simulated 30-second queue exceeds the job budget; no dependence on
        # Windows interpreter startup fitting inside a fragile 200ms limit.
        clock["now"] += 30
        release_queue.set()
        result = await next_job
        assert result["text"]
        record = json.loads((owner.environment.root / "jobs" / (digest("queued") + ".json")).read_text())
        assert 1031 < record["deadline"] <= 1032
        await first
    asyncio.run(scenario())


def test_capability_validation_requires_owned_live_job(tmp_path, monkeypatch):
    from sap_business_agents_platform.runtime_policy import ORCHESTRATION_VERSION, orchestration_digest
    manager = WorkBuddyManager(tmp_path)
    binding = {"provider_id": "workbuddy", "environment_digest": "a" * 64}
    path = manager.environment.root / "jobs" / (digest("case") + ".json")
    job = {"status": "completed", "cleanup_complete": True, "operation": "review_workflow",
           "environment_digest": "a" * 64, "runtime_digest": digest(binding),
           "permission_mode": "bounded", "validation_job": True,
           "orchestration_version": ORCHESTRATION_VERSION, "orchestration_digest": orchestration_digest("review_workflow")}
    evidence = {"platform_contract_passed": True, "kind": "live_validation"}
    atomic_json(path, {**job, "validation_job": False})
    with pytest.raises(Exception, match="complete contract"):
        manager.record_operation_validation(binding, "review_workflow", job_id="case", permission_modes=["bounded"], evidence=evidence)
    atomic_json(path, job)
    with pytest.raises(Exception, match="complete contract"):
        manager.record_operation_validation(binding, "review_workflow", job_id="case", permission_modes=["trusted_local"], evidence=evidence)
    manager.record_operation_validation(binding, "review_workflow", job_id="case", permission_modes=["bounded"], evidence=evidence)
    assert manager.capabilities("a" * 64)["review_workflow"]["status"] == "validated"
    assert "plan" not in manager.capabilities("a" * 64)
    assert manager.capabilities("a" * 64)["author_draft"]["status"] == "unverified"


def test_model_refresh_and_probes_do_not_write_codex_configuration(tmp_path, monkeypatch):
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManager
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()}, selection_path=tmp_path / "default.json")
    asyncio.run(manager.check_provider("codex"))
    asyncio.run(manager.refresh_models("codex"))
    asyncio.run(manager.set_default_model("codex", "test-model"))
    before = manager.runtime_snapshot("codex")
    before.pop("selected_at", None)
    config = (tmp_path / "default.json").read_bytes()
    value = release(manager.workbuddy.environment)
    manager.workbuddy.environment.mutate(lambda state: state.update(environment_digest=value["environment_digest"],
        authenticated=True, authentication_environment=value["environment_digest"]))
    monkeypatch.setattr(manager.workbuddy.environment, "release", lambda *_: value)
    async def fake_probe(**kwargs):
        return {"text": '{"status":"ok"}', "actual_model": "concrete-model"}
    monkeypatch.setattr(manager.workbuddy.supervisor, "run", fake_probe)
    asyncio.run(manager.refresh_models("workbuddy"))
    asyncio.run(manager.check_model("workbuddy", "route"))
    asyncio.run(manager.set_default_model("workbuddy", "route"))
    manager.set_enabled("workbuddy", False)
    after = manager.runtime_snapshot("codex")
    after.pop("selected_at", None)
    assert before == after
    assert config == (tmp_path / "default.json").read_bytes()
    assert manager.default_provider_id == "codex"


def test_inherited_reservations_never_spawn_parallel_execution_workers(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    async def scenario():
        async with owner.reserve():
            tasks = [asyncio.create_task(owner.run(task_id=str(i), snapshot=binding, operation="review_workflow",
                    payload={"scenario": "delayed"}, seconds=2)) for i in range(2)]
            while not all(task.done() for task in tasks):
                assert len(owner.active) <= 1
                await asyncio.sleep(0.01)
            await asyncio.gather(*tasks)
    asyncio.run(scenario())


def test_added_environment_files_and_nested_manifest_are_rejected(tmp_path):
    environment = WorkBuddyEnvironment(tmp_path)
    data = {"python": "python.exe", "cli": "cli.exe", "worker": "worker.py",
            "files": {name: __import__('hashlib').sha256(b"safe").hexdigest() for name in ("python.exe", "cli.exe", "worker.py")}}
    key = digest(data)
    directory = environment.root / "releases" / key
    directory.mkdir(parents=True)
    for name in data["files"]:
        (directory / name).write_bytes(b"safe")
    atomic_json(directory / "environment.json", {**data, "environment_digest": key})
    assert environment.release(key)
    nested = directory / "injected"
    nested.mkdir()
    atomic_json(nested / "environment.json", {})
    with pytest.raises(WorkBuddyError, match="digest_mismatch"):
        environment.release(key)


@pytest.mark.parametrize("actual,known,code", [("other-model", True, "workbuddy_model_identity_changed"),
        (None, False, "workbuddy_model_identity_unknown")])
def test_acceptance_worker_must_report_frozen_model(tmp_path, monkeypatch, actual, known, code):
    owner, binding = supervisor(tmp_path, monkeypatch)
    binding.update(actual_model=actual, model_identity_known=known, model_identity_source="assistant_message")
    with pytest.raises(WorkBuddyError, match=code):
        asyncio.run(owner.run(task_id="formal", snapshot=binding, operation="acceptance_baseline", payload={}, seconds=2))
    assert not owner.active and not owner.pending()


def test_validation_scope_is_exact_and_does_not_escape_to_other_operations(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    def reject(*_, **__):
        raise WorkBuddyError("runtime_operation_unavailable")
    monkeypatch.setattr(owner.environment, "assert_operation", reject)
    with owner.verification(binding, {"sample_discovery"}):
        assert owner.check_operation(binding, "sample_discovery")
        for snapshot, operation in ((binding, "review_workflow"), ({**binding, "model": "changed"}, "sample_discovery")):
            with pytest.raises(WorkBuddyError, match="runtime_operation_unavailable"):
                owner.check_operation(snapshot, operation)
    with pytest.raises(WorkBuddyError, match="runtime_operation_unavailable"):
        owner.check_operation(binding, "sample_discovery")


def test_platform_history_excludes_secrets_sdk_ids_and_acceptance_answers():
    from types import SimpleNamespace
    from sap_business_agents_platform.workbuddy_harness import WorkBuddyHarnessController
    class Store:
        def get_run(self, run_id):
            return SimpleNamespace(query="current" if run_id == "current" else "Password=secret 采购订单")
        def get_free_query_session_by_run(self, run_id):
            return {"session_id": "session", "original_query": "采购订单"}
        def list_free_query_iterations(self, _):
            return [{"run_id": "previous", "iteration": 1, "feedback": "只查询工厂1010", "execution_action": "requery"},
                    {"run_id": "current", "iteration": 2}]
        def get_harness_state(self, _):
            return {"platform_clarification_question": "哪个采购组织？"}
    controller = WorkBuddyHarnessController(None, Store(), None, None)
    context = controller.conversation_context("current", baseline=False)
    assert context["pending_question"] == "哪个采购组织？"
    assert context["previous_user_requests"][0]["feedback"] == "只查询工厂1010"
    assert "Password=secret" not in json.dumps(context)
    assert controller.conversation_context("current", baseline=True) == {}


def test_workbuddy_baseline_returns_normalized_result_and_rejects_alias():
    from sap_business_agents_platform.acceptance import validate_direct_baseline, canonical_hash
    normalized = {"records": [], "metrics": {}, "source_complete": True, "evidence_complete": True,
                  "business_complete": True, "business_status": "normal", "limitations": [], "evidence_gap_codes": []}
    value = {"schema_version": "workbuddy-baseline/1", "runtime": "workbuddy_sdk_direct_sap",
             "candidate_access": False, "used_sap_business_agents": False,
             "runtime_snapshot": {"provider_id": "workbuddy", "model_identity_known": True,
                "model_identity_source": "assistant_message",
                "model": "concrete-model", "actual_model": "concrete-model", "reasoning_effort": None,
                "environment_digest": "a" * 64, "configuration_digest": "frozen", "sdk_fingerprint": "fingerprint"},
             "sources": [{"read_only": True, "evidence_ref": "run:evidence", "http_method": "GET"}],
             "normalized_result": normalized, "result_hash": canonical_hash(normalized)}
    assert validate_direct_baseline(value) == normalized
    value["runtime_snapshot"]["model"] = "route-alias"
    with pytest.raises(ValueError, match="concrete Runtime binding"):
        validate_direct_baseline(value)


def test_maintenance_lock_reentrant_locally_but_busy_for_other_manager(tmp_path):
    first = WorkBuddyEnvironment(tmp_path)
    second = WorkBuddyEnvironment(tmp_path)
    with first.maintenance():
        first.mutate(lambda state: state.update(default_model_id="model"))
        with pytest.raises(WorkBuddyError, match="maintenance_busy"):
            second.mutate(lambda state: state.update(default_model_id="other"))
    assert second.state()["default_model_id"] == "model"
    second.mutate(lambda state: state.update(default_model_id="next"))
    assert first.state()["default_model_id"] == "next"


def test_corrupt_workbuddy_state_cannot_break_codex_discovery_or_configuration(tmp_path):
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManager
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()}, selection_path=tmp_path / "default.json")
    asyncio.run(manager.check_provider("codex"))
    asyncio.run(manager.refresh_models("codex"))
    asyncio.run(manager.set_default_model("codex", "test-model"))
    before = manager.runtime_snapshot("codex")
    before.pop("selected_at", None)
    manager.workbuddy.environment.root.mkdir(parents=True, exist_ok=True)
    manager.workbuddy.environment.state_path.write_text("not JSON", encoding="utf-8")
    projection = manager.list()
    assert next(item for item in projection if item["provider_id"] == "workbuddy")["selectable"] is False
    after = manager.runtime_snapshot("codex")
    after.pop("selected_at", None)
    assert before == after


def test_workbuddy_harness_preflight_never_calls_tools_or_model():
    from types import SimpleNamespace
    from sap_business_agents_platform.workbuddy_harness import WorkBuddyHarnessController
    controller = WorkBuddyHarnessController(SimpleNamespace(max_harness_turns=2), None, None, None)
    ambiguous = {"turn_count": 1, "assessment_intent": {"intent": "ambiguous"}}
    assert controller.preflight(ambiguous).status == "waiting_input"
    assert controller.preflight(ambiguous).turn_count == 1
    assert controller.preflight({**ambiguous, "acceptance_spec": {"schemaVersion": "1"}}) is None
    exhausted = controller.preflight({"turn_count": 2})
    assert exhausted.stop_reason == "limit_reached" and exhausted.limit_kind == "turns"
    assert controller.preflight({"turn_count": 1}) is None


def test_workbuddy_report_errors_preserve_safe_contract_diagnostics():
    from sap_business_agents_platform.workbuddy_harness import parse_output
    from sap_business_agents_platform.harness import AcceptanceReportValidationError
    schema = {"type": "object", "properties": {"status": {"type": "string", "enum": ["ok"]}},
              "required": ["status"], "additionalProperties": False}
    assert parse_output('{"status":"ok"}', schema) == {"status": "ok"}
    with pytest.raises(AcceptanceReportValidationError) as failure:
        parse_output('{"status":"Password=secret"}', schema, formal=True)
    assert failure.value.code == "acceptance_report_validation_failed"
    assert failure.value.detail["validation_issues"][0]["path"] == "/status"
    assert "secret" not in json.dumps(failure.value.detail)
    with pytest.raises(WorkBuddyError) as failure:
        parse_output('Password=secret', schema)
    assert failure.value.detail["validation_issues"][0]["constraint"] == "json"
    assert "secret" not in str(failure.value)


def test_sample_instructions_use_bound_workbuddy_model_not_codex_defaults():
    from types import SimpleNamespace
    from sap_business_agents_platform.workbuddy_sample import sample_prompt
    context = SimpleNamespace(properties={"plant": {"type": "string", "default": "fixture", "examples": ["old"]}},
        manifest={"id": "test-agent"}, revision=2, required_fields=["plant"],
        missing_fields=lambda: ["plant"], supplied_inputs={}, plans=[], skill_steps=[])
    text = sample_prompt(context, {"model": "verified-concrete"})
    assert "verified-concrete" in text and '"revision": 2' in text
    assert '"fixture"' not in text and '"examples"' not in text and "gpt-5.6" not in text
    assert "540 seconds" in text and "60 seconds" in text and "10 data reads" in text


def test_cancelling_queued_worker_prevents_dispatch(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    async def scenario():
        async with owner.reserve():
            # A fresh context must queue rather than inherit the reservation.
            import contextvars
            pending = asyncio.create_task(owner.run(task_id="withdrawn", snapshot=binding,
                operation="review_workflow", payload={}, seconds=2), context=contextvars.Context())
            await asyncio.sleep(0)
            assert "withdrawn" in owner.queued and not owner.active
            assert await owner.cancel("withdrawn")
        with pytest.raises(WorkBuddyError, match="workbuddy_cancelled"):
            await pending
        assert not owner.active and not owner.queued
        assert not (owner.environment.root / "jobs" / (digest("withdrawn") + ".json")).exists()
    asyncio.run(scenario())


def test_cli_model_discovery_is_candidate_only_and_environment_bound(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    manager.environment.mutate(lambda state: state.update(environment_digest=value["environment_digest"]))
    monkeypatch.setattr(manager.environment, "release", lambda *_: value)
    async def discover(**kwargs):
        assert kwargs["operation"] == "models" and kwargs["probe"] is True
        return {"models": ["concrete-model", "default-model"], "source": "locked_cli_help", "model_catalog_complete": False}
    monkeypatch.setattr(manager.supervisor, "run", discover)
    models = asyncio.run(manager.refresh_models())
    assert models["source"] == "locked_cli_help" and models["model_catalog_complete"] is False
    assert [item["model_id"] for item in models["items"]] == ["concrete-model", "default-model"]
    assert not any(item["selectable"] for item in models["items"])
    manager.environment.mutate(lambda state: state.update(environment_digest="b" * 64))
    assert manager.models()["items"] == []
    assert manager.models()["source"] == "not_loaded"


@pytest.mark.parametrize("authenticated,auth_environment", [(False, "a" * 64), (True, "b" * 64)])
def test_model_probe_requires_current_environment_login_before_starting_worker(tmp_path, monkeypatch, authenticated, auth_environment):
    manager = WorkBuddyManager(tmp_path)
    value = release(manager.environment)
    monkeypatch.setattr(manager.environment, "release", lambda *_: value)
    manager.environment.mutate(lambda state: state.update(authenticated=authenticated,
        authentication_environment=auth_environment))
    async def forbidden(**_):
        pytest.fail("An unauthenticated or stale environment must not start a model probe")
    monkeypatch.setattr(manager.supervisor, "run", forbidden)
    with pytest.raises(Exception) as failure:
        asyncio.run(manager.check_model("concrete-model"))
    assert failure.value.code == "workbuddy_authentication_required"
    assert manager.environment.state()["models"] == {}


@pytest.mark.parametrize("patch", [
    {"models": []}, {"models": {"model": None}}, {"capabilities": []},
    {"capabilities": {"operation": "validated"}}, {"installation": []},
    {"model_discovery": []}, {"model_discovery": {"models": "model"}},
    {"model_discovery": {"models": [None]}}, {"enabled": "false"},
    {"authenticated": 1}, {"revision": True}, {"revision": -1},
])
def test_structurally_invalid_workbuddy_state_fails_closed(tmp_path, patch):
    manager = WorkBuddyManager(tmp_path)
    atomic_json(manager.environment.state_path, {"revision": 0, "enabled": False, **patch})
    with pytest.raises(WorkBuddyError, match="workbuddy_state_invalid"):
        manager.environment.state()
    assert manager.snapshot()["selectable"] is False


@pytest.mark.parametrize("manifest", [[], None, "invalid", {"files": []},
    {"files": {"python.exe": None}, "python": "python.exe", "cli": "python.exe", "worker": "python.exe"},
    {"files": {"python.exe": "a" * 64}, "python": [], "cli": "python.exe", "worker": "python.exe"},
    {"files": {"python.exe": "a" * 64}, "python": "python.exe", "cli": "python.exe"},
])
def test_structurally_invalid_release_cannot_break_runtime_listing(tmp_path, manifest):
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManager
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
        runtime_probes={"codex": FakeRuntimeProbe()}, selection_path=tmp_path / "default.json")
    asyncio.run(manager.check_provider("codex"))
    asyncio.run(manager.refresh_models("codex"))
    asyncio.run(manager.set_default_model("codex", "test-model"))
    before = manager.runtime_snapshot("codex")
    before.pop("selected_at", None)
    key = "a" * 64
    atomic_json(manager.workbuddy.environment.root / "releases" / key / "environment.json", manifest)
    manager.workbuddy.environment.mutate(lambda state: state.update(environment_digest=key))
    items = manager.list()
    workbuddy = next(item for item in items if item["provider_id"] == "workbuddy")
    assert workbuddy["installed"] is False and workbuddy["selectable"] is False
    assert "workbuddy_environment_invalid" in workbuddy["blockers"]
    after = manager.runtime_snapshot("codex")
    after.pop("selected_at", None)
    assert after == before


def test_release_access_denied_has_safe_distinct_diagnostic(tmp_path, monkeypatch):
    manager = WorkBuddyManager(tmp_path)
    manager.environment.mutate(lambda state: state.update(environment_digest="a" * 64))
    original = Path.read_text
    def deny(path, *args, **kwargs):
        if path.name == "environment.json":
            raise PermissionError("private-path-and-secret")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(WorkBuddyError) as failure:
        manager.environment.release()
    assert failure.value.code == "workbuddy_environment_access_denied"
    snapshot = manager.snapshot()
    assert "workbuddy_environment_access_denied" in snapshot["blockers"]
    assert "private-path-and-secret" not in json.dumps(snapshot)
    assert not snapshot["selectable"]


def test_release_embedded_identity_must_match_requested_digest(tmp_path):
    environment = WorkBuddyEnvironment(tmp_path)
    data = {"python": "python.exe", "cli": "cli.exe", "worker": "worker.py",
            "files": {name: __import__('hashlib').sha256(b"safe").hexdigest()
                      for name in ("python.exe", "cli.exe", "worker.py")}}
    key = digest(data)
    directory = environment.root / "releases" / key
    directory.mkdir(parents=True)
    for name in data["files"]:
        (directory / name).write_bytes(b"safe")
    atomic_json(directory / "environment.json", {**data, "environment_digest": "b" * 64})
    with pytest.raises(WorkBuddyError, match="workbuddy_environment_digest_mismatch"):
        environment.release(key)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell login helper")
def test_login_helper_is_valid_powershell_and_refuses_missing_registry_interpreter(tmp_path):
    import subprocess
    script = ROOT / "scripts/login-workbuddy-runtime.ps1"
    command = ("$parseTokens=$null; $parseErrors=$null; "
               "[System.Management.Automation.Language.Parser]::ParseFile($args[0], "
               "[ref]$parseTokens, [ref]$parseErrors) | Out-Null; "
               "if ($parseErrors.Count) { exit 1 }")
    # Supply a quoted literal: powershell.exe -Command does not populate $args
    # consistently when an extra positional argument follows the script string.
    command = command.replace("$args[0]", "'" + str(script).replace("'", "''") + "'")
    parsed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, timeout=15)
    assert parsed.returncode == 0
    copy_path = tmp_path / "scripts/login-workbuddy-runtime.ps1"
    copy_path.parent.mkdir()
    copy_path.write_bytes(script.read_bytes())
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-File", str(copy_path), "-VerifyOnly"], capture_output=True, timeout=15)
    assert result.returncode != 0
    assert b"Platform Python is needed only" in result.stderr
    assert not (tmp_path / ".local-data").exists()
