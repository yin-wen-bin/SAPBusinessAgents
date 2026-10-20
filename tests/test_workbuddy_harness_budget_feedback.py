"""Platform deadline encoding only: no SDK, SAP or manufactured business output."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workbuddy_harness import budgeted_tool_response


@pytest.mark.parametrize('phase,now,remaining', [('querying', 120, 480), ('finalizing',565,35),
    ('finalizing',600,0), ('finalizing',610,0)])
def test_budget_notice_is_post_tool_and_never_extends_deadline(monkeypatch, phase, now, remaining):
    from sap_business_agents_platform import workbuddy_harness as module
    monkeypatch.setattr(module.time, 'monotonic', lambda: now)
    calls = []
    broker = SimpleNamespace(review_deadline=lambda run: calls.append(run) or {
        'deadline_phase':phase,'hard_limit_seconds':600,'elapsed_seconds':now})
    raw = {'ok':True,'evidence_ref':'saved-evidence','rows':[{'existing':1}], 'source_complete':False}
    original = copy.deepcopy(raw)
    result = budgeted_tool_response(raw,broker=broker,run_id='run',deadline=600,tool_name='sap_evidence_assess')
    assert calls == ['run'] and raw == original
    assert {k:v for k,v in result.items() if k != 'platform_time_budget'} == original
    notice = result['platform_time_budget']
    assert notice['remaining_seconds'] == remaining and notice['hard_limit_seconds'] == 600
    assert notice['extends_deadline'] is False
    assert notice['deadline_phase'] == ('deadline_exceeded' if not remaining else phase)
    assert ('instruction' in notice) == (phase=='finalizing' and remaining>0)
    assert 'saved-evidence' not in json.dumps(notice)


def test_successful_report_notice_requires_native_terminal_without_rewriting_report(monkeypatch):
    from sap_business_agents_platform import workbuddy_harness as module
    monkeypatch.setattr(module.time,'monotonic',lambda:575)
    broker = SimpleNamespace(review_deadline=lambda _: {
        'deadline_phase':'finalizing','hard_limit_seconds':600,'elapsed_seconds':575})
    raw = {'ok':True,'validation_ref':'actual-saved-reference'}
    result = budgeted_tool_response(raw,broker=broker,run_id='r',deadline=600,tool_name='sap_final_report_validate')
    assert result['validation_ref']==raw['validation_ref']
    assert 'unchanged validated report' in result['platform_time_budget']['instruction']
    assert 'not a native terminal' in result['platform_time_budget']['instruction']
    denied = budgeted_tool_response({'ok':False,'code':'acceptance_report_validation_failed'},
        broker=broker,run_id='r',deadline=600,tool_name='sap_final_report_validate')
    assert denied['ok'] is False and denied['code']=='acceptance_report_validation_failed'
    assert 'Report validation passed' not in denied['platform_time_budget']['instruction']


def test_driver_callback_delivers_budget_and_preserves_stop_and_business_payload(tmp_path, monkeypatch):
    from sap_business_agents_platform import runtime_harness as shared
    from sap_business_agents_platform.models import RuntimeSnapshot
    from sap_business_agents_platform.runtime_contract import RuntimeRequest
    from sap_business_agents_platform.workbuddy_driver import WorkBuddyHarnessDriver
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
    from sap_business_agents_platform import workbuddy_harness as module
    monkeypatch.setattr(module.time,'monotonic',lambda:565)
    raw = {'ok':False,'code':'acceptance_report_validation_failed','validation_issues':[{'path':'/records'}]}
    async def callback(*args): return raw
    monkeypatch.setattr(shared,'tool_call',callback)
    observed = []
    async def native(**kwargs):
        observed.append(await kwargs['tool_handler']('sap_final_report_validate',{}))
        raise WorkBuddyError('workbuddy_cancelled')
    runtime = RuntimeSnapshot(provider_id='workbuddy',sdk_id='codebuddy-agent-sdk',model='fixture',configuration_digest='fixture')
    store = SimpleNamespace(get_run=lambda _:SimpleNamespace(runtime=runtime),append_event=lambda *a:None,
        get_harness_state=lambda _:{'acceptance_validation_failure':{'terminal':True}})
    owner = SimpleNamespace(store=store,broker=SimpleNamespace(review_deadline=lambda _:{
        'deadline_phase':'finalizing','hard_limit_seconds':600,'elapsed_seconds':565}),
        manager=SimpleNamespace(supervisor=SimpleNamespace(run=native)))
    request = RuntimeRequest('workbuddy','acceptance_baseline','harness',runtime.model_dump(mode='json'),
        'fixture',{'type':'object'},cwd=str(tmp_path),permissions={'acceptance_direct_baseline':True},deadline=600)
    context = {'run_id':'run','state':{},'turn_count':1,'cleanup_state':{}}
    from sap_business_agents_platform.runtime_report_contract import freeze
    context['report_contract'] = freeze('run', 'offline', request.binding)
    with pytest.raises(RuntimeError,match='runtime_interrupted'):
        asyncio.run(WorkBuddyHarnessDriver(owner).harness_turn(request,context))
    assert len(observed)==1 and raw=={'ok':False,'code':'acceptance_report_validation_failed','validation_issues':[{'path':'/records'}]}
    assert observed[0]['_workbuddy_stop_after_response'] is True
    assert observed[0]['platform_time_budget']['remaining_seconds']==35
    assert observed[0]['validation_issues']==raw['validation_issues']
