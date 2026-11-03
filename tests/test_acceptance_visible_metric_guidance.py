import copy

import pytest

from tests.test_acceptance_projection import SPEC, PROJECTION
from sap_business_agents_platform.acceptance_projection import visible_projection_issues
from sap_business_agents_platform.runtime_report_contract import instructions, repair_feedback, tool_schema
from scripts.run_three_stage_acceptance import CanonicalTestCase, _acceptance_prompt

ROOT_IDS={'business_status','source_complete','evidence_complete','business_complete'}


def report():
    return {'blocks':[{'columns':[{'key':'document'},{'key':'amount'}],
        'rows':[{'values':[{'zh':'1','en':'1'},{'zh':'0.00','en':'0.00'}]}],
        'metrics':[{'id':'count','value':{'zh':'1','en':'1'}}]}]}


def visible_roots(value=None):
    value=value or PROJECTION
    result=report()
    for key in ROOT_IDS:
        item=value[key]
        text='null' if item is None else str(item).lower() if isinstance(item,bool) else str(item)
        result['blocks'][0]['metrics'].append({'id':key,'value':{'zh':text,'en':text}})
    return result


def test_count_alone_does_not_satisfy_root_metric_visibility():
    original=copy.deepcopy(PROJECTION)
    issues=visible_projection_issues(SPEC,PROJECTION,report())
    assert {issue['field'] for issue in issues}==ROOT_IDS
    assert all(issue['code']=='acceptance_projection_visible_metrics_mismatch' for issue in issues)
    assert all(issue['constraint']=='missing_metric' and issue['locale']=='zh' for issue in issues)
    assert PROJECTION==original
    feedback=repair_feedback(issues,acceptance=True)
    assert feedback['retry_scope']=='report_only' and feedback['extends_deadline'] is False
    assert {issue['field'] for issue in feedback['issues']}==ROOT_IDS


@pytest.mark.parametrize('key',sorted(ROOT_IDS))
@pytest.mark.parametrize('locale',['zh','en'])
def test_each_wrong_locale_value_remains_rejected(key,locale):
    candidate=visible_roots()
    next(item for item in candidate['blocks'][0]['metrics'] if item['id']==key)['value'][locale]='translated or wrong'
    issues=visible_projection_issues(SPEC,PROJECTION,candidate)
    assert len(issues)==1 and issues[0]['field']==key and issues[0]['locale']==locale
    assert issues[0]['constraint']=='canonical_value_mismatch'


def test_false_null_and_zero_are_preserved_without_claiming_completeness():
    projection={**PROJECTION,'metrics':{'count':0},'business_status':'inconclusive',
        'source_complete':False,'evidence_complete':False,'business_complete':None}
    candidate=visible_roots(projection);candidate['blocks'][0]['metrics'][0]['value']={'zh':'0','en':'0'}
    assert visible_projection_issues(SPEC,projection,candidate)==[]
    candidate['blocks'][0]['metrics']=candidate['blocks'][0]['metrics'][1:]
    assert visible_projection_issues(SPEC,projection,candidate)[0]['field']=='count'


def test_acceptance_request_tool_contract_and_feedback_explain_same_rendering_rule():
    case=CanonicalTestCase.from_dict({'schema_version':'2.0','case_id':'display-guidance','agent_id':'test',
        'question':{'zh':'测试','en':'Test'},'input':{},'business_conditions':{},'expected_grain':['document'],
        'expected_output':{'record_fields':['document'],'metric_ids':['count'],
            'minimum_primary_evidence_rows':0,'allow_empty_result':True,'evidence_scope':'complete'}})
    request=_acceptance_prompt(case,{'business_keys':['document'],'facts':['amount'],'metrics':['count']})
    texts=[request,instructions(acceptance=True),tool_schema(SPEC)['properties']['report']['description'],
        repair_feedback([{'code':'acceptance_projection_visible_metrics_mismatch'}],acceptance=True)['instruction']]
    for text in texts:
        assert 'visible metrics items' in text
        assert 'value.zh and value.en' in text and 'lowercase true/false' in text
        assert all(key in text for key in ROOT_IDS)
    assert 'visible metrics items' not in instructions(acceptance=False)
    assert 'acceptance_projection' not in tool_schema()['properties']
