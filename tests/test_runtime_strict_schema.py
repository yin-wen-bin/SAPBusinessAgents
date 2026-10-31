"""Strict SDK transport and authoritative report verification, without external I/O."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from sap_business_agents_platform.runtime_contract import RuntimeContractError, RuntimeRequest
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from sap_business_agents_platform.runtime_report_contract import supported_presentation_schema
from sap_business_agents_platform.runtime_strict_schema import NativeContract, exact_json, preflight

QUERY = '查询供应商17300003截止2022/12/31的未清发票校验'


def test_incident_schema_is_rejected_and_both_terminal_versions_are_native_safe():
    with pytest.raises(RuntimeContractError) as err:
        preflight(terminal_schema(version=2))
    assert err.value.code == 'runtime_output_contract_invalid'
    assert err.value.detail['validation_issues'][0]['constraint'] == 'properties_required'
    assert 'value_bindings' in str(err.value.detail) or 'source_key' in str(err.value.detail)
    for version in (1,2):
        original=terminal_schema(version=version);before=copy.deepcopy(original)
        compiled=NativeContract(original);preflight(compiled.schema)
        assert original==before
        if version==1:assert compiled.schema==original and compiled.instructions()==''
        else:assert compiled.mapping_paths and 'native_validated_report' in compiled.instructions()


@pytest.mark.parametrize('bad', [
    {'type':'object'},
    {'type':'object','properties':{},'additionalProperties':True},
    {'type':'object','properties':{'x':{'type':'integer'}},'required':[],'additionalProperties':False},
    {'type':'array'},
])
def test_native_preflight_fails_closed(bad):
    with pytest.raises(RuntimeContractError,match='runtime_output_contract_invalid'):preflight(bad)


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_preflight_precedes_driver_connection(provider):
    if provider=='codex':
        from sap_business_agents_platform.codex_driver import CodexHarnessDriver as Driver
    else:
        from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver as Driver
    request=SimpleNamespace(output_schema=terminal_schema(version=2))
    bad=SimpleNamespace(schema={'type':'object'})
    # No owner, SDK client, supervisor or even task binding can be touched.
    with pytest.raises(RuntimeContractError,match='runtime_output_contract_invalid'):
        asyncio.run(Driver(None).harness_turn(request,{'native_output_contract':bad}))


@pytest.mark.parametrize('value',[{}, {'a':'string','b':-3,'c':None,'d':True,'e':[1,{'金额':'-4000.00'}]},
    {'escaped':'a\\b\"c','empty':[],'nested':{'x':0}}, {'甲':{'complete':False,'issues':[{'path':'/a'}]}}])
def test_unconstrained_maps_are_lossless_and_closed(value):
    codec=NativeContract({'type':'object','additionalProperties':{}})
    encoded=codec.encode(value)
    assert codec.decode(encoded)==value
    preflight(codec.schema)
    assert set(encoded[0])=={'key','value'} if encoded else True
    assert codec.read(json.dumps(encoded,ensure_ascii=False))==value


@pytest.mark.parametrize('wire', [
    [{'key':'x','value':'1'},{'key':'x','value':'2'}],
    [{'key':'x','value':'{"a":1,"a":2}'}],
    [{'key':'x','value':'NaN'}],
    [{'key':'x','value':'not JSON'}],
    [{'key':'x','value':'1','unexpected':True}],
    {'x':1},
])
def test_transport_never_repairs_duplicates_invalid_json_or_wrong_shapes(wire):
    codec=NativeContract({'type':'object','additionalProperties':{}});before=copy.deepcopy(wire)
    with pytest.raises(RuntimeContractError,match='runtime_structured_output_invalid'):codec.decode(wire)
    assert wire==before


def test_root_duplicate_keys_and_non_finite_values_are_rejected():
    for value in ('{"a":1,"a":2}', 'NaN', '{"n":Infinity}', '{"n":1e999}'):
        with pytest.raises(ValueError):exact_json(value)


async def prepared_report(tmp_path,provider):
    from tests.test_runtime_report_contract import make_broker, report
    from tests.test_shared_query_money_chain import seed,row,metric
    from tests.test_report_verification_v2 import table
    broker,store,run,token=make_broker(tmp_path,query=QUERY,runtime={
        'provider_id':provider,'sdk_id':'fixture-'+provider,'model':'fixture-model','configuration_digest':'fixture'})
    records=[row(),row(AccountingDocument='D2')];ref=seed(broker,store,run,records)
    calc=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref})
    candidate=report(schema_version='2.0',blocks=[{'type':'metrics','metrics':[metric(calc)]},table(ref,records)])
    accepted=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
    assert accepted['ok'],accepted.get('validation_issues')
    return broker,store,run,token,accepted


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_real_broker_report_roundtrip_preserves_all_proof_and_rejects_tampering(tmp_path,provider):
    async def scenario():
        broker,store,run,token,result=await prepared_report(tmp_path,provider)
        codec=NativeContract(supported_presentation_schema(strict=True))
        canonical=codec.decode(result['native_validated_report'])
        assert canonical==result['validated_report']
        assert canonical['verification']==result['verification']
        assert canonical['blocks'][1]['rows'][0]['value_bindings']['amount']['source_key']
        canonical['blocks'][1]['rows'][0]['value_bindings']['amount']['source_key']['AccountingDocument']='different'
        bad=await broker.handle(run,token,'sap_final_report_validate',{'report':canonical})
        assert not bad['ok'] and bad['validation_ref'] is None
        assert 'native_validated_report' not in bad
    asyncio.run(scenario())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_real_driver_full_terminal_with_same_question_and_synthetic_evidence(tmp_path,monkeypatch,provider):
    async def scenario():
        broker,store,run,token,accepted=await prepared_report(tmp_path,provider)
        from tests.test_workbuddy_harness_nullable import envelope
        canonical=envelope()
        for key in ('input_kind','input_field','clarification_question'):canonical.pop(key)
        canonical.update(presentation=accepted['validated_report'],evidence_refs=[
            accepted['validated_report']['blocks'][1]['evidence_refs'][0]])
        codec=NativeContract(terminal_schema(version=2));wire=codec.encode(canonical);observed=[]
        runtime=store.get_run(run).runtime
        owner=SimpleNamespace(store=store,broker=broker,settings=broker.settings,_active_turns={})
        request=RuntimeRequest(provider,'free_query','harness',{'provider_id':provider,'model':'fixture-model','reasoning_effort':'max'},
            QUERY,terminal_schema(version=2),cwd=str(tmp_path),instructions='Preserve scope and authorization.',
            permissions={'full_access':False,'acceptance_direct_baseline':False},deadline=time.monotonic()+60)
        context={'run_id':run,'capability':token,'workspace':tmp_path,'state':{'terminal_protocol':2},'turn_count':1,
            'thread_id':None,'cleanup_state':{},'native_output_contract':codec,'report_contract':broker.report_contract(run)}
        if provider=='workbuddy':
            from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver as Driver
            async def native(**kwargs):
                observed.append(kwargs);preflight(kwargs['payload']['output_schema'])
                return {'text':json.dumps(wire),'output_format':'native_json_schema','actual_model':'fixture-model'}
            owner.manager=SimpleNamespace(supervisor=SimpleNamespace(run=native,cleanup_deadlines={},reconcile=lambda:[]))
        else:
            from sap_business_agents_platform.codex_driver import CodexHarnessDriver as Driver
            import sap_business_agents_platform.harness as harness
            class Turn:
                id='fixture-turn'
                async def stream(self):
                    yield SimpleNamespace(method='item/completed',payload=SimpleNamespace(item=SimpleNamespace(
                        model_dump=lambda **_: {'type':'agentMessage','text':json.dumps(wire)})))
                    yield SimpleNamespace(method='turn/completed',payload=SimpleNamespace(turn=SimpleNamespace(status='completed')))
            class Client:
                id='fixture-thread'
                async def __aenter__(self):return self
                async def __aexit__(self,*_):pass
                async def thread_start(self,**kwargs):observed.append(kwargs);return self
                async def turn(self,prompt,**kwargs):
                    assert prompt==QUERY;observed.append(kwargs);preflight(kwargs['output_schema']);return Turn()
            monkeypatch.setattr(harness,'_safe_codex',lambda *a,**kw:Client())
            async def monitor(*a):await asyncio.Event().wait()
            owner._monitor_deadline=monitor
        result=await Driver(owner).harness_turn(request,context)
        assert result.output==canonical and result.cleanup_complete
        assert observed
        # Both real native adapters preserve the same business object and bindings.
        assert result.output['presentation']['validation_ref']==accepted['validation_ref']
        assert store.get_run(run).query==QUERY
        import os
        if os.environ.get('SAPBA_STRICT_FIXTURES'):
            from pathlib import Path
            from sap_business_agents_platform.runtime_harness_result import finalize
            outcome=finalize(broker.settings,store,broker,run,result.output,thread_id=result.session.session_id,
                turn_count=1,run_started=time.monotonic())
            from sap_business_agents_platform.models import RunResult
            final=RunResult(run_id='synthetic-'+provider,mode='free_query',query=QUERY,summary=outcome.summary,presentation=outcome.presentation,
                verification=outcome.presentation.verification,tool_calls=outcome.tool_calls,evidence=outcome.evidence)
            root=Path(os.environ['SAPBA_STRICT_FIXTURES']);root.mkdir(parents=True,exist_ok=True)
            (root/(provider+'.json')).write_text(json.dumps({'run_id':'synthetic-'+provider,'mode':'free_query',
                'status':'inconclusive','query':QUERY,'input':{},'runtime':{'provider_id':provider,'version':'offline-fixture'},
                'result':final.model_dump(mode='json'),'error':None,
                'created_at':'2026-10-30T00:00:00Z','started_at':'2026-10-30T00:00:00Z','completed_at':'2026-10-30T00:00:01Z'},
                ensure_ascii=False),encoding='utf-8')
    asyncio.run(scenario())
