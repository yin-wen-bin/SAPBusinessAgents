"""Explicit recovery is a new synthetic round, never replay of a cancelled job."""
import asyncio,copy,json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sap_business_agents_platform.engine import RunExecutionError,_stable_digest
from sap_business_agents_platform.harness import _presentation_hash
from sap_business_agents_platform.models import FreeQueryFeedback,RunCreate,RunMode,RunResult,RunStatus,RunPresentation,Completeness
from sap_business_agents_platform.runtime_terminal_contract import TerminalChecks
from sap_business_agents_platform.runtime_harness_contract import terminal_schema
from tests.test_free_query_lifecycle import coordinator

def seed(tmp_path, provider='codex'):
    owner=coordinator(tmp_path)
    runtime={'provider_id':provider,'sdk_id':'synthetic','model':'synthetic','execution_flow':'harness','configuration_digest':'synthetic-binding'}
    for rid in ('saved','stopped'):
        owner.store.create_run(rid,RunCreate(mode=RunMode.free_query,query='synthetic question'),runtime=runtime)
    report=RunPresentation.model_validate({'title':{'zh':'合成结果','en':'Synthetic result'},
        'blocks':[{'type':'notice','text':{'zh':'证据范围有限','en':'Limited evidence'},'evidence_refs':['ev_owned']}]})
    result=RunResult(run_id='saved',mode=RunMode.free_query,presentation=report,
        evidence=[{'evidence_ref':'ev_owned','source_complete':True}],thread_id='old-thread',
        completeness=Completeness(source_complete=True,business_complete=False,missing_evidence=['fixture_gap']))
    owner.store.update_run('saved',status=RunStatus.inconclusive,result_json=result,thread_id='old-thread')
    owner.store.create_free_query_session(session_id='session',run_id='saved',original_query='synthetic question',runtime=runtime,thread_id='old-thread')
    owner.store.complete_free_query_iteration('saved',thread_id='old-thread',plan_digest=None,result_digest=_stable_digest(result.model_dump(mode='json')))
    owner.store.create_free_query_iteration(session_id='session',iteration=2,run_id='stopped',parent_iteration=1,
        feedback='a question',feedback_type='business_rule',execution_action='requery',decision={})
    owner.store.update_run('stopped',status=RunStatus.cancelled,cancel_requested=True)
    owner.store.complete_free_query_iteration('stopped',thread_id=None,plan_digest=None,result_digest=None,status='cancelled')
    owner.store.update_harness_state('stopped',{'cancellation':{'status':'complete'},'cleanup_incomplete':False})
    for cid,tool,output,ref in (
        ('read','sap_query_execute',{'ok':True,'evidence_ref':'ev_owned','source_complete':True},'ev_owned'),
        ('validate','sap_final_report_validate',{'ok':True,'_validated_report':report.model_dump(mode='json'),
             'report_hash':_presentation_hash(report),'presentation_contract':'sapba.presentation-money/1',
             'validation_ref':'validation_'+_presentation_hash(report).removeprefix('sha256:')[:24]},None)):
        owner.store.begin_harness_tool_call(call_id=cid,run_id='stopped',tool_name=tool,request_hash=cid,safe_input={})
        owner.store.complete_harness_tool_call(cid,status='completed',output=output,evidence_ref=ref)
    owner._future_session_binding=lambda session:runtime
    owner._future_runtime_binding=lambda record:runtime
    owner._schedule_run=AsyncMock()
    owner._write_artifacts=lambda result:[]
    return owner

def payload(owner,kind='saved_result',**changes):
    session=owner.store.get_free_query_session('session')
    option=next(x for x in owner._free_query_recovery_options(session)['recovery_sources'] if x['kind']==kind)
    return FreeQueryFeedback.model_validate({'baseIteration':2,'feedback':'Explain the saved context only',
        'requestId':'r'*32,'recoveryIteration':option['iteration'],'recoveryKind':kind,
        'recoveryDigest':option['context_digest'],**changes})


def failed_recovery(owner, *, proof=True):
    response=asyncio.run(owner.submit_free_query_feedback('session',payload(owner)))
    rid=response['run_id']
    owner.store.update_run(rid,status=RunStatus.failed,error_json={
        'code':'free_query_presentation_revision_failed','message':'historical failure','detail':None})
    owner.store.complete_free_query_iteration(rid,thread_id=None,plan_digest=None,result_digest=None,status='failed')
    if proof:
        record=owner.store.get_run(rid)
        owner.store.update_harness_state(rid,{'recovery_cleanup':{
            'protocol':'sapba.failed-recovery-cleanup/1','status':'complete','run_id':rid,
            'error_digest':_stable_digest(record.error),
            'runtime_digest':_stable_digest(record.runtime.model_dump(mode='json'))}})
    return rid


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('kind',['saved_result','confirmed_progress'])
def test_failed_recovery_can_explicitly_continue_with_earlier_context(tmp_path,provider,kind):
    owner=seed(tmp_path,provider);rid=failed_recovery(owner)
    original=owner.store.get_run(rid).model_dump(mode='json')
    options=owner._free_query_recovery_options(owner.store.get_free_query_session('session'))
    assert options['recovery_available'] and not options['recovery_blocker']
    request=payload(owner,kind,baseIteration=3,requestId='next-request-0001')
    async def scenario():
        first=await owner.submit_free_query_feedback('session',request)
        second=await owner.submit_free_query_feedback('session',request)
        return first,second
    first,second=asyncio.run(scenario())
    assert first['run_id']==second['run_id'] and owner._schedule_run.await_count==2
    fourth=owner.store.get_free_query_iteration('session',4)
    assert fourth['parent_iteration']==3 and fourth['source_run_id'] in {'saved','stopped'}
    assert fourth['decision']['_recovery']['cancelled_run_id']=='stopped'
    assert fourth['decision']['_recovery']['previous_run_id']==rid
    assert owner.store.get_run(rid).model_dump(mode='json')==original
    assert not owner.store.list_harness_tool_calls(fourth['run_id'])


@pytest.mark.parametrize('condition',['missing_proof','changed_error','changed_runtime','pending','active','locked'])
def test_failed_recovery_keeps_cleanup_binding_and_lock_gates(tmp_path,condition):
    owner=seed(tmp_path);rid=failed_recovery(owner,proof=condition!='missing_proof')
    if condition=='changed_error':owner.store.update_run(rid,error_json={'code':'changed'})
    if condition=='changed_runtime':
        runtime=owner.store.get_run(rid).runtime.model_dump(mode='json');runtime['model']='changed'
        owner.store.update_run(rid,runtime_json=runtime)
    if condition=='pending':owner.store.update_harness_state(rid,{'cleanup_incomplete':True})
    if condition=='active':owner._active_execution_tasks[rid]=SimpleNamespace(done=lambda:False)
    if condition=='locked':owner.store.update_free_query_session('session',status='draft_created')
    options=owner._free_query_recovery_options(owner.store.get_free_query_session('session'))
    assert not options['recovery_sources']
    if condition!='locked':assert options['recovery_blocker']=='free_query_recovery_cleanup_pending'
    assert len(owner.store.list_free_query_iterations('session'))==3


def test_ordinary_failed_query_does_not_gain_recovery_permission(tmp_path):
    owner=seed(tmp_path)
    owner.store.update_run('stopped',status=RunStatus.failed)
    assert not owner._free_query_recovery_options(owner.store.get_free_query_session('session'))['recovery_available']


def test_failed_recovery_rechecks_binding_and_cleanup_at_submission(tmp_path):
    owner=seed(tmp_path);rid=failed_recovery(owner)
    request=payload(owner,baseIteration=3,requestId='explicit-next-0001')
    def reject(session):raise RunExecutionError('changed binding',code='runtime_authentication_binding_changed')
    owner._future_session_binding=reject
    with pytest.raises(RunExecutionError,match='changed binding'):
        asyncio.run(owner.submit_free_query_feedback('session',request))
    owner.store.update_harness_state(rid,{'cleanup_incomplete':True})
    with pytest.raises(RunExecutionError) as caught:
        asyncio.run(owner.submit_free_query_feedback('session',request))
    assert caught.value.code=='free_query_recovery_cleanup_pending'
    assert len(owner.store.list_free_query_iterations('session'))==3 and owner._schedule_run.await_count==1

@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('kind',['saved_result','confirmed_progress'])
def test_explicit_recovery_preserves_cancelled_history_and_has_no_read_job(tmp_path,provider,kind):
    owner=seed(tmp_path,provider); old=owner.store.get_run('stopped').model_dump(mode='json')
    request=payload(owner,kind)
    response=asyncio.run(owner.submit_free_query_feedback('session',request))
    assert response['status']=='iteration_created'
    assert owner.store.get_run('stopped').model_dump(mode='json')==old
    item=owner.store.get_free_query_iteration('session',3)
    assert item['parent_iteration']==2 and item['execution_action']=='reinterpret'
    assert item['decision']['_recovery']['kind']==kind
    assert owner.store.get_run(item['run_id']).thread_id is None
    assert owner._schedule_run.await_count==1
    assert len(owner.store.list_harness_tool_calls('stopped'))==2
    assert not owner.store.list_harness_tool_calls(item['run_id'])

def test_same_request_recovers_original_after_current_round_advanced(tmp_path):
    owner=seed(tmp_path); request=payload(owner)
    async def scenario():
        return await asyncio.gather(*(owner.submit_free_query_feedback('session',request) for _ in range(8)))
    results=asyncio.run(scenario())
    assert len({r['feedback_request_id'] for r in results})==1
    assert owner._schedule_run.await_count==1 and len(owner.store.list_free_query_iterations('session'))==3
    changed=request.model_copy(update={'feedback':'different content'})
    with pytest.raises(RunExecutionError,match='different content'):
        asyncio.run(owner.submit_free_query_feedback('session',changed))

@pytest.mark.parametrize('condition',['pending','active','binding','stale_digest'])
def test_recovery_rejects_unsafe_context_before_creating_round(tmp_path,condition):
    owner=seed(tmp_path); request=payload(owner)
    if condition=='pending': owner.store.update_harness_state('stopped',{'cleanup_incomplete':True})
    if condition=='active': owner.store.update_run('stopped',status=RunStatus.running)
    if condition=='binding':
        def reject(_):raise RunExecutionError('binding changed',code='runtime_authentication_binding_changed')
        owner._future_session_binding=reject
    if condition=='stale_digest': request=request.model_copy(update={'recovery_digest':'sha256:'+'0'*64})
    with pytest.raises(RunExecutionError):asyncio.run(owner.submit_free_query_feedback('session',request))
    assert len(owner.store.list_free_query_iterations('session'))==2
    assert not owner._schedule_run.await_count

def test_cancelled_round_is_not_misreported_as_still_running(tmp_path):
    owner=seed(tmp_path)
    with pytest.raises(RunExecutionError) as caught:owner._validate_free_query_feedback_context('session',2)
    assert caught.value.code=='free_query_recovery_required'

def test_forged_report_progress_is_not_offered(tmp_path):
    owner=seed(tmp_path)
    with owner.store._connect() as db:
        data=json.loads(db.execute("SELECT output_json FROM harness_tool_calls WHERE call_id='validate'").fetchone()[0])
        data['_validated_report']['title']['en']='tampered'
        db.execute('UPDATE harness_tool_calls SET output_json=? WHERE call_id=?',(json.dumps(data),'validate'))
    options=owner._free_query_recovery_options(owner.store.get_free_query_session('session'))
    assert [x['kind'] for x in options['recovery_sources']]==['saved_result']

@pytest.mark.parametrize('kind',['saved_result','confirmed_progress'])
def test_new_round_executes_fresh_analysis_without_sap_and_stays_inconclusive(tmp_path,kind):
    owner=seed(tmp_path); seen={}
    async def revise(**kwargs):
        seen.update(kwargs)
        return {'summary':{'zh':'离线分析','en':'Offline analysis'},'presentation':kwargs['previous_presentation'],'thread_id':'fresh-thread'}
    owner.planner=SimpleNamespace(revise_free_query_presentation=revise)
    async def scenario():
        await owner.submit_free_query_feedback('session',payload(owner,kind))
        item=owner.store.get_free_query_iteration('session',3)
        await owner._execute_free_query_reinterpret(item['run_id'],item)
        return owner.store.get_run(item['run_id'])
    record=asyncio.run(scenario())
    assert seen['thread_id'] is None and seen['recovery'] is True
    assert record.status==RunStatus.inconclusive and not record.result.completeness.business_complete
    assert record.thread_id=='fresh-thread' and record.result.presentation.validation_ref is None
    assert owner.store.get_run('stopped').status==RunStatus.cancelled
    assert owner.store.get_run('stopped').result is None


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_recovery_lifecycle_context_and_single_binding(tmp_path,provider):
    owner=seed(tmp_path,provider); seen={}; bindings=[]
    owner.store.append_event('stopped','cancellation_requested',{'reason':'user_cancelled','private':'must not leak'})
    async def revise(**kwargs):
        seen.update(kwargs)
        return {'summary':{'zh':'收到取消请求','en':'Cancellation received'},
                'presentation':kwargs['previous_presentation'],'thread_id':'new-owned-thread'}
    owner.planner=SimpleNamespace(revise_free_query_presentation=revise,
        pin_snapshot=lambda binding:nullcontext())
    def binding(record):
        bindings.append(record.run_id)
        return {'provider_id':provider,'model':'synthetic','authentication_binding_revision':0}
    owner._future_runtime_binding=binding
    async def scenario():
        await owner.submit_free_query_feedback('session',payload(owner))
        item=owner.store.get_free_query_iteration('session',3)
        await owner._execute_free_query_reinterpret(item['run_id'],item)
    asyncio.run(scenario())
    assert len(bindings)==1
    context=seen['execution_context']
    historical=context['historical_cancelled_round']
    assert historical['cancellation_reason']=='user_cancelled' and historical['browser_action']=='not_recorded'
    assert historical['status']=='cancelled' and historical['run_id']=='stopped'
    assert context['current_round']['run_id'] != historical['run_id']
    assert context['current_round']['cancel_requested'] is False
    assert context['current_round']['sap_reads_authorized'] is False
    assert 'private' not in json.dumps(context)


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('stage',['runtime_binding','runtime_invocation'])
def test_recovery_preserves_safe_cause_without_raw_error_or_retry(tmp_path,provider,stage):
    from sap_business_agents_platform.sdk_manager import SDKManagerError
    owner=seed(tmp_path,provider)
    calls=[]
    error=SDKManagerError('token=secret business row',code='runtime_authentication_binding_changed',detail={'token':'secret'})
    async def revise(**kwargs):
        calls.append(kwargs)
        raise error
    def pin(binding):
        if stage=='runtime_binding':raise error
        return nullcontext()
    owner.planner=SimpleNamespace(revise_free_query_presentation=revise,pin_snapshot=pin)
    owner._future_runtime_binding=lambda record:{'authentication_binding_revision':0}
    async def scenario():
        await owner.submit_free_query_feedback('session',payload(owner))
        item=owner.store.get_free_query_iteration('session',3)
        with pytest.raises(RunExecutionError) as caught:
            await owner._execute_free_query_reinterpret(item['run_id'],item)
        return caught.value,item['run_id']
    caught,run_id=asyncio.run(scenario())
    assert caught.code=='free_query_presentation_revision_failed'
    assert caught.detail['phase']==stage and caught.detail['cause_code']==error.code
    assert caught.detail['automatic_retry'] is False
    assert 'secret' not in json.dumps(caught.detail) and str(error) not in str(caught)
    assert len(calls)==(stage=='runtime_invocation')
    event=next(e for e in owner.store.events_after(run_id) if e.type=='feedback_reinterpretation_failed')
    assert event.data==caught.detail
    assert owner.store.get_run('stopped').status==RunStatus.cancelled

def test_handoff_rejects_missing_default_and_preserves_original_candidate():
    report=RunPresentation.model_validate({'title':{'zh':'合成','en':'Synthetic'},'blocks':[{'type':'notice','text':{'zh':'空项','en':'Empty items'}}]}).model_dump(mode='json')
    report['validation_ref']='validation_'+'a'*24
    checks=TerminalChecks(terminal_schema(version=2));checks.report_ready(1000,now=1,validated_report=report)
    value={'status':'inconclusive','intent':'fixture','summary':{'zh':'合成','en':'Synthetic'},
        'source_complete':False,'business_complete':False,'missing_evidence':['fixture'],
        'evidence_refs':[],'executed_plans':[],'presentation':copy.deepcopy(report)}
    del value['presentation']['blocks'][0]['items']; before=copy.deepcopy(value)
    result=checks.check(value,submission_id='a'*64,origin='formatter_hook',phase='native_candidate')
    assert not result['repair_feedback']['terminal_validated']
    assert any(i['path']=='/presentation/blocks/0/items' for i in result['repair_feedback']['validation_issues'])
    assert 'report_handoff' in result['repair_feedback'] and value==before
    value['presentation']=copy.deepcopy(report)
    result=checks.check(value,submission_id='b'*64,origin='formatter_hook',phase='native_candidate')
    assert result['repair_feedback']['terminal_validated']
    assert not checks.accepts_native_result(json.dumps(value))
    checks.check(value,submission_id=None,origin='native_terminal',phase='native_terminal')
    assert checks.accepts_native_result(json.dumps(value))


def test_late_analysis_reply_cannot_save_after_new_round_cancelled(tmp_path):
    owner=seed(tmp_path)
    async def scenario():
        await owner.submit_free_query_feedback('session',payload(owner))
        item=owner.store.get_free_query_iteration('session',3)
        async def revise(**kwargs):
            owner.store.update_run(item['run_id'],status=RunStatus.cancelled,cancel_requested=True)
            return {'summary':{'zh':'迟到','en':'Late'},'presentation':kwargs['previous_presentation'],'thread_id':'late'}
        owner.planner=SimpleNamespace(revise_free_query_presentation=revise)
        with pytest.raises(asyncio.CancelledError):await owner._execute_free_query_reinterpret(item['run_id'],item)
        return owner.store.get_run(item['run_id'])
    record=asyncio.run(scenario())
    assert record.status==RunStatus.cancelled and record.result is None


@pytest.mark.parametrize('provider',['codex','workbuddy'])
@pytest.mark.parametrize('recovery',[False,True])
def test_shared_planner_starts_fresh_only_for_recovery(tmp_path,provider,recovery):
    from sap_business_agents_platform.shared_planner import SharedPlanner
    events=[]
    report=RunPresentation.model_validate({'title':{'zh':'合成','en':'Synthetic'},'blocks':[]}).model_dump(mode='json')
    class Thread:
        id='fresh-thread'
        async def run(self,prompt,**kwargs):
            assert kwargs['output_schema']['type']=='object'
            if recovery:
                assert 'Recorded platform execution context' in prompt
                assert 'does not prove which browser button' in prompt
            return SimpleNamespace(final_response=json.dumps({'summary':{'zh':'合成','en':'Synthetic'},'presentation':report}))
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
        async def thread_start(self,**kwargs):events.append(('start',kwargs));return Thread()
        async def thread_resume(self,thread_id,**kwargs):events.append(('resume',thread_id));return Thread()
    def client(**kwargs):events.append(('client',kwargs));return Client()
    owner=SharedPlanner();owner._driver=SimpleNamespace(provider_id=provider,client=client)
    owner.repository_root=tmp_path;owner.model='synthetic';owner.reasoning_effort=None
    result=asyncio.run(owner.revise_free_query_presentation(thread_id='old-thread',query='synthetic',feedback='explain',
        previous_presentation=report,allowed_evidence_refs=[],completeness={},rule_results=[],recovery=recovery))
    assert events[1][0]==('start' if recovery else 'resume')
    assert events[0][1].get('evidence_only',False)==recovery
    assert ('thread_id' in result)==recovery


def test_workbuddy_recovery_transport_with_fully_mocked_supervisor(tmp_path):
    from contextlib import asynccontextmanager
    from sap_business_agents_platform.workbuddy_planner import WorkBuddyPlanner
    report=RunPresentation.model_validate({'title':{'zh':'合成','en':'Synthetic'},'blocks':[]}).model_dump(mode='json')
    calls=[]
    class Supervisor:
        @asynccontextmanager
        async def reserve(self):yield
        def pending(self):return []
        cleanup_deadlines={}
        async def run(self,**kwargs):
            calls.append(kwargs)
            assert kwargs['operation']=='revise_free_query_presentation'
            assert not kwargs['payload'].get('tools') and kwargs['tool_handler'] is None
            return {'text':json.dumps({'summary':{'zh':'收到取消请求','en':'Cancellation received'},'presentation':report}),
                    'session_id':'synthetic-native','output_format':'native_json_schema','actual_model':'synthetic'}
    owner=WorkBuddyPlanner(tmp_path,'synthetic',supervisor=Supervisor(),
        runtime_snapshot={'provider_id':'workbuddy','model':'synthetic'})
    result=asyncio.run(owner.revise_free_query_presentation(thread_id=None,query='synthetic',feedback='Why cancelled?',
        previous_presentation=report,allowed_evidence_refs=[],completeness={},rule_results=[],recovery=True,
        execution_context={'cancellation_reason':'user_cancelled','browser_action':'not_recorded'}))
    assert len(calls)==1 and result['thread_id']
    assert 'user_cancelled' in calls[0]['payload']['prompt']


@pytest.mark.parametrize('cleanup_failure',[False,True])
def test_owned_client_cleanup_proof_is_not_inferred_when_cleanup_fails(cleanup_failure):
    from sap_business_agents_platform.shared_planner import _RecoveryClient
    error=RuntimeError('safe synthetic error')
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):
            if cleanup_failure:error.detail={'cleanup_failure_code':'runtime_cleanup_incomplete'}
            return False
    async def scenario():
        with pytest.raises(RuntimeError):
            async with _RecoveryClient(Client()):raise error
    asyncio.run(scenario())
    assert (getattr(error,'detail',{}).get('cleanup_complete') is True)==(not cleanup_failure)


@pytest.mark.parametrize('cleanup_failed',[False,True])
def test_wrapped_cleanup_proof_is_preserved_but_failure_wins(tmp_path,cleanup_failed):
    from sap_business_agents_platform.runtime_contract import RuntimeContractError
    owner=seed(tmp_path)
    async def revise(**kwargs):
        inner=RuntimeContractError('runtime_report_validation_failed')
        inner.detail={'cleanup_complete':True}
        try:raise inner
        except RuntimeContractError as error:
            outer=RuntimeError('wrapped error')
            if cleanup_failed:outer.detail={'cleanup_failure_code':'runtime_cleanup_incomplete'}
            raise outer from error
    owner.planner=SimpleNamespace(revise_free_query_presentation=revise)
    async def scenario():
        await owner.submit_free_query_feedback('session',payload(owner))
        item=owner.store.get_free_query_iteration('session',3)
        with pytest.raises(RunExecutionError) as caught:
            await owner._execute_free_query_reinterpret(item['run_id'],item)
        return caught.value
    error=asyncio.run(scenario())
    assert error.detail['cleanup_complete']==(not cleanup_failed)


def test_codex_recovery_client_reuses_tool_free_policy_and_cannot_resume(tmp_path,monkeypatch):
    import tempfile
    from sap_business_agents_platform.codex_driver import CodexSDKDriver
    from sap_business_agents_platform import codex_planner
    from sap_business_agents_platform.runtime_contract import RuntimeContractError,Sandbox,ApprovalMode
    folder=tmp_path/'empty';folder.mkdir();captured=[]
    monkeypatch.setattr(tempfile,'mkdtemp',lambda **kwargs:str(folder))
    monkeypatch.setattr(codex_planner,'_agent_authoring_codex',lambda directory:captured.append(directory) or object())
    client=CodexSDKDriver(SimpleNamespace()).client(evidence_only=True)
    native=SimpleNamespace(thread_start=AsyncMock(return_value=SimpleNamespace(id='new')))
    client.active=native
    async def scenario():
        await client.thread_start(cwd=str(tmp_path/'forbidden'),sandbox=Sandbox.read_only,approval_mode=ApprovalMode.deny_all)
        with pytest.raises(RuntimeContractError,match='runtime_recovery_thread_resume_forbidden'):
            await client.thread_resume('old')
        client.cleanup_state['complete']=True
        client._owned=SimpleNamespace(__aexit__=AsyncMock(return_value=False))
        await client.__aexit__(None,None,None)
    asyncio.run(scenario())
    assert captured==[folder] and not folder.exists()
    assert native.thread_start.call_args.kwargs['cwd']==str(folder)
    assert 'No tools' in native.thread_start.call_args.kwargs['developer_instructions']
