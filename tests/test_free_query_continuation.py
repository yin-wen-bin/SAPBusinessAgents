"""Offline end-to-end continuation receipts and lifecycle facts, both Providers."""
import asyncio,copy,json
from contextlib import contextmanager,nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from tests.test_free_query_recovery import seed,payload
from sap_business_agents_platform.engine import RunExecutionError,_stable_digest,_free_query_change_summary
from sap_business_agents_platform.models import FreeQueryFeedback,RunStatus
from sap_business_agents_platform.free_query_display import project

def reviewed(owner, action='requery',provider='codex'):
    seen={}
    async def review(**kwargs):
        seen.update(kwargs)
        return {'action':action,'feedback_type':'scope_or_filter' if action=='requery' else 'presentation',
                'revised_query':kwargs['original_query'],'reason':'synthetic decision',
                'clarification_question':'Which date?' if action=='clarify' else ''}
    owner.planner=SimpleNamespace(review_free_query_feedback=review,pin_snapshot=lambda binding:nullcontext(),
                                 supports=lambda operation:True)
    return seen

@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('action',['requery','reinterpret','clarify','start_new_session'])
def test_recovery_review_does_not_force_evidence_reuse(tmp_path,provider,action):
    owner=seed(tmp_path,provider);seen=reviewed(owner,action,provider)
    old=owner.store.get_run('stopped').model_dump(mode='json')
    request=payload(owner,feedback='Rerun the original query',continuationMode='review')
    async def scenario():
        receipt=await owner.submit_free_query_feedback('session',request)
        assert receipt['status']=='queued' and owner._schedule_run.await_count==0
        await owner._execute_feedback_request_locked(receipt['feedback_request_id'])
        return owner.free_query_feedback_request('session',receipt['feedback_request_id'])
    result=asyncio.run(scenario())
    assert seen['recovery'] and seen['thread_id'] is None
    assert seen['execution_context']['current_round']['cancel_requested'] is False
    assert seen['execution_context']['historical_cancelled_round']['run_id']=='stopped'
    assert owner.store.get_run('stopped').model_dump(mode='json')==old
    assert result['decision']['_submission']['request_digest']
    if action in {'requery','reinterpret'}:
        item=owner.store.get_free_query_iteration('session',3)
        run=owner.store.get_run(item['run_id'])
        assert item['execution_action']==action and run.thread_id is None
        assert run.query=='synthetic question'
        assert owner._schedule_run.await_count==1
        assert bool(owner.store.list_free_query_evidence_links(run.run_id))==(action=='reinterpret')
    else:
        assert len(owner.store.list_free_query_iterations('session'))==2
        assert owner._schedule_run.await_count==0

def normal(owner):
    result=owner.store.get_run('saved').result.model_copy(update={'run_id':'stopped'})
    owner.store.update_run('stopped',status=RunStatus.inconclusive,cancel_requested=False,
                           result_json=result,thread_id='live-thread')
    owner.store.complete_free_query_iteration('stopped',thread_id='live-thread',plan_digest=None,
        result_digest=_stable_digest(result.model_dump(mode='json')))
    return FreeQueryFeedback(baseIteration=2,feedback='Change the wording',requestId='normal-request-0001',continuationMode='review')

def test_normal_submission_idempotency_survives_decision_and_round_advance(tmp_path):
    owner=seed(tmp_path); request=normal(owner);reviewed(owner,'reinterpret')
    async def scenario():
        receipts=await asyncio.gather(*(owner.submit_free_query_feedback('session',request) for _ in range(5)))
        assert len({r['feedback_request_id'] for r in receipts})==1
        await owner._execute_feedback_request_locked(receipts[0]['feedback_request_id'])
        return await owner.submit_free_query_feedback('session',request)
    receipt=asyncio.run(scenario())
    assert receipt['status']=='iteration_created' and len(owner.store.list_free_query_iterations('session'))==3
    assert owner._schedule_run.await_count==1
    with pytest.raises(RunExecutionError) as caught:
        asyncio.run(owner.submit_free_query_feedback('session',request.model_copy(update={'feedback':'different'})))
    assert caught.value.code=='free_query_request_conflict'

def test_iteration_commit_rolls_back_all_partial_records(tmp_path,monkeypatch):
    owner=seed(tmp_path);request=normal(owner);reviewed(owner,'reinterpret')
    receipt=asyncio.run(owner.submit_free_query_feedback('session',request))
    before=owner.store.get_free_query_session('session')
    original=owner.store.create_free_query_iteration
    def fail(**kwargs):
        original(**kwargs)
        raise RuntimeError('synthetic commit failure')
    monkeypatch.setattr(owner.store,'create_free_query_iteration',fail)
    with pytest.raises(RuntimeError,match='synthetic commit'):
        asyncio.run(owner._execute_feedback_request_locked(receipt['feedback_request_id']))
    assert owner.store.get_free_query_session('session')['current_iteration']==before['current_iteration']
    with owner.store._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==2
    assert owner._schedule_run.await_count==0

def test_recovery_context_is_fresh_and_runtime_events_are_persisted(tmp_path):
    owner=seed(tmp_path,'workbuddy');events=[]
    @contextmanager
    def bind(sink):
        events.append('bound')
        sink('agent_runtime_connected',{'private_log':'not rendered'})
        yield
    async def revise(**kw):
        assert kw['execution_context']['current_round']['status']!='cancelled'
        return {'summary':{'zh':'合成分析','en':'Synthetic analysis'},'presentation':kw['previous_presentation'],'thread_id':'fresh'}
    owner.planner=SimpleNamespace(revise_free_query_presentation=revise,pin_snapshot=lambda binding:nullcontext(),bind_events=bind)
    async def scenario():
        receipt=await owner.submit_free_query_feedback('session',payload(owner))
        item=owner.store.get_free_query_iteration_by_run(receipt['run_id'])
        await owner._execute_free_query_reinterpret(item['run_id'],item)
        return item,owner.store.get_run(item['run_id'])
    item,run=asyncio.run(scenario())
    view=project(owner.store,run,item)
    assert events==['bound'] and view['activity']['type']=='agent_runtime_connected'
    assert 'private_log' not in json.dumps(view)
    assert view['steps'][2]['state']=='skipped'
    facts=owner._free_query_execution_facts(run,item)
    assert not facts['cancel_requested'] and not facts['legacy_recovery_context']
    changes=_free_query_change_summary(owner.store,'session',3)
    assert changes['comparison_iteration']==1 and not changes['plan_changed'] and not changes['result_changed']

@pytest.mark.parametrize('tamper',['digest','cleanup','binding'])
def test_review_recovery_retains_full_submission_gates(tmp_path,tamper):
    owner=seed(tmp_path);request=payload(owner,continuationMode='review');reviewed(owner)
    if tamper=='digest':request=request.model_copy(update={'recovery_digest':'sha256:'+'0'*64})
    if tamper=='cleanup':owner.store.update_harness_state('stopped',{'cleanup_incomplete':True})
    if tamper=='binding':
        def refuse(_):raise RunExecutionError('binding changed',code='runtime_authentication_binding_changed')
        owner._future_session_binding=refuse
    with pytest.raises(RunExecutionError):asyncio.run(owner.submit_free_query_feedback('session',request))
    assert len(owner.store.list_free_query_iterations('session'))==2
    assert not owner.store.active_free_query_feedback_request('session')

def test_evidence_only_mode_rejects_new_reads(tmp_path):
    owner=seed(tmp_path);request=normal(owner).model_copy(update={'continuation_mode':'evidence_only'})
    reviewed(owner,'requery')
    receipt=asyncio.run(owner.submit_free_query_feedback('session',request))
    with pytest.raises(RunExecutionError) as caught:
        asyncio.run(owner._execute_feedback_request_locked(receipt['feedback_request_id']))
    assert caught.value.code=='free_query_evidence_only_requested'
    assert len(owner.store.list_free_query_iterations('session'))==2

def test_lost_scheduler_handoff_recovers_the_committed_run_not_another_round(tmp_path):
    owner=seed(tmp_path);request=normal(owner);reviewed(owner,'requery')
    receipt=asyncio.run(owner.submit_free_query_feedback('session',request))
    owner._schedule_run=AsyncMock(side_effect=RuntimeError('synthetic scheduler interruption'))
    asyncio.run(owner._execute_feedback_request(receipt['feedback_request_id']))
    persisted=owner.free_query_feedback_request('session',receipt['feedback_request_id'])
    assert persisted['status']=='iteration_created'
    committed=persisted['run_id']
    owner._schedule_run=AsyncMock()
    recovered=asyncio.run(owner.submit_free_query_feedback('session',request))
    assert recovered['run_id']==committed
    assert owner._schedule_run.await_count==1
    assert len(owner.store.list_free_query_iterations('session'))==3
    asyncio.run(owner.submit_free_query_feedback('session',request))
    assert owner._schedule_run.await_count==1

def test_progress_projection_cannot_assert_unobserved_sap_completion(tmp_path):
    owner=seed(tmp_path)
    run=owner.store.get_run('saved').model_copy(update={'status':RunStatus.planning})
    run.progress=run.progress.model_copy(update={'phase':'preparing_result'})
    view=project(owner.store,run,{'execution_action':'initial'})
    assert view['activity'] is None
    assert view['steps'][2]['state']=='pending'
    assert view['steps'][-1]['state']=='active'
