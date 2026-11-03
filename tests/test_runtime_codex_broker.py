"""Offline transport checks only; these do not grant live formal acceptance."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft7Validator

from tests.test_agent_sample_discovery import setup_broker
from sap_business_agents_platform.runtime_report_contract import freeze
from tests.test_acceptance_projection import SPEC

ROOT=Path(__file__).resolve().parents[1]
from sap_business_agents_platform import runtime_codex_broker as module
NativeBrokerTools=module.NativeBrokerTools


def make(tmp_path, *, output=None, deadline=None):
    _,store,real,_=setup_broker(tmp_path)
    contract=freeze('run_sample','request',{'provider_id':'codex','version':'0.163.0'},SPEC)
    captured=[]
    def request(method,params,**kwargs):
        captured.append((method,copy.deepcopy(params)))
        return SimpleNamespace(thread=SimpleNamespace(id='native-thread'))
    deny=lambda method,params:{'decision':'decline'}
    client=SimpleNamespace(request=request,_approval_handler=deny)
    broker=SimpleNamespace(store=store,report_contract=lambda _:contract,
        handle=AsyncMock(return_value=output or {'ok':True,'evidence_ref':'ev_test'}))
    sdk=SimpleNamespace(_client=SimpleNamespace(_sync=client))
    transport=NativeBrokerTools(sdk,broker,'run_sample','private-capability',deadline=deadline or module.time.monotonic()+30).install()
    original={'sandbox':'read-only','approvalPolicy':'never','model':'gpt-6.1-sol','cwd':'empty-baseline-directory'}
    client.request('thread/start',original)
    assert 'dynamicTools' not in original
    return transport,client,broker,captured,request,deny


def call(**change):
    return {**{'threadId':'native-thread','turnId':'native-turn','callId':'native-call',
        'namespace':'sapba_broker','tool':'sap_catalog_search','arguments':{'query':'purchase order'}},**change}


def unpack(value):
    response_schema=json.loads((ROOT/'tests/fixtures/codex-0.163.0/DynamicToolCallResponse.json').read_text())
    Draft7Validator(response_schema).validate(value)
    return json.loads(value['contentItems'][0]['text'])


def test_native_tools_match_reviewed_cli_schema_without_exposing_shell_or_capability(tmp_path):
    async def scenario():
        transport,client,broker,captured,request,deny=make(tmp_path)
        params=captured[0][1]
        schema=json.loads((ROOT/'tests/fixtures/codex-0.163.0/v2/ThreadStartParams.json').read_text())
        Draft7Validator(schema).validate(params)
        assert params['sandbox']=='read-only' and params['approvalPolicy']=='never'
        assert 'private-capability' not in json.dumps(params) and 'run_sample' not in json.dumps(params)
        assert {'sap_schema_get','sap_query_validate','sap_query_execute','sap_final_report_validate'} <= transport.names
        assert not {'exec_command','apply_patch','external_tool_execute'} & transport.names
        result=await asyncio.to_thread(client._approval_handler,'item/tool/call',call())
        assert result['success'] is True and unpack(result)=={'ok':True,'evidence_ref':'ev_test'}
        broker.handle.assert_awaited_once_with('run_sample','private-capability','sap_catalog_search',
            {'query':'purchase order','tool_call_id':'native-call'})
        assert transport.accepts_item({'namespace':'sapba_broker','tool':'sap_schema_get'})
        assert not transport.accepts_item({'namespace':'elsewhere','tool':'sap_schema_get'})
        assert client._approval_handler('item/fileChange/requestApproval',{})=={'decision':'decline'}
        await transport.aclose()
        assert client.request is request and client._approval_handler is deny
        assert unpack(transport.handle('item/tool/call',call()))['code']=='runtime_broker_tool_binding_denied'
    asyncio.run(scenario())


@pytest.mark.parametrize('change',[{'namespace':'other'},{'tool':'exec_command'},{'threadId':'other-thread'},
    {'arguments':[]},{'callId':''},{'turnId':''}])
def test_cross_session_unknown_or_malformed_calls_never_reach_broker(tmp_path,change):
    async def scenario():
        transport,client,broker,*_=make(tmp_path)
        result=await asyncio.to_thread(client._approval_handler,'item/tool/call',call(**change))
        assert result['success'] is False and unpack(result)['code']=='runtime_broker_tool_binding_denied'
        broker.handle.assert_not_awaited();await transport.aclose()
    asyncio.run(scenario())


def test_broker_denial_is_preserved_without_repairing_its_answer(tmp_path):
    async def scenario():
        denied={'ok':False,'code':'sample_scope_not_preserved'}
        transport,client,broker,*_=make(tmp_path,output=denied)
        result=await asyncio.to_thread(client._approval_handler,'item/tool/call',call())
        assert result['success'] is False and unpack(result)==denied
        await transport.aclose()
    asyncio.run(scenario())


def test_deadline_rejects_new_reads(tmp_path):
    async def scenario():
        transport,client,broker,*_=make(tmp_path,deadline=module.time.monotonic()-1)
        result=await asyncio.to_thread(client._approval_handler,'item/tool/call',call())
        assert unpack(result)['code']=='runtime_deadline_exceeded'
        broker.handle.assert_not_awaited();await transport.aclose()
    asyncio.run(scenario())


def test_close_cancels_and_drains_active_callback_before_sdk_cleanup(tmp_path):
    async def scenario():
        transport,client,broker,*_=make(tmp_path)
        started,finished=asyncio.Event(),asyncio.Event()
        async def wait(*args):
            started.set()
            try:await asyncio.Event().wait()
            finally:finished.set()
        broker.handle.side_effect=wait
        callback=asyncio.create_task(asyncio.to_thread(client._approval_handler,'item/tool/call',call()))
        await asyncio.wait_for(started.wait(),1)
        await transport.aclose()
        await asyncio.wait_for(callback,1)
        assert finished.is_set() and not transport.tasks and not transport.pending
    asyncio.run(scenario())


def test_resume_keeps_persisted_tools_and_binds_only_returned_owned_thread(tmp_path):
    async def scenario():
        first,client,broker,captured,*_=make(tmp_path)
        await first.aclose()
        resumed=NativeBrokerTools(SimpleNamespace(_client=SimpleNamespace(_sync=client)),
            broker,'run_sample','private-capability',deadline=module.time.monotonic()+30).install()
        original={'threadId':'native-thread','sandbox':'read-only','approvalPolicy':'never'}
        client.request('thread/resume',original)
        assert captured[-1]==('thread/resume',original)
        result=await asyncio.to_thread(client._approval_handler,'item/tool/call',call())
        assert result['success'] is True
        with pytest.raises(RuntimeError,match='runtime_broker_thread_binding_invalid'):
            client.request('thread/start',{})
        await resumed.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('unknown',[False,True])
def test_proposed_driver_recovers_only_saved_validated_report_and_keeps_unknown_tools_denied(tmp_path,monkeypatch,unknown):
    from dataclasses import replace
    from tests.test_runtime_report_contract import make_broker,report
    from tests.test_runtime_report_drivers import request_context
    from sap_business_agents_platform.harness import CodexHarnessController
    from sap_business_agents_platform.runtime_contract import cleanup_binding
    from sap_business_agents_platform import harness
    from openai_codex.generated.v2_all import DynamicToolCallThreadItem

    from sap_business_agents_platform import codex_driver as driver_module
    broker,store,run,token=make_broker(tmp_path)
    owner=CodexHarnessController(broker.settings,store,broker)
    request,context=request_context(broker,run,token,'codex')
    request=replace(request,binding={**request.binding,'cli_version':'0.163.0'})
    context['state']['acceptance_direct_baseline']=True
    calls=[]
    def raw_request(method,params,**kwargs):
        calls.append((method,copy.deepcopy(params)))
        return SimpleNamespace(thread=SimpleNamespace(id='native-thread'))
    deny=lambda *args:{'decision':'decline'}
    class Client:
        id='native-thread'
        closed=False
        interrupted=0
        native_terminal_seen=False
        def __init__(self):
            self._client=SimpleNamespace(_sync=SimpleNamespace(request=raw_request,_approval_handler=deny))
        async def __aenter__(self):return self
        async def close(self):self.closed=True
        async def thread_start(self,**options):
            assert options['sandbox'].value=='read-only'
            assert 'sapba_broker' in options['developer_instructions']
            await asyncio.to_thread(self._client._sync.request,'thread/start',{'sandbox':'read-only'})
            return self
        async def turn(self,*args,**kwargs):return self
        async def interrupt(self):self.interrupted+=1
        async def stream(self):
            if not unknown:
                response=await asyncio.to_thread(self._client._sync._approval_handler,'item/tool/call',
                    call(tool='sap_final_report_validate',arguments={'report':report()}))
                assert response['success'] is True
            item=DynamicToolCallThreadItem.model_validate({'type':'dynamicToolCall','id':'native-call',
                'namespace':'unapproved' if unknown else 'sapba_broker','tool':'sap_final_report_validate',
                'arguments':{'report':report()},'status':'completed','success':True})
            yield SimpleNamespace(method='item/completed',payload=SimpleNamespace(item=SimpleNamespace(root=item)))
            self.native_terminal_seen=True
            raise AssertionError('Driver must stop at saved validation or deny the unregistered tool')
    client=Client()
    monkeypatch.setattr(harness,'_safe_codex',lambda *args,**kwargs:client)
    driver=driver_module.CodexHarnessDriver(owner)
    async def scenario():
        try:return await driver.harness_turn(request,context)
        finally:await driver.cleanup(run,context)
    with cleanup_binding():
        if unknown:
            with pytest.raises(RuntimeError,match='capability_isolation_failed:dynamicToolCall'):
                asyncio.run(scenario())
            assert not store.get_harness_state(run).get('validated_report')
        else:
            result=asyncio.run(scenario())
            assert result.cleanup_complete and result.output['presentation']['validation_ref']
    assert calls[0][1]['dynamicTools'][0]['name']=='sapba_broker'
    assert client.closed and not client.native_terminal_seen and not owner._active_turns
    assert client._client._sync.request is raw_request and client._client._sync._approval_handler is deny
