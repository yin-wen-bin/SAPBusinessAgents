"""Offline admission: selected-provider gates, responsive loop, no native/SAP calls."""
import asyncio
import contextvars
import threading
import time
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator, RunExecutionError
from sap_business_agents_platform.models import RunCreate
from sap_business_agents_platform.runtime import RuntimeRouter, RuntimeUnavailableError
from sap_business_agents_platform.sdk_manager import SDKManager


def test_http_health_and_existing_run_reads_remain_responsive_during_admission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from tests.test_platform_runtime import create_app, _settings, FakePlanner, FakeEmbeddedProvider
    entered, release = threading.Event(), threading.Event()
    planner = FakePlanner()
    def snapshot():
        entered.set()
        assert release.wait(3)
        error = RuntimeError("Offline admission rejection")
        error.code = "runtime_not_selectable"
        raise error
    planner.snapshot = snapshot
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=FakeEmbeddedProvider())
    # Inject at the actual router boundary; StaticRuntimeRouter intentionally
    # does not call an injected planner's snapshot method.
    app.state.coordinator.planner.snapshot = snapshot
    app.state.store.create_run("existing-offline", RunCreate(mode="free_query", query="Saved offline record"))
    app.state.store.update_run("existing-offline", status="cancelled")
    with TestClient(app) as client, ThreadPoolExecutor(1) as pool:
        future = pool.submit(client.post, "/api/runs", json={"mode": "free_query", "query": "Offline only"})
        try:
            assert entered.wait(2)
            started = time.monotonic()
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/runs/existing-offline").status_code == 200
            assert time.monotonic() - started < 1
        finally: release.set()
        response = future.result(3)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "runtime_not_selectable"
        assert [run.run_id for run in app.state.store.list_runs()] == ["existing-offline"]


@pytest.mark.parametrize("provider", ["codex", "workbuddy"])
@pytest.mark.parametrize("allowed", [True, False])
def test_router_checks_selected_provider_without_list_or_display_cache(tmp_path, provider, allowed):
    # Reuse the existing deterministic SDK adapters while keeping real manager
    # initialization and prepare_runtime_snapshot's consistency checks active.
    from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
    from sap_business_agents_platform.sdk_manager import SDKManagerError
    registry = tmp_path / "sdks.json"
    _write_runtime_registry(registry)
    manager = SDKManager(registry, tmp_path, adapters={"python": FakeAdapter()},
                         runtime_probes={"codex": FakeRuntimeProbe()})
    calls = []
    definition = manager._get_provider(provider)
    manager._get_provider = lambda name: definition if name == provider else pytest.fail("Other provider accessed")
    def full_gate(value, **options):
        assert options == ({"admission": True} if value.provider_id == "workbuddy" else {})
        calls.append(value.provider_id)
        return {"selectable": allowed, "blockers": [] if allowed else ["authentication_not_checked"]}
    manager._snapshot = full_gate
    manager.list = lambda: pytest.fail("Cross-provider traversal")
    def runtime_snapshot(selected):
        # The Codex atomic snapshot owns its full gate. RuntimeRouter already
        # performed the WorkBuddy gate before asking for its bound snapshot.
        if selected == "codex":
            gate = manager.admission_status(selected)
            if not gate["selectable"]:
                raise SDKManagerError("Offline admission rejection", code="runtime_not_selectable",
                                      detail={"blockers": gate["blockers"]})
        return {"provider_id": selected, "model": "unchanged",
                "reasoning_effort": "max" if selected == "codex" else None,
                "version": "1.0.0", "cli_version": "1.0.0",
                "configuration_digest": "unchanged", "runtime_configuration_revision": 1}
    manager.runtime_snapshot = runtime_snapshot
    manager.authentication_service = None
    router = RuntimeRouter(manager, {provider: object()})
    if allowed:
        result = router.snapshot(provider)
        assert result["model"] == "unchanged"
        assert result["reasoning_effort"] == ("max" if provider == "codex" else None)
    else:
        with pytest.raises(RuntimeUnavailableError, match="authentication_not_checked"):
            router.snapshot(provider)
    assert calls == [provider]


@pytest.mark.parametrize("allowed", [True, False])
def test_submit_waits_for_full_admission_off_loop_before_persisting_or_dispatching(tmp_path, allowed):
    store = RunStore(tmp_path / "offline.sqlite3")
    coordinator = RunCoordinator.__new__(RunCoordinator)
    coordinator.store = store
    entered, release = threading.Event(), threading.Event()
    context = contextvars.ContextVar("offline_frozen_context")
    scheduled = []
    loop_thread = threading.get_ident()
    def snapshot():
        assert threading.get_ident() != loop_thread
        assert context.get() == "frozen"
        entered.set()
        assert release.wait(3)
        if not allowed:
            error = RuntimeError("Full gate denied")
            error.code = "runtime_not_selectable"
            raise error
        return {"provider_id": "codex", "sdk_id": "codex-python-sdk", "model": "unchanged",
            "reasoning_effort": "max", "configuration_digest": "offline", "capabilities": []}
    coordinator.planner = SimpleNamespace(snapshot=snapshot)
    async def schedule(run_id, mode):
        scheduled.append(run_id)
    coordinator._schedule_run = schedule
    async def scenario():
        token = context.set("frozen")
        try:
            task = asyncio.create_task(coordinator.submit(RunCreate(mode="free_query", query="Offline only")))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set(), "Admission blocked the event loop"
            assert not task.done() and not store.list_runs() and not scheduled
            release.set()
            if allowed:
                run_id = await task
                assert scheduled == [run_id]
                assert store.get_run(run_id).runtime.reasoning_effort == "max"
            else:
                with pytest.raises(RunExecutionError) as caught:
                    await task
                assert caught.value.code == "runtime_not_selectable"
                assert not store.list_runs() and not scheduled
        finally:
            release.set()
            context.reset(token)
    asyncio.run(scenario())


@pytest.mark.parametrize("change,code", [("cancel", "workbuddy_cancelled"),
    ("site", "workbuddy_service_site_changed"), ("auth", "runtime_authentication_changed"),
    ("deadline", "workbuddy_deadline_exceeded")])
def test_dispatch_rechecks_after_slow_admission_without_spawning(tmp_path, monkeypatch, change, code):
    from contextlib import nullcontext
    from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
    from sap_business_agents_platform.workbuddy_site import make_configuration
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    current = {**make_configuration("china", 1), "authentication_revision": 4}
    snapshot = {**current}
    entered, release = threading.Event(), threading.Event()
    supervisor = WorkBuddySupervisor(SimpleNamespace(root=tmp_path, state=lambda: dict(current), maintenance=nullcontext))
    def gate(*args):
        entered.set()
        assert release.wait(3)
        return {"service_site_contract": "1"}
    supervisor.check_operation = gate
    async def no_process(*args, **kwargs): pytest.fail("Stale admission dispatched a worker")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    async def scenario():
        task = asyncio.create_task(supervisor.run(task_id="offline", snapshot=snapshot,
            operation="free_query", payload={}, seconds=.001 if change == "deadline" else 20))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            assert entered.is_set()
            if change == "cancel": assert await supervisor.cancel("offline")
            elif change == "site": current.update(make_configuration("international", 2))
            elif change == "auth": current["authentication_revision"] = 5
        finally: release.set()
        with pytest.raises(WorkBuddyError) as caught: await task
        assert caught.value.code == code
        assert not supervisor.active
    asyncio.run(scenario())


def test_cancelling_admission_does_not_create_a_late_run(tmp_path):
    coordinator = RunCoordinator.__new__(RunCoordinator)
    coordinator.store = RunStore(tmp_path / "cancelled.sqlite3")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def snapshot():
        entered.set()
        release.wait(3)
        finished.set()
        return {}
    coordinator.planner = SimpleNamespace(snapshot=snapshot)
    async def scenario():
        task = asyncio.create_task(coordinator.submit(RunCreate(mode="free_query", query="Offline only")))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        assert not coordinator.store.list_runs()
    asyncio.run(scenario())


def test_worker_dispatch_gate_preserves_context_lock_thread_and_denial(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
    from sap_business_agents_platform.workbuddy_site import make_configuration
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    current = make_configuration("china", 1)
    entered, release = threading.Event(), threading.Event()
    lock_threads = []
    @contextmanager
    def maintenance():
        lock_threads.append(threading.get_ident())
        try: yield
        finally: lock_threads.append(threading.get_ident())
    environment = SimpleNamespace(root=tmp_path, state=lambda: current, maintenance=maintenance)
    supervisor = WorkBuddySupervisor(environment)
    def full_gate(*args):
        assert supervisor._verification.get() == "offline-context"
        entered.set()
        assert release.wait(3)
        raise WorkBuddyError("runtime_operation_unavailable")
    supervisor.check_operation = full_gate
    async def no_process(*args, **kwargs): pytest.fail("Denied task started a process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_process)
    async def scenario():
        supervisor._verification.set("offline-context")
        task = asyncio.create_task(supervisor.run(task_id="offline", snapshot=current,
            operation="free_query", payload={}, seconds=20))
        try:
            for _ in range(100):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            assert entered.is_set(), "Worker admission blocked the event loop"
            assert not supervisor.active and not task.done()
        finally: release.set()
        with pytest.raises(WorkBuddyError) as caught: await task
        assert caught.value.code == "runtime_operation_unavailable"
        assert lock_threads[0] == lock_threads[-1] != threading.get_ident()
    asyncio.run(scenario())
