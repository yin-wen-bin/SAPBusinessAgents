"""Read-only startup probe keeps credentials/site while avoiding project cwd."""
import asyncio
from pathlib import Path

import pytest

from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from tests.test_workbuddy_isolation import supervisor


def test_manager_authentication_check_has_empty_temp_cwd_and_same_protocol(tmp_path, monkeypatch):
    from tests.test_workbuddy_qualification import manager_fixture
    manager, release = manager_fixture(tmp_path)
    observed = []
    async def run(**kwargs):
        directory = Path(kwargs['process_cwd'])
        assert directory.is_dir() and not list(directory.iterdir())
        assert not directory.is_relative_to(tmp_path)
        assert kwargs['operation'] == 'authentication' and kwargs['payload'] == {}
        from sap_business_agents_platform.workbuddy_manager import AUTHENTICATION_CHECK_SECONDS
        assert kwargs['seconds'] == AUTHENTICATION_CHECK_SECONDS == 60 and kwargs['probe'] is True
        assert kwargs['snapshot']['environment_digest'] == release['environment_digest']
        assert kwargs['snapshot']['service_site'] == 'china'
        observed.append(directory)
        return {'authenticated': True, 'status': 'existing_login'}
    monkeypatch.setattr(manager.supervisor, 'run', run)
    monkeypatch.setattr(manager, '_record_environment_check', lambda *a: None)
    assert asyncio.run(manager.check())['authenticated'] is True
    assert len(observed) == 1 and not observed[0].exists()
    assert manager.environment.state()['authentication_revision'] == 4


def test_owned_worker_uses_explicit_authentication_cwd(tmp_path, monkeypatch):
    owner, binding = supervisor(tmp_path, monkeypatch)
    directory = tmp_path / 'isolated'
    directory.mkdir()
    spawn = asyncio.create_subprocess_exec
    observed = []
    async def capture(*args, **kwargs):
        observed.append(kwargs.get('cwd'))
        return await spawn(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
    asyncio.run(owner.run(task_id='auth-cwd', snapshot=binding, operation='authentication',
        payload={}, seconds=10, probe=True, process_cwd=str(directory)))
    assert observed == [str(directory)] and not owner.pending()
    with pytest.raises(WorkBuddyError, match='workbuddy_probe_scope_invalid'):
        asyncio.run(owner.run(task_id='not-auth', snapshot=binding, operation='review_workflow',
            payload={}, seconds=10, process_cwd=str(directory)))
