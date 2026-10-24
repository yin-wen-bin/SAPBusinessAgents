"""Offline launcher checks: no real services, SDKs, models or SAP."""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import startup_control as control
from sap_business_agents_platform.acceptance_runtime import acceptance_runtime_digest, resolve_acceptance_runtime

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("launcher", ROOT / "scripts/service-startup.py")
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)


def test_freeze_contains_dirty_untracked_content_and_rejects_tampering(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir(); source = tmp_path / "src/new_untracked.py"
    source.write_text("answer = 1")
    manifest = launcher.source_manifest(tmp_path)
    archive = launcher.freeze(tmp_path, manifest)
    assert "src/new_untracked.py" in manifest["files"]
    source.write_text("answer = 2")
    assert launcher.source_manifest(tmp_path)["digest"] != manifest["digest"]
    assert (archive / "source/src/new_untracked.py").read_text() == "answer = 1"
    (archive / "source/src/new_untracked.py").write_text("answer = 2")
    with pytest.raises(launcher.control.StartupBlocked, match="startup_archive_changed"):
        launcher.verify_archive(archive, manifest)


def test_unknown_runtime_never_claims_current_source():
    assert control.startup_projection()["verified"] is False


def test_plain_cmd_enters_shared_orchestrator_without_powershell_bootstrap():
    source=(ROOT/'start-sap-business-agents.cmd').read_text()
    assert 'if "%~1"=="" goto native' in source
    assert 'service-startup.py" ensure --root "%~dp0."' in source
    assert 'set "SAPBA_INTERNAL_API_URL=http://127.0.0.1:8765"' in source
    assert 'Start-SAPBusinessAgents.ps1" %*' in source
    assert '--no-browser --reuse-only' in source


def test_launcher_settings_do_not_eagerly_import_the_api(tmp_path):
    import subprocess
    import sys
    code="import runpy,sys;from pathlib import Path;launcher=runpy.run_path(sys.argv[1]);settings=launcher['settings_for'](Path(sys.argv[2]));assert settings.repository_root==Path(sys.argv[2]);assert 'sap_business_agents_platform.app' not in sys.modules"
    subprocess.run([sys.executable,'-I','-B','-c',code,str(ROOT/'scripts/service-startup.py'),str(ROOT)],cwd=tmp_path,check=True,timeout=10)


def test_frozen_runtime_includes_its_native_presentation_validator(tmp_path):
    import subprocess
    import sys
    manifest=launcher.source_manifest(ROOT)
    archive=launcher.freeze(ROOT,manifest)
    assert set(launcher.SOURCE_RESOURCES)<=manifest['files'].keys()
    for name in launcher.SOURCE_RESOURCES:
        assert launcher.control.sha(archive/'source'/name)==manifest['files'][name]
    # Isolated cwd prevents the JS fallback from accidentally using checkout
    # resources and hiding an incomplete frozen release.
    code="import sys;sys.path.insert(0,sys.argv[1]);from sap_business_agents_platform.agent_presentation import authoring_presentation_guidance,inspect_agent_presentation;assert 'Agent presentation contract' in authoring_presentation_guidance();result=inspect_agent_presentation({});assert result['contract_version']!='unavailable',result"
    subprocess.run([sys.executable,'-I','-B','-c',code,str(archive/'source/src')],cwd=tmp_path,check=True,timeout=20)


def test_frontend_history_is_pruned_without_changing_fingerprint_material(tmp_path,monkeypatch):
    for name in ('site/src/page.ts','site/src/generated/agents.ts','agents/FI/a/agent.json','agents/FI/a/versions/old/agent.json'):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:'v24.16.0\n')
    before=launcher.site_fingerprint(tmp_path,4321,8765)
    (tmp_path/'agents/FI/a/versions/old/agent.json').write_text('old history changed')
    (tmp_path/'site/src/generated/agents.ts').write_text('derived projection changed')
    assert launcher.site_fingerprint(tmp_path,4321,8765)==before
    (tmp_path/'site/src/page.ts').write_text('current UI changed')
    assert launcher.site_fingerprint(tmp_path,4321,8765)!=before


def test_activity_is_read_only_and_keeps_cleanup_pending(tmp_path):
    path = tmp_path / "platform.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE runs(status text)")
        database.executemany("INSERT INTO runs VALUES (?)", [("completed",), ("cleanup_pending",)])
    before = path.read_bytes()
    assert control.activity(tmp_path) == {"runs": 1}
    assert path.read_bytes() == before


def test_maintenance_rejects_admission_and_does_not_grant_execution(tmp_path):
    control.atomic(tmp_path / "startup-maintenance.json", {"pid": 123})
    with pytest.raises(control.StartupBlocked, match="startup_maintenance_pending"):
        with control.admission(tmp_path): pytest.fail("not accepted")


def test_shared_admission_and_exclusive_maintenance(tmp_path):
    path = tmp_path / "startup-admission.lock"
    with control.file_lock(path, shared=True):
        with control.file_lock(path, shared=True): pass
        with pytest.raises(control.StartupBlocked):
            with control.file_lock(path): pytest.fail("maintenance raced an accepted request")


def test_frozen_default_uses_same_model_and_keeps_formal_identity_gate(tmp_path):
    calls = []
    def snapshot(model=None, **kwargs):
        calls.append((model, kwargs)); return {"provider_id": "workbuddy", "model": "saved", "actual_model": "saved", **manager.context}
    from tests.test_acceptance_runtime_binding import make_manager
    manager = make_manager(tmp_path)
    manager.workbuddy.runtime_snapshot = snapshot
    assert resolve_acceptance_runtime(manager)["model"] == "saved"
    assert calls == [(None, {"formal": True})]
    a = acceptance_runtime_digest({"model": "saved", "authentication_revision": 9, "selected_at": "a"})
    from sap_business_agents_platform.acceptance_runtime import AgentAcceptanceCampaignRequest
    assert len(a)==64
    payload=AgentAcceptanceCampaignRequest(expectedRevision=1,requestId='digest-roundtrip',expectedRuntimeDigest=a,cases=[{'caseId':'one','input':{}}])
    assert payload.expected_runtime_digest==a
    assert a == acceptance_runtime_digest({"model": "saved", "authentication_revision": 9, "selected_at": "b"})
    assert a != acceptance_runtime_digest({"model": "different", "authentication_revision": 9})


def test_changed_default_rejected_before_campaign_and_preserves_old_request_hash():
    from sap_business_agents_platform.acceptance_runtime import AgentAcceptanceCampaignRequest
    from pydantic import ValidationError
    old = AgentAcceptanceCampaignRequest(expectedRevision=1, requestId="same", cases=[{"caseId": "case-1", "input": {}}])
    assert old.expected_runtime_digest is None
    with pytest.raises(ValidationError):
        AgentAcceptanceCampaignRequest(expectedRevision=1, requestId="same", expectedRuntimeDigest="forged", cases=[{"caseId": "case-1", "input": {}}])


def test_changed_default_does_not_reserve_or_dispatch_a_campaign(tmp_path):
    from sap_business_agents_platform.database import RunStore
    from sap_business_agents_platform.acceptance_runtime import AgentAcceptanceCampaignRequest
    from sap_business_agents_platform.agent_acceptance_campaigns import AgentAcceptanceJobs
    from tests.test_agent_acceptance_campaigns import _seed_runnable, _Lifecycle, _Coordinator, _Runtime
    store = RunStore(tmp_path / "platform.sqlite3")
    draft = _seed_runnable(store, tmp_path)
    jobs = AgentAcceptanceJobs(SimpleNamespace(data_root=tmp_path), store, _Lifecycle(), _Coordinator(), _Runtime())
    payload = AgentAcceptanceCampaignRequest(expectedRevision=4, requestId="changed", expectedRuntimeDigest="0" * 64,
        cases=[{"caseId": "case-1", "input": {"company_code": "1710"}}])
    with pytest.raises(Exception) as error:
        jobs.start(draft["draft_id"], payload)
    assert error.value.code == "agent_acceptance_runtime_changed"
    assert store.get_agent_operation(draft["draft_id"]) is None
    assert store.list_agent_acceptance_campaigns(draft["draft_id"]) == []


def test_maintenance_does_not_block_get_cancel_or_accept_new_requests(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    application = FastAPI()
    control.install_barrier(application, SimpleNamespace(data_root=tmp_path))
    @application.get('/status')
    def status(): return {'ok': True}
    @application.post('/start')
    def start(): pytest.fail('not dispatched')
    @application.post('/run/cancel')
    def cancel(): return {'cancelled': True}
    control.atomic(tmp_path / 'startup-maintenance.json', {'pid': 123})
    with TestClient(application) as client:
        assert client.get('/status').status_code == 200
        assert client.post('/run/cancel').status_code == 200
        assert client.post('/start').status_code == 503


@pytest.fixture
def orchestrator(tmp_path, monkeypatch):
    state = SimpleNamespace(ports={8765:100,4321:200}, stopped=[], launches=[], commands=[], ready=[], current=False)
    manifest = {'digest':'target-source','files':{}}
    health = lambda: {'ok':True,'loopback_only':True,'sap_read':{'selected_provider':'embedded','data':{'read_only':True}},
                      'startup_binding':{'verified':state.current,'source_digest':'target-source' if state.current else 'old','pid':state.ports.get(8765),'boot_id':'offline-test-boot'}}
    args = SimpleNamespace(root=tmp_path,port=8765,site_port=4321,timeout=90,restart=False,rebuild=False,no_browser=True,dev=False,reuse_only=False)
    data = tmp_path/'.local-data'
    control.atomic(data/'site-builds/current.json',{'fingerprint':'ui','dist_path':str(tmp_path/'old-ui'),'pid':200})
    control.atomic(data/'startup-api-current.json',{'archive':str(tmp_path/'old-source'),'source_digest':'old'})
    monkeypatch.setattr(launcher,'settings_for',lambda root:SimpleNamespace(data_root=data))
    monkeypatch.setattr(launcher,'source_manifest',lambda root:manifest)
    monkeypatch.setattr(launcher,'site_fingerprint',lambda *args:'ui')
    monkeypatch.setattr(launcher,'request',lambda url,*args,**kwargs: {'fingerprint':'ui'} if 'sapba-build' in url else health())
    monkeypatch.setattr(launcher,'listeners',lambda:dict(state.ports))
    monkeypatch.setattr(launcher,'identity',lambda pid,root:{'ProcessId':pid,'CreationDate':str(pid),'CommandLine':str(root)})
    monkeypatch.setattr(launcher,'assert_writers',lambda *args:None)
    monkeypatch.setattr(launcher,'freeze',lambda *args:tmp_path/'target-source')
    monkeypatch.setattr(launcher,'check_admission',lambda *args:None)
    monkeypatch.setattr(launcher,'jobs_pending',lambda *args:False)
    monkeypatch.setattr(launcher.control,'activity',lambda *args:{})
    def stop(saved,root):
        state.stopped.append(saved['ProcessId']); state.ports.pop(8765,None)
    monkeypatch.setattr(launcher,'stop_owned',stop)
    class Process:
        def __init__(self,pid):self.pid=pid
        def poll(self):return None if state.ports.get(8765)==self.pid else 0
        def wait(self,timeout):return 0
    def launch(root,archive,port,log):
        pid=101+len(state.launches);state.launches.append(str(archive));state.ports[port]=pid;return Process(pid)
    monkeypatch.setattr(launcher,'launch_api',launch)
    monkeypatch.setattr(launcher,'wait_api',lambda url,digest,process,timeout:state.ready.append(digest))
    def command(argv,**kwargs):
        state.commands.append(argv)
        if argv[0]=='taskkill.exe':state.ports.pop(8765,None)
    monkeypatch.setattr(launcher.subprocess,'run',command)
    monkeypatch.setattr(launcher,'probe_site',lambda *args:pytest.fail('No frontend update required'))
    monkeypatch.setattr(launcher,'switch_site',lambda *args:pytest.fail('No frontend update required'))
    return args,state,manifest,data


def test_api_only_update_does_not_restart_frontend(orchestrator):
    args,state,_,data=orchestrator
    result=launcher.ensure(args)
    assert state.stopped==[100] and len(state.launches)==1
    assert state.ports[4321]==200 and state.ready==['target-source']
    assert not (data/'startup-maintenance.json').exists() and not result['api_reused']


def test_loaded_binding_requires_verified_process_and_boot_identity():
    complete={'startup_binding':{'verified':True,'source_digest':'exact','pid':123,'boot_id':'boot'}}
    assert launcher.valid_binding(complete,'exact')
    for field in ('verified','pid','boot_id'):
        missing={'startup_binding':{key:value for key,value in complete['startup_binding'].items() if key!=field}}
        assert not launcher.valid_binding(missing,'exact')
    assert not launcher.valid_binding(complete,'different')


@pytest.mark.parametrize(('command','expected'),[
    ('"D:\\SAPBusinessAgents\\.venv\\Scripts\\python.exe" -m sap_business_agents_platform.cli',True),
    ('node D:/SAPBusinessAgents/site/node_modules/astro/bin/astro.mjs preview',True),
    ('node D:/SAPBusinessAgents-other/site/node_modules/astro/bin/astro.mjs preview --compare D:/SAPBusinessAgents',False),
    ('python D:/SAPBusinessAgents/unknown-tool.py --port 8765',False),
])
def test_only_managed_service_commands_are_owned(command,expected):
    assert launcher.project_service_command(command,Path('D:/SAPBusinessAgents')) is expected


def test_reused_parent_pid_cannot_exempt_an_unknown_writer(tmp_path,monkeypatch):
    import os
    own=os.getpid();server=own+1000;reused=own+2000
    rows=[{'ProcessId':own,'ParentProcessId':0,'CreationDate':'/Date(4000)/','Name':'python.exe','CommandLine':str(tmp_path/'launcher.py')},
          {'ProcessId':server,'ParentProcessId':reused,'CreationDate':'/Date(1000)/','Name':'python.exe','CommandLine':str(tmp_path/'start-api.py')},
          {'ProcessId':reused,'ParentProcessId':0,'CreationDate':'/Date(2000)/','Name':'python.exe','CommandLine':str(tmp_path/'unknown-writer.py')}]
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:json.dumps(rows))
    with pytest.raises(launcher.control.StartupBlocked,match='startup_other_writers_active'):
        launcher.assert_writers(tmp_path,[server])


def test_wrong_build_metadata_is_rejected_before_old_frontend_is_stopped(monkeypatch):
    monkeypatch.setattr(launcher,'request',lambda *a,**k:{'fingerprint':'wrong'})
    with pytest.raises(launcher.control.StartupBlocked,match='startup_site_build_binding_invalid'):
        launcher.assert_site_metadata(4321,'expected')


def test_frontend_restore_checks_old_build_instead_of_failed_candidate(tmp_path,monkeypatch):
    target=tmp_path/'.local-data/site-builds/new/dist'
    target.mkdir(parents=True);(target/'sapba-build.json').write_text('{}')
    args=SimpleNamespace(site_port=4321,dev=False,timeout=1)
    previous={'dist_path':str(tmp_path/'old/dist'),'fingerprint':'old','mode':'preview'}
    checks=[];spawns=[]
    class Process:
        pid=123
        def poll(self):return None
        def wait(self,timeout):return 0
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'SAP Business Agents'
    def spawn(argv,*args):spawns.append(argv);return Process()
    def metadata(port,expected,**kwargs):
        checks.append(expected)
        if expected=='new':raise launcher.control.StartupBlocked('simulated_candidate_failure')
    monkeypatch.setattr(launcher,'spawn',spawn)
    monkeypatch.setattr(launcher.LOCAL_HTTP,'open',lambda *a,**k:Response())
    monkeypatch.setattr(launcher,'assert_site_metadata',metadata)
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:None)
    with pytest.raises(launcher.control.StartupBlocked,match='simulated_candidate_failure'):
        launcher.switch_site(tmp_path,'new',args,None,previous,tmp_path/'logs')
    assert checks==['new','old'] and len(spawns)==2
    assert json.loads((tmp_path/'.local-data/site-builds/current.json').read_text())['fingerprint']=='old'


def test_local_child_uses_local_site_base_without_mutating_parent_environment(tmp_path,monkeypatch):
    import os
    monkeypatch.setenv('PUBLIC_SITE_BASE','/SAPBusinessAgents')
    captured={}
    def popen(argv,**kwargs):captured.update(kwargs);return 'child'
    monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    assert launcher.spawn(['node.exe','preview'],tmp_path,tmp_path/'logs/site')=='child'
    assert captured['env']['PUBLIC_SITE_BASE']=='/'
    assert os.environ['PUBLIC_SITE_BASE']=='/SAPBusinessAgents'


def test_warm_reuse_never_audits_jobs_or_sdk(orchestrator,monkeypatch):
    args,state,_,_=orchestrator;state.current=True;args.reuse_only=True
    monkeypatch.setattr(launcher,'jobs_pending',lambda *a:pytest.fail('No history scan on warm reuse'))
    monkeypatch.setattr(launcher,'check_admission',lambda *a:pytest.fail('No SDK audit on warm reuse'))
    assert launcher.ensure(args)['api_reused']
    assert not state.stopped and not state.launches


def test_warm_reuse_enumerates_listeners_only_once(orchestrator,monkeypatch):
    args,state,_,_=orchestrator;state.current=True;calls=[]
    def listeners():calls.append(True);return dict(state.ports)
    monkeypatch.setattr(launcher,'listeners',listeners)
    assert launcher.ensure(args)['api_reused'] and len(calls)==1


def test_reuse_only_measurement_cannot_update_service(orchestrator):
    args,state,_,_=orchestrator;args.reuse_only=True
    with pytest.raises(launcher.control.StartupBlocked,match='startup_reuse_not_proven'):
        launcher.ensure(args)
    assert not state.stopped and not state.launches


def test_missing_target_proof_keeps_old_api_and_clears_marker(orchestrator,monkeypatch):
    args,state,_,data=orchestrator
    def denied(*args):raise launcher.control.StartupBlocked('sdk_certificate_missing')
    monkeypatch.setattr(launcher,'check_admission',denied)
    with pytest.raises(launcher.control.StartupBlocked,match='sdk_certificate_missing'):launcher.ensure(args)
    assert not state.stopped and not (data/'startup-maintenance.json').exists()


def test_cancel_idle_wait_does_not_hold_switch_lock_or_cancel_tasks(orchestrator,monkeypatch):
    args,state,_,data=orchestrator
    monkeypatch.setattr(launcher.control,'activity',lambda *args:{'runs':1})
    def interrupt(seconds):
        assert seconds==5
        with launcher.control.file_lock(data/'deployment.lock'):pass
        assert not (data/'startup-maintenance.json').exists()
        raise KeyboardInterrupt()
    monkeypatch.setattr(launcher.time,'sleep',interrupt)
    with pytest.raises(KeyboardInterrupt):launcher.ensure(args)
    assert not state.stopped and not state.launches


def test_source_change_during_prepare_cannot_stop_old_service(orchestrator,monkeypatch):
    args,state,manifest,_=orchestrator;calls=[]
    def changed(root):
        calls.append(root);return {**manifest,'digest':str(len(calls))}
    monkeypatch.setattr(launcher,'source_manifest',changed)
    with pytest.raises(launcher.control.StartupBlocked,match='startup_target_changed'):launcher.ensure(args)
    assert not state.stopped and not state.launches


def test_single_source_change_reprepares_outside_locks_before_switch(orchestrator,monkeypatch):
    args,state,manifest,data=orchestrator
    calls=[]
    def changed(root):
        calls.append(root)
        if len(calls)==1:return {**manifest,'digest':'previous-target'}
        if len(calls)==3:
            assert state.ports[8765]==100 and not state.stopped
            assert not (data/'startup-maintenance.json').exists()
            with launcher.control.file_lock(data/'deployment.lock'):pass
            with launcher.control.file_lock(data/'startup-admission.lock'):pass
        return manifest
    monkeypatch.setattr(launcher,'source_manifest',changed)
    result=launcher.ensure(args)
    assert result['source_digest']==manifest['digest']
    assert state.stopped==[100] and len(state.launches)==1
    assert len(args.preparation_attempts)==1
    assert (args.attempt/'preparation-attempts.json').exists()


def test_reprepare_still_requires_target_admission(orchestrator,monkeypatch):
    args,state,manifest,_=orchestrator
    calls=[]
    def changed(root):
        calls.append(root)
        return {**manifest,'digest':'previous-target'} if len(calls)==1 else manifest
    monkeypatch.setattr(launcher,'source_manifest',changed)
    def denied(*args):raise launcher.control.StartupBlocked('sdk_certificate_missing')
    monkeypatch.setattr(launcher,'check_admission',denied)
    with pytest.raises(launcher.control.StartupBlocked,match='sdk_certificate_missing'):launcher.ensure(args)
    assert not state.stopped and not state.launches


def test_frontend_change_also_reprepares_without_stopping_old_service(orchestrator,monkeypatch):
    args,state,manifest,_=orchestrator
    calls=[]
    def fingerprint(*unused):
        calls.append(True)
        if len(calls)==1:return 'previous-ui'
        if len(calls)==3:assert not state.stopped
        return 'ui'
    monkeypatch.setattr(launcher,'site_fingerprint',fingerprint)
    monkeypatch.setattr(launcher,'probe_site',lambda *args:None)
    result=launcher.ensure(args)
    assert result['source_digest']==manifest['digest']
    assert state.stopped==[100] and len(args.preparation_attempts)==1
    assert args.preparation_attempts[0]['error']=='startup_site_source_changed_prepare_again'


def test_manifest_changes_never_logs_configuration_values():
    expected={'files':{'src/a.py':'old'},'dependencies':{'startup_settings_sha256':'secret-old'}}
    actual={'files':{'src/a.py':'new','src/b.py':'new'},'dependencies':{'startup_settings_sha256':'secret-new'}}
    changes=launcher.manifest_changes(expected,actual)
    assert changes=={'files':['src/a.py','src/b.py'],'dependencies':['startup_settings_sha256']}
    assert 'secret' not in json.dumps(changes)


def test_expected_startup_block_has_actionable_cli_message_without_traceback(tmp_path,monkeypatch,capsys):
    def denied(args):
        args.attempt=tmp_path/'attempt'
        raise launcher.control.StartupBlocked('startup_target_changed_prepare_again')
    monkeypatch.setattr(launcher,'ensure',denied)
    assert launcher.main(['ensure','--root',str(tmp_path),'--no-browser'])==1
    output=capsys.readouterr().err
    assert 'Files are still changing' in output and 'result.json' in output
    assert 'Traceback' not in output


@pytest.mark.parametrize('rollback_fails',[False,True])
def test_api_failure_rolls_back_or_keeps_fail_closed_barrier(orchestrator,monkeypatch,rollback_fails):
    args,state,_,data=orchestrator
    def ready(url,digest,process,timeout):
        if digest=='target-source' or rollback_fails:raise launcher.control.StartupBlocked('simulated_ready_failure')
        state.ready.append(digest)
    monkeypatch.setattr(launcher,'wait_api',ready)
    with pytest.raises(launcher.control.StartupBlocked):launcher.ensure(args)
    assert len(state.launches)==2 and state.stopped==[100]
    assert (data/'startup-maintenance.json').exists()==rollback_fails
    if not rollback_fails:assert state.ready==['old']
