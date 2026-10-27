"""Sanitized real failure timing, real broker/codec, fake SDK. No vendor or SAP I/O."""
import asyncio
import copy
import json
from pathlib import Path
import queue
import sys
import time
from types import SimpleNamespace

import pytest

from tests.test_runtime_report_contract import make_broker, report
from sap_business_agents_platform.runtime_report_contract import repair_feedback
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks, VERSION
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from sap_business_agents_platform.workbuddy_harness import budgeted_tool_response
from sap_business_agents_platform.runtime_harness_result import outcome_errors


def test_passed_report_feedback_has_no_report_retry_or_success_promotion():
    passed = repair_feedback([])
    assert passed['retry_scope'] == 'terminal_only' and passed['report_validated']
    assert 'unverified' in passed['instruction'] and 'do not claim business completeness' in passed['instruction']
    assert passed['extends_deadline'] is False
    issue = {'code':'schema_invalid','path':'/report/title'}
    failed = repair_feedback([issue])
    assert failed['retry_scope'] == 'report_only' and not failed['report_validated']
    assert failed['issues'] == [issue]


def test_unverified_warning_after_acceptance_is_not_a_new_repair_request(tmp_path):
    from tests.test_shared_query_money_chain import seed, row
    broker, store, run, token = make_broker(tmp_path,query='Synthetic amount report')
    ref=seed(broker,store,run,[row()])
    candidate=report(blocks=[{'type':'text','claim_scope':'customer_business_fact',
        'text':{'zh':'合成金额限制','en':'Synthetic amount limitation'},'evidence_refs':[ref]}])
    async def replay():
        value=await broker.handle(run,token,'sap_final_report_validate',{'report':candidate})
        assert value['ok'] and value['financial_verification']=='unverified'
        assert value['repair_feedback']['retry_scope']=='terminal_only'
        assert 'another report submission' in value['financial_verification_details']['repair_action']
        invalid=copy.deepcopy(candidate);invalid['financial_verification']='verified_metrics_only'
        rejected=await broker.handle(run,token,'sap_final_report_validate',{'report':invalid})
        assert rejected['ok'] is False and rejected['validation_ref'] is None
        issue=next(i for i in rejected['validation_issues'] if i['code']=='money_verification_claim_invalid')
        assert 'was accepted' not in issue['repair_action']
        assert 'Validate the corrected candidate' in issue['repair_action']
    asyncio.run(replay())


def test_original_timing_uses_handoff_not_hard_budget_and_never_resets(tmp_path, monkeypatch):
    fixture = json.loads((Path(__file__).parent/'fixtures/run_f43d68f262024114.handoff.json').read_bytes())
    broker, store, run, token = make_broker(tmp_path)
    clock = [fixture['first_report_elapsed']]
    monkeypatch.setattr(broker, '_elapsed_seconds', lambda _:clock[0])
    store.update_harness_state(run, {'terminal_protocol':2, 'time_budget':{
        'hard_limit_seconds':1800,'query_seconds_granted':1200,'finalization_seconds_reserved':300,
        'deadline_phase':'querying'}})
    async def replay():
        first = await broker.handle(run, token, 'sap_final_report_validate', {'report':report()})
        assert first['ok'] and first['terminal_handoff']['remaining_seconds'] == 300
        frozen = store.get_harness_state(run)['terminal_handoff_elapsed_deadline']
        clock[0] = fixture['second_report_elapsed']
        second = await broker.handle(run, token, 'sap_final_report_validate', {'report':report()})
        assert second['ok'] and second['terminal_handoff']['remaining_seconds'] == 100
        assert store.get_harness_state(run)['terminal_handoff_elapsed_deadline'] == frozen == 1277
        notice = budgeted_tool_response(second, broker=broker, run_id=run,
            deadline=time.monotonic()+623, tool_name='sap_final_report_validate')['platform_time_budget']
        assert notice['remaining_seconds'] == 100 and notice['deadline_scope'] == 'terminal_handoff'
        assert not notice['extends_deadline']
        clock[0] = 1277
        denied = await broker.handle(run, token, 'sap_final_report_validate', {'report':report()})
        assert not denied['ok'] and denied['code'] == 'harness_deadline_exceeded'
        assert store.get_harness_state(run)['terminal_handoff_elapsed_deadline'] == frozen
    asyncio.run(replay())


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('code',['runtime_structured_output_missing','runtime_structured_output_invalid','runtime_terminal_correction_stalled'])
def test_shared_failure_is_execution_handoff_not_sap_business_gap(provider, code):
    outcome = SimpleNamespace(status='inconclusive', stop_reason='terminal_handoff_failed', missing_evidence=[code])
    result = outcome_errors(outcome)
    assert result[0]['code'] == code and result[0]['category'] == 'runtime_terminal_handoff'
    assert result[0]['phase'] == 'final_handoff'
    from sap_business_agents_platform.models import HarnessResult
    assert HarnessResult(stop_reason=outcome.stop_reason).stop_reason == 'terminal_handoff_failed'
    assert outcome_errors(SimpleNamespace(status='completed')) == []


@pytest.mark.parametrize('native',[True,False])
def test_real_worker_mcp_report_to_native_terminal_without_second_report(tmp_path, monkeypatch, native):
    from sap_business_agents_platform import workbuddy_worker as worker
    from sap_business_agents_platform.workbuddy_site import make_configuration, routing
    broker, store, run, token = make_broker(tmp_path)
    store.update_harness_state(run, {'terminal_protocol':2})
    schema = terminal_schema(version=2)
    checks = TerminalChecks(schema)
    frames, replies, outputs = asyncio.Queue(), queue.Queue(), []
    options_seen, report_calls = [], []
    class Use:
        def __init__(self,value): self.name='StructuredOutput'; self.id='synthetic-native'; self.input=value
    class Assistant:
        model='synthetic-model'
        def __init__(self,content): self.content=content
    class Result:
        is_error=False; session_id='synthetic-session'; result='not-a-terminal'
        def __init__(self,value): self.structured_output=value if native else None
    def decorate(name,description,definition):
        def wrapped(handler): return SimpleNamespace(name=name,handler=handler)
        return wrapped
    class Client:
        def __init__(self,*,options): self.options=options; options_seen.append(options)
        async def __aenter__(self): return self
        async def __aexit__(self,*_): pass
        async def query(self,_): pass
        async def receive_messages(self):
            tool=self.options.mcp_servers['sapba'].tools[0]
            response=await tool.handler({'report':report()})
            assert response['isError'] is False
            passed=json.loads(response['content'][0]['text'])
            assert passed['repair_feedback']['retry_scope']=='terminal_only'
            assert passed['terminal_handoff']['remaining_seconds']<=300
            value=dict(status='inconclusive',intent='Synthetic',summary={'zh':'合成限制','en':'Synthetic limitation'},
                source_complete=False,business_complete=False,missing_evidence=['synthetic_gap'],evidence_refs=[],
                executed_plans=[],presentation=passed['validated_report'])
            # This is the simulated MODEL'S object, never a platform fallback.
            if native:
                guard=self.options.hooks['PreToolUse'][0].hooks[0]
                assert await guard({'tool_name':'StructuredOutput','tool_input':value},'synthetic-native',{}) == {}
                yield Assistant([Use(value)])
            yield Result(value)
    monkeypatch.setitem(sys.modules,'codebuddy_agent_sdk', SimpleNamespace(
        CodeBuddyAgentOptions=lambda **kw:SimpleNamespace(**kw), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type('Text',(),{}),
        ToolUseBlock=Use, ToolResultBlock=type('ToolResult',(),{}), PermissionResultAllow=SimpleNamespace,
        PermissionResultDeny=SimpleNamespace, HookMatcher=SimpleNamespace,tool=decorate,
        create_sdk_mcp_server=lambda name,tools:SimpleNamespace(tools=tools)))
    monkeypatch.setattr(worker,'read_frame',lambda: replies.get(timeout=3))
    def send(kind,**value):
        if kind in {'tool_call','schema_check'}: frames.put_nowait((kind,value))
        outputs.append((kind,value))
    async def supervisor():
        while True:
            kind,value=await frames.get()
            if kind=='tool_call':
                report_calls.append(value['tool'])
                response=await broker.handle(run,token,value['tool'],value['arguments'])
                checks.report_ready(time.monotonic()+1800,validated_report=response['validated_report'])
                replies.put({'kind':'tool_result','call_id':value['call_id'],'result':response})
            else:
                checked=checks.check(value['value'],submission_id=value.get('submission_id'),origin=value['origin'],phase=value['phase'])
                replies.put({'kind':'schema_check_result','task_id':'fixture','attempt_id':'attempt',
                    'call_id':value['call_id'],'schema_sha256':worker.fingerprint(schema),
                    'schema_valid':checked['repair_feedback']['terminal_validated'],
                    'validation_issues':checked['repair_feedback']['validation_issues'],**checked})
    async def run_worker():
        task=asyncio.create_task(supervisor())
        try:
            site=make_configuration('china',1)
            return await asyncio.wait_for(worker.execute({'operation':'free_query','mode':'bounded',
                'task_id':'fixture','attempt_id':'attempt','terminal_contract':VERSION,
                'native_output_diagnostics':worker.NATIVE_DIAGNOSTICS,'cli_path':'synthetic-only',
                'snapshot':{'model':'synthetic-model',**site},'service_site_config':routing(site),
                'payload':{'cwd':str(tmp_path),'prompt':'Synthetic','output_schema':schema,'timeout_ms':1800000,
                    'tools':[{'name':'sap_final_report_validate','description':'fixture','inputSchema':{}}]}},send),timeout=10)
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
    if native:
        result=asyncio.run(run_worker())
        assert checks.accepts_native_result(result['text'])
        assert json.loads(result['text'])['business_complete'] is False
    else:
        with pytest.raises(worker.NativeTerminalFailure,match='workbuddy_structured_output_missing'):
            asyncio.run(run_worker())
    assert report_calls == ['sap_final_report_validate']
    assert options_seen[0].max_turns==50 and options_seen[0].request_timeout_ms==1800000
    assert options_seen[0].allowed_tools==['StructuredOutput','mcp__sapba__sap_final_report_validate']
