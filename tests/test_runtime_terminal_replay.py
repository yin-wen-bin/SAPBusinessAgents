"""Safe historical shapes, real codecs/validators, and fake SDK only."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform.runtime_terminal_contract import VERSION, TerminalChecks, repair_feedback
from sap_business_agents_platform import workbuddy_worker as worker
from sap_business_agents_platform.workbuddy_diagnostics import native_output_diagnostic
from sap_business_agents_platform.workbuddy_site import make_configuration, routing
from tests.test_workbuddy_harness_nullable import schema, envelope


def digest(value): return worker.fingerprint(value)
def bad(presentation=False):
    value={**envelope(), 'input_kind':'private-illegal', 'input_field':'private-illegal'}
    if presentation: value['presentation']={'blocks':{}}
    return value


def test_historical_schema_binding_and_four_attempt_shape_replay():
    saved=json.loads((Path(__file__).parent/'fixtures/run_1768d6b67a8845d4.terminal.json').read_bytes())
    frozen=schema()
    for name in ('input_kind','input_field'): frozen['properties'][name].pop('description')
    assert digest(frozen)==saved['schema_sha256']
    checks=TerminalChecks(schema())
    counters=[]
    for index, attempt in enumerate(saved['attempts']):
        value=bad(attempt['presentation_error'])
        result=checks.check(value, submission_id=digest(index), origin='formatter_hook', phase='native_candidate')
        paths={x['path'] for x in result['repair_feedback']['validation_issues']}
        assert set(attempt['enum_errors']) <= paths
        assert ('/presentation' in paths)==attempt['presentation_error']
        count=result['convergence']['without_progress']; counters.append(count)
        observed=checks.check(value, submission_id=digest(index), origin='message_observation',phase='native_candidate')
        assert observed['convergence']['without_progress']==count
    assert counters==[1,2,1,2]  # Actual presentation repair is progress, not a fake 4-attempt cap.
    checks.check(None,submission_id=None,origin='native_terminal',phase='native_terminal')
    assert not checks.accepts_native_result(json.dumps(envelope()))
    assert {x['path'] for x in checks.last_rejected}=={'/input_kind','/input_field'}


@pytest.mark.parametrize('provider',['codex','workbuddy','synthetic'])
def test_shared_feedback_never_repairs_or_leaks_values(provider):
    value=bad(); before=copy.deepcopy(value); checks=TerminalChecks(schema())
    result=checks.check(value, submission_id=digest('submission'), origin='formatter_hook',phase='native_candidate')
    assert value==before
    fields={item['path']:item for item in result['repair_feedback']['validation_issues']}
    assert fields['/input_kind']['allowed_values']==['secure_business_reference',None]
    assert fields['/input_field']['allowed_values']==['receipt_reference',None]
    assert 'not a SAP' in fields['/input_kind']['field_instruction']
    assert 'private-illegal' not in json.dumps(result)
    assert result['repair_feedback']==repair_feedback(schema(),native_output_diagnostic(value,schema(),operation='free_query',phase='native_candidate')['validation_issues'])


def test_generic_enum_feedback_is_schema_driven_not_field_specific():
    frozen={'type':'object','properties':{'selection':{'type':['string','null'],'enum':['a',None]}},'required':['selection']}
    result=TerminalChecks(frozen).check({'selection':'private-illegal'},submission_id=digest(1),origin='formatter_hook',phase='native_candidate')
    assert result['repair_feedback']['validation_issues'][0]['allowed_values']==['a',None]
    assert 'input_kind' not in json.dumps(result)


@pytest.mark.parametrize('invalid',['null','',False,7])
def test_equivalent_schema_feedback_has_the_same_allowed_values_and_meaning(invalid):
    from scripts.workbuddy_schema_contrast import equivalent_union_schema
    canonical=schema(); wire=equivalent_union_schema(canonical)
    before={**envelope(),'input_kind':invalid,'input_field':invalid}
    outputs=[]
    for frozen in (canonical,wire):
        result=TerminalChecks(frozen).check(before,submission_id=digest(1),origin='formatter_hook',phase='native_candidate')
        outputs.append({x['path']:(x['allowed_values'],x['expected_type'],x['field_instruction'])
            for x in result['repair_feedback']['validation_issues'] if 'allowed_values' in x})
        assert not Draft202012Validator(frozen).is_valid(before)
    assert outputs[0]==outputs[1] and before['input_kind'] is invalid


def test_three_stagnant_submissions_stop_but_duplicate_checks_do_not_count():
    checks=TerminalChecks(schema())
    for index in range(3):
        call=digest(index); result=checks.check(bad(),submission_id=call,origin='formatter_hook',phase='native_candidate')
        duplicate=checks.check(bad(),submission_id=call,origin='formatter_hook',phase='native_candidate')
        assert result==duplicate and result['convergence']['without_progress']==index+1
        assert result['stop'] is (index==2)
    good=checks.check(envelope(),submission_id=digest(4),origin='formatter_hook',phase='native_candidate')
    assert good['stop']  # A late valid candidate cannot reopen a stopped attempt.
    assert not checks.accepts_native_result(json.dumps(envelope()))


def test_reused_call_with_changed_body_is_protocol_failure():
    checks=TerminalChecks(schema()); call=digest(1)
    checks.check(bad(),submission_id=call,origin='formatter_hook',phase='native_candidate')
    with pytest.raises(ValueError,match='terminal_protocol_invalid'):
        checks.check(envelope(),submission_id=call,origin='formatter_hook',phase='native_candidate')


def test_candidate_or_text_cannot_replace_native_terminal():
    checks=TerminalChecks(schema()); good=envelope()
    checks.check(good,submission_id=digest(1),origin='formatter_hook',phase='native_candidate')
    assert not checks.accepts_native_result(json.dumps(good))
    checks.check(good,submission_id=None,origin='native_terminal',phase='native_terminal')
    assert checks.accepts_native_result(json.dumps(good))
    assert not checks.accepts_native_result(json.dumps({**good,'input_kind':'null'}))
    assert not checks.accepts_native_result('ordinary text')


def test_nullable_report_exposes_nested_required_and_enum_issues_without_values():
    from sap_business_agents_platform.runtime_diagnostics import schema_issues, safe_stage_diagnostic
    frozen = schema()
    value = {**envelope(), 'presentation': {'title': {'zh': 'private-content', 'en': 'private-content'},
        'blocks': [{'type': 'private-invalid-kind'}]}}
    original = copy.deepcopy(value)
    issues = schema_issues(value, frozen)
    assert {'code': 'runtime_report_schema_invalid', 'path': '/presentation', 'constraint': 'anyOf'} in issues
    assert {'code': 'runtime_report_schema_invalid', 'path': '/presentation/schema_version', 'constraint': 'required'} in issues
    assert {'code': 'runtime_report_schema_invalid', 'path': '/presentation/blocks/0/type', 'constraint': 'enum'} in issues
    feedback = repair_feedback(frozen, issues)
    field = next(i for i in feedback['validation_issues'] if i['path'] == '/presentation/blocks/0/type')
    assert 'notice' in field['allowed_values'] and field['expected_type'] == 'string'
    safe = safe_stage_diagnostic({'failure_code': 'workbuddy_structured_output_invalid', 'validation_issues': issues}, schema=frozen)
    assert any(i['path'] == '/presentation/blocks/0/type' for i in safe['validation_issues'])
    assert 'private-content' not in json.dumps(feedback) and 'private-invalid-kind' not in json.dumps(feedback)
    assert value == original and Draft202012Validator(frozen).is_valid(value) is False


def test_ambiguous_union_does_not_guess_a_branch_and_unknown_paths_are_not_exposed():
    from sap_business_agents_platform.runtime_diagnostics import schema_issues, schema_definition
    frozen = {'anyOf': [{'type': 'object', 'properties': {'item': {'type': 'string'}}, 'required': ['item']},
        {'type': 'object', 'properties': {'item': {'type': 'integer'}}, 'required': ['item']}]}
    issues = schema_issues({'item': None, 'private-key': 'private-value'}, frozen)
    assert issues == [{'code': 'runtime_report_schema_invalid', 'path': '/', 'constraint': 'anyOf'}]
    assert schema_definition(frozen, '/item') is None and schema_definition(frozen, '/private-key') is None


def run_worker(monkeypatch,candidates,*,terminal=True,corrupt=False):
    pending=[]; options=[]; reasons=[]; errors=[]; captured=[]; checks=TerminalChecks(schema())
    class ToolUse:
        name='StructuredOutput'
        def __init__(self,value,identifier): self.input=value; self.id=identifier
    class ToolResult:
        is_error=False
        def __init__(self,identifier,content): self.tool_use_id=identifier; self.content=content
    class Assistant:
        model='offline-model'
        def __init__(self,content): self.content=content
    class User:
        def __init__(self,content): self.content=content
    class Result:
        is_error=False; session_id='private-session'; subtype='success'; stop_reason='end_turn'; result='not a valid terminal'
        def __init__(self): self.structured_output=copy.deepcopy(captured[-1]) if captured and terminal else None
    class Client:
        def __init__(self,*,options): self.options=options
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
        async def query(self,_): pass
        async def receive_messages(self):
            guard=self.options.hooks['PreToolUse'][0].hooks[0]
            for i,value in enumerate(candidates):
                call='private-call-'+str(i)
                decision=await guard({'tool_name':'StructuredOutput','tool_input':value},call,{})
                yield Assistant([ToolUse(value,call)])
                if decision:
                    reason=decision['hookSpecificOutput']['permissionDecisionReason']; reasons.append(reason)
                    yield User([ToolResult(call,[{'type':'text','text':'Error: '+reason}])])
                else:
                    captured.append(copy.deepcopy(value)); yield User([ToolResult(call,'captured')])
            yield Result()
    monkeypatch.setitem(sys.modules,'codebuddy_agent_sdk',SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kwargs: options.append(SimpleNamespace(**kwargs)) or options[-1],
        CodeBuddySDKClient=Client,AssistantMessage=Assistant,ResultMessage=Result,ToolUseBlock=ToolUse,ToolResultBlock=ToolResult,
        TextBlock=type('Text',(),{}),PermissionResultAllow=SimpleNamespace,PermissionResultDeny=SimpleNamespace,HookMatcher=SimpleNamespace))
    def send(kind,**data):
        if kind=='schema_check':
            diagnosis=native_output_diagnostic(data['value'],schema(),operation='free_query',phase=data['phase'])
            reply=checks.check(data['value'],submission_id=data.get('submission_id'),origin=data['origin'],phase=data['phase'])
            if corrupt: reply['repair_feedback']['schema_sha256']='forged'
            pending.append({'kind':'schema_check_result','task_id':'task','attempt_id':'attempt',
                'call_id':data['call_id'],'schema_sha256':digest(schema()),'schema_valid':diagnosis['schema_valid'],
                'validation_issues':diagnosis['validation_issues'],**reply})
        elif kind=='error': errors.append(data)
    monkeypatch.setattr(worker,'read_frame',lambda:pending.pop(0))
    async def execute():
        return await worker.execute({'operation':'free_query','mode':'bounded','task_id':'task','attempt_id':'attempt',
            'terminal_contract':VERSION,'native_output_diagnostics':worker.NATIVE_DIAGNOSTICS,'cli_path':'fixture-only',
            'snapshot':{'model':'offline-model',**make_configuration('china',1)},'service_site_config':routing(make_configuration('china',1)),
            'payload':{'cwd':'fixture','prompt':'synthetic','output_schema':schema(),'timeout_ms':1800000,'max_turns':50}},send)
    return execute, options, reasons, errors, checks


def test_real_worker_shared_feedback_then_model_supplied_null_succeeds(monkeypatch):
    invalid=bad(); before=copy.deepcopy(invalid)
    run,options,reasons,errors,checks=run_worker(monkeypatch,[invalid,envelope()])
    result=asyncio.run(run())
    assert json.loads(result['text'])==envelope() and not errors and invalid==before
    assert 'allowed_values' in reasons[0] and 'literal JSON null' in reasons[0] and 'private-illegal' not in reasons[0]
    assert checks.accepts_native_result(result['text'])
    assert options[0].request_timeout_ms==1800000 and options[0].max_turns==50


def test_real_worker_stagnation_signals_failure_before_close(monkeypatch):
    run,_,_,errors,checks=run_worker(monkeypatch,[bad(),bad(),bad(),envelope()])
    with pytest.raises(worker.NativeTerminalFailure,match='terminal_correction_stalled'): asyncio.run(run())
    assert errors[0]['code']=='workbuddy_terminal_correction_stalled'
    assert checks.state['without_progress']==3


@pytest.mark.parametrize('candidates',[[bad()],[envelope()]])
def test_real_worker_missing_terminal_never_promotes_candidates(monkeypatch,candidates):
    run,_,_,_,_=run_worker(monkeypatch,candidates,terminal=False)
    with pytest.raises(worker.NativeTerminalFailure,match='structured_output_missing'): asyncio.run(run())


def test_real_worker_rejects_wrong_feedback_binding(monkeypatch):
    run,_,_,errors,_=run_worker(monkeypatch,[bad()],corrupt=True)
    with pytest.raises(worker.NativeTerminalFailure,match='terminal_protocol_invalid'): asyncio.run(run())
    assert errors[0]['code']=='workbuddy_terminal_protocol_invalid'


@pytest.mark.parametrize('scenario,code',[
    ('terminal_stalled','workbuddy_terminal_correction_stalled'),
    ('terminal_empty','workbuddy_structured_output_missing'),
    ('terminal_candidate_only','workbuddy_structured_output_missing'),
    ('terminal_spoof','workbuddy_structured_output_missing'),
    ('terminal_old_worker','workbuddy_handshake_mismatch'),
    ('terminal_valid',None)])
def test_owned_supervisor_negotiation_convergence_and_cleanup(tmp_path,monkeypatch,scenario,code):
    from tests.test_workbuddy_isolation import supervisor
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    owner,binding=supervisor(tmp_path,monkeypatch)
    frozen={'type':'object','additionalProperties':False,'properties':{'answer':{'type':'string','enum':['allowed']}},'required':['answer']}
    async def execute():
        return await owner.run(task_id='terminal-test',snapshot=binding,operation='free_query',seconds=10,
            payload={'scenario':scenario,'output_schema':frozen,'terminal_contract':VERSION})
    if code:
        with pytest.raises(WorkBuddyError,match=code) as error: asyncio.run(execute())
        if scenario=='terminal_empty':
            assert error.value.detail['last_output_check']['output_type']=='null'
            assert error.value.detail['last_rejected_candidate']['validation_issues'][0]['path']=='/answer'
    else:
        assert json.loads(asyncio.run(execute())['text'])=={'answer':'allowed'}
    record=json.loads(next((owner.environment.root/'jobs').glob('*.json')).read_bytes())
    assert record['cleanup_complete'] is True and not owner.pending() and not owner.active
    assert (record['status']=='completed') is (code is None)
    if scenario=='terminal_stalled': assert record['terminal_convergence']['without_progress']==3
    assert 'private-illegal' not in json.dumps(record)


def test_schema_constraints_remain_identical_except_clarifying_descriptions():
    before=schema(); after=copy.deepcopy(before)
    for field in ('input_kind','input_field'): before['properties'][field].pop('description')
    original=Draft202012Validator(before); current=Draft202012Validator(after)
    for field in ('input_kind','input_field'):
        for value in [None,'null','',False,1,{},[],*before['properties'][field]['enum']]:
            raw={**envelope(),field:value}
            assert original.is_valid(raw)==current.is_valid(raw)
    assert original.is_valid(envelope()) and current.is_valid(envelope())


@pytest.mark.parametrize('code',['workbuddy_terminal_correction_stalled','workbuddy_structured_output_missing'])
def test_shared_harness_preserves_specific_terminal_cause_not_generic_runtime_unavailable(tmp_path,code):
    from tests.test_runtime_report_contract import make_broker
    from sap_business_agents_platform.runtime_harness import run as harness_run
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    broker,store,run,_=make_broker(tmp_path,runtime={'provider_id':'workbuddy','sdk_id':'offline','model':'offline-model',
        'configuration_digest':'offline-frozen'})
    owner=SimpleNamespace(settings=broker.settings,store=store,broker=broker)
    class Driver:
        provider_id='workbuddy'; engineering_copy=False
        def binding(self,*_): return store.get_run(run).runtime.model_dump(mode='json')
        async def harness_turn(self,request,context):
            error=WorkBuddyError(code)
            error.detail={'terminal_contract':VERSION,'terminal_convergence':{'without_progress':3},
                'last_rejected_candidate':{'validation_issues':[{'code':'runtime_report_schema_invalid','path':'/input_kind','constraint':'enum'}]}}
            raise error
        async def cleanup(self,run_id,context): context['cleanup_state']['complete']=True
    outcome=asyncio.run(harness_run(owner,Driver(),run,'Synthetic offline report'))
    assert outcome.status=='inconclusive' and not outcome.business_complete
    assert outcome.missing_evidence==[code.replace('workbuddy_','runtime_',1)]
    assert 'harness_runtime_unavailable' not in outcome.missing_evidence
