"""Synthetic dependencies and service processes only; no SDK/model/SAP calls."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import runtime_environment as env
from sap_business_agents_platform import codex_activation as activation
from sap_business_agents_platform.codex_environment_update import CodexEnvironmentUpdate
from sap_business_agents_platform.sdk_activation_handoff import verify_executor
from sap_business_agents_platform.sdk_manager import SDKManagerError
from sap_business_agents_platform.startup_control import atomic
from tests.test_startup_versioning import launcher, orchestrator
from tests.test_sdk_update import pipeline


def fake_environment(path, packages=None):
    packages=packages or {'openai-codex':'1.0.0','openai-codex-cli-bin':'1.0.0','existing-dependency':'9.0.0'}
    python=env.python_path(path);python.parent.mkdir(parents=True,exist_ok=True);python.write_bytes(b'fake interpreter')
    (path/'pyvenv.cfg').write_text('include-system-site-packages = false\n')
    base=path/'base-python.exe';base.write_bytes(b'fake base')
    site=path/'Lib/site-packages';site.mkdir(parents=True,exist_ok=True)
    for name,version in packages.items():
        info=site/(name.replace('-','_')+'-'+version+'.dist-info');info.mkdir()
        metadata=info/'METADATA';metadata.write_text(f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n')
        hashed=base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).decode().rstrip('=')
        (info/'RECORD').write_text(f'{metadata.relative_to(site).as_posix()},sha256={hashed},{metadata.stat().st_size}\n{info.name}/RECORD,,\n')
    def probe(args,**kw):
        assert args[:4]==[str(python),'-I','-B','-c']
        return SimpleNamespace(stdout=json.dumps({'prefix':str(path),'base_executable':str(base),
            'version':'3.fixture','site':str(site)}))
    return env.capture(path,run=probe)


def test_environment_capture_verify_and_deep_records(tmp_path):
    binding=fake_environment(tmp_path/'env')
    assert env.verify(binding,deep=True)==binding
    assert binding['python']==str(env.python_path(tmp_path/'env'))
    assert binding['packages']['openai-codex']=='1.0.0'


@pytest.mark.parametrize('change',['python','base_python','cfg','metadata','inventory','digest'])
def test_environment_external_changes_never_reregister(tmp_path,change):
    binding=fake_environment(tmp_path/'env');before=json.dumps(binding,sort_keys=True)
    if change in {'python','base_python'}:Path(binding[change]).write_bytes(b'changed')
    elif change=='cfg':(Path(binding['path'])/'pyvenv.cfg').write_text('include-system-site-packages=true')
    elif change=='metadata':next(Path(binding['site']).glob('*/METADATA')).write_text('changed')
    elif change=='inventory':(Path(binding['site'])/'unknown.dist-info').mkdir()
    else:binding['packages']['openai-codex']='other'
    with pytest.raises(env.EnvironmentError):env.verify(binding)
    if change!='digest':assert json.dumps(binding,sort_keys=True)==before


def test_legacy_requires_exact_old_dependency_proof(tmp_path,monkeypatch):
    binding=fake_environment(tmp_path/'.venv');binding['kind']='legacy';binding['digest']=env.digest({k:v for k,v in binding.items() if k!='digest'})
    monkeypatch.setattr(env,'capture',lambda *a,**k:binding)
    deps={'.venv/Lib/site-packages/'+k:v for k,v in binding['files'].items()}
    deps.update({'.venv/pyvenv.cfg':binding['cfg_sha256'],'interpreter':{
        'sha256':binding['python_sha256'],'base_sha256':binding['base_sha256']}})
    assert env.legacy_binding(tmp_path,{'dependencies':deps})==binding
    deps['.venv/pyvenv.cfg']='changed'
    with pytest.raises(env.EnvironmentError,match='legacy_binding'):env.legacy_binding(tmp_path,{'dependencies':deps})


def release(tmp_path,binding):
    manifest={'version':1,'files':{},'dependencies':{'environment':binding}}
    manifest['digest']=launcher.control.digest(manifest)
    archive=tmp_path/'.local-data/startup-releases'/manifest['digest'];(archive/'source').mkdir(parents=True)
    atomic(archive/'manifest.json',manifest)
    return archive,manifest


def test_launch_and_admission_use_target_python_not_bootstrap(tmp_path,monkeypatch):
    binding=fake_environment(tmp_path/'env');archive,_=release(tmp_path,binding);calls=[]
    monkeypatch.setattr(launcher,'spawn',lambda args,*a:calls.append(args))
    launcher.launch_api(tmp_path,archive,8765,tmp_path/'logs')
    assert calls[0][0]==binding['python']
    def run(args,**kw):calls.append(args);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(launcher.subprocess,'run',run)
    launcher.check_admission(tmp_path,archive)
    assert calls[1][0]==binding['python']
    Path(binding['python']).write_bytes(b'changed')
    with pytest.raises(launcher.environments.EnvironmentError):launcher.launch_api(tmp_path,archive,8765,tmp_path/'logs')
    assert len(calls)==2


def test_independent_candidate_does_not_install_into_baseline(tmp_path,pipeline,monkeypatch):
    package,calls,network,contents=pipeline
    binding=fake_environment(tmp_path/'old');before={p:p.read_bytes() for p in (tmp_path/'old').rglob('*') if p.is_file()}
    archive=tmp_path/'archive';(archive/'source').mkdir(parents=True)
    (archive/'source/pyproject.toml').write_text('[project]\ndependencies=["openai-codex>=1,<2"]\n[tool.sapba.codex]\nreviewed_versions=["1.1.0"]\n')
    commands=[]
    class Owned:
        def __init__(self,python):self.python=str(python)
        def run(self,args,**kw):
            commands.append((self.python,args,kw))
            if kw.get('command') and '-m' in kw['command']:fake_environment(Path(kw['command'][-1]),{
                'openai-codex':'1.1.0','openai-codex-cli-bin':'1.1.0','existing-dependency':'9.0.0'})
    def capture(path):
        python=env.python_path(path);site=path/'Lib/site-packages'
        return env.capture(path,run=lambda *a,**k:SimpleNamespace(stdout=json.dumps({
            'prefix':str(path),'site':str(site),'base_executable':str(path/'base-python.exe'),'version':'3.fixture'})))
    installer=CodexEnvironmentUpdate(tmp_path,{'environment':binding,'archive':str(archive)},'update_'+'a'*32,
        pipeline=package,owned_factory=Owned,capture=capture)
    lock=installer.prepare('1.1.0',lambda **v:None)
    receipt=installer.install(lock,lambda **v:None)
    assert receipt['activated'] is False
    assert all(p.read_bytes()==value for p,value in before.items())
    assert commands[0][2]['command'][:4]==[binding['python'],'-I','-B','-m']
    assert commands[1][0]!=binding['python'] and '--require-hashes' in commands[1][1]
    assert commands[2][1]==['check']
    assert 'find_spec' in commands[3][2]['command'][-1] and commands[3][0]==commands[1][0]
    assert all(0<kw['seconds']<=300 for _,_,kw in commands)
    assert not (tmp_path/'.local-data/startup-api-current.json').exists()
    with pytest.raises(SDKManagerError,match='SDK maintenance'):installer.install(lock,lambda **v:None)


def test_target_support_and_project_constraints_fail_before_install(tmp_path):
    binding=fake_environment(tmp_path/'old');archive=tmp_path/'source';archive.mkdir()
    (archive/'pyproject.toml').write_text('[project]\ndependencies=["openai-codex==1.0.0"]\n[tool.sapba.codex]\nreviewed_versions=["1.1.0"]\n')
    update=CodexEnvironmentUpdate(tmp_path,{'environment':binding,'archive':str(tmp_path)},'update_'+'a'*32)
    with pytest.raises(SDKManagerError) as error:update._constraints({'openai-codex':'2.0.0'})
    assert error.value.code=='sdk_target_evidence_required'
    with pytest.raises(SDKManagerError) as error:update._constraints({'openai-codex':'1.1.0'})
    assert error.value.code=='sdk_platform_dependency_conflict'


@pytest.mark.parametrize('fault',['sid','ancestry','pid_reuse','command'])
def test_independent_handoff_rejects_unsafe_owners(fault):
    api={'pid':10,'parent':1,'birth':'2001','sid':'same','command':'api'}
    child={'pid':20,'parent':2,'birth':'2002','sid':'same','command':'executor'}
    rows={'sid':'same','processes':[dict(api),child]}
    assert verify_executor(rows,20,10,api,'executor')['pid']==20
    if fault=='sid':child['sid']='other'
    elif fault=='ancestry':child['parent']=10
    elif fault=='pid_reuse':rows['processes'][0]['birth']='2003'
    else:child['command']='other'
    with pytest.raises(SDKManagerError):verify_executor(rows,20,10,api,'executor')


def test_switch_journal_exists_before_old_service_stops(orchestrator,monkeypatch):
    args,state,manifest,data=orchestrator;old=launcher.stop_owned
    def stop(saved,root):
        journal=json.loads((data/'startup/pending-switch.json').read_bytes())
        assert journal['phase']=='stopping' and journal['previous']['source_digest']=='old'
        return old(saved,root)
    monkeypatch.setattr(launcher,'stop_owned',stop)
    launcher.ensure(args)
    assert not (data/'startup/pending-switch.json').exists()


def test_unknown_interrupted_switch_cannot_be_replaced(orchestrator):
    args,state,_,data=orchestrator
    atomic(data/'startup/pending-switch.json',{'phase':'starting'})
    with pytest.raises(launcher.control.StartupBlocked,match='recovery_required'):launcher.ensure(args)
    assert not state.stopped and not state.launches


def test_request_identity_replays_original_even_after_binding_changes(tmp_path,monkeypatch):
    from tests.test_sdk_update import managed, StagedAdapter
    manager=managed(tmp_path,StagedAdapter());job=manager.codex_activation
    base={'environment':{'packages':{'openai-codex':'1.0.0'}},'source_digest':'old'}
    monkeypatch.setattr(activation,'read_base',lambda *a:base)
    calls=[]
    async def nothing(record):calls.append(record['operation_id'])
    monkeypatch.setattr(job,'run',nothing)
    async def scenario():
        definition=manager._get_sdk('codex-python-sdk')
        first=await job.submit(definition,'1.1.0','request-1');await job.task
        monkeypatch.setattr(activation,'read_base',lambda *a:pytest.fail('replay must not rebind'))
        assert (await job.submit(definition,'1.1.0','request-1'))['operation_id']==first['operation_id']
        with pytest.raises(SDKManagerError) as error:await job.submit(definition,'1.2.0','request-1')
        assert error.value.code=='sdk_update_request_conflict'
        assert len(calls)==1
        restored=activation.CodexActivation(manager)
        assert restored.status()['operation_id']==first['operation_id'] and restored.task is None
    asyncio.run(scenario())


@pytest.mark.parametrize('change',['injected_hook','installed_file'])
def test_deep_binding_detects_changes_outside_package_metadata(tmp_path,change):
    binding=fake_environment(tmp_path/'env');site=Path(binding['site'])
    (site/('unknown.pth' if change=='injected_hook' else 'sdk_module.py')).write_text('not trusted')
    with pytest.raises(env.EnvironmentError):env.verify(binding,deep=True)


def test_orphan_request_receipt_replays_without_second_install(tmp_path,monkeypatch):
    from tests.test_sdk_update import managed,StagedAdapter
    manager=managed(tmp_path,StagedAdapter());job=manager.codex_activation
    request_id='crash-before-current';operation='update_'+activation.digest(request_id)[:32]
    atomic(job.root/(operation+'.json'),{'operation_id':operation,'request_id':request_id,'status':'running','phase':'preparing',
        'request_fingerprint':activation.digest({'sdk_id':'codex-python-sdk','target_version':'1.1.0'}),'target_version':'1.1.0'})
    monkeypatch.setattr(activation,'read_base',lambda *a:pytest.fail('must not rebind/reinstall'))
    result=asyncio.run(job.submit(manager._get_sdk('codex-python-sdk'),'1.1.0',request_id))
    assert result['operation_id']==operation and job.task is None


@pytest.mark.parametrize('phase',['stopping','starting','health','committed'])
def test_crash_recovery_restores_matching_source_and_environment(orchestrator,monkeypatch,phase):
    args,state,manifest,data=orchestrator
    previous={'archive':str(args.root/'old-source'),'source_digest':'old','environment':{'digest':'old-env'}}
    target={'archive':str(args.root/'new-source'),'source_digest':'new','environment':{'digest':'new-env'}}
    saved={'owner_pid':1,'phase':phase,'previous':previous,'target':target,'api_changed':True,'site_changed':False,
           'previous_site':{'fingerprint':'ui'},'target_site':'ui','log':str(args.root/'log'),
           'new_identity':{'ProcessId':101},'owned':{'8765':{'ProcessId':100}}}
    atomic(data/'startup/pending-switch.json',saved);atomic(data/'startup-maintenance.json',{'pid':1})
    if phase=='starting':state.ports.pop(8765)
    elif phase in {'health','committed'}:state.ports[8765]=101
    def belongs(pid,owner,root):return owner is not None and pid==owner['ProcessId']
    monkeypatch.setattr(launcher,'listener_belongs_to',belongs)
    def request(url,*a,**kw):
        return {'ok':True,'loopback_only':True,'sap_read':{'selected_provider':'embedded','data':{'read_only':True}},
                'startup_binding':{'verified':True,'pid':state.ports.get(8765),'boot_id':'test',
                    'source_digest':'new' if phase=='committed' else 'old' if phase=='stopping' else 'unhealthy',
                    'environment_digest':'new-env' if phase=='committed' else 'old-env'}}
    monkeypatch.setattr(launcher,'request',request)
    (args.root/'new-source').mkdir();atomic(args.root/'new-source/manifest.json',{})
    monkeypatch.setattr(launcher,'verify_archive',lambda *a:None)
    monkeypatch.setattr(launcher.environments,'verify',lambda *a,**k:None)
    result=launcher.recover_switch(args)
    assert result['outcome']==('activated' if phase=='committed' else 'rolled_back')
    current=json.loads((data/'startup-api-current.json').read_bytes())
    if phase=='committed':assert current['environment']==target['environment'] and not state.stopped
    elif phase!='stopping':
        assert current['environment']==previous['environment'] and state.launches==[previous['archive']]
    assert state.ports[4321]==200 and not (data/'startup/pending-switch.json').exists()


def test_recovery_cannot_stop_reused_pid_or_unknown_listener(orchestrator,monkeypatch):
    args,state,_,data=orchestrator
    atomic(data/'startup/pending-switch.json',{'owner_pid':1,'api_changed':True,'site_changed':False,
        'target':{'source_digest':'new'},'previous':{'source_digest':'old'},'owned':{},'new_identity':{'ProcessId':100}})
    monkeypatch.setattr(launcher,'listener_belongs_to',lambda *a:False)
    with pytest.raises(launcher.control.StartupBlocked,match='process_unconfirmed'):launcher.recover_switch(args)
    assert not state.stopped and (data/'startup/pending-switch.json').exists()


def test_configuration_change_during_preparation_blocks_handoff(tmp_path,monkeypatch):
    from tests.test_sdk_update import managed,StagedAdapter
    manager=managed(tmp_path,StagedAdapter());job=manager.codex_activation
    binding=fake_environment(tmp_path/'target');record={'operation_id':'update_'+'c'*32,'target':{
        'environment':binding,'archive':str(tmp_path/'archive')},'configuration':{},'phase':'awaiting_review'}
    monkeypatch.setattr(activation.runpy,'run_path',lambda *a:{'check_admission':lambda *a:None})
    job.handoff=lambda *a:pytest.fail('must not switch on changed configuration')
    asyncio.run(job.finish(record))
    assert record['status']=='needs_review' and record['error']['code']=='sdk_update_binding_changed'


def test_twenty_unchanged_starts_never_prepare_install_or_switch(orchestrator,monkeypatch):
    import time
    args,state,_,data=orchestrator;state.current=True
    monkeypatch.setattr(launcher,'freeze',lambda *a:pytest.fail('unchanged source must not freeze'))
    monkeypatch.setattr(launcher,'check_admission',lambda *a:pytest.fail('healthy unchanged service must not re-audit'))
    args.attempt=data/'warm-read';args.started=time.monotonic();args.phases={}
    for _ in range(20):
        result=launcher._ensure(args)
        assert result['api_reused'] and result['site_cache']['reused']
    assert not state.launches and not state.stopped and not state.commands


@pytest.mark.parametrize('provider',['codex','workbuddy'])
def test_other_maintenance_blocks_switch_but_own_handoff_does_not(tmp_path,provider,monkeypatch):
    import sap_business_agents_platform.workbuddy_job_index as index
    monkeypatch.setattr(index,'enabled',lambda *a:False)
    directory=tmp_path/'.local-data/sdk-update-jobs'/provider;operation='update_'+'d'*32
    atomic(directory/'current.json',{'operation_id':operation})
    atomic(directory/(operation+'.json'),{'operation_id':operation,'status':'running','phase':'waiting_maintenance','cleanup_complete':True})
    assert launcher.jobs_pending(tmp_path) is True
    assert launcher.jobs_pending(tmp_path,operation) is False
    atomic(directory/(operation+'.json'),{'operation_id':operation,'status':'needs_review','cleanup_complete':False})
    assert launcher.jobs_pending(tmp_path) is True


def test_restart_projection_does_not_dispatch_or_rewrite_pending_operation(tmp_path,monkeypatch):
    from tests.test_sdk_update import managed,StagedAdapter
    manager=managed(tmp_path,StagedAdapter());job=manager.codex_activation;operation='update_'+'e'*32
    record={'operation_id':operation,'owner_boot_id':'old','status':'running','phase':'switching','target':{},'target_version':'1.1.0'}
    atomic(job.root/'current.json',{'operation_id':operation});atomic(job.root/(operation+'.json'),record)
    monkeypatch.setattr(activation,'startup_projection',lambda:{'boot_id':'new'})
    assert job.status()['status']=='needs_review' and job.task is None
    assert json.loads((job.root/(operation+'.json')).read_bytes())==record


def test_environment_error_is_a_clear_block_not_a_traceback(tmp_path,monkeypatch,capsys):
    def refuse(*a):raise launcher.environments.EnvironmentError('runtime_environment_changed')
    monkeypatch.setattr(launcher,'ensure',refuse)
    assert launcher.main(['ensure','--root',str(tmp_path)])==1
    message=capsys.readouterr().err
    assert 'runtime_environment_changed' in message and 'No new binding was granted' in message
    assert 'Traceback' not in message


def test_recapturing_changed_record_content_cannot_grant_trust(tmp_path):
    binding=fake_environment(tmp_path/'env');site=Path(binding['site']);info=next(site.glob('*.dist-info'))
    wheel=info/'WHEEL';wheel.write_bytes(b'modified historical metadata')
    with (info/'RECORD').open('a') as stream:
        stream.write(f'{info.name}/WHEEL,sha256='+base64.urlsafe_b64encode(b'0'*32).decode().rstrip('=')+',87\n')
    binding['files'],binding['packages']=env._inventory(site)
    binding['payload_files']=env.payload_files(Path(binding['path']))
    binding['digest']=env.digest({k:v for k,v in binding.items() if k!='digest'})
    with pytest.raises(env.EnvironmentError,match='changed'):env.verify(binding)


def test_handoff_uses_bound_interpreter_ports_and_ticket(tmp_path,monkeypatch):
    import os
    from sap_business_agents_platform import sdk_activation_handoff as h
    operation='update_'+'b'*32;area=tmp_path/'.local-data/sdk-update-jobs/codex'/operation
    api={'pid':os.getpid(),'parent':0,'birth':'2001','sid':'user','command':'api'}
    child={'pid':os.getpid()+100,'parent':0,'birth':'2002','sid':'user'}
    monkeypatch.setattr(h,'inventory',lambda *a,**k:{'sid':'user','processes':[api,child]})
    record={'operation_id':operation,'base':{'archive':str(tmp_path/'archive'),'api_port':9876,'site_port':5432,
            'environment':{'python':str(tmp_path/'old/Scripts/python.exe'),'base_python':'must-not-use-system-python'}},
            'target':{'source_digest':'target'},'configuration':{}}
    def create(command,*a):
        assert str(tmp_path/'old/Scripts/python.exe') in command and 'must-not-use' not in command
        assert '--port 9876' in command and '--site-port 5432' in command
        child['command']=command;ticket=json.loads((area/'handoff.json').read_bytes())
        identity={k:child[k] for k in ('pid','birth','sid','command')}
        atomic(area/'executor-ready.json',{'ticket_digest':ticket['digest'],'identity':identity})
        return child['pid']
    monkeypatch.setattr(h,'create_executor',create)
    identity=h.handoff(tmp_path,record)
    accepted=json.loads((area/'executor-accepted.json').read_bytes())
    assert accepted['identity']==identity
    assert accepted['ticket_digest']==json.loads((area/'handoff.json').read_bytes())['digest']


def test_read_base_requires_actual_bound_port_not_default_guess(tmp_path,monkeypatch):
    binding=fake_environment(tmp_path/'old');archive,manifest=release(tmp_path,binding)
    record={'archive':str(archive),'source_digest':manifest['digest'],'environment':binding,'api_port':9876,'site_port':5432}
    atomic(tmp_path/'.local-data/startup-api-current.json',record)
    monkeypatch.setattr(activation.runpy,'run_path',lambda *a:{'verify_archive':launcher.verify_archive})
    actual={'verified':True,'source_digest':manifest['digest'],'environment_digest':binding['digest'],'api_port':9876}
    monkeypatch.setattr(activation,'startup_projection',lambda:actual)
    assert activation.read_base(tmp_path)['api_port']==9876
    actual['api_port']=8765
    with pytest.raises(SDKManagerError) as error:activation.read_base(tmp_path)
    assert error.value.code=='sdk_running_release_unconfirmed'
