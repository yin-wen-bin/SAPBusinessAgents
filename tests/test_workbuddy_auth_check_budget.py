"""Offline only: bounded official account checks must not block the API loop."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import runtime_authentication_adapters as adapters
from sap_business_agents_platform.runtime_authentication import AUTH_CHECK_SECONDS
from tests.test_workbuddy_post_use_auth_sync import fixture, drain


def test_slow_preparation_cancellation_cannot_late_spawn_helper(tmp_path, monkeypatch):
    async def run():
        adapter = adapters.OfficialAuthenticationAdapter(tmp_path, 'workbuddy')
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def preparation(*_):
            entered.set()
            try:
                assert release.wait(5)
                return {}, {}, {}, 'unused'
            finally:
                finished.set()
        monkeypatch.setattr(adapter, '_prepare_workbuddy', preparation)
        async def spawn(*a, **kw):
            pytest.fail('cancelled preparation must not create a helper')
        monkeypatch.setattr(adapters.asyncio, 'create_subprocess_exec', spawn)
        task = asyncio.create_task(adapter.check())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            # The caller/API loop remains responsive during a slow disk check.
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert adapter.cleanup_complete is True  # no process was created
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)
    asyncio.run(run())


def test_official_adapter_keeps_both_integrity_fences_off_the_event_loop(tmp_path, monkeypatch):
    from sap_business_agents_platform.workbuddy_site import make_configuration
    async def run():
        adapter = adapters.OfficialAuthenticationAdapter(tmp_path, 'workbuddy')
        loop_thread = threading.get_ident()
        seen = []
        context = make_configuration('china', 1)
        release = {'python_path': 'fixture-python', 'cli_path': 'fixture-cli', 'environment_digest': 'f'*64}
        def preparation(*_):
            seen.append(('before', threading.get_ident()))
            return release, context, {'sdk_environment': 'internal'}, 'a'*64
        def verification(*_):
            seen.append(('after', threading.get_ident()))
        monkeypatch.setattr(adapter, '_prepare_workbuddy', preparation)
        monkeypatch.setattr(adapter, '_verify_workbuddy', verification)
        class Job:
            def attach(self, pid): pass
            def terminate(self): pass
            def empty(self): return True
            def close(self): pass
        class Input:
            def write(self, data): assert data == b'owned\n'
            async def drain(self): pass
        class Process:
            pid = 123
            returncode = 0
            stdin = Input()
            async def wait(self): return 0
        async def spawn(*a, **kw):
            assert threading.get_ident() == loop_thread
            reader = asyncio.StreamReader()
            reader.feed_data(json.dumps({'kind':'result','value':{'authenticated':True,
                'identity':'fixture-user','method':'browser','source':'official_cli'}}).encode()+b'\n')
            reader.feed_eof()
            return SimpleNamespace(pid=123, returncode=0, stdin=Input(), stdout=reader, wait=Process().wait)
        monkeypatch.setattr(adapters, 'WindowsJob', Job)
        monkeypatch.setattr(adapters, 'process_identity', lambda _: 'fixture-birth')
        monkeypatch.setattr(adapters.asyncio, 'create_subprocess_exec', spawn)
        if hasattr(adapters.os, 'killpg'):
            monkeypatch.setattr(adapters.os, 'killpg', lambda *a: None)
        result = await adapter.check(context=context)
        assert result['authenticated'] is True and adapter.cleanup_complete
        assert [name for name,_ in seen] == ['before','after']
        assert all(thread != loop_thread for _,thread in seen)
        assert adapter.check_diagnostic == {'phase':'completed'}
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['preparing','waiting_official_result','verifying_installation','private-token'])
def test_post_use_timeout_is_classified_without_retry_or_identity_replacement(tmp_path, phase):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        original = copy.deepcopy(service._account('workbuddy'))
        async def timeout(**_):
            adapter.calls.append('check')
            adapter.check_diagnostic = {'phase': phase, 'raw':'secret-must-not-escape'}
            raise TimeoutError('private-url-token')
        adapter.check = timeout
        async with service.use('workbuddy', service.bind('workbuddy', {})):
            adapter.stamp['cache_digest'] = 'SDK-renewed'
        await drain(service)
        view = service.view('workbuddy')
        assert view['failure_code'] == 'runtime_authentication_check_timeout'
        assert view['authentication_context_status'] == 'check_required'
        assert view['authenticated'] is None
        diagnostic = view['last_check_diagnostic']
        assert diagnostic['phase'] == ('official_check' if phase == 'private-token' else phase)
        assert diagnostic['cleanup_complete'] is True
        assert 'private' not in json.dumps(view) and 'secret-must-not-escape' not in json.dumps(view)
        assert service._account('workbuddy')['confirmed_binding'] == original['confirmed_binding']
        assert service._account('workbuddy')['revision'] == original['revision']
        assert adapter.calls == ['check', 'check']
        for _ in range(5): service.view('workbuddy')
        assert adapter.calls == ['check', 'check'] and AUTH_CHECK_SECONDS == 30
        await service.close()
    asyncio.run(run())
