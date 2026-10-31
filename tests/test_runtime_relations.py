"""Composite-key relations: synthetic reviewed rules, never production aliases."""
import asyncio
import copy

import pytest

from sap_business_agents_platform import runtime_evidence_bindings as bindings
from sap_business_agents_platform.runtime_query_contract import ReadScope
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_relations import validate_rules
from tests.test_five_model_unified_repair import offline
from tests.test_runtime_report_contract import make_broker
from tests.test_shared_query_money_chain import plan,row


def rule():
    p=plan()
    return {'id':'synthetic-invoice-link','review_reference':'synthetic-test-only',
        'source':{k:p[k] for k in ('service_name','odata_version','entity_set')},
        'target':{'service_name':'SYNTHETIC_INVOICE','odata_version':'2.0','entity_set':'Invoices'},
        'source_fields':['CompanyCode','FiscalYear','OriginalReferenceDocument'],
        'target_fields':['CompanyCode','FiscalYear','SupplierInvoice'],
        'company_field':'CompanyCode','cardinality':'one_to_one','system_context_digest':'a'*64}


def setup(tmp_path,monkeypatch,provider='codex',reviewed=True):
    monkeypatch.setattr('sap_business_agents_platform.runtime_relations.context_digest',lambda _: 'a'*64)
    registry=bindings.registry()
    if reviewed:
        registry={**registry,'version':'sapba.evidence-bindings/2','relations':[rule()]}
        monkeypatch.setattr(bindings,'registry',lambda:copy.deepcopy(registry))
    broker,store,run,token=make_broker(tmp_path,query='Company code C1 supplier 17300003',runtime={
        'provider_id':provider,'sdk_id':'synthetic','configuration_digest':'synthetic'})
    scope=ReadScope('Frozen inputs',{'company_code':'C1','supplier':'17300003'})
    for entity,fields in ((rule()['source'],['CompanyCode','FiscalYear','OriginalReferenceDocument','Supplier']),
                          (rule()['target'],['CompanyCode','FiscalYear','SupplierInvoice','InvoicingParty'])):
        scope.record_schema({'ok':True,'data':{'schema_authority':True,'fields_truncated':False,
            'service':entity,'fields':[{'entity_set':entity['entity_set'],'field_name':f} for f in fields]}})
    broker.bind_read_scope(run,scope)
    p=plan();p['filters']=[{'field':'CompanyCode','value':'C1'},{'field':'Supplier','value':'17300003'}]
    p['select_fields'].extend(['Supplier','OriginalReferenceDocument'])
    rows=[row(OriginalReferenceDocument='INVOICE_A'),row(AccountingDocument='D2',FiscalYear='2021',OriginalReferenceDocument='INVOICE_B')]
    ref=broker._save_evidence(run,'sap_live',{'rows':rows,'source_complete':True})
    store.begin_harness_tool_call(call_id='relation-source',run_id=run,tool_name='sap_query_execute',request_hash='read',safe_input={'plan':p})
    store.complete_harness_tool_call('relation-source',status='completed',evidence_ref=ref,
        output={'ok':True,'evidence_ref':ref,'source_complete':True,'row_count':2})
    return broker,store,run,token,scope,ref


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_reviewed_relation_preserves_composite_tuples(tmp_path,monkeypatch,provider):
    async def scenario():
        broker,store,run,token,scope,ref=setup(tmp_path,monkeypatch,provider)
        result=await broker.handle(run,token,'sap_relation_resolve',{'relation_id':rule()['id'],'evidence_ref':ref})
        assert result['ok'],result
        target=result['target_plan'];scope.check(target)
        assert target['tuple_filters'][0]['values']==[['C1','2020','INVOICE_A'],['C1','2021','INVOICE_B']]
        before=copy.deepcopy(store.list_harness_tool_calls(run)[0])
        bad=copy.deepcopy(target);bad['tuple_filters'][0]['values']=[['C1','2020','INVOICE_B']]
        with pytest.raises(RuntimeContractError):scope.check(bad)
        bad=copy.deepcopy(target);bad['tuple_filters'][0]['values'][0][0]='OTHER'
        with pytest.raises(RuntimeContractError):scope.check(bad)
        bad=copy.deepcopy(target);bad['filters']=[{'field':'CompanyCode','value':'OTHER'}]
        with pytest.raises(RuntimeContractError):scope.check(bad)
        assert store.list_harness_tool_calls(run)[0]==before
        broker._read_scopes.pop(run)
        from sap_business_agents_platform.runtime_relations import check
        assert check(broker,run,target)
    asyncio.run(scenario())


@pytest.mark.parametrize('mutation',['missing_rule','missing_metadata','unknown_evidence','incomplete_source','source_key','stale_ref','system_context'])
def test_unproved_relations_remain_blocked(tmp_path,monkeypatch,mutation):
    async def scenario():
        broker,store,run,token,scope,ref=setup(tmp_path,monkeypatch,reviewed=mutation!='missing_rule')
        if mutation=='missing_metadata':scope.schemas.pop(tuple(rule()['target'].values()))
        if mutation=='unknown_evidence':ref='ev_'+'0'*24
        if mutation=='system_context':monkeypatch.setattr('sap_business_agents_platform.runtime_relations.context_digest',lambda _: 'b'*64)
        if mutation=='incomplete_source':
            store.complete_harness_tool_call('relation-source',status='completed',evidence_ref=ref,output={'ok':True,'evidence_ref':ref,'source_complete':False,'row_count':2})
        if mutation=='source_key':
            # A complete source without the reviewed link field cannot grant a relation.
            raw={'rows':[row(),row(AccountingDocument='D2')],'source_complete':True}
            ref=broker._save_evidence(run,'sap_live',raw)
            store.complete_harness_tool_call('relation-source',status='completed',evidence_ref=ref,output={'ok':True,'evidence_ref':ref,'source_complete':True,'row_count':2})
        result=await broker.handle(run,token,'sap_relation_resolve',{'relation_id':rule()['id'],'evidence_ref':ref})
        if mutation=='stale_ref':
            assert result['ok'];target=result['target_plan'];target['scope_relation_ref']='relation_'+'0'*64
            with pytest.raises(RuntimeContractError):scope.check(target)
        else:
            assert result['ok'] is False,result
        unreviewed={**rule()['target'],'filters':[{'field':'CompanyCode','value':'C1'},{'field':'InvoicingParty','value':'17300003'}]}
        with pytest.raises(RuntimeContractError):scope.check(unreviewed)
    asyncio.run(scenario())


def test_registry_rejects_unreviewed_or_incomplete_keys():
    r=rule();r['review_reference']=''
    with pytest.raises(ValueError):validate_rules([r])
    r=rule();r['target_fields'].remove('CompanyCode')
    with pytest.raises(ValueError):validate_rules([r])


def test_user_confirmed_subset_is_also_an_execution_boundary():
    f={'field':'SpecialGLCode','operator':'eq','value':''}
    scope=ReadScope('Frozen inputs',{'supplier':'17300003','answer_scope':{'filters':[f],'categories':['posted_normal']}})
    scope.schemas[('S','2.0','Items')]={'Supplier','SpecialGLCode'}
    plan={'service_name':'S','odata_version':'2.0','entity_set':'Items','filters':[{'field':'Supplier','value':'17300003'}]}
    with pytest.raises(RuntimeContractError):scope.check(plan)
    plan['filters'].append(f);scope.check(plan)
