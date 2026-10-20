"""Offline admission tests. Never spawn an official CLI or access SAP."""
import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_admission import RuntimeAdmissionRequest, prepare
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest, atomic_json
from sap_business_agents_platform import workbuddy_job_index as index
from sap_business_agents_platform import workbuddy_integration as integration
from tests.test_workbuddy_three_layers import fixture


def clearance():
    return dict(all_writers_checked=True, old_writers=[], active_jobs=[], cleanup_pending=[], checked_at=time.time())


def test_one_boundary_installs_once_and_targets_operation(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch, {'free_query': ['bounded'], 'review_workflow': ['bounded']})
    monkeypatch.setattr('platform.system', lambda: 'Windows')
    manager.set_enabled(True)
    manager.snapshot = lambda **_: pytest.fail('full status queried')
    manager.models = lambda **_: pytest.fail('model catalog queried')
    manager.business_evaluation = lambda *_: pytest.fail('quality history queried')
    reads = []
    original = Path.read_bytes
    def read(path):
        reads.append(path.name)
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    snapshot = manager.prepare_admission(RuntimeAdmissionRequest(operation='free_query')).snapshot
    assert snapshot['model'] == 'new-model'
    assert 'review_workflowbounded.json' not in reads
    assert reads.count('free_querybounded.json') == 2  # Actual read + end fence.
    assert not manager.environment.state().get('qualification_records')


@pytest.mark.parametrize('change,expected', [
    ('auth', 'workbuddy_authentication_required'), ('enabled', 'runtime_disabled'),
    ('model', 'runtime_model_check_required'), ('operation', 'workbuddy_sdk_operation_not_certified'),
    ('platform', 'platform_not_supported'), ('deadline', 'runtime_deadline_exceeded')])
def test_targeted_path_keeps_hard_gates(tmp_path, monkeypatch, change, expected):
    manager, _ = fixture(tmp_path, monkeypatch, {'free_query': ['bounded']})
    monkeypatch.setattr('platform.system', lambda: 'Linux' if change == 'platform' else 'Windows')
    manager.environment.mutate(lambda s: s.update(enabled=change != 'enabled', authenticated=change != 'auth'))
    if change == 'model':
        manager.environment.mutate(lambda s: s['models']['new-model'].update(compatible=False))
    request = RuntimeAdmissionRequest(operation='author_draft' if change == 'operation' else 'free_query',
                                      deadline=0 if change == 'deadline' else None)
    with pytest.raises(Exception) as error:
        manager.prepare_admission(request)
    assert error.value.code == expected


def test_targeted_proof_rejects_own_tampering_not_unrelated_business_history(tmp_path, monkeypatch):
    manager, release = fixture(tmp_path, monkeypatch, {'free_query': ['bounded'], 'review_workflow': ['bounded']})
    state = manager.environment.state()
    folder = manager.environment.root / 'certification-test-evidence'
    unrelated = folder / 'review_workflowbounded.json'
    unrelated.write_text('{}')
    assert integration.require_operation(manager.environment, state, release, 'free_query', 'bounded')['status'] == 'certified'
    assert integration.projection(manager.environment, state, release)['status'] == 'not_certified'
    (folder / 'free_querybounded.json').write_text('{}')
    with pytest.raises(WorkBuddyError):
        integration.require_operation(manager.environment, state, release, 'free_query', 'bounded')


def activate(manager):
    index.rebuild(manager.environment, clearance=clearance)


def test_index_does_not_read_terminal_history(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    jobs = manager.environment.root / 'jobs'
    for number in range(150):
        atomic_json(jobs / (digest(str(number)) + '.json'),
                    dict(task_id=str(number), status='completed', cleanup_complete=True))
    activate(manager)
    original = Path.glob
    def no_history(path, pattern):
        if path == jobs:
            pytest.fail('normal admission scanned historical jobs')
        return original(path, pattern)
    monkeypatch.setattr(Path, 'glob', no_history)
    assert manager.supervisor.pending() == []
    assert manager.supervisor.reconcile() == []


def test_index_claim_running_cleanup_and_terminal(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    activate(manager)
    root = manager.environment.root
    index.claim(root, {'task_id': 'one'}, deadline=time.monotonic() + 2)
    with pytest.raises(WorkBuddyError, match='workbuddy_task_busy'):
        index.claim(root, {'task_id': 'one'}, deadline=time.monotonic() + 2)
    assert manager.supervisor.pending()[0]['status'] == 'dispatching'
    path = root / 'jobs' / (digest('one') + '.json')
    atomic_json(path, dict(task_id='one', status='running', pid=123, process_identity='birth', cleanup_complete=False))
    assert len(index.paths(root)) == 1
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity', lambda _: 'birth')
    assert manager.supervisor.reconcile()[0]['status'] == 'cleanup_pending'
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity', lambda _: 'different-birth')
    assert manager.supervisor.reconcile() == []
    assert index.paths(root) == []
    assert json.loads(path.read_text())['status'] == 'interrupted'


@pytest.mark.parametrize('fault', ['partial_write', 'missing', 'missing_marker', 'tamper', 'unknown_process'])
def test_index_fails_closed(tmp_path, monkeypatch, fault):
    manager, _ = fixture(tmp_path, monkeypatch)
    activate(manager)
    root = manager.environment.root
    if fault in {'partial_write', 'unknown_process'}:
        index.claim(root, {'task_id': 'one'}, deadline=time.monotonic() + 2)
    if fault == 'partial_write':
        value = json.loads((root / 'unfinished-jobs.json').read_text())
        value['dirty'] = digest('one') + '.json'
        index._store(root, value)
    elif fault == 'missing':
        (root / 'unfinished-jobs.json').unlink()
    elif fault == 'missing_marker':
        index.marker(root).unlink()
    elif fault == 'tamper':
        (root / 'unfinished-jobs.json').write_text('{}')
    else:
        monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity', lambda _: 'unknown')
        assert manager.supervisor.reconcile()[0]['status'] == 'dispatching'
        return
    with pytest.raises(WorkBuddyError, match='workbuddy_job_index_recovery_required'):
        manager.supervisor.pending()


def test_rebuild_requires_stopped_writers_and_preserves_history(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(WorkBuddyError, match='writer_clearance'):
        index.rebuild(manager.environment, clearance=lambda: {**clearance(), 'old_writers': ['old']})
    path = manager.environment.root / 'jobs' / (digest('pending') + '.json')
    atomic_json(path, dict(task_id='pending', status='cleanup_pending', cleanup_complete=False))
    before = path.read_bytes()
    with pytest.raises(WorkBuddyError, match='unresolved_job'):
        activate(manager)
    assert path.read_bytes() == before


def test_auth_same_binding_singleflight_and_cancelled_waiter(tmp_path):
    from tests.test_runtime_authentication import service
    async def run():
        auth, _ = service(tmp_path)
        adapter = auth.adapters['workbuddy']
        started, release = asyncio.Event(), asyncio.Event()
        async def check():
            adapter.calls.append('check')
            started.set()
            await release.wait()
            return dict(authenticated=True, identity=adapter.identity, method='browser', source='official_cli')
        adapter.check = check
        first = asyncio.create_task(auth.check('workbuddy'))
        await asyncio.wait_for(asyncio.wait({first, asyncio.create_task(started.wait())}, return_when=asyncio.FIRST_COMPLETED), 2)
        if first.done():
            first.result()
        second = asyncio.create_task(auth.check('workbuddy'))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        assert (await second)['status'] == 'authenticated'
        assert adapter.calls == ['check']
    asyncio.run(run())


def test_legacy_provider_adapter_delegates_unchanged():
    calls = []
    planner = SimpleNamespace(snapshot=lambda: calls.append('original') or {'provider_id': 'codex'})
    assert asyncio.run(prepare(planner, RuntimeAdmissionRequest(operation='free_query'))) == {'provider_id': 'codex'}
    assert calls == ['original']


def test_index_parallel_writers_and_late_progress_fenced(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    manager, _ = fixture(tmp_path, monkeypatch)
    activate(manager)
    def write(number):
        task = 'parallel-' + str(number)
        index.claim(manager.environment.root, {'task_id': task}, deadline=time.monotonic() + 5)
        atomic_json(manager.environment.root / 'jobs' / (digest(task) + '.json'),
                    dict(task_id=task, status='completed', cleanup_complete=True))
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(write, range(9)))
    assert index.paths(manager.environment.root) == []
    with pytest.raises(WorkBuddyError, match='already_closed'):
        atomic_json(manager.environment.root / 'jobs' / (digest('parallel-0') + '.json'),
                    dict(task_id='parallel-0', status='running', cleanup_complete=False))


def test_partial_transaction_recovery_checks_process_birth(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    activate(manager)
    root = manager.environment.root
    index.claim(root, {'task_id': 'crash'}, deadline=time.monotonic() + 5)
    value = json.loads((root / 'unfinished-jobs.json').read_text())
    value['dirty'] = digest('crash') + '.json'
    index._store(root, value)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity', lambda _: 'unknown')
    with pytest.raises(WorkBuddyError, match='unresolved_job'):
        index.rebuild(manager.environment, clearance=clearance, recover=True)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity', lambda _: None)
    index.rebuild(manager.environment, clearance=clearance, recover=True)
    assert index.paths(root) == []
    assert len(list((root / 'job-index-recovery').glob('*.json'))) == 1


def test_job_record_reuse_is_only_within_synchronous_fence(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_record_io import read_record
    from sap_business_agents_platform.workbuddy_inspection import inspect
    manager, _ = fixture(tmp_path, monkeypatch)
    path = manager.environment.root / 'jobs' / (digest('observed') + '.json')
    atomic_json(path, dict(task_id='observed', status='completed', cleanup_complete=True))
    with pytest.raises(WorkBuddyError, match='inspection_changed'):
        with inspect(manager.environment):
            assert read_record(path)['status'] == 'completed'
            content = json.loads(path.read_text())
            content['changed'] = True
            atomic_json(path, content)
            assert 'changed' not in read_record(path)  # Final fence rejects it.
    with inspect(manager.environment):
        assert read_record(path)['changed'] is True


def test_saved_binding_cannot_dispatch_after_disable(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch, {'free_query': ['bounded']})
    manager.set_enabled(True)
    saved = manager.runtime_snapshot()
    manager.environment.mutate(lambda value: value.update(enabled=False))
    with pytest.raises(WorkBuddyError, match='runtime_disabled'):
        manager.supervisor.check_operation(saved, 'free_query')


def test_rebuild_preserves_confirmed_historical_timeout(tmp_path, monkeypatch):
    manager, _ = fixture(tmp_path, monkeypatch)
    path = manager.environment.root / 'jobs' / (digest('old-timeout') + '.json')
    atomic_json(path, dict(task_id='old-timeout', status='timed_out', cleanup_complete=True))
    before = path.read_bytes()
    activate(manager)
    assert path.read_bytes() == before and manager.supervisor.pending() == []
