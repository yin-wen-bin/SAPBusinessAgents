"""Offline auth renewal, silent CLI failure and current-account synchronization."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService, error
from sap_business_agents_platform.workbuddy_auth_signals import authentication_failure, StderrAuthenticationSignal
from sap_business_agents_platform.workbuddy_auth_context import clean_environment, observe
from sap_business_agents_platform.workbuddy_failure_watch import OwnedFailureWatch, trace_failure
from sap_business_agents_platform.workbuddy_environment import WorkBuddyError
from sap_business_agents_platform.workbuddy_site import make_configuration
from tests.test_runtime_authentication import Adapter
from tests.test_workbuddy_isolation import supervisor

ROOT = Path(__file__).resolve().parents[1]


class ObservedAdapter(Adapter):
    def __init__(self):
        super().__init__()
        self.stamp = {'context_digest': 'profile-and-source', 'cache_digest': 'initial'}
        self.site = make_configuration('china', 1)
        self.rejection = None
        self.change_context = False

    def context(self): return dict(self.site)
    def observation(self): return dict(self.stamp)

    async def check(self, *, context=None):
        self.calls.append('check')
        if self.rejection:
            raise error(self.rejection)
        if self.change_context:
            self.stamp['context_digest'] = 'other-profile-or-code'
        return {'authenticated': True, 'identity': self.identity, 'method': 'browser', 'source': 'official_cli'}

    async def execute(self, action, event, *, context=None):
        return await self.check(context=context)


def auth_service(tmp_path):
    adapter, changed = ObservedAdapter(), []
    service = RuntimeAuthenticationService(tmp_path, {'workbuddy': adapter, 'codex': Adapter()},
        changed=lambda p, a: changed.append((p, a['changed'])))
    return service, adapter, changed


@pytest.mark.parametrize('value,scope,expected', [
    ('AuthenticationError: HTTP 401 token=private', False, 'workbuddy_authentication_required'),
    ({'status_code': 401}, True, 'workbuddy_authentication_required'),
    ('TokenRefreshError: private', False, 'workbuddy_authentication_refresh_failed'),
    ('ERROR SAP authentication HTTP 401', False, None),
    ('Invoice 401', False, None),
    ('rate limit HTTP 429', True, None),
    ({'status_code': 401}, False, None),
])
def test_safe_auth_signal_classification(value, scope, expected):
    assert authentication_failure(value, authentication_scope=scope) == expected


def test_stderr_chunking_secrets_and_business_rows():
    reader = StderrAuthenticationSignal()
    assert reader.feed(b'AuthenticationError: HTTP 4') is None
    value = reader.feed(b'01 token=must-not-save\n')
    assert value == {'failure_code': 'workbuddy_authentication_required', 'failure_source': 'owned_worker_stderr', 'http_status': 401}
    assert 'must-not-save' not in json.dumps(value)
    assert StderrAuthenticationSignal().feed(b'{"message":"AuthenticationError: HTTP 401"}\n') is None
    assert StderrAuthenticationSignal().feed(b'ERROR SAP authentication HTTP 401\n') is None
    reader = StderrAuthenticationSignal()
    assert reader.feed(b'x' * 40000) is None
    assert reader.feed(b'AuthenticationError: HTTP 401\n') is None
    assert reader.feed(b'AuthenticationError: HTTP 401\n')['http_status'] == 401


def test_same_account_renewal_rechecks_without_revision_or_quality_change(tmp_path):
    service, adapter, changed = auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    original = service.bind('workbuddy', {})
    before = service.path.read_bytes()
    adapter.stamp['cache_digest'] = 'renewed-token'
    assert service.view('workbuddy')['authenticated'] is None
    assert service.path.read_bytes() == before and adapter.calls == ['check']
    async def use():
        async with service.use('workbuddy', original): pass
    asyncio.run(use())
    assert service.bind('workbuddy', {}) == original
    assert service.view('workbuddy')['authenticated'] is True
    assert adapter.calls == ['check', 'check'] and changed == [('workbuddy', False), ('workbuddy', False)]


@pytest.mark.parametrize('code,status', [('workbuddy_authentication_required', 'expired'),
                                      ('workbuddy_authentication_refresh_failed', 'check_failed')])
def test_renewal_failure_preserves_last_success_and_blocks_dispatch(tmp_path, code, status):
    service, adapter, changed = auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    original = copy.deepcopy(service.state['accounts']['workbuddy'])
    adapter.stamp['cache_digest'] = 'changed'
    adapter.rejection = code
    calls = []
    async def run():
        with pytest.raises(Exception) as caught:
            async with service.use('workbuddy', service.bind('workbuddy', {})):
                calls.append('must-not-dispatch')
        assert caught.value.code == 'runtime_authentication_check_required'
    asyncio.run(run())
    account = service.state['accounts']['workbuddy']
    assert account['status'] == status and account['authenticated'] is None
    assert account['last_successful_check'] == original['last_successful_check']
    assert account['confirmed_binding'] == original['confirmed_binding']
    assert account['revision'] == original['revision'] and calls == []


def test_external_account_change_blocks_old_snapshot(tmp_path):
    service, adapter, _ = auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    old = service.bind('workbuddy', {})
    adapter.identity = 'different@example.test'
    adapter.stamp['cache_digest'] = 'new-login'
    async def run():
        with pytest.raises(Exception) as caught:
            async with service.use('workbuddy', old): pytest.fail('old account dispatched')
        assert caught.value.code == 'runtime_authentication_binding_changed'
    asyncio.run(run())
    assert service.state['accounts']['workbuddy']['revision'] == old['authentication_binding_revision'] + 1


def test_context_changed_during_check_is_not_login_success(tmp_path):
    service, adapter, _ = auth_service(tmp_path)
    adapter.change_context = True
    result = asyncio.run(service.check('workbuddy'))
    assert result['authenticated'] is None and result['failure_code'] == 'runtime_authentication_context_changed'


def test_owned_401_sync_is_fenced_against_old_job_or_sap_error(tmp_path):
    service, adapter, _ = auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    snapshot = service.bind('workbuddy', {})
    diagnostic = {'failure_code': 'workbuddy_authentication_required', 'http_status': 401, 'failure_source': 'owned_worker_stderr'}
    before = service.path.read_bytes()
    service.report_workbuddy_failure(snapshot, diagnostic, started_at=0)
    assert service.path.read_bytes() == before  # A newer official check wins.
    service.report_workbuddy_failure(snapshot, {**diagnostic, 'failure_source': 'sap_query_execute'}, started_at=time.time()+1)
    assert service.path.read_bytes() == before
    service.report_workbuddy_failure({**snapshot, 'authentication_binding_revision': -1}, diagnostic, started_at=time.time()+1)
    assert service.path.read_bytes() == before
    service.report_workbuddy_failure(snapshot, diagnostic, started_at=time.time()+1)
    assert service.view('workbuddy')['status'] == 'expired'
    assert service.state['accounts']['workbuddy']['revision'] == snapshot['authentication_binding_revision']


def test_codex_does_not_observe_workbuddy_cache_or_recheck(tmp_path):
    service, adapter, _ = auth_service(tmp_path)
    service._observation = lambda provider: pytest.fail('Codex must not read WorkBuddy observation')
    async def run():
        async with service.use('codex', service.bind('codex', {})): pass
    asyncio.run(run())
    assert service.adapters['codex'].calls == [] and adapter.calls == []


def trace(prompt='fixture prompt', pid=123, error_text='AuthenticationError: HTTP 401 secret=do-not-save'):
    clock = datetime.now(timezone.utc).isoformat()
    return {'trace': {'traceId':'trace-fixture','workerPid': pid, 'startedAt': clock, 'endedAt': clock, 'status': 'error'},
            'spans': [{'traceId':'trace-fixture','spanId':'input','parentId':None,'status': 'error', 'error': error_text,
                       'toolInput': json.dumps({'query': '<user_query>' + prompt + '</user_query>'})}]}


def expected(prompt='fixture prompt'):
    return {'input_length': len(prompt.encode()), 'input_sha256': hashlib.sha256(prompt.encode()).hexdigest()}


def test_terminal_trace_requires_owned_pid_prompt_time_and_error():
    value = trace()
    result = trace_failure(value, pid=123, expected=expected(), launched_at=time.time()-1)
    assert result['http_status'] == 401 and 'secret' not in json.dumps(result)
    assert trace_failure(value, pid=456, expected=expected(), launched_at=0) is None
    assert trace_failure(value, pid=123, expected=expected('other'), launched_at=0) is None
    assert trace_failure(value, pid=123, expected=expected(), launched_at=time.time()+1) is None
    value['trace']['status'] = 'running'
    assert trace_failure(value, pid=123, expected=expected(), launched_at=0) is None
    generic = trace_failure(trace(error_text='Error streaming response'), pid=123, expected=expected(), launched_at=0)
    assert generic['failure_code'] == 'workbuddy_upstream_error' and 'http_status' not in generic


def test_trace_pid_reuse_partial_files_and_no_parent_escape(tmp_path, monkeypatch):
    from sap_business_agents_platform import workbuddy_failure_watch as module
    job = type('Job', (), {'cli_processes': lambda self, cli: {123: 'birth'}})()
    directory = tmp_path/'123'
    directory.mkdir()
    path = directory/'trace_a.json'
    path.write_text('{')
    watch = OwnedFailureWatch(job, 'fixture', expected(), 0, root=tmp_path)
    monkeypatch.setattr(module, 'process_identity', lambda pid: 'reused')
    assert watch.poll() is None
    monkeypatch.setattr(module, 'process_identity', lambda pid: 'birth')
    assert watch.poll() is None
    path.write_text(json.dumps(trace()))
    assert watch.poll()['failure_code'] == 'workbuddy_authentication_required'


@pytest.mark.parametrize('scenario', ['stderr_auth_401_hang', 'trace_auth_401_hang'])
def test_401_without_worker_terminal_is_fast_and_cleanup_is_owned(tmp_path, monkeypatch, scenario):
    owner, binding = supervisor(tmp_path, monkeypatch)
    release = owner.environment.release()
    if scenario.startswith('trace'):
        release.update(cli_version='2.141.0', cli_path=str(getattr(sys, '_base_executable', sys.executable)))
        from sap_business_agents_platform import workbuddy_failure_watch as module
        original = module.OwnedFailureWatch
        monkeypatch.setattr(module, 'OwnedFailureWatch', lambda *args, **kw: original(*args, **kw, root=tmp_path/'traces'))
    synced = []
    owner.authentication_failed = lambda snapshot, diagnostic, **kw: synced.append(diagnostic['http_status'])
    start = time.monotonic()
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id='401', snapshot=binding, operation='review_workflow',
            payload={'scenario': scenario, 'prompt': 'fixture prompt', 'trace_root': str(tmp_path/'traces')}, seconds=60))
    assert caught.value.code == 'workbuddy_authentication_required'
    assert time.monotonic()-start < 10 and synced == [401]
    saved = json.loads(next((owner.environment.root/'jobs').glob('*.json')).read_text())
    assert saved['status'] == 'failed' and saved['cleanup_complete'] is True
    assert not owner.pending() and 'do-not-save' not in json.dumps(saved)


def test_context_observation_is_metadata_only_and_drops_home(tmp_path, monkeypatch):
    assert clean_environment({'HOME': 'other-user', 'USERPROFILE': 'current', 'SAP_PASSWORD': 'private'}) == {'USERPROFILE': 'current'}
    for relative in ('scripts/runtime-authentication-helper.py', 'scripts/login-workbuddy-runtime.py',
                     'src/sap_business_agents_platform/workbuddy_auth_context.py',
                     'src/sap_business_agents_platform/workbuddy_auth_signals.py',
                     'src/sap_business_agents_platform/runtime_authentication_adapters.py'):
        p=tmp_path/relative; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('# fixture')
    cache=tmp_path/'local/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info'
    cache.parent.mkdir(parents=True); cache.write_text('credential-must-never-be-opened')
    original=Path.open
    def opened(path,*a,**kw):
        assert path != cache, 'credential content read'
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',opened)
    env={'USERPROFILE':str(tmp_path),'LOCALAPPDATA':str(tmp_path/'local')}
    before=observe(tmp_path,env,'release')
    os.utime(cache, ns=(cache.stat().st_atime_ns,cache.stat().st_mtime_ns+1_000_000))
    after=observe(tmp_path,env,'release')
    assert before['context_digest'] == after['context_digest'] and before['cache_digest'] != after['cache_digest']
    assert 'credential' not in json.dumps(after) and str(tmp_path) not in json.dumps(after)


def test_auth_control_401_is_not_collapsed_to_login_required_or_generic(monkeypatch):
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('auth_helper',ROOT/'scripts/runtime-authentication-helper.py')
    helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    class Transport:
        def __init__(self, **kw): pass
        async def connect(self): pass
        async def close(self): pass
        async def write(self,value): self.request=json.loads(value)['request_id']
        async def read_messages(self):
            yield {'type':'control_response','response':{'request_id':self.request,'subtype':'error',
                   'error':{'status_code':401,'message':'private credential'}}}
    monkeypatch.setitem(sys.modules,'codebuddy_agent_sdk',SimpleNamespace(CodeBuddyAgentOptions=lambda **kw:kw))
    monkeypatch.setitem(sys.modules,'codebuddy_agent_sdk.transport',SimpleNamespace(SubprocessTransport=Transport))
    with pytest.raises(Exception) as caught:
        asyncio.run(helper.workbuddy('check','fixture-cli','internal'))
    assert str(caught.value)=='workbuddy_authentication_required'


def test_failure_event_and_stage_diagnostics_keep_401_but_no_content():
    from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
    from sap_business_agents_platform.runtime_diagnostics import safe_stage_diagnostic
    value={'provider_id':'workbuddy','failure_code':'workbuddy_authentication_required',
           'failure_source':'owned_cli_trace','http_status':401,'raw':'secret', 'authentication_sync_failed':True}
    event=safe_progress('workbuddy_native_failure',value)
    stage=safe_stage_diagnostic(value,schema={'type':'object'},operation='free_query')
    assert event['http_status']==stage['http_status']==401 and stage['failure_category']=='authentication'
    assert stage['authentication_sync_failed'] is True
    assert 'secret' not in json.dumps([event,stage])


def test_auth_sync_failure_does_not_replace_401_or_cleanup(tmp_path, monkeypatch):
    owner,binding=supervisor(tmp_path,monkeypatch)
    def broken(*a,**kw): raise OSError('secret path')
    owner.authentication_failed=broken
    with pytest.raises(WorkBuddyError) as caught:
        asyncio.run(owner.run(task_id='sync-error',snapshot=binding,operation='review_workflow',
            payload={'scenario':'stderr_auth_401_hang'},seconds=60))
    assert caught.value.code=='workbuddy_authentication_required'
    assert caught.value.detail['authentication_sync_failed'] is True
    saved=json.loads(next((owner.environment.root/'jobs').glob('*.json')).read_text())
    assert saved['cleanup_complete'] and 'secret path' not in json.dumps(saved)


def test_loaded_api_source_mismatch_fails_closed_without_sdk(tmp_path):
    from types import SimpleNamespace
    from sap_business_agents_platform.runtime_authentication_adapters import OfficialAuthenticationAdapter
    for relative in ('scripts/runtime-authentication-helper.py','scripts/login-workbuddy-runtime.py',
                     'src/sap_business_agents_platform/workbuddy_auth_context.py',
                     'src/sap_business_agents_platform/workbuddy_auth_signals.py',
                     'src/sap_business_agents_platform/runtime_authentication_adapters.py'):
        path=tmp_path/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('# first')
    adapter=OfficialAuthenticationAdapter(tmp_path,'workbuddy',SimpleNamespace(environment=SimpleNamespace(state=lambda:{'environment_digest':'frozen'})))
    original=adapter.observation()
    (tmp_path/'scripts/runtime-authentication-helper.py').write_text('# changed without API restart')
    with pytest.raises(Exception) as caught: adapter.observation()
    assert caught.value.code=='runtime_authentication_context_changed'
    assert original['source_digest']


def test_helper_rejects_old_api_protocol_before_importing_sdk(monkeypatch,capsys):
    import io
    spec=importlib.util.spec_from_file_location('auth_main',ROOT/'scripts/runtime-authentication-helper.py')
    helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    monkeypatch.setattr(sys,'argv',['helper','--provider','workbuddy','--action','check',
                                  '--service-site','china','--sdk-environment','internal'])
    monkeypatch.setattr(sys,'stdin',io.StringIO('owned\n'))
    monkeypatch.setattr(helper,'workbuddy',lambda *a:pytest.fail('SDK was started'))
    assert helper.main()==1
    assert json.loads(capsys.readouterr().out)=={'kind':'error','value':{'code':'runtime_authentication_context_changed'}}


def test_helper_codex_protocol_and_result_are_unchanged(monkeypatch,capsys):
    import io
    spec=importlib.util.spec_from_file_location('codex_main',ROOT/'scripts/runtime-authentication-helper.py')
    helper=importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    monkeypatch.setattr(sys,'argv',['helper','--provider','codex','--action','check'])
    monkeypatch.setattr(sys,'stdin',io.StringIO('owned\n'))
    async def codex(action):
        assert action=='check'
        return {'authenticated':True,'source':'official_cli','method':'browser'}
    monkeypatch.setattr(helper,'codex',codex)
    assert helper.main()==0
    assert json.loads(capsys.readouterr().out)=={'kind':'result','value':{'authenticated':True,'source':'official_cli','method':'browser'}}


def test_unattributed_or_unavailable_trace_does_not_stop_job(tmp_path,monkeypatch):
    from sap_business_agents_platform import workbuddy_failure_watch as module
    job=type('Job',(),{'cli_processes':lambda self,cli:{123:'birth'}})()
    monkeypatch.setattr(module,'process_identity',lambda pid:'birth')
    directory=tmp_path/'123'; directory.mkdir()
    (directory/'trace_x.json').write_text(json.dumps(trace(prompt='unrelated')))
    watch=OwnedFailureWatch(job,'fixture',expected(),0,root=tmp_path)
    assert watch.poll() is None
    # Missing paths and incomplete files are diagnostic absence, not success.
    missing=OwnedFailureWatch(job,'fixture',expected(),0,root=tmp_path/'missing')
    assert missing.poll() is None


def test_history_binding_read_is_not_a_live_authentication_probe(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    binding=service.bind('workbuddy',{})
    before=service.path.read_bytes()
    adapter.observation=lambda:pytest.fail('historical binding must not read a credential cache')
    service.assert_binding('workbuddy',binding)
    assert service.bind('workbuddy',{})==binding and service.path.read_bytes()==before


def test_cancelled_input_requires_same_trace_failed_ancestor():
    value=trace()
    value['spans'][0].update(status='cancelled',parentId='parent',error='cancelled')
    ancestor={'traceId':'trace-fixture','spanId':'parent','parentId':None,'status':'error','error':'Error streaming response'}
    value['spans'].append(ancestor)
    result=trace_failure(value,pid=123,expected=expected(),launched_at=0)
    assert result['failure_code']=='workbuddy_upstream_error' and 'http_status' not in result
    value['spans'][0]['parentId']='unrelated'
    assert trace_failure(value,pid=123,expected=expected(),launched_at=0) is None
    value['spans'][0]['parentId']='parent'; ancestor['parentId']='input'; ancestor['status']='cancelled'
    assert trace_failure(value,pid=123,expected=expected(),launched_at=0) is None
    ancestor.update(status='error',traceId='foreign')
    assert trace_failure(value,pid=123,expected=expected(),launched_at=0) is None


def test_same_account_renewal_keeps_model_and_business_history(tmp_path):
    from types import SimpleNamespace
    from sap_business_agents_platform.sdk_manager import SDKManager
    service,adapter,_=auth_service(tmp_path)
    state={**adapter.context(),'models':{'fixture':{'compatible':True,'check_digest':'original'}},
           'business_evaluations':{'old-pass':{'status':'PASS'}},'capabilities':{'old':'validated'}}
    class Environment:
        def state(self): return state
        def release(self): return {'environment_digest':'release'}
        def mutate(self,fn): fn(state)
    manager=SDKManager.__new__(SDKManager)
    manager.workbuddy=SimpleNamespace(environment=Environment())
    service.changed=manager.apply_authentication_state
    asyncio.run(service.check('workbuddy'))
    before=copy.deepcopy(state)
    adapter.stamp['cache_digest']='normal-refresh'
    async def run():
        async with service.use('workbuddy',service.bind('workbuddy',{})):pass
    asyncio.run(run())
    for key in ('models','business_evaluations','capabilities','authentication_revision'):
        assert state[key]==before[key]


def test_windows_directory_selection_is_verified_not_rewritten():
    from sap_business_agents_platform.workbuddy_auth_context import validate_user_environment, AuthenticationContextError
    expected={'USERPROFILE':'C:/Users/current','LOCALAPPDATA':'C:/Users/current/AppData/Local'}
    validate_user_environment(expected,expected=expected)
    env={**expected,'LOCALAPPDATA':'C:/Users/other/AppData/Local'}
    original=dict(env)
    with pytest.raises(AuthenticationContextError): validate_user_environment(env,expected=expected)
    assert env==original


def test_native_known_folders_match_normal_test_user():
    if os.name=='nt':
        from sap_business_agents_platform.workbuddy_auth_context import validate_user_environment
        validate_user_environment(os.environ)  # Windows metadata only; no SDK/cache content.


def test_pid_reuse_cannot_rebind_watch_ownership(tmp_path,monkeypatch):
    from sap_business_agents_platform import workbuddy_failure_watch as module
    birth=['first']
    job=type('Job',(),{'cli_processes':lambda self,cli:{123:birth[0]}})()
    monkeypatch.setattr(module,'process_identity',lambda pid:birth[0])
    directory=tmp_path/'123'; directory.mkdir()
    path=directory/'trace_x.json'; path.write_text('{')
    watch=OwnedFailureWatch(job,'fixture',expected(),0,root=tmp_path)
    assert watch.poll() is None
    birth[0]='second'; path.write_text(json.dumps(trace()))
    assert watch.poll() is None and watch.identities[123]=='first'
