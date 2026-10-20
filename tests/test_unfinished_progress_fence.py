"""Progress is not a cleanup/control change; no native SDK processes."""
import time
import asyncio
import hashlib
import json
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_runtime_admission_targeted import fixture, clearance
from sap_business_agents_platform import workbuddy_job_index as journal
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, WorkBuddyError
from sap_business_agents_platform.workbuddy_inspection import inspect


def running(manager):
    journal.rebuild(manager.environment, clearance=clearance)
    journal.claim(manager.environment.root, {'task_id':'progress'}, deadline=time.monotonic()+5)
    path=manager.environment.root/'jobs'/(digest('progress')+'.json')
    value=dict(task_id='progress', status='running', pid=123, process_identity='birth', cleanup_complete=False)
    atomic_json(path,value)
    return path,value


def test_progress_from_another_writer_does_not_invalidate_admission(tmp_path,monkeypatch):
    manager,_=fixture(tmp_path,monkeypatch)
    path,value=running(manager)
    with inspect(manager.environment):
        assert manager.supervisor.pending()==[]
        with ThreadPoolExecutor(1) as pool:
            pool.submit(atomic_json,path,{**value,'last_progress':{'message_count':1}}).result()
        assert manager.supervisor.pending()==[]


def test_actual_cleanup_change_still_invalidates_admission(tmp_path,monkeypatch):
    manager,_=fixture(tmp_path,monkeypatch)
    path,value=running(manager)
    with pytest.raises(WorkBuddyError,match='inspection_changed'):
        with inspect(manager.environment):
            assert manager.supervisor.pending()==[]
            with ThreadPoolExecutor(1) as pool:
                pool.submit(atomic_json,path,{**value,'status':'cleanup_pending'}).result()
    assert manager.supervisor.pending()[0]['status']=='cleanup_pending'


def test_unknown_spawn_window_not_cleared_merely_because_api_owner_exited(tmp_path,monkeypatch):
    manager,_=fixture(tmp_path,monkeypatch)
    journal.rebuild(manager.environment,clearance=clearance)
    journal.claim(manager.environment.root,{'task_id':'spawn-window'},deadline=time.monotonic()+5)
    journal.spawning(manager.environment.root,{'task_id':'spawn-window'},deadline=time.monotonic()+5)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity',lambda _:None)
    assert manager.supervisor.reconcile()[0]['status']=='dispatching'
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_job.process_identity',lambda _:None)
    with pytest.raises(WorkBuddyError,match='unresolved_job'):
        journal.rebuild(manager.environment,clearance=clearance,recover=True)


def test_owned_spawn_pid_birth_is_reconciled_not_its_api_owner(tmp_path,monkeypatch):
    manager,_=fixture(tmp_path,monkeypatch)
    journal.rebuild(manager.environment,clearance=clearance)
    journal.claim(manager.environment.root,{'task_id':'owned-window'},deadline=time.monotonic()+5)
    journal.spawning(manager.environment.root,{'task_id':'owned-window'},deadline=time.monotonic()+5)
    journal.owned(manager.environment.root,{'task_id':'owned-window'},123,'child-birth',deadline=time.monotonic()+5)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity',lambda pid:'child-birth' if pid==123 else None)
    assert manager.supervisor.reconcile()[0]['status']=='dispatching'
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.process_identity',lambda _:None)
    assert manager.supervisor.reconcile()==[] and journal.paths(manager.environment.root)==[]


def test_actual_local_worker_maintains_dispatch_and_cleanup_index(tmp_path,monkeypatch):
    from tests.test_workbuddy_isolation import supervisor
    owner,binding=supervisor(tmp_path,monkeypatch)
    journal.rebuild(owner.environment,clearance=clearance)
    result=asyncio.run(owner.run(task_id='indexed-local',snapshot=binding,operation='review_workflow',payload={},seconds=5))
    assert result and journal.paths(owner.environment.root)==[]


@pytest.mark.parametrize('scenario,code',[('success','workbuddy_job_index_recovery_required'),
    ('error_close_hang','workbuddy_upstream_empty_stream')])
def test_cleanup_index_failure_never_hides_primary_or_grants_success(tmp_path,monkeypatch,scenario,code):
    from tests.test_workbuddy_isolation import supervisor
    owner,binding=supervisor(tmp_path,monkeypatch)
    journal.rebuild(owner.environment,clearance=clearance)
    original=journal.write
    def fail_final(path,value,**kwargs):
        if value.get('cleanup_complete') is True:
            raise WorkBuddyError('workbuddy_job_index_recovery_required')
        return original(path,value,**kwargs)
    monkeypatch.setattr(journal,'write',fail_final)
    with pytest.raises(WorkBuddyError,match=code) as caught:
        asyncio.run(owner.run(task_id='indexed-error',snapshot=binding,operation='review_workflow',payload={'scenario':scenario},seconds=5))
    assert not owner.active
    if scenario!='success':
        assert caught.value.detail['cleanup_storage_failure_code']=='workbuddy_job_index_recovery_required'


@pytest.mark.parametrize('input_status',[None,'ok','success','completed'])
def test_owned_terminal_parent_error_is_not_hidden_by_successful_input(input_status):
    from sap_business_agents_platform.workbuddy_failure_watch import trace_failure
    prompt='offline-task-bound-input'
    start=datetime.now(timezone.utc)-timedelta(seconds=2)
    end=start+timedelta(seconds=1)
    value={'trace':{'traceId':'one','workerPid':123,'status':'error','startedAt':start.isoformat(),'endedAt':end.isoformat()},
        'spans':[{'traceId':'one','spanId':'root','parentId':None,'status':'error','error':'Error in agent run'},
                 {'traceId':'one','spanId':'input','parentId':'root','status':input_status,'toolInput':json.dumps({'query':'<user_query>'+prompt+'</user_query>'})}]}
    expected={'input_length':len(prompt.encode()),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
    result=trace_failure(value,pid=123,expected=expected,launched_at=start.timestamp()-1)
    assert result['failure_code']=='workbuddy_upstream_error' and 'http_status' not in result
    value['trace']['status']='completed'
    assert trace_failure(value,pid=123,expected=expected,launched_at=start.timestamp()-1) is None


def test_unrelated_error_branch_cannot_fail_a_successful_request_branch():
    from sap_business_agents_platform.workbuddy_failure_watch import trace_failure
    prompt='offline-success-branch'
    start=datetime.now(timezone.utc)-timedelta(seconds=2)
    value={'trace':{'traceId':'one','workerPid':123,'status':'error','startedAt':start.isoformat(),'endedAt':(start+timedelta(seconds=1)).isoformat()},
        'spans':[{'traceId':'one','spanId':'unrelated','parentId':None,'status':'error','error':'Error in agent run'},
                 {'traceId':'one','spanId':'input','parentId':None,'status':'ok','toolInput':json.dumps({'query':prompt})}]}
    expected={'input_length':len(prompt.encode()),'input_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
    assert trace_failure(value,pid=123,expected=expected,launched_at=start.timestamp()-1) is None
