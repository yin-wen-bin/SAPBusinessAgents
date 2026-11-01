"""Synthetic contract checks only. No SAP connection, SDK or model calls."""
import copy
import asyncio
import pytest

from sap_business_agents_platform import financial_classification as adapter
from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.source_semantics import classify, catalog


def fixture():
    row = dict(CompanyCode='C1', FiscalYear='2020', AccountingDocument='D1', AccountingDocumentItem='1',
               PostingDate='2020-02-01', FinancialAccountType='K', SpecialGLCode='Q')
    p = next(p for p in catalog()['sources'] if p['id'] == 'operational-account-items')
    datasets = [[{'KOART':'K','UMSKZ':'Q','UMSKS':'A','MERKP':'X','MANDT':'100'}],
                [{'DOMNAME':'BSTAT','DOMVALUE_L':'','DDTEXT':'Normal document','DDLANGUAGE':'E','AS4LOCAL':'A'},
                 {'DOMNAME':'BSTAT','DOMVALUE_L':'S','DDTEXT':'Noted items','DDLANGUAGE':'E','AS4LOCAL':'A'}],
                [{'BUKRS':'C1','BELNR':'D1','GJAHR':'2020','BSTAT':'','BUDAT':'20200201','MANDT':'100'}]]
    reads = []
    for payload, rows in zip(adapter.recipes([row]), datasets):
        raw = {'skill_id':adapter.SKILL,'read_only':True,'validated':True,'status':'complete',
            'completeness':{'source_complete':True,'paging_complete':True,'truncated':False},
            'scope':{k:copy.deepcopy(payload[k]) for k in ('source_type','object','fields','filters','max_rows')},
            'source':{'metadata_validated_at':'offline-metadata'}, 'row_count':len(rows), 'validation_issues':[],
            'artifacts':[{'type':'metadata','sha256':'synthetic-metadata'}, {'type':'output_manifest','verified':True}]}
        reads.append((raw, rows))
    return row, p, reads


def decode(row, p, reads):
    return adapter.decode([row],p,reads,evidence_ref='owned-proof',source_ref='owned-items',client='100')


def test_current_configuration_does_not_replace_posted_historical_status():
    row, p, reads = fixture()
    proof = decode(row,p,reads)
    assert proof[0]['noted_item'] is False
    assert proof[0]['current_configuration_noted_item'] is True
    assert 'valid_from' not in proof[0] and 'valid_to' not in proof[0]
    assert classify(row,p,proof)[0] == 'posted_special'
    reads[-1][1][0]['BSTAT'] = 'S'
    proof = decode(row,p,reads)
    assert classify(row,p,proof)[0] == 'noted_item'


@pytest.mark.parametrize('field,value',[('AccountingDocument','OTHER'),('AccountingDocumentItem','2'),
                                      ('CompanyCode','OTHER'),('PostingDate','2020-02-02')])
def test_document_bound_proof_cannot_classify_another_item(field,value):
    row,p,reads=fixture(); proof=decode(row,p,reads)
    assert classify({**row,field:value},p,proof)[0]=='unknown'


@pytest.mark.parametrize('mutation',['partial','scope','manifest','client','duplicate','posting_date','parked','missing_header','domain'])
def test_unproved_or_mismatched_evidence_never_produces_a_proof(mutation):
    row,p,reads=fixture()
    if mutation=='partial':reads[0][0]['completeness']['paging_complete']=False
    if mutation=='scope':reads[-1][0]['scope']['filters'][0]['value']='OTHER'
    if mutation=='manifest':reads[0][0]['artifacts']=[]
    if mutation=='client':reads[0][1][0]['MANDT']='200'
    if mutation=='duplicate':
        reads[-1][1].append(copy.deepcopy(reads[-1][1][0]));reads[-1][0]['row_count']=2
    if mutation=='posting_date':reads[-1][1][0]['BUDAT']='20200202'
    if mutation=='parked':reads[-1][1][0]['BSTAT']='V'
    if mutation=='missing_header':reads[-1][1].clear();reads[-1][0]['row_count']=0
    if mutation=='domain':reads[1][1][1]['DDTEXT']='Unreviewed meaning'
    with pytest.raises(RuntimeContractError):decode(row,p,reads)


def test_generic_current_configuration_is_still_insufficient_for_history():
    row,p,_=fixture()
    proof={'account_type':'K','code':'Q','noted_item':True,'verified':True,'evidence_ref':'current-config'}
    assert classify(row,p,[proof])[0]=='unknown'


def test_read_recipes_are_bounded_to_owned_document_keys():
    row,_,_=fixture(); recipes=adapter.recipes([row])
    assert [p['object'] for p in recipes]==['T074U','DD07T','BKPF']
    assert recipes[-1]['filters']==[adapter.eq('BUKRS','C1'),adapter.eq('GJAHR','2020'),adapter.one_of('BELNR',{'D1'})]
    assert all(p['max_rows']<=200 and not {'url','credentials','connection_profile','sql'} & p.keys() for p in recipes)


from tests.test_source_reassessment_closure import reviewed, source_read, configuration_read
from tests.test_runtime_report_contract import make_broker, report
from tests.test_report_verification_v2 import table
from tests.test_shared_query_money_chain import row as stored_row


def test_v2_coverage_consumes_fresh_proof_without_rewriting_the_read(tmp_path, reviewed):
    async def scenario():
        broker,store,run,token=make_broker(tmp_path,query='Synthetic financial report as of 2020-12-31')
        ref,original=source_read(broker,store,run)
        candidate=report(schema_version='2.0',blocks=[table(ref,[stored_row(SpecialGLCode='X')])])
        before=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        assert before['ok'] and before['verification']['coverage']['status']=='needs_evidence'
        configuration_read(broker,store,run)
        after=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate,'tool_call_id':'after-proof'})
        assert after['ok'] and after['verification']['coverage']['status']=='verified'
        assert after['source_reassessment_refs']
        assert store.list_harness_tool_calls(run)[0]==original
    asyncio.run(scenario())


def test_reviewed_supplier_invoice_role_does_not_admit_other_narrowing():
    from sap_business_agents_platform.report_scope import same_filters
    from sap_business_agents_platform.runtime_query_contract import ReadScope
    frozen=catalog();p=frozen['sources'][1]
    query='查询供应商V1截止2020/12/31的未清发票'
    scope=ReadScope(query,{})
    plan={k:p[k] for k in ('service_name','odata_version','entity_set')}
    plan['filters']=[{'field':'Supplier','operator':'eq','value':'V1'},
                     {'field':'FinancialAccountType','operator':'eq','value':'K'},
                     {'field':'PostingDate','operator':'le','value':'2020-12-31'}]
    answer={'filters':[],'categories':[]}
    assert same_filters(plan,scope,query,answer,frozen)
    assert same_filters(plan,scope,query+'，保留特别总账标识',answer,frozen)
    assert not same_filters(plan,scope,query,answer)
    for field,value in [('CompanyCode','C1'),('PostingKey','31'),('FiscalYear','2020'),('IsCleared','false')]:
        narrower=copy.deepcopy(plan);narrower['filters'].append({'field':field,'operator':'eq','value':value})
        assert not same_filters(narrower,scope,query,answer,frozen)
    other=copy.deepcopy(plan);other['filters'][1]['value']='D'
    assert not same_filters(other,scope,query,answer,frozen)
    assert not same_filters(plan,scope,query.replace('未清发票','总账关联'),answer,frozen)
    assert not same_filters(plan,scope,query+'，不要限制账户类型',answer,frozen)
    assert not same_filters(plan,scope,query+'，保留所有账户类型',answer,frozen)
    other=copy.deepcopy(plan);other['entity_set']='UNREGISTERED'
    assert not same_filters(other,scope,query,answer,frozen)
