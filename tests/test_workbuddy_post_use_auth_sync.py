"""Synthetic-only SDK completion -> bounded official auth synchronization."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService, error, AUTH_CHECK_SECONDS
from sap_business_agents_platform.sdk_manager import SDKManager
from tests.test_runtime_authentication import Adapter
from tests.test_workbuddy_auth_hardening import ObservedAdapter


class ControlledAdapter(ObservedAdapter):
    def __init__(self):
        super().__init__()
        self.stamp['source_digest'] = 'frozen-helpers'
        self.entered = self.release = None
        self.negative = False
        self.concurrent_cache_change = False

    async def check(self, *, context=None):
        result = await super().check(context=context)
        if self.entered is not None:
            self.entered.set()
            await self.release.wait()
        if self.negative:
            return {'authenticated': False, 'authentication_evidence': 'initialize_account_absent'}
        if self.concurrent_cache_change:
            proof = dict(self.stamp)
            self.stamp['cache_digest'] = 'unrelated-concurrent-change'
            return {**result, 'authentication_observation': proof}
        return result


async def fixture(tmp_path):
    adapter, changes = ControlledAdapter(), []
    service = RuntimeAuthenticationService(tmp_path, {'workbuddy': adapter, 'codex': Adapter()},
        changed=lambda provider, account: changes.append((provider, account['changed'])))
    await service.check('workbuddy')
    return service, adapter, changes


async def drain(service):
    task = service._post_use_checks.get('workbuddy')
    if task is not None:
        await task
    await asyncio.sleep(0)


def test_success_waits_for_official_identity_without_delaying_sdk_result(tmp_path):
    async def run():
        service, adapter, changes = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        original = copy.deepcopy(service._account('workbuddy'))
        adapter.entered, adapter.release = asyncio.Event(), asyncio.Event()
        async def SDK():
            async with service.use('workbuddy', snapshot):
                adapter.stamp['cache_digest'] = 'SDK-renewed'
                return {'saved': True, 'model': 'space-bunny'}
        assert await SDK() == {'saved': True, 'model': 'space-bunny'}
        await adapter.entered.wait()
        view = service.view('workbuddy')
        assert view['authenticated'] is None and view['authentication_sync']['status'] == 'checking'
        assert service._account('workbuddy')['authentication_observation'] == original['authentication_observation']
        adapter.release.set(); await drain(service)
        assert service.view('workbuddy')['authentication_context_status'] == 'current'
        assert service._account('workbuddy')['revision'] == original['revision']
        assert service._account('workbuddy')['confirmed_binding'] == original['confirmed_binding']
        assert changes == [('workbuddy', False), ('workbuddy', False)]
        assert adapter.calls == ['check', 'check']
        assert AUTH_CHECK_SECONDS == 30
        await service.close()
    asyncio.run(run())


def test_unchanged_cache_and_read_only_views_never_start_checks(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        async with service.use('workbuddy', service.bind('workbuddy', {})): pass
        assert not service._post_use_checks and adapter.calls == ['check']
        adapter.stamp['cache_digest'] = 'external-change'
        saved = service.path.read_bytes()
        for _ in range(20): assert service.view('workbuddy')['authenticated'] is None
        await asyncio.sleep(0)
        assert adapter.calls == ['check'] and service.path.read_bytes() == saved
        await service.close()
    asyncio.run(run())


def test_nested_leases_schedule_only_after_last_owner(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        async with service.use('workbuddy', snapshot):
            async with service.use('workbuddy', snapshot):
                adapter.stamp['cache_digest'] = 'renewed'
            assert not service._post_use_checks and service.leases['workbuddy'] == 1
        assert len(service._post_use_checks) == 1
        await drain(service)
        assert adapter.calls == ['check', 'check'] and not service._post_use_checks
        await service.close()
    asyncio.run(run())


def test_concurrent_sdk_returns_schedule_one_check_after_last_cleanup(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        entered, release, first_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow_sdk():
            async with service.use('workbuddy', snapshot):
                entered.set(); await release.wait()
        async def fast_sdk():
            await entered.wait()
            async with service.use('workbuddy', snapshot): adapter.stamp['cache_digest']='renewed'
            first_done.set()
        slow, fast = asyncio.create_task(slow_sdk()), asyncio.create_task(fast_sdk())
        await first_done.wait()
        assert not service._post_use_checks and service.leases['workbuddy']==1
        release.set(); await asyncio.gather(slow,fast); await drain(service)
        assert adapter.calls==['check','check'] and service.leases['workbuddy']==0
        assert service.view('workbuddy')['authenticated'] is True
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('missing', ['context_digest', 'source_digest', 'cache_digest'])
def test_unknown_observation_cannot_trigger_automatic_attestation(tmp_path, missing):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        async with service.use('workbuddy', service.bind('workbuddy', {})):
            adapter.stamp['cache_digest']='changed'
            adapter.stamp[missing]=None
        await asyncio.sleep(0)
        assert adapter.calls==['check'] and not service._post_use_checks
        assert service.view('workbuddy')['authenticated'] is None
        await service.close()
    asyncio.run(run())


def test_explicit_check_and_next_dispatch_join_the_single_background_check(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        adapter.entered, adapter.release = asyncio.Event(), asyncio.Event()
        async with service.use('workbuddy', snapshot): adapter.stamp['cache_digest'] = 'renewed'
        await adapter.entered.wait()
        explicit = asyncio.create_task(service.check('workbuddy'))
        dispatches = []
        async def next_call():
            async with service.use('workbuddy', snapshot): dispatches.append('authorized')
        next_task = asyncio.create_task(next_call())
        await asyncio.sleep(0); assert dispatches == [] and adapter.calls == ['check', 'check']
        adapter.release.set()
        await asyncio.gather(explicit, next_task); await drain(service)
        assert dispatches == ['authorized'] and adapter.calls == ['check', 'check']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['workbuddy_authentication_required', 'workbuddy_authentication_refresh_failed', 'timeout', 'missing_account', 'racing_cache'])
def test_failed_official_recheck_does_not_grant_or_retry_or_replace_result(tmp_path, failure):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        original = copy.deepcopy(service._account('workbuddy'))
        if failure in {'workbuddy_authentication_required', 'workbuddy_authentication_refresh_failed'}:
            adapter.rejection = failure
        elif failure == 'missing_account': adapter.negative = True
        elif failure == 'racing_cache': adapter.concurrent_cache_change = True
        elif failure == 'timeout':
            async def timeout(**kwargs):
                adapter.calls.append('check'); raise TimeoutError()
            adapter.check = timeout
        async def SDK():
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                adapter.stamp['cache_digest'] = 'renewed'; return 'original-SDK-result'
        assert await SDK() == 'original-SDK-result'
        await drain(service)
        view = service.view('workbuddy'); assert view['authenticated'] is not True
        assert service._account('workbuddy')['confirmed_binding'] == original['confirmed_binding']
        assert service._account('workbuddy')['revision'] == original['revision']
        assert service._account('workbuddy')['last_successful_check'] == original['last_successful_check']
        with pytest.raises(Exception): service.assert_binding('workbuddy', service.bind('workbuddy', {}))
        for _ in range(10): service.view('workbuddy')
        await asyncio.sleep(0)
        assert adapter.calls == ['check', 'check'] and not service._post_use_checks
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['site', 'revision', 'source', 'profile', 'interactive_auth'])
def test_queued_completion_cannot_follow_a_changed_binding(tmp_path, change):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        async with service.use('workbuddy', service.bind('workbuddy', {})): adapter.stamp['cache_digest'] = 'renewed'
        if change == 'site': adapter.site['service_site_revision'] += 1
        elif change == 'revision': service._account('workbuddy')['revision'] += 1
        elif change == 'source': adapter.stamp['source_digest'] = 'new-unreviewed-code'
        elif change == 'profile': adapter.stamp['context_digest'] = 'other-user'
        else: service._active = lambda *_: True
        await drain(service)
        assert adapter.calls == ['check']
        await service.close()
    asyncio.run(run())


def test_real_account_change_from_official_check_rejects_old_snapshot(tmp_path):
    async def run():
        service, adapter, changes = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        async with service.use('workbuddy', snapshot):
            adapter.stamp['cache_digest'] = 'other-login'; adapter.identity = 'different@example.test'
        await drain(service)
        assert service._account('workbuddy')['revision'] == snapshot['authentication_binding_revision']+1
        with pytest.raises(Exception) as caught: service.assert_binding('workbuddy', snapshot)
        assert caught.value.code == 'runtime_authentication_binding_changed'
        assert changes[-1] == ('workbuddy', True) and adapter.calls == ['check','check']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['cancelled', 'timeout', 'cleanup_pending', 'auth401'])
def test_unconfirmed_cleanup_or_auth_failures_never_launch_followup(tmp_path, kind):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        snapshot = service.bind('workbuddy', {})
        if kind == 'auth401':
            async with service.use('workbuddy', snapshot):
                adapter.stamp['cache_digest'] = 'revoked'
                service.report_workbuddy_failure(snapshot, {'failure_code':'workbuddy_authentication_required',
                    'http_status':401,'failure_source':'owned_worker_stderr'}, started_at=10**12)
            assert service.view('workbuddy')['status'] == 'expired'
        else:
            original = asyncio.CancelledError() if kind == 'cancelled' else error('runtime_cleanup_incomplete' if kind=='cleanup_pending' else 'workbuddy_deadline_exceeded')
            with pytest.raises(BaseException) as caught:
                async with service.use('workbuddy', snapshot):
                    adapter.stamp['cache_digest'] = 'changed'; raise original
            assert caught.value is original
        await asyncio.sleep(0)
        assert not service._post_use_checks and adapter.calls == ['check']
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('ownership', ['active', 'pending', 'unreadable', 'drained'])
def test_returned_failure_record_still_checks_owned_process_cleanup(tmp_path, ownership):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        def pending():
            if ownership=='unreadable':raise OSError('private-job-path')
            return [{'status':'cleanup_pending'}] if ownership=='pending' else []
        adapter.workbuddy=SimpleNamespace(supervisor=SimpleNamespace(
            active={'owned-job':{}} if ownership=='active' else {}, pending=pending))
        # A model API can return a failed result after its internal exception
        # handler. It must not start another official process while uncleared.
        async with service.use('workbuddy', service.bind('workbuddy', {})):
            adapter.stamp['cache_digest']='changed'
        await drain(service)
        assert adapter.calls==(['check','check'] if ownership=='drained' else ['check'])
        assert (service.view('workbuddy')['authenticated'] is True)==(ownership=='drained')
        assert 'private-job-path' not in json.dumps(service._post_use_status)
        await service.close()
    asyncio.run(run())


def test_close_cancels_the_owned_recheck_without_blessing_new_cache(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        adapter.entered, adapter.release = asyncio.Event(), asyncio.Event()
        async with service.use('workbuddy', service.bind('workbuddy', {})): adapter.stamp['cache_digest']='renewed'
        await adapter.entered.wait(); await service.close(); await asyncio.sleep(0)
        assert not service.checking and not service._post_use_checks
        assert service.view('workbuddy')['authenticated'] is None
    asyncio.run(run())


def test_observation_error_cannot_replace_a_completed_result_or_confirm_identity(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        original_observe = adapter.observation
        async def SDK():
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                adapter.observation = lambda: (_ for _ in ()).throw(OSError('private-path'))
                return 'saved-result'
        assert await SDK() == 'saved-result'
        assert not service._post_use_checks and adapter.calls == ['check']
        assert 'private-path' not in json.dumps(service._post_use_status)
        assert service.view('workbuddy')['authenticated'] is None
        adapter.observation = original_observe; await service.close()
    asyncio.run(run())


def test_codex_never_observes_or_schedules_workbuddy_recheck(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        service._observation = lambda *_: pytest.fail('Codex must not observe WorkBuddy')
        async with service.use('codex', service.bind('codex', {})): pass
        assert not service._post_use_checks and service.adapters['codex'].calls == []
        assert adapter.calls == ['check']; await service.close()
    asyncio.run(run())


def test_sdk_model_check_uses_shared_lifecycle_without_changing_sdk_request(tmp_path):
    async def run():
        service, adapter, _ = await fixture(tmp_path)
        manager = SDKManager.__new__(SDKManager); manager.authentication_service = service
        seen=[]
        async def check(provider, model, effort, request):
            seen.append((provider,model,effort,request)); adapter.stamp['cache_digest']='renewed'
            return {'check':{'compatible':True},'job':'already-cleaned'}
        manager._check_model=check
        result=await manager.check_model('workbuddy','space-bunny',request_id='original-request')
        assert result=={'check':{'compatible':True},'job':'already-cleaned'}
        assert seen==[('workbuddy','space-bunny',None,'original-request')]
        await drain(service)
        assert adapter.calls==['check','check']; await service.close()
    asyncio.run(run())


def test_same_account_callback_preserves_model_quality_and_other_providers(tmp_path):
    async def run():
        adapter=ControlledAdapter()
        saved={**adapter.context(),'environment_digest':'a'*64,
            'models':{'space-bunny':{'compatible':True,'check_digest':'original-real-PASS'}},
            'model_check_history':[{'check_digest':'older-real-PASS'}],
            'qualification_records':{'record':{'status':'PASS'}},'validation_ledger':{'entry':{'kind':'live_pass'}}}
        manager=SDKManager.__new__(SDKManager)
        env=SimpleNamespace(state=lambda:saved, release=lambda:{'environment_digest':'a'*64}, mutate=lambda change:change(saved))
        manager.workbuddy=SimpleNamespace(environment=env)
        service=RuntimeAuthenticationService(tmp_path,{'workbuddy':adapter,'codex':Adapter()},changed=manager.apply_authentication_state)
        await service.check('workbuddy')
        baseline=copy.deepcopy(saved)
        async with service.use('workbuddy',service.bind('workbuddy',{})):adapter.stamp['cache_digest']='renewed'
        await drain(service)
        for key in ('models','model_check_history','qualification_records','validation_ledger',
                    'authentication_revision','authentication_identity_digest'):
            assert saved.get(key)==baseline.get(key)
        assert 'codex' not in service.state['accounts']
        await service.close()
    asyncio.run(run())


def test_real_http_model_check_finishes_auth_sync_without_another_user_POST(tmp_path):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_runtime_authentication_api import Healthy
    adapter=ControlledAdapter();calls=[]
    manager=SimpleNamespace(default_provider_id='workbuddy',list=lambda:[
        {'provider_id':'workbuddy','sdk_id':'codebuddy-agent-sdk','authenticated':True,'enabled':True,'selectable':True}])
    async def fake_model_check(provider,model,effort,request):
        calls.append((provider,model,effort,request))
        adapter.stamp['cache_digest']='SDK-cache-update'
        return {'check':{'compatible':True},'cleanup_complete':True}
    manager._check_model=fake_model_check
    manager.check_model=SDKManager.check_model.__get__(manager,type(manager))
    root=Path(__file__).resolve().parents[1]
    app=create_app(Settings(repository_root=root,data_root=tmp_path/'data',draft_root=tmp_path/'drafts',skillhub_root=tmp_path/'skills'),
        planner=object(),sdk_manager=manager,embedded_provider=Healthy(),authentication_adapters={'workbuddy':adapter})
    auth='/api/system/sdk-runtimes/workbuddy/authentication'
    headers={'X-SAPBA-Action':'runtime-authentication','Origin':'http://127.0.0.1:4321'}
    with TestClient(app) as client:
        assert client.post(auth+'/check',headers=headers).json()['authentication']['authenticated'] is True
        revision=client.get(auth).json()['authentication']['revision']
        reply=client.post('/api/system/sdk-runtimes/workbuddy/models/check',
            json={'model_id':'space-bunny','requestId':'one-model-request'})
        assert reply.status_code==200 and reply.json()=={'check':{'compatible':True},'cleanup_complete':True}
        for _ in range(100):
            current=client.get(auth).json()['authentication']
            if current['authenticated'] is True and current['authentication_context_status']=='current':break
        assert current['authenticated'] is True and current['revision']==revision
        assert calls==[('workbuddy','space-bunny',None,'one-model-request')]
        assert adapter.calls==['check','check']
