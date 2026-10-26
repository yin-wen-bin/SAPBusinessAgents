"""External cache notifications: fake official identity only, no SDK/SAP."""
import asyncio
import copy
from types import SimpleNamespace
import pytest
from tests.test_workbuddy_post_use_auth_sync import fixture, drain

async def setup(tmp_path):
    service, adapter, changes = await fixture(tmp_path)
    adapter.workbuddy = SimpleNamespace(supervisor=SimpleNamespace(active={}, pending=lambda: False))
    service.start_external_sync()
    return service, adapter, changes

async def tick(service):
    await asyncio.to_thread(service._external_cache_tick)
    await asyncio.sleep(0)
    await drain(service)

def test_external_change_checks_once_preserves_revision_and_old_snapshot(tmp_path):
    async def run():
        s,a,c=await setup(tmp_path);old=copy.deepcopy(s._account('workbuddy'));snapshot=s.bind('workbuddy',{})
        a.stamp['cache_digest']='external'
        for _ in range(20):assert s.view('workbuddy')['authenticated'] is None
        assert a.calls==['check']
        await tick(s)
        assert s.view('workbuddy')['authentication_context_status']=='current'
        assert s._account('workbuddy')['revision']==old['revision']
        s.assert_binding('workbuddy',snapshot)
        for _ in range(5):await tick(s)
        assert a.calls==['check','check'] and all(not changed for _,changed in c)
        assert s.adapters['codex'].calls==[]
        await s.close()
    asyncio.run(run())

@pytest.mark.parametrize('busy',['store','process','cleanup','lease','maintenance'])
def test_deferred_external_check_resumes_after_blocker_without_manual_refresh(tmp_path,busy):
    async def run():
        s,a,_=await setup(tmp_path);a.stamp['cache_digest']='external';active=[True]
        if busy=='store':s.busy=lambda _: [{'code':'active'}] if active[0] else []
        if busy=='process':a.workbuddy.supervisor.active={1:True}
        if busy=='cleanup':a.workbuddy.supervisor.pending=lambda:active[0]
        if busy=='lease':s.leases['workbuddy']=1
        if busy=='maintenance':s.sdk_maintenance_active.add('workbuddy')
        await tick(s);assert a.calls==['check'] and s.view('workbuddy')['authenticated'] is None
        active[0]=False;a.workbuddy.supervisor.active={};s.leases['workbuddy']=0;s.sdk_maintenance_active.clear()
        await tick(s);assert a.calls==['check','check'] and s.view('workbuddy')['authenticated'] is True
        await s.close()
    asyncio.run(run())

@pytest.mark.parametrize('failure',['negative','timeout'])
def test_failed_generation_is_not_retried_and_new_cache_can_be_rechecked(tmp_path,failure):
    async def run():
        s,a,_=await setup(tmp_path);old=copy.deepcopy(s._account('workbuddy'));a.stamp['cache_digest']='external'
        original=a.check
        if failure=='negative':a.negative=True
        else:
            async def timeout(**kwargs):a.calls.append('check');raise TimeoutError()
            a.check=timeout
        await tick(s)
        assert s.view('workbuddy')['authenticated'] is not True
        for _ in range(5):await tick(s)
        assert a.calls==['check','check']
        assert s._account('workbuddy')['revision']==old['revision']
        a.check=original;a.negative=False;a.concurrent_cache_change=False;a.stamp['cache_digest']='new-external'
        await tick(s);assert a.calls==['check','check','check']
        assert s.view('workbuddy')['authenticated'] is True
        await s.close()
    asyncio.run(run())

def test_concurrent_cache_write_requires_its_own_official_confirmation(tmp_path):
    async def run():
        s,a,_=await setup(tmp_path);a.stamp['cache_digest']='external';a.concurrent_cache_change=True
        await tick(s);assert s.view('workbuddy')['authenticated'] is None
        a.concurrent_cache_change=False
        await tick(s);assert s.view('workbuddy')['authenticated'] is True
        assert a.calls==['check','check','check']
        await s.close()
    asyncio.run(run())

def test_maintenance_barrier_defers_observer_without_consuming_attempt(tmp_path):
    async def run():
        s,a,_=await setup(tmp_path);a.stamp['cache_digest']='external'
        marker=s.root.parent/'startup-maintenance.json';marker.write_text('{}')
        await tick(s);assert a.calls==['check']
        marker.unlink();await tick(s);assert a.calls==['check','check']
        await s.close()
    asyncio.run(run())

@pytest.mark.parametrize('changed',['source','profile','site','revoked','signed_out','unknown','missing_supervisor'])
def test_unknown_or_changed_authority_never_automatically_restored(tmp_path,changed):
    async def run():
        s,a,_=await setup(tmp_path);a.stamp['cache_digest']='external'
        if changed=='source':a.stamp['source_digest']='other'
        if changed=='profile':a.stamp['context_digest']='other'
        if changed=='site':a.site['service_site_revision']+=1
        if changed=='revoked':s._account('workbuddy')['invalidation_pending']=True
        if changed=='signed_out':s._account('workbuddy').update(status='signed_out',authenticated=False)
        if changed=='unknown':a.stamp['cache_digest']=None
        if changed=='missing_supervisor':a.workbuddy=None
        await tick(s);assert a.calls==['check']
        assert s.view('workbuddy')['authenticated'] is not True
        await s.close()
    asyncio.run(run())

def test_external_account_change_rejects_original_binding(tmp_path):
    async def run():
        s,a,c=await setup(tmp_path);snapshot=s.bind('workbuddy',{})
        a.stamp['cache_digest']='new-login';a.identity='new@example.test'
        await tick(s)
        with pytest.raises(Exception) as caught:s.assert_binding('workbuddy',snapshot)
        assert caught.value.code=='runtime_authentication_binding_changed' and c[-1]==('workbuddy',True)
        await s.close()
    asyncio.run(run())

def test_watcher_is_lifespan_owned_and_close_cancels_it(tmp_path,monkeypatch):
    async def run():
        monkeypatch.setattr('sap_business_agents_platform.runtime_authentication.EXTERNAL_CACHE_POLL_SECONDS',.001)
        s,a,_=await setup(tmp_path);task=s._external_monitor;s.start_external_sync();assert s._external_monitor is task
        a.stamp['cache_digest']='external'
        for _ in range(100):
            if a.calls==['check','check'] and s.view('workbuddy')['authenticated'] is True:break
            await asyncio.sleep(.002)
        assert a.calls==['check','check']
        await s.close();assert task.done()
        a.stamp['cache_digest']='after-close';await tick(s);assert len(a.calls)==2
    asyncio.run(run())

def test_app_lifespan_recovers_external_change_with_get_only(tmp_path,monkeypatch):
    import time
    from pathlib import Path
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_runtime_authentication_api import Healthy
    monkeypatch.setattr('sap_business_agents_platform.runtime_authentication.EXTERNAL_CACHE_POLL_SECONDS',.01)
    seed,adapter,_=asyncio.run(fixture(tmp_path/'data/sdk-runtimes'))
    adapter.workbuddy=SimpleNamespace(supervisor=SimpleNamespace(active={},pending=lambda:False))
    revision=seed._account('workbuddy')['revision']
    manager=SimpleNamespace(default_provider_id='workbuddy',list=lambda:[])
    app=create_app(Settings(repository_root=Path(__file__).resolve().parents[1],data_root=tmp_path/'data',
        draft_root=tmp_path/'drafts',skillhub_root=tmp_path/'skills'),planner=object(),sdk_manager=manager,
        embedded_provider=Healthy(),authentication_adapters={'workbuddy':adapter})
    with TestClient(app) as client:
        adapter.stamp['cache_digest']='external-after-startup'
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            current=client.get('/api/system/sdk-runtimes/workbuddy/authentication').json()['authentication']
            if current['authenticated'] is True and current['authentication_context_status']=='current':break
        assert current['authenticated'] is True and current['revision']==revision
        assert adapter.calls==['check','check']
