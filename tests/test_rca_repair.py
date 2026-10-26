"""Five failure shapes through platform boundaries; synthetic, no SDK or SAP."""
import asyncio
import copy
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from sap_business_agents_platform.runtime_contract import RuntimeContractError
from sap_business_agents_platform.runtime_diagnostics import input_schema_feedback, safe_upstream_diagnostic, project_upstream_diagnostic, safe_stage_diagnostic
from sap_business_agents_platform.runtime_query_contract import plans
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks, handoff_instructions, handoff_requirements
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from sap_business_agents_platform.workbuddy_diagnostics import host_failure
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError, digest
from sap_business_agents_platform.workbuddy_failure_watch import trace_failure
from tests.test_workbuddy_isolation import supervisor
from tests.test_terminal_v2 import envelope


@pytest.mark.parametrize('version', [1, 'v1', None])
def test_query_version_requires_string_and_preserves_candidate(version):
    plan = dict(service_name='S', entity_set='Items', odata_version='2.0', schema_version=version)
    original = copy.deepcopy(plan)
    with pytest.raises(RuntimeContractError) as caught: plans(plan)
    issue = caught.value.detail['validation_issues'][0]
    assert issue['path'] == '/schema_version'
    assert issue.get('expected_type') == 'string' or issue.get('allowed_values') == ['1.0']
    assert plan == original
    plan['schema_version'] = '1.0'
    assert plans(plan)


def test_skill_feedback_is_path_specific_and_version_is_independent():
    schema = {'type':'object','required':['schema_version','fields','filters'],
        'properties':{'schema_version':{'type':'integer','const':1},
            'fields':{'type':'array','items':{'type':'string'}},
            'filters':{'type':'array','items':{'type':'object'}}},'additionalProperties':False}
    bad = {'schema_version':1,'fields':{'private-field':'private-value'},'filters':{}}
    issues = input_schema_feedback(bad,schema)
    assert {(i['path'],i['expected_type'],i['actual_type']) for i in issues} == {
        ('/fields','array','object'),('/filters','array','object')}
    assert 'private' not in json.dumps(issues)
    corrected={'schema_version':1,'fields':['Field'],'filters':[]}
    assert input_schema_feedback(corrected,schema) == []
    missing=input_schema_feedback({},schema)
    assert len(missing)==3 and all(i['actual_type']=='missing' for i in missing)


def test_real_broker_returns_skill_field_errors(tmp_path, monkeypatch):
    from tests.test_runtime_report_contract import make_broker
    broker, store, run, token = make_broker(tmp_path)
    schema={'type':'object','properties':{'fields':{'type':'array'},'filters':{'type':'array'}},'required':['fields','filters']}
    monkeypatch.setattr(broker.store,'list_harness_tool_calls',lambda _: [
        {'tool_name':n} for n in ('sap_catalog_search','sap_schema_get','sap_query_validate')])
    monkeypatch.setattr(broker,'_approved_skill',lambda _: {'input_schema':schema})
    monkeypatch.setattr(broker,'review_deadline',lambda _: {'deadline_phase':'querying'})
    def invalid(*_): raise ValidationError('private-invalid-value')
    monkeypatch.setattr(broker.skills,'validate_input',invalid,raising=False)
    result=broker._assess_evidence(run,{'skill_input':{'fields':{},'filters':{}},'missing_evidence':['synthetic']})
    assert result['ok'] is False and result['code']=='skill_input_invalid'
    assert {i['path'] for i in result['validation_issues']}=={'/fields','/filters'}
    assert not result.get('gap_token') and 'private' not in json.dumps(result)


def test_double_encoded_report_is_rejected_then_new_object_submit_passes():
    schema=terminal_schema(version=2); checks=TerminalChecks(schema)
    value=envelope()
    from tests.test_runtime_report_contract import report as make_report
    from sap_business_agents_platform.models import RunPresentation
    report=RunPresentation.model_validate(make_report()).model_dump(mode='json')
    value['presentation']=json.dumps(report)
    result=checks.check(value,submission_id='a'*64,origin='formatter_hook',phase='native_candidate')
    assert any(i['path']=='/presentation' for i in result['repair_feedback']['validation_issues'])
    assert 'never a JSON-encoded string' in result['repair_feedback']['instruction']
    assert isinstance(value['presentation'],str) and not result['stop']
    value['presentation']=report
    result=checks.check(value,submission_id='b'*64,origin='formatter_hook',phase='native_candidate')
    assert result['repair_feedback']['terminal_validated']
    assert 'Do not JSON.stringify' in handoff_instructions(schema)
    assert handoff_requirements(schema)['presentation']['type'] == ['object','null']
    assert checks.native_digest is None  # A valid candidate cannot promote itself.


@pytest.mark.parametrize('failure,label', [(sqlite3.OperationalError('private-db'),'SQLiteError'),
    (ValidationError('private-value'),'SchemaValidationError')])
def test_host_trusted_exception_diagnosis_is_safe(failure,label):
    detail=host_failure(failure,'tool_handler')
    assert detail['host_error_class']==label and len(detail['host_error_type_sha256'])==64
    projected=safe_stage_diagnostic(detail,schema=terminal_schema(version=2))
    assert projected['host_error_class']==label and projected['host_error_type_sha256']==detail['host_error_type_sha256']
    assert 'private' not in json.dumps(projected)


def test_forged_exception_name_is_not_trusted():
    Fake=type('ValidationError',(Exception,),{})
    assert host_failure(Fake('private'),'tool_handler')['host_error_class']=='unknown'


def test_platform_contract_exception_retains_trusted_code():
    failure=RuntimeContractError('runtime_plan_contract_invalid')
    result=host_failure(failure,'tool_handler')
    assert result['host_error_class']=='PlatformContractError'
    assert result['host_error_code']=='runtime_plan_contract_invalid'
    assert safe_stage_diagnostic(result,schema=terminal_schema(version=2))['host_error_code']=='runtime_plan_contract_invalid'


def test_skill_catalog_namespace_hint_does_not_authorize_execution(tmp_path,monkeypatch):
    from tests.test_runtime_report_contract import make_broker
    broker,store,run,token=make_broker(tmp_path)
    monkeypatch.setattr(broker.skills,'list_all_approved_skills',lambda:[{'skill_id':'sap-adt-table-export'}])
    result=asyncio.run(broker.handle(run,token,'tool_catalog_inspect',{'tool_id':'sap-adt-table-export'}))
    assert result['ok'] is False and result['code']=='tool_not_found'
    assert result['suggested_tool_id']=='skill:sap-adt-table-export'
    result=asyncio.run(broker.handle(run,token,'tool_catalog_inspect',{'tool_id':result['suggested_tool_id']}))
    assert result['ok'] is True and result['tool']['available'] is False
    assert result['tool']['unavailable_reason']=='skill_requires_broker_gate'


def test_step_tool_callback_failure_retains_stage_and_cleanup(tmp_path, monkeypatch):
    owner,binding=supervisor(tmp_path,monkeypatch)
    async def tool(*_): raise sqlite3.OperationalError('private-database-path')
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id='step-shape',snapshot=binding,operation='review_workflow',
            payload={'scenario':'tool'},seconds=10,tool_handler=tool))
    detail=caught.value.detail
    assert detail['host_error_class']=='SQLiteError'
    assert detail['tool_callback_phase']=='dispatching' and detail['tool_arguments_type']=='object'
    assert len(detail['tool_arguments_sha256'])==64
    saved=json.loads((owner.environment.root/'jobs'/(digest('step-shape')+'.json')).read_bytes())
    assert saved['cleanup_complete'] and not owner.active and not owner.pending()
    assert 'private' not in json.dumps(saved)


def test_nested_upstream_keeps_protocol_facts_not_secrets():
    result=safe_upstream_diagnostic({'message':'private prose','error':{
        'status_code':429,'code':'rate_limit_exceeded','request_id':'private-request-id','headers':{'authorization':'private'}}})
    result=project_upstream_diagnostic(result)
    assert result['upstream_http_status']==429 and result['upstream_category']=='rate_limited'
    assert result['upstream_request_id_sha256']==hashlib.sha256(b'private-request-id').hexdigest()
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('error', ['Error streaming response', {'status_code':503,'code':'serverOverloaded'}])
def test_trace_child_error_beats_generic_ancestor_without_cross_trace_leak(error):
    clock=datetime.now(timezone.utc).isoformat(); prompt='synthetic'
    value={'trace':{'traceId':'t','workerPid':123,'startedAt':clock,'endedAt':clock,'status':'error'},
        'spans':[{'traceId':'t','spanId':'root','status':'error','error':'Error in agent run'},
            {'traceId':'t','spanId':'input','parentId':'root','status':'completed','toolInput':json.dumps({'query':prompt})},
            {'traceId':'t','spanId':'generation','parentId':'root','status':'cancelled','error':error}]}
    result=trace_failure(value,pid=123,expected={'input_length':len(prompt),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()},launched_at=time.time()-1)
    if isinstance(error,str): assert result['upstream_phase']=='response_stream' and result['upstream_category']=='unknown'
    else: assert result['upstream_http_status']==503
    assert trace_failure(value,pid=999,expected={},launched_at=0) is None


def test_ready_native_error_wins_over_generic_trace(tmp_path, monkeypatch):
    owner,binding=supervisor(tmp_path,monkeypatch)
    release=owner.environment.release(); release['cli_version']='2.141.0'
    async def run():
        ready=asyncio.Event()
        async def watch(_):
            await ready.wait()
            return {'failure_code':'workbuddy_upstream_error','failure_source':'owned_cli_trace'}
        from sap_business_agents_platform.workbuddy_failure_watch import OwnedFailureWatch
        monkeypatch.setattr(OwnedFailureWatch,'wait',watch)
        original=owner._read
        async def read(stream):
            message=await original(stream)
            if message.get('kind')=='error':
                ready.set(); await asyncio.sleep(0)
            return message
        monkeypatch.setattr(owner,'_read',read)
        with pytest.raises(WorkBuddyError) as caught:
            await owner.run(task_id='native-race',snapshot=binding,operation='review_workflow',
                payload={'scenario':'error_close_hang'},seconds=10)
        assert caught.value.code=='workbuddy_upstream_empty_stream'
    asyncio.run(run())
    assert not owner.active and not owner.pending()
