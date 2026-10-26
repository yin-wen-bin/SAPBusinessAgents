"""Owned saved rows -> late proof -> report -> finalizer -> real comparator.

No vendor SDK, model, SAP or authentication calls. Synthetic sources only;
no production service equivalence or historical configuration is approved.
"""
import asyncio
import copy
import json
import time

import pytest

from sap_business_agents_platform import runtime_evidence_bindings as bindings
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.source_semantics import assess, public_assessment, contexts_from_run
from sap_business_agents_platform.source_reassessment import effective_assessments
from sap_business_agents_platform.runtime_harness_result import finalize
from sap_business_agents_platform.acceptance import compare_semantic_results
from tests.test_five_model_unified_repair import offline  # Includes Windows socketpair allowance, all business connects denied.
from tests.test_runtime_report_contract import make_broker, report
from tests.test_shared_query_money_chain import plan, row, metric


@pytest.fixture
def reviewed(monkeypatch):
    current = bindings.registry()
    current['configuration_sources'] = [{'service_name':'SYNTHETIC_CONFIG','odata_version':'4.0','entity_set':'History',
        'review_reference':'synthetic-only-not-production', 'fields':{k:k for k in
            ('account_type','code','noted_item','valid_from','valid_to','company_code')}}]
    monkeypatch.setattr(bindings, 'registry', lambda:copy.deepcopy(current))


def source_read(broker, store, run):
    rows = [row(SpecialGLCode='X')]
    frozen = store.get_harness_state(run)['source_semantics_catalog']
    sem = [public_assessment(assess(plan(), rows, frozen, source_complete=True))]
    raw = {'rows':rows,'source_complete':True}
    ref = broker._save_evidence(run,'sap_live',raw)
    store.begin_harness_tool_call(call_id='source',run_id=run,tool_name='sap_query_execute',request_hash=ref,safe_input={'plan':plan()})
    output = {'ok':True,'evidence_ref':ref,'source_type':'sap_live','source_complete':True,'row_count':len(rows),'source_semantics':sem}
    store.complete_harness_tool_call('source',status='completed',output=output,evidence_ref=ref)
    assert not sem[0]['business_complete']
    return ref, copy.deepcopy(store.list_harness_tool_calls(run)[0])


def configuration_read(broker, store, run, *, variant='valid', call_id='config'):
    value = {'account_type':'K','code':'X','noted_item':True,'company_code':'C1',
        'valid_from':'2019-01-01','valid_to':'2021-12-31'}
    if variant=='current':value.update(valid_from='2026-01-01',valid_to='2026-12-31')
    if variant=='company':value['company_code']='OTHER'
    if variant=='opposite':value['noted_item']=False
    rows=[value]
    raw={'rows':rows,'source_complete':True}
    ref=broker._save_evidence(run,'sap_live',raw)
    store.begin_harness_tool_call(call_id=call_id,run_id=run,tool_name='sap_query_execute',request_hash=ref,
        safe_input={'plan':{'service_name':'SYNTHETIC_CONFIG','odata_version':'4.0','entity_set':'History'}})
    store.complete_harness_tool_call(call_id,status='completed',output={'ok':True,'evidence_ref':ref,
        'source_type':'sap_live','source_complete':True,'row_count':len(rows)},evidence_ref=ref)
    return ref


def outcome(broker,store,run,validated,ref):
    return finalize(broker.settings,store,broker,run,{'status':'completed','business_complete':True,
        'source_complete':True,'missing_evidence':[],'presentation':validated['validated_report'],
        'evidence_refs':[ref],'executed_plans':[]},thread_id=None,turn_count=1,run_started=time.monotonic())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_late_proof_closes_shared_finalizer_without_rewriting_old_read(tmp_path, reviewed, provider):
    async def replay():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic source classification',runtime={
            'provider_id':provider,'sdk_id':'synthetic','configuration_digest':'frozen'})
        ref,original=source_read(broker,store,run)
        first=await broker.handle(run,token,'sap_final_report_validate',{'report':report(),'tool_call_id':'before'})
        assert first['ok'] and not outcome(broker,store,run,first,ref).business_complete
        configuration_read(broker,store,run)
        with pytest.raises(RuntimeContractError,match='source_reassessment_required'):
            broker.assert_source_reassessment_current(run)
        stale=outcome(broker,store,run,first,ref)
        assert not stale.business_complete and 'source_reassessment_required' in stale.missing_evidence
        second=await broker.handle(run,token,'sap_final_report_validate',{'report':report(),'tool_call_id':'after'})
        assert second['ok'] and second['source_reassessment_refs']
        accepted=outcome(broker,store,run,second,ref)
        assert accepted.business_complete
        calls,_=broker.snapshot(run)
        projected=contexts_from_run({'run_id':run,'result':{'tool_calls':calls}})
        assert len(projected)==1 and projected[0]['business_complete']
        assert projected[0]['record_kind_counts']['noted_item']==1
        assert store.list_harness_tool_calls(run)[0]==original
        before_calls=copy.deepcopy(calls)
        contexts_from_run({'run_id':run,'result':{'tool_calls':calls}})
        assert calls==before_calls
        repeated=await broker.handle(run,token,'sap_final_report_validate',{'report':report(),'tool_call_id':'after'})
        assert repeated['source_reassessment_refs']==second['source_reassessment_refs']
        assert len([c for c in store.list_harness_tool_calls(run) if c['tool_name']=='sap_query_execute'])==2
        configuration_read(broker,store,run,variant='company',call_id='unrelated')
        third=await broker.handle(run,token,'sap_final_report_validate',{'report':report(
            title={'zh':'新回合','en':'New report'}),'tool_call_id':'new-generation'})
        assert third['ok'] and outcome(broker,store,run,third,ref).business_complete
        old_terminal=outcome(broker,store,run,second,ref)
        assert not old_terminal.business_complete and 'source_reassessment_required' in old_terminal.missing_evidence
        # A later conflicting proof cannot use an earlier accepted report.
        configuration_read(broker,store,run,variant='opposite',call_id='conflict')
        with pytest.raises(RuntimeContractError,match='source_reassessment_required'):
            broker.assert_source_reassessment_current(run)
        assert not outcome(broker,store,run,second,ref).business_complete
    asyncio.run(replay())


@pytest.mark.parametrize('variant',['current','company'])
def test_invalid_historical_applicability_remains_blocked(tmp_path, reviewed, variant):
    async def replay():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic source classification')
        ref,original=source_read(broker,store,run)
        configuration_read(broker,store,run,variant=variant)
        result=await broker.handle(run,token,'sap_final_report_validate',{'report':report()})
        assert result['ok'] and not outcome(broker,store,run,result,ref).business_complete
        contexts,_=broker.source_reassessment(run)
        assert 'source_configuration_applicability_missing' in {i['code'] for c in contexts for i in c['issues']}
        assert store.list_harness_tool_calls(run)[0]==original
    asyncio.run(replay())


def test_receipt_tampering_cross_run_and_missing_proof_cannot_close_scope(tmp_path, reviewed):
    async def replay():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic source classification')
        source_read(broker,store,run); configuration_read(broker,store,run)
        checked=await broker.handle(run,token,'sap_final_report_validate',{'report':report()})
        assert checked['ok']
        calls,_=broker.snapshot(run)
        holder=calls[-1]['source_reassessment']; receipt=holder['receipts'][0]
        for field,value in [('run_id','other'),('before_digest','forged'),('source_plan_digest','forged'),('after',[])]:
            bad=copy.deepcopy(receipt); bad[field]=value
            contexts=effective_assessments(calls,[bad],run_id=run,binding=holder['binding'])
            assert any(i['code']=='source_reassessment_invalid' for c in contexts for i in c.get('issues',[]))
        without_config=[c for c in calls if c['call_id']!='config']
        assert any(c.get('business_complete') is False for c in effective_assessments(without_config,[receipt],run_id=run,binding=holder['binding']))
        cross=contexts_from_run({'run_id':'other','result':{'tool_calls':calls}})
        assert any(i['code']=='source_reassessment_invalid' for c in cross for i in c.get('issues',[]))
    asyncio.run(replay())


def test_sdk_close_allows_readonly_handoff_but_no_late_tools_or_receipt_publication(tmp_path, reviewed):
    async def replay():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic source classification')
        ref,_=source_read(broker,store,run);configuration_read(broker,store,run)
        checked=await broker.handle(run,token,'sap_final_report_validate',{'report':report()})
        assert checked['ok']
        original=copy.deepcopy(store.list_harness_tool_calls(run))
        broker.close_session(run)
        assert outcome(broker,store,run,checked,ref).business_complete
        assert store.list_harness_tool_calls(run)==original
        with pytest.raises(Exception) as closed:broker.source_reassessment(run)
        assert closed.value.code=='harness_run_closed'
        denied=await broker.handle(run,token,'sap_final_report_validate',{'report':report()})
        assert not denied['ok']
        store.update_run(run,cancel_requested=True)
        with pytest.raises(Exception) as cancelled:outcome(broker,store,run,checked,ref)
        assert cancelled.value.code=='harness_run_closed'
    asyncio.run(replay())


def test_independent_sources_use_own_receipts_and_real_acceptance_comparator(tmp_path, reviewed):
    async def one(path):
        broker,store,run,token=make_broker(path,query='Synthetic source classification')
        from sap_business_agents_platform.models import RunCreate, RunMode
        run = 'run_' + path.name
        store.create_run(run,RunCreate(mode=RunMode.free_query,query='Synthetic source classification'))
        token = broker.open_session(run)
        ref,original=source_read(broker,store,run)
        before=contexts_from_run({'run_id':run,'result':{'tool_calls':broker.snapshot(run)[0]}})
        configuration_read(broker,store,run)
        checked=await broker.handle(run,token,'sap_final_report_validate',{'report':report()})
        assert checked['ok'] and outcome(broker,store,run,checked,ref).business_complete
        after=contexts_from_run({'run_id':run,'result':{'tool_calls':broker.snapshot(run)[0]}})
        return before,after
    async def replay():
        baseline_old,baseline_new=await one(tmp_path/'baseline')
        candidate_old,candidate_new=await one(tmp_path/'candidate')
        definition={'version':'1.0','view':'account_items','ledgerScope':None,
            'categories':['noted_item'],'configurationTime':'posting_date'}
        contract={'business_keys':['id'],'facts':['record_kind'],'source_semantics':definition}
        def normalized(contexts):
            return {'records':[{'id':'one','record_kind':'noted_item'}],'metrics':{},'limitations':[],
                'source_complete':True,'source_semantics':contexts}
        assert compare_semantic_results(normalized(baseline_old),normalized(candidate_old),contract).verdict=='BLOCKED'
        assert compare_semantic_results(normalized(baseline_new),normalized(candidate_old),contract).verdict=='BLOCKED'
        assert compare_semantic_results(normalized(baseline_new),normalized(candidate_new),contract).verdict=='MATCH'
        # This MATCH is synthetic, not a persisted Campaign or business PASS.
    asyncio.run(replay())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_formal_report_gate_consumes_late_proof_not_old_projection_pass(tmp_path, reviewed, provider):
    async def replay():
        definition={'version':'1.0','view':'account_items','ledgerScope':None,
            'categories':['noted_item'],'configurationTime':'posting_date'}
        broker,store,run,token=make_broker(tmp_path,query='Synthetic source classification',
            runtime={'provider_id':provider,'sdk_id':'synthetic','configuration_digest':'frozen'},
            acceptance={'record_fields':['record_kind'],'source_semantics':definition})
        ref,original=source_read(broker,store,run)
        text=lambda value:{'zh':str(value),'en':str(value)}
        candidate=report(blocks=[{'type':'table','claim_scope':'customer_business_fact','evidence_refs':[ref],
            'columns':[{'key':'record_kind','label':text('Kind')}],
            'rows':[{'values':[text('noted_item')],'evidence_refs':[ref]}]},
            {'type':'metrics','claim_scope':'customer_business_fact','evidence_refs':[ref],
             'metrics':[{'id':key,'label':text(key),'value':text(value),'evidence_refs':[ref]}
                for key,value in [('business_status','normal'),('source_complete','true'),
                    ('evidence_complete','true'),('business_complete','true')]]}])
        projection={'records':[{'record_kind':'noted_item','evidence_refs':[ref]}],'metrics':{},'business_status':'normal',
            'source_complete':True,'evidence_complete':True,'business_complete':True,
            'evidence_gap_codes':[],'evidence_refs':[ref]}
        args={'report':candidate,'acceptance_projection':projection}
        failed=await broker.handle(run,token,'sap_final_report_validate',{**args,'tool_call_id':'before'})
        assert not failed['ok'] and failed['validation_ref'] is None
        assert 'source_configuration_applicability_missing' in {i['code'] for i in failed['validation_issues']}
        configuration_read(broker,store,run)
        checked=await broker.handle(run,token,'sap_final_report_validate',{**args,'tool_call_id':'after'})
        assert checked['ok'], checked
        assert outcome(broker,store,run,checked,ref).business_complete
        assert store.list_harness_tool_calls(run)[0]==original
    asyncio.run(replay())


def test_late_configuration_requires_new_calculation_not_only_new_receipt(tmp_path, reviewed):
    async def replay():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic amount report')
        ref,original=source_read(broker,store,run)
        old=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref,'tool_call_id':'old-calc'})
        assert old['ok'] and not old['business_complete']
        old_copy=copy.deepcopy(old)
        candidate=report(blocks=[{'type':'metrics','metrics':[metric(old)]}])
        first=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate,'tool_call_id':'old'})
        assert first['ok'] and not outcome(broker,store,run,first,ref).business_complete
        configuration_read(broker,store,run)
        replayed=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref,'tool_call_id':'old-calc'})
        assert replayed['calculation_ref']==old['calculation_ref'] and not replayed['business_complete']
        rejected=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate,'tool_call_id':'stale-calculation'})
        assert not rejected['ok'] and rejected['code']=='money_calculation_changed'
        new=await broker.handle(run,token,'sap_money_aggregate',{'evidence_ref':ref,'tool_call_id':'new-calc'})
        assert new['ok'] and new['business_complete'] and new['calculation_ref']!=old['calculation_ref']
        assert new['row_count']==old['row_count'] and new['source_rows_digest']==old['source_rows_digest']
        assert sorted(m['amount'] for m in new['metrics'])==sorted(m['amount'] for m in old['metrics'])
        checked=await broker.handle(run,token,'sap_final_report_validate',{'report':report(blocks=[
            {'type':'metrics','metrics':[metric(new)]}]),'tool_call_id':'new'})
        assert checked['ok'] and outcome(broker,store,run,checked,ref).business_complete
        assert store.list_harness_tool_calls(run)[0]==original and old==old_copy
        assert old['calculation_ref'] in store.get_harness_state(run)['money_calculations']
    asyncio.run(replay())
