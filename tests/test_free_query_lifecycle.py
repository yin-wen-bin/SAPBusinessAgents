"""Offline UI lifetime, owned cancellation, bounded scheduling and SAP GETs."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.database import RunStore
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.models import RunCreate, RunStatus
from sap_business_agents_platform.runtime_capacity import RuntimeCapacity
from sap_business_agents_platform.scheduler import LocalRunScheduler, WorkloadClass

TOKEN = 'a' * 32


class Broker:
    def __init__(self):
        self.closed = set()
        self._tool_tasks = {}

    def close_session(self, run_id):
        self.closed.add(run_id)

    async def cancel_tools(self, run_id, timeout=10):
        self.close_session(run_id)
        return True


def coordinator(tmp_path, workers=10):
    value = RunCoordinator.__new__(RunCoordinator)
    value.store = RunStore(tmp_path / 'offline.sqlite3')
    value.settings = Settings(data_root=tmp_path)
    value.harness = SimpleNamespace(broker=Broker())
    value.planner = SimpleNamespace()
    value._active_execution_tasks = {}
    value._cancellation_tasks = {}
    value._active_feedback_tasks = {}
    value._feedback_cancel_tasks = {}
    value._free_query_session_locks = {}
    value._acceptance_runs = set()
    value.scheduler = LocalRunScheduler(value.store, {WorkloadClass.free_query: value._execute_scheduled_run},
        worker_counts={WorkloadClass.free_query: workers})
    return value


def new_run(owner, name):
    owner.store.create_run(name, RunCreate(mode='free_query', query='Synthetic offline only'))


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


def test_leases_forbidden_on_campaign_or_fixed_execution():
    with pytest.raises(ValueError):
        RunCreate(mode='agent', agentId='example', pageLeaseId=TOKEN)
    with pytest.raises(ValueError):
        RunCreate(mode='free_query', query='q', pageLeaseId='short')


@pytest.mark.parametrize('lease_state', ['current', 'expired'])
def test_old_page_metadata_cannot_cancel_queued_or_running_work(tmp_path, lease_state):
    owner = coordinator(tmp_path)
    new_run(owner, 'old-page')
    metadata = {'state': lease_state, 'last_seen_at': 0, 'expires_at': 120, 'monitor_feedback': True}
    owner.store.update_harness_state('old-page', {'query_lifecycle': metadata})
    entered, release = None, None
    async def execute(run):
        owner.store.update_run(run, status=RunStatus.running)
        entered.set()
        await release.wait()
        owner.store.update_run(run, status=RunStatus.completed)
    owner._execute = execute
    async def scenario():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        await owner._schedule_run('old-page', owner.store.get_run('old-page').mode)
        assert not owner.store.get_run('old-page').cancel_requested
        await owner.start()
        try:
            await until(entered.is_set)
            assert owner.store.get_run('old-page').status == RunStatus.running
            assert not owner.store.get_run('old-page').cancel_requested
            assert owner.store.get_harness_state('old-page')['query_lifecycle'] == metadata
            assert not owner.harness.broker.closed
            release.set()
            await until(lambda: owner.store.get_run('old-page').status == RunStatus.completed)
        finally:
            release.set()
            await owner.stop()
    asyncio.run(scenario())


def test_task_timeout_still_bounds_unobserved_work(tmp_path):
    owner = coordinator(tmp_path)
    owner.settings = Settings(data_root=tmp_path, max_run_seconds=1)
    new_run(owner, 'timeout')
    stopped = []
    async def execute(run):
        owner.store.update_run(run, status=RunStatus.running)
        try:
            await asyncio.Future()
        finally:
            stopped.append(run)
    owner._execute = execute
    asyncio.run(owner._execute_scheduled_run('timeout'))
    record = owner.store.get_run('timeout')
    assert record.status == RunStatus.inconclusive
    assert record.error['code'] == 'run_timeout'
    assert stopped == ['timeout']


def test_ten_queries_parallel_eleventh_queued_cancel_one_preserves_peers(tmp_path):
    owner = coordinator(tmp_path)
    entered, active = [], set()
    release = None
    async def execute(run):
        entered.append(run)
        active.add(run)
        owner.store.update_run(run, status=RunStatus.running)
        try:
            await release.wait()
            if not owner.store.get_run(run).cancel_requested:
                owner.store.update_run(run, status=RunStatus.completed)
        finally:
            active.discard(run)
    owner._execute = execute
    async def scenario():
        nonlocal release
        release = asyncio.Event()
        for n in range(11):
            new_run(owner, f'r{n}')
            await owner._schedule_run(f'r{n}', owner.store.get_run(f'r{n}').mode)
        await owner.start()
        try:
            await until(lambda: len(active) == 10)
            assert 'r10' not in entered
            # Cancellation is durable and idempotent; it never touches r1..r9.
            await asyncio.gather(owner.cancel('r0'), owner.cancel('r0'))
            assert owner.store.get_run('r0').status == RunStatus.cancelled
            await until(lambda: 'r10' in entered)
            assert len(active) == 10
            assert owner.harness.broker.closed == {'r0'}
            assert all(owner.store.get_run(f'r{n}').status == RunStatus.running for n in range(1, 11))
            events = owner.store.events_after('r0')
            assert sum(e.type == 'cancellation_requested' for e in events) == 1
            assert sum(e.type == 'run_cancelled' for e in events) == 1
        finally:
            release.set()
            await until(lambda: not active)
            await owner.stop()
    asyncio.run(scenario())


def test_cancel_queued_does_not_dispatch_or_own_native_worker(tmp_path):
    owner = coordinator(tmp_path)
    new_run(owner, 'never-start')
    async def scenario():
        job = await owner._schedule_run('never-start', owner.store.get_run('never-start').mode)
        await owner.cancel('never-start')
        assert owner.store.get_execution_job(job['job_id'])['status'] == 'cancelled'
        assert owner.store.get_run('never-start').status == RunStatus.cancelled
        assert not owner._active_execution_tasks
    asyncio.run(scenario())


def test_sdk_query_capacity_ten_then_exclusive_and_owner_isolation():
    from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner = WorkBuddySupervisor(SimpleNamespace())
    async def scenario():
        entered = set()
        release = asyncio.Event()
        async def query(index):
            async with owner.reserve(task_id=f'q{index}', parallel=True):
                async with owner._capacity(False, f'q{index}', 'free_query'):
                    entered.add(index)
                    await release.wait()
        jobs = [asyncio.create_task(query(n)) for n in range(11)]
        await until(lambda: len(entered) == 10)
        assert 10 not in entered
        jobs[0].cancel()
        await asyncio.gather(jobs[0], return_exceptions=True)
        await until(lambda: 10 in entered)
        release.set()
        await asyncio.gather(*jobs, return_exceptions=True)
        async with owner.reserve(task_id='real-owner', parallel=True):
            with pytest.raises(WorkBuddyError, match='owner_mismatch'):
                async with owner._capacity(False, 'wrong-child', 'free_query'):
                    pytest.fail('Inherited reservation admitted another query')
        async with owner.reserve():
            blocked = asyncio.create_task(query(20))
            await asyncio.sleep(0)
            assert 20 not in entered
        await blocked
    asyncio.run(scenario())


def test_exclusive_waiter_prevents_query_starvation():
    capacity = RuntimeCapacity(10)
    order = []
    async def scenario():
        async def exclusive():
            async with capacity.acquire(shared=False):
                order.append('exclusive')
        async def query():
            async with capacity.acquire(shared=True):
                order.append('query')
        async with capacity.acquire(shared=True):
            writer = asyncio.create_task(exclusive())
            await asyncio.sleep(0)
            reader = asyncio.create_task(query())
            await asyncio.sleep(0)
            assert not order
        await asyncio.gather(writer, reader)
    asyncio.run(scenario())
    assert order == ['exclusive', 'query']


def test_ten_sap_get_slots_shared_and_cancelled_waiter_never_gets_sent():
    from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider
    active, peak, started = 0, 0, []
    release = None
    async def handler(request):
        nonlocal active, peak
        started.append(request.url.path)
        active += 1
        peak = max(peak, active)
        try:
            await release.wait()
            return httpx.Response(200, json={'value': []})
        finally:
            active -= 1
    provider = EmbeddedODataProvider(base_url='https://offline.invalid', username='fake', password='fake',
        max_concurrent_requests=10, transport=httpx.MockTransport(handler))
    async def scenario():
        nonlocal release
        release = asyncio.Event()
        tasks = [asyncio.create_task(provider._request(f'/fake-{n}', params=None)) for n in range(12)]
        await until(lambda: active == 10)
        tasks[11].cancel()
        await asyncio.gather(tasks[11], return_exceptions=True)
        assert len(started) == 10
        release.set()
        await asyncio.gather(*tasks[:11])
    asyncio.run(scenario())
    assert peak == 10 and '/fake-11' not in started


def test_defaults_ten_not_ten_per_query():
    assert Settings().local_free_query_workers == 10
    assert Settings().max_concurrent_sap_gets == 10


def test_cleanup_unconfirmed_revokes_immediately_and_never_reports_cancelled(tmp_path):
    owner = coordinator(tmp_path)
    new_run(owner, 'uncertain')
    async def unconfirmed(*args, **kwargs):
        return False
    owner.harness.broker.cancel_tools = unconfirmed
    async def scenario():
        await owner.cancel('uncertain')
        assert 'uncertain' in owner.harness.broker.closed
        assert owner.store.get_run('uncertain').cancel_requested
        assert owner.store.get_run('uncertain').status != RunStatus.cancelled
        state = owner.store.get_harness_state('uncertain')
        assert state['cancellation']['status'] == 'cleanup_pending'
        assert state['cleanup_incomplete']
        # Neither late completion nor an error can overwrite revoked work.
        owner._complete_result('uncertain', SimpleNamespace())
        owner._finish_error('uncertain', RuntimeError('late'), RunStatus.failed)
        assert owner.store.get_run('uncertain').status != RunStatus.completed
        await owner.cancel('uncertain')
        assert len(owner.store.events_after('uncertain')) == 2
    asyncio.run(scenario())


def test_caller_disconnect_cannot_abandon_owned_cancellation(tmp_path):
    owner = coordinator(tmp_path)
    new_run(owner, 'disconnect')
    async def scenario():
        release = asyncio.Event()
        async def cleanup(*args, **kwargs):
            await release.wait()
            return True
        owner.harness.broker.cancel_tools = cleanup
        caller = asyncio.create_task(owner.cancel('disconnect'))
        await until(lambda: 'disconnect' in owner.harness.broker.closed)
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert not owner._cancellation_tasks['disconnect'].done()
        release.set()
        await owner._cancellation_tasks['disconnect']
        assert owner.store.get_run('disconnect').status == RunStatus.cancelled
    asyncio.run(scenario())


@pytest.mark.parametrize('feedback_type', ['scope_or_filter', 'presentation'])
def test_feedback_ignores_legacy_lease_and_history_reads_never_reexecute(tmp_path, feedback_type):
    from fastapi.testclient import TestClient
    from tests.test_platform_runtime import create_app, _settings, FeedbackPlanner, FakeEmbeddedProvider, _wait, _wait_feedback
    planner, embedded = FeedbackPlanner(), FakeEmbeddedProvider()
    app = create_app(_settings(tmp_path), planner=planner, embedded_provider=embedded)
    with TestClient(app) as client:
        first = client.post('/api/runs', json={'mode': 'free_query', 'query': 'Synthetic query', 'pageLeaseId': TOKEN}).json()
        _wait(client, first['run_id'])
        metadata = {'state': 'expired', 'last_seen_at': 0, 'expires_at': 120, 'token_sha256': 'obsolete'}
        app.state.store.update_harness_state(first['run_id'], {'query_lifecycle': metadata})
        reviewed = client.post(f"/api/free-query-sessions/{first['session_id']}/feedback", json={
            'baseIteration': 1, 'feedback': 'Synthetic follow-up', 'feedbackTypeHint': feedback_type,
            'locale': 'en', 'pageLeaseId': 'b' * 32})
        assert reviewed.status_code == 202, reviewed.text
        review = _wait_feedback(client, first['session_id'], reviewed.json()['feedback_request_id'])
        assert review['status'] == 'iteration_created', review
        final = _wait(client, review['run_id'])
        assert final['status'] == 'completed', final.get('error')
        assert 'query_lifecycle' not in app.state.store.get_harness_state(review['run_id'])
        assert app.state.store.get_harness_state(first['run_id'])['query_lifecycle'] == metadata
        calls, plans, runs = planner.calls, len(embedded.executed_plans), len(app.state.store.list_runs())
        for _ in range(3):
            assert client.get(f"/api/runs/{review['run_id']}").json()['result'] == final['result']
            assert client.get(f"/api/free-query-sessions/{first['session_id']}").status_code == 200
        assert (planner.calls, len(embedded.executed_plans), len(app.state.store.list_runs())) == (calls, plans, runs)


def test_same_session_runs_remain_serial(tmp_path):
    owner = coordinator(tmp_path)
    new_run(owner, 'first')
    owner.store.create_free_query_session(session_id='same', run_id='first', original_query='offline', runtime={})
    new_run(owner, 'second')
    owner.store.create_free_query_iteration(session_id='same', iteration=2, run_id='second', parent_iteration=1,
        feedback='offline', feedback_type='presentation', execution_action='reinterpret', decision={})
    entered = []
    async def scenario():
        release = asyncio.Event()
        async def execute(run):
            entered.append(run)
            if run == 'first':
                await release.wait()
            owner.store.update_run(run, status=RunStatus.completed)
        owner._execute = execute
        for run in ('first', 'second'):
            await owner._schedule_run(run, owner.store.get_run(run).mode)
        await owner.start()
        try:
            await until(lambda: bool(entered))
            await asyncio.sleep(0)
            assert entered == ['first']
            release.set()
            await until(lambda: len(entered) == 2)
        finally:
            release.set()
            await owner.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('indexed', [False, True])
def test_ten_native_fixture_workers_cancel_one_without_touching_others(tmp_path, monkeypatch, indexed):
    from tests.test_workbuddy_isolation import supervisor
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner, binding = supervisor(tmp_path, monkeypatch)
    if indexed:
        import time
        from sap_business_agents_platform.workbuddy_job_index import rebuild
        rebuild(owner.environment, clearance=lambda: dict(all_writers_checked=True, old_writers=[], active_jobs=[],
            cleanup_pending=[], checked_at=time.time()))
    async def scenario():
        tasks = [asyncio.create_task(owner.run(task_id=f'owned-{n}', snapshot=binding,
            operation='free_query', payload={'scenario': 'hang'}, seconds=30)) for n in range(11)]
        try:
            async with asyncio.timeout(15):
                while len(owner.active) != 10:
                    await asyncio.sleep(.01)
            peers = {key: item['process'].pid for key, item in owner.active.items() if key != 'owned-0'}
            assert 'owned-10' not in owner.active
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            async with asyncio.timeout(15):
                while 'owned-10' not in owner.active:
                    await asyncio.sleep(.01)
            assert all(owner.active[key]['process'].pid == pid for key, pid in peers.items())
            assert len(owner.active) == 10
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            assert not owner.active and not owner.pending()
    asyncio.run(scenario())


def test_known_spawn_window_is_not_mistaken_for_unknown_cleanup(tmp_path, monkeypatch):
    import time
    from sap_business_agents_platform.workbuddy_job_index import rebuild
    from tests.test_workbuddy_isolation import supervisor
    owner, binding = supervisor(tmp_path, monkeypatch)
    rebuild(owner.environment, clearance=lambda: dict(all_writers_checked=True, old_writers=[], active_jobs=[],
        cleanup_pending=[], checked_at=time.time()))
    original = asyncio.create_subprocess_exec
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_spawn(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', slow_spawn)
        first = asyncio.create_task(owner.run(task_id='first', snapshot=binding, operation='free_query', payload={}, seconds=10))
        second = None
        try:
            await entered.wait()
            assert not owner.pending()
            assert not await asyncio.to_thread(owner.reconcile)
            second = asyncio.create_task(owner.run(task_id='second', snapshot=binding, operation='free_query', payload={}, seconds=10))
            await asyncio.sleep(.02)
            assert not second.done()
            release.set()
            results = await asyncio.gather(first, second)
            assert all(result['text'] == 'ok' for result in results)
        finally:
            release.set()
            for task in (first, second):
                if task and not task.done(): task.cancel()
            await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)
            assert not owner.active and not owner.pending()
    asyncio.run(scenario())


def test_legacy_heartbeat_is_read_only_and_manual_cancel_still_works(tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_platform_runtime import create_app, _settings, FakePlanner, FakeEmbeddedProvider
    app = create_app(_settings(tmp_path), planner=FakePlanner(), embedded_provider=FakeEmbeddedProvider())
    owner = app.state.coordinator
    async def execute(run):
        owner.store.update_run(run, status=RunStatus.running)
        await asyncio.Future()
    owner._execute = execute
    with TestClient(app) as client:
        response = client.post('/api/runs', json={'mode': 'free_query', 'query': 'Synthetic only', 'pageLeaseId': TOKEN})
        assert response.status_code == 202
        run = response.json()['run_id']
        before = owner.store.get_harness_state(run)
        for action in ['renew', 'depart']:
            assert client.put(f'/api/runs/{run}/view-lease', json={'pageLeaseId': 'b' * 32, 'action': action}).json() == {'enabled': False}
        assert owner.store.get_harness_state(run) == before
        assert 'query_lifecycle' not in before
        assert client.put('/api/runs/missing/view-lease', json={'pageLeaseId': TOKEN}).status_code == 404
        body = client.get(f'/api/runs/{run}').json()
        assert body['page_lease'] == {'enabled': False} and TOKEN not in str(body)
        assert client.post(f'/api/runs/{run}/cancel').status_code == 202
        body = client.get(f'/api/runs/{run}').json()
        assert body['status'] == 'cancelled'
        assert body['cancellation']['status'] == 'complete'
        assert client.put(f'/api/runs/{run}/view-lease', json={'pageLeaseId': TOKEN}).json() == {'enabled': False}
        assert client.get(f'/api/runs/{run}').json()['status'] == 'cancelled'
        assert client.post(f'/api/runs/{run}/cancel').status_code == 202
