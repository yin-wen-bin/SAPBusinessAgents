"""Explicit non-401 early authentication failure. Offline, no real account/SAP."""
import asyncio
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import time

import pytest

from sap_business_agents_platform.workbuddy_auth_signals import authentication_failure, StderrAuthenticationSignal
from sap_business_agents_platform.workbuddy_failure_watch import early_authentication_failure, trace_failure, OwnedFailureWatch
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_diagnostics import safe_native_failure
from sap_business_agents_platform.runtime_diagnostics import safe_stage_diagnostic
from tests.test_workbuddy_auth_hardening import auth_service, expected
from tests.test_workbuddy_isolation import supervisor

MESSAGE = 'Authentication required. Please use /login command to sign in to your account'


@pytest.mark.parametrize('value', [MESSAGE, 'AuthenticationError: '+MESSAGE,
                                 {'message':MESSAGE}, {'error':MESSAGE}])
def test_exact_login_error_without_http_status(value):
    assert authentication_failure(value) == 'workbuddy_authentication_required'


@pytest.mark.parametrize('value', ['User says '+MESSAGE, '"'+MESSAGE+'"',
                                 'SAP '+MESSAGE, 'The tool_result was '+MESSAGE,
                                 'Authentication required for supplier 401',
                                 {'message':'Business invoice 401','status_code':401}])
def test_no_inference_from_business_or_quoted_messages(value):
    assert authentication_failure(value) is None


def early_fixture(tmp_path, *, pid=123, write_log=True):
    clock = datetime.now(timezone.utc)
    workspace = tmp_path/'unique-run-workspace'
    workspace.mkdir(exist_ok=True)
    value = {'trace':{'traceId':'trace-owned','workerPid':pid,'startedAt':clock.isoformat(),
                     'endedAt':clock.isoformat(),'status':'error'},
             'spans':[{'traceId':'trace-owned','spanId':'agent','parentId':None,
                       'status':'error','error':'Error in agent run'}]}
    directory = tmp_path/'logs'/clock.astimezone().strftime('%Y-%m-%d')
    directory.mkdir(parents=True,exist_ok=True)
    log = directory/(workspace.name+'__fixture.log')
    at = clock.astimezone().strftime('%Y/%m/%d %H:%M:%S.%f')
    text = f'Workspace Path: {workspace}\n[{at}] [Error] [pid={pid}] [e: {MESSAGE}]  Failed to handle run result:\n'
    if write_log:
        log.write_text(text,encoding='utf-8')
    return value,workspace,log,text


def early(value, workspace, tmp_path):
    return early_authentication_failure(value,pid=123,workspace=str(workspace),
                                        launched_at=time.time()-10,log_root=tmp_path/'logs')


def test_early_failure_needs_paired_log_not_weakened_trace_binding(tmp_path):
    value,workspace,log,text = early_fixture(tmp_path,write_log=False)
    assert trace_failure(value,pid=123,expected=expected(),launched_at=time.time()-10) is None
    assert early(value,workspace,tmp_path) is None
    log.write_text(text,encoding='utf-8')
    result = early(value,workspace,tmp_path)
    assert result == {'failure_code':'workbuddy_authentication_required','failure_source':'owned_cli_trace',
                      'authentication_reason':'login_required','trace_terminal_error':True}
    assert 'http_status' not in result and 'raw' not in result


@pytest.mark.parametrize('change', ['pid','workspace','trace_id','trace_input','no_error','old_trace',
                                  'log_pid','log_time','quoted_log','log_workspace'])
def test_early_failure_rejects_incomplete_or_unrelated_ownership(tmp_path,change):
    value,workspace,log,text = early_fixture(tmp_path)
    if change == 'pid': value['trace']['workerPid'] = 456
    elif change == 'workspace': workspace = tmp_path/'other'
    elif change == 'trace_id': value['spans'][0]['traceId']='foreign'
    elif change == 'trace_input': value['spans'][0]['toolInput']='{}'
    elif change == 'no_error': value['spans'][0]['status']='ok'
    elif change == 'old_trace': value['trace']['startedAt']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    elif change == 'log_pid': log.write_text(text.replace('[pid=123]','[pid=456]'),encoding='utf-8')
    elif change == 'log_time': log.write_text(text.replace(datetime.now().strftime('%Y/%m/%d'),'2000/01/01'),encoding='utf-8')
    elif change == 'quoted_log': log.write_text(text.replace('[Error]','[Info]'),encoding='utf-8')
    elif change == 'log_workspace': log.write_text(text.replace(str(workspace),str(tmp_path/'foreign')),encoding='utf-8')
    assert early(value,workspace,tmp_path) is None


def test_trace_flush_before_log_does_not_permanently_cache_failure_absence(tmp_path,monkeypatch):
    from sap_business_agents_platform import workbuddy_failure_watch as module
    value,workspace,log,text = early_fixture(tmp_path,write_log=False)
    directory=tmp_path/'traces'/'123'; directory.mkdir(parents=True)
    (directory/'trace_fixture.json').write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(module,'process_identity',lambda _: 'same-birth')
    job=type('Job',(),{'cli_processes':lambda self,cli:{123:'same-birth'}})()
    watch=OwnedFailureWatch(job,'fixture',expected(),time.time()-10,root=tmp_path/'traces',workspace=str(workspace))
    assert watch.poll() is None
    log.write_text(text,encoding='utf-8')
    assert watch.poll()['authentication_reason'] == 'login_required'


def test_no_http_status_is_invented_or_required_for_state_sync(tmp_path):
    diagnostic=StderrAuthenticationSignal().feed(('AuthenticationError: '+MESSAGE+'\n').encode())
    assert diagnostic['authentication_reason']=='login_required' and 'http_status' not in diagnostic
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    snapshot=service.bind('workbuddy',{})
    original=copy.deepcopy(service.state['accounts']['workbuddy'])
    service.report_workbuddy_failure(snapshot,diagnostic,started_at=time.time()+1)
    account=service.state['accounts']['workbuddy']
    assert account['status']=='expired' and account['authenticated'] is None
    assert account['confirmed_binding']==original['confirmed_binding'] and account['revision']==original['revision']
    for projected in (safe_native_failure(diagnostic), safe_stage_diagnostic(diagnostic,schema={'type':'object'})):
        assert projected['authentication_reason']=='login_required' and 'http_status' not in projected


def test_explicit_login_error_is_fast_and_owned_cleanup_complete(tmp_path,monkeypatch):
    owner,snapshot=supervisor(tmp_path,monkeypatch)
    synced=[]
    owner.authentication_failed=lambda snap,detail,**kw:synced.append(detail)
    started=time.monotonic()
    with pytest.raises(WorkBuddyError) as failure:
        asyncio.run(owner.run(task_id='login-required',snapshot=snapshot,operation='review_workflow',
                             payload={'scenario':'stderr_auth_required_hang'},seconds=60))
    assert failure.value.code=='workbuddy_authentication_required'
    assert time.monotonic()-started<10
    assert len(synced)==1 and synced[0]['authentication_reason']=='login_required'
    saved=json.loads(next((owner.environment.root/'jobs').glob('*.json')).read_bytes())
    assert saved['cleanup_complete'] is True and saved['status']=='failed'
    assert 'http_status' not in saved['failure_diagnostic']


def test_malformed_trace_is_not_a_new_host_failure(tmp_path,monkeypatch):
    from sap_business_agents_platform import workbuddy_failure_watch as module
    directory=tmp_path/'123';directory.mkdir()
    (directory/'trace_bad.json').write_text('[]',encoding='utf-8')
    monkeypatch.setattr(module,'process_identity',lambda _: 'same-birth')
    job=type('Job',(),{'cli_processes':lambda self,cli:{123:'same-birth'}})()
    watch=OwnedFailureWatch(job,'fixture',expected(),time.time()-10,root=tmp_path,workspace=str(tmp_path/'workspace'))
    assert watch.poll() is None


def test_early_log_reads_obey_shared_file_and_byte_budget(tmp_path):
    value,workspace,_,_=early_fixture(tmp_path)
    assert early_authentication_failure(value,pid=123,workspace=str(workspace),launched_at=time.time()-10,
        log_root=tmp_path/'logs',budget={'bytes':1,'files':1}) is None
    assert early_authentication_failure(value,pid=123,workspace=str(workspace),launched_at=time.time()-10,
        log_root=tmp_path/'logs',budget={'bytes':8*1024*1024,'files':0}) is None
