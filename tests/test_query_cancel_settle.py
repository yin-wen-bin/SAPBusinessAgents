"""Pure local cleanup observations: no SDK, authentication, model or SAP calls."""
import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.models import RunCreate, RunStatus
from sap_business_agents_platform.runtime_cancellation import cleanup_confirmed, record_cleanup_confirmation
from tests.test_free_query_lifecycle import coordinator, new_run, until, Broker


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_late_confirmed_cleanup_releases_only_its_original_query(tmp_path,monkeypatch,provider):
    owner=coordinator(tmp_path)
    new_run(owner,'owned');new_run(owner,'peer')
    owner.store.update_run('owned',runtime_json={'provider_id':provider,'sdk_id':'offline',
        'configuration_digest':'frozen','model':'offline'})
    controller=SimpleNamespace(broker=Broker())
    if provider=='workbuddy':
        controller.manager=SimpleNamespace(supervisor=SimpleNamespace(active={}))
        owner.workbuddy_harness=controller
    else:
        owner.harness=controller
    real_wait=asyncio.wait
    async def short_wait(checks,*,timeout=None,**kwargs):
        return await real_wait(checks,timeout=.01 if timeout==10 else timeout,**kwargs)
    monkeypatch.setattr(asyncio,'wait',short_wait)
    async def scenario():
        tools=asyncio.Event();release=asyncio.Event()
        calls={'tools':0,'interrupt':0}
        async def cancel_tools(*args,**kwargs):
            calls['tools']+=1
            await tools.wait();return True
        async def interrupt(*args):
            calls['interrupt']+=1
            await asyncio.Future()
        async def executing():
            try:await asyncio.Future()
            finally:
                await release.wait()
                assert record_cleanup_confirmation(owner.store,'owned',provider,{'complete':True})
        controller.broker.cancel_tools=cancel_tools
        controller.interrupt=interrupt
        execution=asyncio.create_task(executing());owner._active_execution_tasks['owned']=execution
        await asyncio.sleep(0)
        await owner.cancel('owned')
        assert owner.store.get_harness_state('owned')['cancellation']['status']=='cleanup_pending'
        assert owner.store.get_run('owned').status!=RunStatus.cancelled
        release.set();tools.set()
        await until(lambda:owner.store.get_run('owned').status==RunStatus.cancelled)
        assert owner.store.get_harness_state('owned')['cleanup_incomplete'] is False
        assert owner.store.get_run('peer').cancel_requested is False
        assert sum(e.type=='run_cancelled' for e in owner.store.events_after('owned'))==1
        await owner.cancel('owned')
        assert sum(e.type=='run_cancelled' for e in owner.store.events_after('owned'))==1
        assert calls=={'tools':1,'interrupt':1}
    asyncio.run(scenario())


@pytest.mark.parametrize('fault',['incomplete','wrong_run','wrong_provider','wrong_binding','wrong_turn'])
def test_missing_or_stale_cleanup_receipt_cannot_clear_a_blocker(tmp_path,fault):
    owner=coordinator(tmp_path);new_run(owner,'owned')
    state={'complete':fault!='incomplete'}
    assert record_cleanup_confirmation(owner.store,'owned','codex',state)==(fault!='incomplete')
    saved=owner.store.get_harness_state('owned')
    proof=saved.get('cleanup_confirmation') or {}
    if fault=='wrong_run':proof['run_id']='peer'
    if fault=='wrong_provider':proof['provider_id']='workbuddy'
    if fault=='wrong_binding':proof['binding_digest']='different'
    if fault=='wrong_turn':saved['active_turn_id']='next-turn'
    assert not cleanup_confirmed(owner.store.get_run('owned'),saved)
    assert owner.store.get_run('owned').cancel_requested is False


def test_positive_cleanup_does_not_override_failed_tool_drain(tmp_path):
    owner=coordinator(tmp_path);new_run(owner,'owned')
    owner.store.update_run('owned',cancel_requested=True)
    record_cleanup_confirmation(owner.store,'owned','codex',{'complete':True})
    async def scenario():
        async def failed():return False
        tool=asyncio.create_task(failed());await tool
        assert not await owner._settle_query_cancellation('owned',owner.harness,{tool},tool,None,None)
        assert owner.store.get_run('owned').status!=RunStatus.cancelled
    asyncio.run(scenario())


def test_finished_waiters_or_missing_processes_alone_cannot_clear_old_pending_cleanup(tmp_path):
    owner=coordinator(tmp_path);new_run(owner,'owned')
    owner.store.update_run('owned',cancel_requested=True)
    owner.store.update_harness_state('owned',{'cleanup_incomplete':True,
        'cancellation':{'status':'cleanup_pending','reason':'user_cancelled'}})
    async def scenario():
        async def drained():return True
        tool=asyncio.create_task(drained());await tool
        assert not await owner._settle_query_cancellation('owned',owner.harness,{tool},tool,None,None)
        assert owner.store.get_run('owned').status!=RunStatus.cancelled
        assert owner.store.get_harness_state('owned')['cleanup_incomplete'] is True
    asyncio.run(scenario())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('cleanup_complete',[False,True])
def test_shared_harness_issues_only_current_native_cleanup_confirmation(tmp_path,provider,cleanup_complete):
    from tests.test_runtime_report_contract import make_broker
    from sap_business_agents_platform.runtime_harness import run as harness_run
    broker,store,run,_=make_broker(tmp_path,runtime={'provider_id':provider,'sdk_id':'offline',
        'model':'offline-model','configuration_digest':'offline-frozen'})
    owner=SimpleNamespace(settings=broker.settings,store=store,broker=broker)
    assert record_cleanup_confirmation(store,run,provider,{'complete':True})
    class Driver:
        provider_id=provider
        engineering_copy=False
        def binding(self,*_):return store.get_run(run).runtime.model_dump(mode='json')
        async def harness_turn(self,request,context):
            # The previous call's proof cannot survive entry into a new call.
            assert store.get_harness_state(run).get('cleanup_confirmation') is None
            context['cleanup_state'].update(started=True,complete=False)
            raise asyncio.CancelledError()
        async def cleanup(self,run_id,context):
            context['cleanup_state']['complete']=cleanup_complete
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(harness_run(owner,Driver(),run,'Synthetic offline report'))
    assert cleanup_confirmed(store.get_run(run),store.get_harness_state(run)) is cleanup_complete


def test_cancel_http_acknowledges_before_native_cleanup_without_losing_ownership(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from tests.test_platform_runtime import create_app,_settings,FeedbackPlanner,FakeEmbeddedProvider
    app=create_app(_settings(tmp_path),planner=FeedbackPlanner(),embedded_provider=FakeEmbeddedProvider())
    gate=Event()
    with TestClient(app) as client:
        owner=app.state.coordinator
        owner.store.create_run('owned',RunCreate(mode='free_query',query='offline only'))
        original=owner._cancel_owned_query
        async def held(run_id,controller,broker):
            while not gate.is_set():await asyncio.sleep(.005)
            await original(run_id,controller,broker)
        monkeypatch.setattr(owner,'_cancel_owned_query',held)
        try:
            reply=client.post('/api/runs/owned/cancel')
            assert reply.status_code==202
            assert reply.json()['status']=='cancellation_requested'
            assert owner.store.get_run('owned').cancel_requested is True
            assert not owner._cancellation_tasks['owned'].done()
        finally:
            gate.set()
