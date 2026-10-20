"""Request-local disk reduction and safety fences. No SDK/model/SAP calls."""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path

import pytest

from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, WorkBuddyError, atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_inspection import inspect, current
from sap_business_agents_platform.workbuddy_supervisor import WorkBuddySupervisor


def installation(tmp_path):
    env = WorkBuddyEnvironment(tmp_path)
    files = {'python.exe': b'python', 'cli.exe': b'cli', 'worker.py': b'worker'}
    import hashlib
    data = {'python': 'python.exe', 'cli': 'cli.exe', 'worker': 'worker.py',
            'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    key = digest(data)
    directory = env.root / 'releases' / key
    directory.mkdir(parents=True)
    for name, value in files.items():
        (directory / name).write_bytes(value)
    atomic_json(directory / 'environment.json', {**data, 'environment_digest': key})
    env.mutate(lambda state: state.update(environment_digest=key))
    return env, directory, key


def count_releases(env, monkeypatch):
    calls = []
    original = env._release
    def load(*args):
        calls.append(args[0])
        return original(*args)
    monkeypatch.setattr(env, '_release', load)
    return calls


def test_release_one_traversal_per_scope_and_no_cross_request_cache(tmp_path, monkeypatch):
    env, _, key = installation(tmp_path)
    calls = count_releases(env, monkeypatch)
    with inspect(env):
        result = env.release()
        result['files'].clear()
        with inspect(env):
            assert env.release()['files']
            assert env.release(key)['environment_digest'] == key
    assert calls == [key]
    with inspect(env):
        env.release()
    assert calls == [key, key]


@pytest.mark.parametrize('change', ['worker', 'extra', 'removed', 'state'])
def test_mutation_during_inspection_is_not_a_ready_result(tmp_path, change):
    env, directory, _ = installation(tmp_path)
    with pytest.raises(WorkBuddyError, match='inspection_changed'):
        with inspect(env):
            env.release()
            if change == 'worker':
                (directory / 'worker.py').write_bytes(b'changed')
            elif change == 'extra':
                (directory / 'extra.py').write_bytes(b'extra')
            elif change == 'removed':
                (directory / 'cli.exe').unlink()
            else:
                env.mutate(lambda state: state.update(enabled=True))


def test_worker_identical_stat_rewrite_still_rejected_at_fence(tmp_path, monkeypatch):
    env, directory, _ = installation(tmp_path)
    worker = (directory / 'worker.py').resolve()
    info, original = worker.stat(), Path.stat
    monkeypatch.setattr(Path, 'stat', lambda path, *a, **kw: info if path == worker else original(path, *a, **kw))
    with pytest.raises(WorkBuddyError, match='inspection_changed'):
        with inspect(env):
            env.release()
            worker.write_bytes(b'change')
            env.release()


def test_failed_release_is_not_cached(tmp_path):
    env, directory, _ = installation(tmp_path)
    (directory / 'worker.py').write_bytes(b'changed')
    with inspect(env):
        for _ in range(2):
            with pytest.raises(WorkBuddyError, match='digest_mismatch'):
                env.release()


def test_context_copy_cannot_share_observations_across_threads(tmp_path, monkeypatch):
    env, _, key = installation(tmp_path)
    calls = count_releases(env, monkeypatch)
    with inspect(env):
        env.release()
        context = contextvars.copy_context()
        def nested():
            assert current(env) is None
            with inspect(env):
                return env.release()['environment_digest']
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(context.run, nested).result() == key
    assert calls == [key, key]


def test_child_async_task_cannot_reuse_parent_observations(tmp_path):
    env, _, _ = installation(tmp_path)
    async def run():
        with pytest.raises(WorkBuddyError, match='inspection_changed'):
            with inspect(env):
                env.release()
                async def child():
                    assert current(env) is None
                    with inspect(env):
                        assert current(env) is not None
                        env.release()
                await asyncio.create_task(child())
    asyncio.run(run())


def test_copied_context_after_scope_close_cannot_reuse_results(tmp_path, monkeypatch):
    env, _, _ = installation(tmp_path)
    calls = count_releases(env, monkeypatch)
    with inspect(env):
        env.release()
        captured = contextvars.copy_context()
    captured.run(env.release)
    assert len(calls) == 2


def test_pending_history_read_once_and_next_request_checks_again(tmp_path, monkeypatch):
    env = WorkBuddyEnvironment(tmp_path)
    owner = WorkBuddySupervisor(env)
    for index in range(50):
        atomic_json(env.root / 'jobs' / f'{index}.json', {'status': 'completed', 'cleanup_complete': True})
    from sap_business_agents_platform import workbuddy_supervisor as module
    original, calls = module.read_record, []
    def read(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(module, 'read_record', read)
    with inspect(env):
        assert owner.pending() == owner.pending() == []
    assert len(calls) == 50
    with inspect(env):
        assert owner.pending() == []
    assert len(calls) == 100


@pytest.mark.parametrize('change', ['new', 'cleanup', 'corrupt'])
def test_history_changes_including_new_pending_records_block_cached_result(tmp_path, change):
    env = WorkBuddyEnvironment(tmp_path)
    path = env.root / 'jobs/one.json'
    atomic_json(path, {'status': 'completed', 'cleanup_complete': True})
    owner = WorkBuddySupervisor(env)
    with pytest.raises(WorkBuddyError, match='inspection_changed'):
        with inspect(env):
            assert owner.pending() == []
            if change == 'new':
                atomic_json(env.root / 'jobs/two.json', {'status': 'cleanup_pending'})
            elif change == 'cleanup':
                atomic_json(path, {'status': 'cleanup_pending'})
            else:
                path.write_text('{invalid', encoding='utf-8')
            owner.pending()
    if change == 'corrupt':
        with pytest.raises(WorkBuddyError, match='job_record_invalid'):
            owner.pending()
    else:
        assert owner.pending()


def test_original_error_is_not_masked_by_guard_error(tmp_path):
    env, directory, _ = installation(tmp_path)
    with pytest.raises(ValueError, match='primary'):
        with inspect(env):
            env.release()
            (directory / 'worker.py').unlink()
            raise ValueError('primary')


def test_certificate_xml_decoded_once_and_tampering_not_cached(tmp_path, monkeypatch):
    from tests.test_workbuddy_three_layers import fixture
    from sap_business_agents_platform import workbuddy_integration as module
    import xml.etree.ElementTree as xml
    manager, release = fixture(tmp_path, monkeypatch)
    cert = next(iter(manager.environment.state()['sdk_integration_records'].values()))
    original, calls = xml.fromstring, []
    def decode(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(xml, 'fromstring', decode)
    with inspect(manager.environment):
        for _ in range(3):
            assert module.projection(manager.environment, manager.environment.state(), release)['status'] == 'certified'
    assert len(calls) == 1
    path = manager.environment.root / cert['evidence']['core']['deadline']['path']
    with pytest.raises(WorkBuddyError, match='inspection_changed'):
        with inspect(manager.environment):
            assert module.projection(manager.environment, manager.environment.state(), release)['status'] == 'certified'
            path.write_text('{}', encoding='utf-8')
    assert module.projection(manager.environment, manager.environment.state(), release)['status'] == 'not_certified'


def test_new_helper_is_part_of_sdk_and_business_dependency_not_model_protocol():
    from sap_business_agents_platform import workbuddy_integration as module, workbuddy_qualification as quality
    assert 'workbuddy_inspection.py' in module.INFRASTRUCTURE_FILES
    assert 'workbuddy_inspection.py' in quality.COMPONENTS['workbuddy_host'][1]
    assert 'workbuddy_inspection.py' not in module.PROTOCOL_FILES
