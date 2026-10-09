"""Role-final failure replay and owned cancellation, with no model or SAP."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.codex_planner import CodexPlanner
from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_diagnostics import checked_output, safe_stage_diagnostic
from sap_business_agents_platform.runtime_prompts import ROLE_MATCHING_OUTPUT_SCHEMA, _await_with_hard_timeout
from sap_business_agents_platform.runtime_role_contract import native_output_schema
from sap_business_agents_platform.runtime_role_consolidation import SCHEMA as FINAL_SCHEMA
from tests.runtime_trace_fixture import TraceClient, cases
from tests.test_workbuddy_isolation import supervisor


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("unsafe-native-timeout-message"), "runtime_deadline_exceeded"),
    (RuntimeError("password=must-not-be-logged"), "role_matching_consolidation_failed"),
    (RuntimeContractError("workbuddy_structured_output_missing"), "workbuddy_structured_output_missing"),
])
@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
def test_consolidation_failure_has_safe_stage_cause(monkeypatch, tmp_path, failure, code, provider):
    class Client(TraceClient):
        async def run(self, prompt, **kwargs):
            if prompt.startswith("Finalize"):
                raise failure
            return await super().run(prompt, **kwargs)
    if provider == "codex":
        planner = CodexPlanner(tmp_path, model="offline")
    else:
        planner = WorkBuddyPlanner(tmp_path, model="offline")
    planner._driver = SimpleNamespace(client=lambda **kwargs: Client([], "analyze_role_matching", **kwargs))
    result = asyncio.run(planner.analyze_role_matching(**cases()["analyze_role_matching"]))["analysis"]
    assert result["catalog_evaluation"]["agent_catalog_complete"]
    assert not result["catalog_evaluation"]["consolidation_complete"]
    assert result["workflow_suggestions"] == []
    diagnostic = result["runtime_diagnostics"][0]
    assert diagnostic["failure_code"] == code
    assert diagnostic["phase"] == "role_consolidation"
    assert diagnostic["budget_seconds"] == 300
    assert diagnostic["input_length"] > 0 and len(diagnostic["input_sha256"]) == 64
    assert "password" not in json.dumps(diagnostic) and "unsafe-native" not in json.dumps(diagnostic)


def test_empty_native_schema_failure_paths_are_unique_and_preserved(monkeypatch, tmp_path):
    schema = FINAL_SCHEMA
    class Client(TraceClient):
        async def run(self, prompt, **kwargs):
            if prompt.startswith("Finalize"):
                checked_output("{}", schema, operation="review_role_matching_feedback", output_format="native_json_schema")
            return await super().run(prompt, **kwargs)
    planner = CodexPlanner(tmp_path, model="offline")
    planner._driver = SimpleNamespace(client=lambda **kwargs: Client([], "review_role_matching_feedback", **kwargs))
    result = asyncio.run(planner.review_role_matching_feedback(**cases()["review_role_matching_feedback"]))["analysis"]
    detail = result["runtime_diagnostics"][0]
    assert detail["failure_category"] == "contract" and detail["output_length"] == 2
    assert len(detail["validation_issues"]) == 4
    assert {item["path"] for item in detail["validation_issues"]} == {"/workflow_suggestions", "/agent_gaps", "/summary_zh", "/summary_en"}


def test_stage_projection_rejects_raw_values_and_arbitrary_paths():
    detail = safe_stage_diagnostic({"failure_code": {"secret": "private"}, "message": "secret=value",
        "output_length": True, "output_sha256": "invalid-secret", "validation_issues": [
            {"code": "runtime_report_schema_invalid", "path": "/private-token", "constraint": "required", "value": "secret"},
            {"code": "runtime_report_schema_invalid", "path": "/summary_zh", "constraint": ["secret"]},
            {"code": "runtime_report_schema_invalid", "path": "/summary_en", "constraint": "required", "value": "secret"},
        ]}, schema=native_output_schema(ROLE_MATCHING_OUTPUT_SCHEMA), phase="role_consolidation")
    assert detail == {"failure_code": "role_matching_consolidation_failed", "phase": "role_consolidation",
        "failure_category": "runtime", "validation_issues": [
            {"code": "runtime_report_schema_invalid", "path": "/summary_en", "constraint": "required"}]}


def test_workbuddy_outer_deadline_waits_for_owned_job_record_cleanup(monkeypatch, tmp_path):
    owner, binding = supervisor(tmp_path, monkeypatch)
    original_run, original_cancel = owner.run, owner.cancel
    async def hanging_worker(**kwargs):
        kwargs["payload"]["scenario"] = "hang"
        return await original_run(**kwargs)
    async def delayed_cleanup(task_id):
        complete = await original_cancel(task_id)
        await asyncio.sleep(0.03)
        return complete
    monkeypatch.setattr(owner, "run", hanging_worker)
    monkeypatch.setattr(owner, "cancel", delayed_cleanup)
    planner = WorkBuddyPlanner(tmp_path, "offline", supervisor=owner, runtime_snapshot=binding)
    planner._operation.set("review_workflow")
    async def scenario():
        with pytest.raises(TimeoutError):
            async with planner._driver.client() as client:
                thread = await client.thread_start()
                await _await_with_hard_timeout(thread.run("offline", output_schema={"type": "object"}), timeout=1)
        records = [json.loads(path.read_text()) for path in (owner.environment.root / "jobs").glob("*.json")]
        assert len(records) == 1
        assert records[0]["cleanup_complete"] and records[0]["status"] != "running"
        assert not owner.active and not owner.pending() and not owner.queued
        assert planner._driver.sessions[thread.id]["history"] == []
    asyncio.run(scenario())


def test_role_revision_and_events_preserve_only_safe_diagnostics(tmp_path):
    from dataclasses import replace
    from pathlib import Path
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.manifests import AgentRepository
    from sap_business_agents_platform.role_matching import RoleMatchingService
    from tests.test_role_matching import _FakeRuntime, _Drafts
    class Runtime(_FakeRuntime):
        async def analyze_role_matching(self, **kwargs):
            result = await super().analyze_role_matching(**kwargs)
            result["analysis"]["runtime_diagnostics"] = [{
                "failure_code": "runtime_report_validation_failed", "message": "secret-password",
                "validation_issues": [{"code": "runtime_report_schema_invalid", "path": "/summary_zh",
                    "constraint": "required", "raw_value": "private-row"}], "output_length": 2}]
            return result
    root = Path(__file__).resolve().parents[1]
    settings = replace(Settings(), repository_root=root, data_root=tmp_path / "data", draft_root=tmp_path / "drafts")
    store = RunStore(settings.database_path)
    service = RoleMatchingService(settings, store, AgentRepository(root / "agents"), Runtime(), _Drafts())
    async def scenario():
        await service.start()
        try:
            session = await service.create(paths=[], role_description="测试只读采购岗位", locale="zh", consent=True)
            for _ in range(100):
                current = service.get(session["session_id"])
                if current["status"] in {"completed", "failed"}:
                    break
                await asyncio.sleep(0.01)
            assert current["status"] == "completed", current.get("error")
            result = service.revision(session["session_id"], 1)["result"]
            assert not result["completeness"]["workflow_validation_complete"]
            assert result["workflow_suggestions"] == []
            assert result["workflow_validation_issues"][0]["diagnostics"] == result["runtime_diagnostics"]
            events = store.role_matching_events_after(session["session_id"])
            failure = next(item for item in events if item["type"] == "role_matching_consolidation_failed")
            assert failure["data"]["diagnostics"][0]["validation_issues"][0]["path"] == "/summary_zh"
            assert "secret-password" not in json.dumps(result) and "private-row" not in json.dumps(events)
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_pending_cleanup_blocks_dispatch_without_touching_foreign_job(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    for name in ("owned", "foreign"):
        record = {"task_id": name, "status": "running", "cleanup_complete": False}
        path = owner.environment.root / "jobs" / (digest(name) + ".json")
        atomic_json(path, record)
        owner.active[name] = {"record": record, "record_path": path, "cancelled": False}
    owner.mark_cleanup_pending({"owned"})
    assert owner.active["owned"]["cancelled"]
    assert owner.active["foreign"]["record"]["status"] == "running"
    assert not owner.active["foreign"]["cancelled"]
    assert [item["task_id"] for item in owner.reconcile()] == ["owned"]
    with pytest.raises(WorkBuddyError, match="workbuddy_cleanup_pending"):
        asyncio.run(owner.run(task_id="retry", snapshot=binding, operation="review_workflow", payload={}, seconds=1))
    assert not owner.queued


def test_cancellation_during_cleanup_waits_for_persisted_outcome(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    original_cancel = owner.cancel
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed_cleanup(task_id):
            started.set()
            await release.wait()
            return await original_cancel(task_id)
        monkeypatch.setattr(owner, "cancel", delayed_cleanup)
        task = asyncio.create_task(owner.run(task_id="cleanup-race", snapshot=binding,
            operation="review_workflow", payload={}, seconds=5))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = next(json.loads(path.read_text()) for path in (owner.environment.root / "jobs").glob("*.json"))
        assert record["cleanup_complete"] and record["status"] == "cancelled"
        assert not owner.active and not owner.pending()
    asyncio.run(scenario())


def test_queued_job_rechecks_cleanup_at_dispatch(tmp_path, monkeypatch):
    import contextvars
    from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    record = {"task_id": "old", "status": "running", "cleanup_complete": False}
    path = owner.environment.root / "jobs" / (digest("old") + ".json")
    atomic_json(path, record)
    owner.active["old"] = {"record": record, "record_path": path, "cancelled": False}
    async def scenario():
        async with owner.reserve():
            next_job = asyncio.create_task(owner.run(task_id="next", snapshot=binding,
                operation="review_workflow", payload={}, seconds=5), context=contextvars.Context())
            await asyncio.sleep(0)
            assert "next" in owner.queued
            owner.mark_cleanup_pending({"old"})
        with pytest.raises(WorkBuddyError, match="workbuddy_cleanup_pending"):
            await next_job
        assert not (owner.environment.root / "jobs" / (digest("next") + ".json")).exists()
        assert not owner.queued
    asyncio.run(scenario())


def test_job_diagnosis_fingerprints_wire_input_without_saving_it(tmp_path, monkeypatch):
    import hashlib
    from sap_business_agents_platform.workbuddy_environment import digest
    owner, binding = supervisor(tmp_path, monkeypatch)
    schema = {"type": "object"}
    prompt = "password=private-wire-input"
    asyncio.run(owner.run(task_id="metadata", snapshot=binding, operation="review_workflow",
        payload={"prompt": prompt, "output_schema": schema}, seconds=5))
    record = next(json.loads(path.read_text()) for path in (owner.environment.root / "jobs").glob("*.json"))
    metadata = record["request_metadata"]
    assert 0 < metadata["budget_ms"] <= 5000
    assert {key: value for key, value in metadata.items() if key != "budget_ms"} == {
        "input_length": len(prompt.encode()), "input_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "schema_sha256": digest(schema)}
    assert "private-wire-input" not in json.dumps(record)


def test_supervisor_changes_invalidate_operation_binding(monkeypatch):
    from pathlib import Path
    from sap_business_agents_platform.runtime_policy import orchestration_digest
    before, original = orchestration_digest("review_role_matching_feedback"), Path.read_bytes
    def changed(path):
        value = original(path)
        return value + b"\n# changed owned cleanup\n" if path.name == "workbuddy_supervisor.py" else value
    monkeypatch.setattr(Path, "read_bytes", changed)
    assert orchestration_digest("review_role_matching_feedback") != before


def test_cancelled_client_waits_and_rejects_late_output(monkeypatch, tmp_path):
    planner = WorkBuddyPlanner(tmp_path, "offline")
    finished = []
    async def ignores_cancellation(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.03)
            finished.append(True)
            return {"answer": "late"}, "native-id"
    monkeypatch.setattr(planner, "_structured_turn", ignores_cancellation)
    async def scenario():
        with pytest.raises(TimeoutError):
            async with planner._driver.client() as client:
                thread = await client.thread_start()
                await _await_with_hard_timeout(thread.run("offline", output_schema={}), timeout=0.01)
        assert finished == [True]
        assert thread.state["history"] == []
        with pytest.raises(RuntimeContractError, match="runtime_cancelled"):
            await thread.run("another", output_schema={})
    asyncio.run(scenario())


def test_unconfirmed_cleanup_fences_only_owned_jobs(monkeypatch, tmp_path):
    import sap_business_agents_platform.workbuddy_driver as driver
    monkeypatch.setattr(driver, "_CLIENT_CLEANUP_SECONDS", 0.01)
    marked = []
    owner = SimpleNamespace(cleanup_deadlines={}, mark_cleanup_pending=lambda ids: marked.append(ids))
    planner = WorkBuddyPlanner(tmp_path, "offline", supervisor=owner)
    async def scenario():
        release = asyncio.Event()
        async def ignores_cancellation(*args, **kwargs):
            planner._driver.turn_owner.get()["jobs"].add("owned")
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await release.wait()
                return {"answer": "late"}, "native-id"
        monkeypatch.setattr(planner, "_structured_turn", ignores_cancellation)
        with pytest.raises(RuntimeContractError, match="runtime_cleanup_incomplete"):
            async with planner._driver.client() as client:
                thread = await client.thread_start()
                await _await_with_hard_timeout(thread.run("offline", output_schema={}), timeout=0.01)
        assert marked == [{"owned"}]
        tasks = list(client.turns)
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(item, RuntimeContractError) and item.code == "runtime_cancelled" for item in results)
        assert not thread.state["history"]
    asyncio.run(scenario())


def test_outer_business_cancel_also_drains_owned_turn(monkeypatch, tmp_path):
    planner = WorkBuddyPlanner(tmp_path, "offline")
    finished = []
    async def ignores_cancellation(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0.02)
            finished.append(True)
    monkeypatch.setattr(planner, "_structured_turn", ignores_cancellation)
    async def scenario():
        async def business():
            async with planner._driver.client() as client:
                thread = await client.thread_start()
                await _await_with_hard_timeout(thread.run("offline", output_schema={}), timeout=5)
        task = asyncio.create_task(business())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == [True]
    asyncio.run(scenario())


@pytest.mark.parametrize("expired_previous", [False, True])
def test_completed_turn_deadline_does_not_mask_current_stage_timeout(expired_previous):
    import time
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyClient
    async def scenario():
        ready, cleaned = asyncio.Event(), []
        deadlines = {"current": time.monotonic() + 1}
        if expired_previous:
            deadlines["completed"] = time.monotonic() - 5
        marked = []
        supervisor = SimpleNamespace(cleanup_deadlines=deadlines, pending=lambda: [],
            mark_cleanup_pending=lambda ids: marked.append(ids))
        client = WorkBuddyClient(SimpleNamespace(owner=SimpleNamespace(supervisor=supervisor)))
        async def current_turn():
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(.02)
                cleaned.append(True)
        task = asyncio.create_task(current_turn())
        await ready.wait()
        client.turns[task] = {"jobs": {"current"}}
        if expired_previous:
            client.jobs.add("completed")
        original_timeout = TimeoutError("stage timeout")
        assert not await client.__aexit__(TimeoutError, original_timeout, None)
        assert cleaned == [True] and task.done() and marked == []
        if expired_previous:
            assert deadlines["completed"] < time.monotonic()
    asyncio.run(scenario())


def test_expired_current_cleanup_deadline_is_not_renewed_or_applied_to_closed_job():
    import time
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyClient
    async def scenario():
        ready, release = asyncio.Event(), asyncio.Event()
        marked = []
        supervisor = SimpleNamespace(cleanup_deadlines={"current": time.monotonic() - 1}, pending=lambda: [],
            mark_cleanup_pending=lambda ids: marked.append(ids))
        client = WorkBuddyClient(SimpleNamespace(owner=SimpleNamespace(supervisor=supervisor)))
        client.jobs.add("completed")
        async def current_turn():
            ready.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        task = asyncio.create_task(current_turn())
        await ready.wait()
        client.turns[task] = {"jobs": {"current"}}
        with pytest.raises(RuntimeContractError, match="runtime_cleanup_incomplete"):
            await client.__aexit__(TimeoutError, TimeoutError(), None)
        assert marked == [{"current"}] and not task.done()
        release.set()
        await task
    asyncio.run(scenario())
