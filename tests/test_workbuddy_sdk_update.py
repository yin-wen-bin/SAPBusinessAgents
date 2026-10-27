"""Synthetic wheels, fake pip and isolated state only. No vendor or SAP calls."""
import asyncio
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from sap_business_agents_platform.sdk_manager import SDKManager, SDKManagerError
from sap_business_agents_platform.workbuddy_sdk_update import WorkBuddyPackageUpdate, source_binding, activate_candidate
from tests.test_sdk_manager import FakeAdapter, _write_runtime_registry
from tests.test_runtime_authentication import service

ROOT=Path(__file__).resolve().parents[1]


def wheel(name,version='1.1.0',cli=b'fake bundled executable',requires=()):
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr(f'{name}-{version}.dist-info/METADATA',f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n'+''.join(f'Requires-Dist: {x}\n' for x in requires))
        if cli is not None:z.writestr(WorkBuddyPackageUpdate.cli_member,cli)
    return f'{name}-{version}-py3-none-any.whl',raw.getvalue()


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();bootstrap=tmp_path/'python.exe';bootstrap.write_bytes(b'fake independent python')
    worker=root/'src/sap_business_agents_platform/workbuddy_worker.py';worker.parent.mkdir(parents=True);worker.write_bytes(b'original-worker')
    state=root/'.local-data/runtimes/workbuddy/state.json';state.parent.mkdir(parents=True);state.write_text('{"unchanged":"active-old-environment"}')
    records=dict([wheel('codebuddy_agent_sdk',requires=['anyio==1.0.0']),wheel('anyio','1.0.0',cli=None)])
    calls=[];network=[];commands=[]
    spec=importlib.util.spec_from_file_location('candidate_builder_test',ROOT/'scripts/prepare-workbuddy-package.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    def fake_process(command,**kwargs):
        commands.append((command,kwargs));text=''
        if 'venv' in command:
            building=Path(command[-1]);(building/'Scripts').mkdir();(building/'Scripts/python.exe').write_bytes(b'new python')
        elif 'install' in command:
            building=Path(command[0]).parents[1];cli=building/'Lib/site-packages'/WorkBuddyPackageUpdate.cli_member
            cli.parent.mkdir(parents=True);cli.write_bytes(b'fake bundled executable')
        elif '-c' in command and 'worker_surface' in command[-1]:
            text=json.dumps({'worker_surface':True})
        elif '-c' in command:
            text=json.dumps({'sdk_version':'1.1.0','dependencies':{'codebuddy-agent-sdk':'1.1.0','anyio':'1.0.0'}})
        elif '--version' in command:text='CodeBuddy Code v2.999.0'
        return SimpleNamespace(stdout=text,returncode=0)
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args and args[0]=='download':
            dest=Path(args[args.index('--dest')+1])
            for name,raw in records.items():(dest/name).write_bytes(raw)
        else:
            command=kwargs['command'];request=Path(command[command.index('--request')+1])
            with monkeypatch.context() as m:
                m.setattr(helper.sys,'executable',str(bootstrap))
                helper.prepare(request,command[-1],run=fake_process)
    def registry(request):
        network.append(str(request.url));name,version=request.url.path.split('/')[-3:-1]
        files=[]
        for filename,raw in records.items():
            if filename.startswith(name.replace('-','_')+'-'+version+'-'):
                files.append({'filename':filename,'size':len(raw),'yanked':False,'digests':{'sha256':hashlib.sha256(raw).hexdigest()}})
        return httpx.Response(200,json={'urls':files})
    instance=WorkBuddyPackageUpdate(root/'.local-data/sdk-update-cache/workbuddy',repository=root,python=bootstrap,
        run=run,client_factory=lambda:httpx.Client(transport=httpx.MockTransport(registry)))
    return SimpleNamespace(updater=instance,root=root,bootstrap=bootstrap,worker=worker,state=state,
        records=records,calls=calls,network=network,commands=commands,helper=helper,runner=fake_process)


def test_verified_download_prepare_candidate_never_changes_active_environment(pipeline):
    p=pipeline;before=p.state.read_bytes();bundle=p.updater.prepare('1.1.0',lambda **v:None)
    receipt=p.updater.prepare_candidate(bundle,{'default_model_id':'glm-5.3'},lambda **v:None)
    assert receipt['activated'] is False and receipt['cli_version']=='2.999.0'
    assert p.state.read_bytes()==before
    assert p.calls[0][1]['seconds']==1800 and p.calls[0][1]['idle_seconds']==60
    assert p.calls[1][1]['seconds']==700
    pip=next(c for c,k in p.commands if 'install' in c)
    assert '--no-index' in pip and '--require-hashes' in pip and '--no-deps' in pip
    assert all(Path(c[0])!=p.bootstrap for c,k in p.commands if 'pip' in c)
    assert sum('-c' in c for c,k in p.commands)==3 # worker surface + two metadata inspections
    assert sum('check' in c for c,k in p.commands)==2
    assert p.updater.verify_candidate(receipt)['sdk_version']=='1.1.0'


def test_verified_cache_reuses_without_network(pipeline):
    p=pipeline;first=p.updater.prepare('1.1.0',lambda **v:None);counts=(len(p.calls),len(p.network))
    assert p.updater.prepare('1.1.0',lambda **v:None)==first
    assert counts==(len(p.calls),len(p.network))


def test_codex_in_place_installer_is_unavailable_for_workbuddy(pipeline):
    p=pipeline;bundle=p.updater.prepare('1.1.0',lambda **v:None);count=len(p.calls)
    with pytest.raises(SDKManagerError) as caught:p.updater.install(bundle,lambda **v:None)
    assert caught.value.code=='workbuddy_update_controlled_activation_required' and len(p.calls)==count


@pytest.mark.parametrize('kind',['wheel','cli_binding','bootstrap','directory','worker_during_install','dependency','wrong_cli','unknown_layout'])
def test_bad_candidate_cannot_touch_active_state(pipeline,kind):
    p=pipeline;before=p.state.read_bytes()
    if kind=='unknown_layout':
        p.records.clear();p.records.update([wheel('codebuddy_agent_sdk',cli=None)])
        with pytest.raises(SDKManagerError) as caught:p.updater.prepare('1.1.0',lambda **v:None)
        assert caught.value.code=='workbuddy_update_target_unsupported'
        assert p.state.read_bytes()==before;return
    bundle=p.updater.prepare('1.1.0',lambda **v:None)
    if kind=='wheel':(Path(bundle['directory'])/next(iter(bundle['files']))).write_bytes(b'changed')
    if kind=='cli_binding':bundle['cli_binding']['sha256']='0'*64
    if kind=='bootstrap':p.bootstrap.write_bytes(b'changed')
    if kind=='directory':bundle['directory']=str(p.root)
    if kind in {'worker_during_install','dependency','wrong_cli'}:
        original=p.runner
        def broken(command,**kwargs):
            result=original(command,**kwargs)
            if kind=='worker_during_install' and 'install' in command:p.worker.write_bytes(b'changed')
            if kind=='dependency' and '-c' in command:result.stdout=result.stdout.replace('"anyio": "1.0.0"','"anyio": "9.0.0"')
            if kind=='wrong_cli' and '--version' in command:result.stdout='not a version'
            return result
        original_prepare=p.helper.prepare
        p.helper.prepare=lambda *a,**kw:original_prepare(*a,**{**kw,'run':broken})
    with pytest.raises((SDKManagerError,ValueError)):p.updater.prepare_candidate(bundle,{},lambda **v:None)
    assert p.state.read_bytes()==before


@pytest.mark.parametrize('version',['1.1.0a1','1.1.0.dev1','1.1.0+local'])
def test_unreviewed_versions_do_not_download(pipeline,version):
    with pytest.raises(SDKManagerError):pipeline.updater.prepare(version,lambda **v:None)
    assert not pipeline.calls


def manager(tmp_path):
    registry=tmp_path/'sdks.json';_write_runtime_registry(registry);adapter=FakeAdapter()
    m=SDKManager(registry,tmp_path,adapters={'python':adapter});s={'environment_digest':'old','default_model_id':'glm-5.3','service_site':'china','authentication_revision':9,'installation':{'python':'fake'}}
    m.workbuddy.environment=SimpleNamespace(state=lambda:copy.deepcopy(s),release=lambda:{'sdk_version':'1.0.0','cli_version':'2.0.0'})
    m.workbuddy.supervisor=SimpleNamespace(active={},queued={},pending=lambda:False)
    return m,adapter,s


def test_version_reads_and_candidate_result_are_flat_no_repair_or_switch(tmp_path):
    async def scenario():
        m,a,s=manager(tmp_path);original=copy.deepcopy(s);events=[]
        class Updater:
            def prepare(self,target,emit):events.append('download');return {'target':target}
            def prepare_candidate(self,bundle,binding,emit,**kwargs):
                events.append('prepare');emit(phase='installation')
                return {'environment_digest':'new','sdk_version':'1.1.0','cli_version':'2.1.0','activated':False}
        m._workbuddy_updater=lambda:Updater()
        result=await m.check_versions('codebuddy-agent-sdk')
        assert result['current_version']=='1.0.0' and result['update_available']
        assert not a.updates
        result=await m._update_workbuddy(m._get_sdk('codebuddy-agent-sdk'))
        assert result['maintenance_kind']=='upgrade_candidate' and not result['activated']
        assert result['current_version']=='1.0.0' and result['update_status']['outcome']=='candidate_prepared'
        assert result['update_status']['status']=='needs_review' and s==original
        with pytest.raises(SDKManagerError):await m._update_workbuddy(m._get_sdk('codebuddy-agent-sdk'))
        assert events==['download','prepare']
        assert (await m.check_versions('codebuddy-agent-sdk'))['update_status']['status']=='needs_review'
    asyncio.run(scenario())


@pytest.mark.parametrize('block',['business','authentication','cleanup','binding'])
def test_source_and_maintenance_gates_rechecked_after_download(tmp_path,block):
    async def scenario():
        m,a,s=manager(tmp_path);auth,_=service(tmp_path/'auth',busy=lambda p:[{'code':block}] if block!='binding' else [])
        m.authentication_service=auth;calls=[]
        class Updater:
            def prepare(self,*args):
                calls.append('download')
                if block=='binding':s['default_model_id']='different'
                return {}
            def prepare_candidate(self,*args):pytest.fail('must not install')
        m._workbuddy_updater=lambda:Updater()
        with pytest.raises(SDKManagerError):await m._update_workbuddy(m._get_sdk('codebuddy-agent-sdk'))
        assert calls==['download'] and not auth.sdk_maintenance_active
    asyncio.run(scenario())


def test_private_activation_requires_auditor_and_target_binding(tmp_path):
    state={'environment_digest':'old','default_model_id':'glm-5.3'}
    env=SimpleNamespace()
    with pytest.raises(SDKManagerError):activate_candidate(env,{},verify_target=None,clearance=lambda:{})
    from contextlib import nullcontext
    env.maintenance=nullcontext;env.state=lambda:state
    with pytest.raises(SDKManagerError) as caught:
        activate_candidate(env,{'environment_digest':'new','source_binding':{}},verify_target=lambda *a:pytest.fail('must not audit'),clearance=lambda:{})
    assert caught.value.code=='sdk_update_binding_changed'


def test_same_operation_recovers_original_candidate_without_another_install(pipeline):
    p=pipeline;b=p.updater.prepare('1.1.0',lambda **v:None);op='update_'+'a'*32
    first=p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op);count=len(p.calls)
    assert p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op)==first
    assert len(p.calls)==count
    with pytest.raises(SDKManagerError):p.updater.prepare_candidate(b,{'changed':True},lambda **v:None,operation_id=op)
    assert len(p.calls)==count


def test_original_candidate_record_and_inventory_are_required_on_resume(pipeline):
    p=pipeline;b=p.updater.prepare('1.1.0',lambda **v:None);op='update_'+'b'*32
    result=p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op)
    folder=p.updater.root/('candidate-'+op);(folder/'result.json').unlink()
    count=len(p.calls)
    with pytest.raises(SDKManagerError) as caught:p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op)
    assert caught.value.code=='sdk_update_outcome_unconfirmed' and len(p.calls)==count


@pytest.mark.parametrize('field,value',[('activated',True),('cleanup_complete',False),('operation_id','different'),('sdk_version','9.0.0')])
def test_resume_cannot_accept_modified_receipt(pipeline,field,value):
    p=pipeline;b=p.updater.prepare('1.1.0',lambda **v:None);op='update_'+'d'*32
    p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op)
    file=p.updater.root/('candidate-'+op)/'result.json';receipt=json.loads(file.read_bytes());receipt[field]=value
    file.write_text(json.dumps(receipt));count=len(p.calls)
    with pytest.raises(SDKManagerError):p.updater.prepare_candidate(b,{},lambda **v:None,operation_id=op)
    assert len(p.calls)==count


def test_api_restart_keeps_unknown_candidate_blocked_without_replaying_install(tmp_path):
    m,a,s=manager(tmp_path);definition=m._get_sdk('codebuddy-agent-sdk')
    op='update_'+'c'*32
    m._updates[definition.sdk_id]={'operation_id':op,'status':'running','phase':'installation','started_at':'2026-10-25'}
    m._update_progress(definition,op,status='needs_review',outcome='candidate_unconfirmed')
    restarted,adapter,state=manager(tmp_path)
    assert restarted.maintenance_status('workbuddy')['status']=='needs_review'
    with pytest.raises(SDKManagerError) as caught:asyncio.run(restarted.update('codebuddy-agent-sdk'))
    assert caught.value.code=='sdk_update_outcome_unconfirmed' and not adapter.updates


@pytest.mark.parametrize('kind',['corrupt','oversize'])
def test_invalid_existing_update_diagnostics_fail_closed(tmp_path,kind):
    root=tmp_path/'.local-data/sdk-update-diagnostics/codebuddy-agent-sdk';root.mkdir(parents=True)
    (root/'update_invalid.json').write_text('?' if kind=='corrupt' else ' '*65537)
    m,a,s=manager(tmp_path)
    assert m.maintenance_status('workbuddy')['status']=='needs_review'
    with pytest.raises(SDKManagerError):asyncio.run(m.update('codebuddy-agent-sdk'))
    assert not a.updates


@pytest.mark.parametrize('reason',['business','missing_clearance','expired_clearance','certificate','authentication','model','late_busy'])
def test_activation_rejects_missing_or_changed_proof_without_writing_state(tmp_path,monkeypatch,reason):
    from contextlib import nullcontext
    from sap_business_agents_platform.workbuddy_environment import digest
    import time
    original={'environment_digest':'old','default_model_id':'glm-5.3','authentication_revision':9,'service_site_digest':'china',
              'models':{'glm-5.3':{'check_digest':'old-pass'}},'validation_ledger':{'old':'unchanged'}}
    states=[copy.deepcopy(original)]
    def guard(snapshot):
        if reason=='model':raise SDKManagerError('No model proof.',code='runtime_model_check_required')
    env=SimpleNamespace(maintenance=nullcontext,state=lambda:copy.deepcopy(states[0]),release=lambda key:{'environment_digest':key},
                        assert_verification_binding=guard,mutate=lambda fn:pytest.fail('must not mutate'))
    count=[0]
    def clear():
        count[0]+=1
        return {'all_writers_checked':reason!='missing_clearance','state_digest':digest(original),'old_writers':[],
                'active_jobs':['busy'] if reason=='business' or reason=='late_busy' and count[0]>1 else [],
                'cleanup_pending':[],'process_inventory_digest':'observed','checked_at':time.time()-(60 if reason=='expired_clearance' else 0)}
    cert={'id':'fake','evidence':{}}
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_integration.audit',lambda *a:{} if reason=='certificate' else cert)
    def audit(*args):return {'certificate':cert,'model_check':{'check_digest':'new-pass'},'admitted':True,
         'authentication_environment':'old' if reason=='authentication' else 'new',
         'snapshot':{'environment_digest':'new','model':'glm-5.3','authentication_binding_revision':9,'service_site_digest':'china'}}
    with pytest.raises(SDKManagerError):activate_candidate(env,{'environment_digest':'new','source_binding':source_binding(original)},verify_target=audit,clearance=clear)
    assert states[0]==original


def test_controlled_switch_and_rollback_preserve_new_and_old_history(tmp_path,monkeypatch):
    from contextlib import nullcontext
    import time
    from sap_business_agents_platform.workbuddy_environment import digest
    from sap_business_agents_platform.workbuddy_sdk_update import rollback_candidate
    original={'environment_digest':'old','default_model_id':'glm-5.3','authentication_revision':9,'service_site_digest':'china',
              'models':{'glm-5.3':{'check_digest':'old-pass'}},'validation_ledger':{'old':'unchanged'}}
    state=copy.deepcopy(original)
    def mutate(fn):fn(state)
    releases={key:{'environment_digest':key,'sdk_version':'1.0.0' if key=='old' else '1.1.0','cli_version':'2.0.0'} for key in ('old','new')}
    env=SimpleNamespace(maintenance=nullcontext,state=lambda:copy.deepcopy(state),_release=lambda key:releases[key],
        release=lambda key:releases[key],assert_verification_binding=lambda snapshot:None,mutate=mutate)
    certs={key:{'id':key,'evidence':{'target':key}} for key in releases}
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_integration.audit',lambda env,release,evidence:certs[release['environment_digest']])
    def audit(release,saved):
        key=release['environment_digest']
        return {'certificate':certs[key],'model_check':{'check_digest':key+'-pass'},'admitted':True,'authentication_environment':key,
            'snapshot':{'environment_digest':key,'model':saved['default_model_id'],'authentication_binding_revision':9,'service_site_digest':'china'}}
    def clear():return {'all_writers_checked':True,'state_digest':digest(state),'old_writers':[],'active_jobs':[],
        'cleanup_pending':[],'process_inventory_digest':'observed','checked_at':time.time()}
    receipt={'environment_digest':'new','source_binding':source_binding(state)}
    assert activate_candidate(env,receipt,verify_target=audit,clearance=clear)['status']=='activated'
    assert state['environment_digest']=='new' and state['model_check_history']==[original['models']['glm-5.3']]
    assert state['validation_ledger']==original['validation_ledger']
    rollback_candidate(env,'old',verify_target=audit,clearance=clear)
    assert state['environment_digest']=='old' and len(state['environment_switch_history'])==2
    assert set(state['sdk_integration_records'])=={'old','new'} and len(state['model_check_history'])==2
    assert state['validation_ledger']==original['validation_ledger']


def test_actual_binding_checker_rejects_candidate_with_failed_model_evidence(pipeline,monkeypatch):
    p=pipeline;bundle=p.updater.prepare('1.1.0',lambda **v:None)
    receipt=p.updater.prepare_candidate(bundle,{},lambda **v:None)
    from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment,digest
    from sap_business_agents_platform.workbuddy_site import make_configuration
    from contextlib import nullcontext
    import time
    env=WorkBuddyEnvironment(p.root);release=env._release(receipt['environment_digest']);site=make_configuration('china',1)
    state={'environment_digest':'old','default_model_id':'glm-5.3','authenticated':True,'authentication_revision':9,
        'authentication_site':site,**site}
    env.maintenance=nullcontext;env.state=lambda:copy.deepcopy(state)
    env.mutate=lambda fn:pytest.fail('actual checker must reject before mutation')
    cert={'id':'synthetic','evidence':{}}
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_integration.audit',lambda *a:cert)
    snapshot={'provider_id':'workbuddy','environment_digest':release['environment_digest'],'model':'glm-5.3',
        'reasoning_effort':None,'version':release['sdk_version'],'cli_version':release['cli_version'],
        'sdk_fingerprint':digest(release['files']),'authentication_revision':9,'authentication_binding_revision':9,**site}
    def auditor(*a):return {'certificate':cert,'snapshot':snapshot,'model_check':{'compatible':False,
        'environment_digest':release['environment_digest']},'admitted':True,'authentication_environment':release['environment_digest']}
    def clearance():return {'all_writers_checked':True,'state_digest':digest(state),'old_writers':[],'active_jobs':[],
        'cleanup_pending':[],'process_inventory_digest':'checked','checked_at':time.time()}
    with pytest.raises(SDKManagerError) as caught:
        activate_candidate(env,{**receipt,'source_binding':source_binding(state)},verify_target=auditor,clearance=clearance)
    assert caught.value.code=='runtime_model_check_required'


def test_selected_target_change_blocks_before_download(tmp_path):
    m,a,s=manager(tmp_path)
    m._workbuddy_updater=lambda:pytest.fail('must not download changed target')
    with pytest.raises(SDKManagerError) as caught:
        asyncio.run(m._update_workbuddy(m._get_sdk('codebuddy-agent-sdk'),expected_target_version='1.2.0'))
    assert caught.value.code=='sdk_update_binding_changed' and a.updates==[]


def test_existing_rest_route_preserves_target_and_never_claims_candidate_installed(tmp_path):
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_sdk_manager import FakeSDKManager,HealthyEmbeddedProvider
    fake=FakeSDKManager();calls=[]
    async def update(sdk_id,**kwargs):
        calls.append((sdk_id,kwargs))
        return {'current_version':'1.0.0','maintenance_kind':'upgrade_candidate','activated':False,'restart_required':False}
    fake.update=update
    app=create_app(Settings(repository_root=ROOT,data_root=tmp_path/'data',draft_root=tmp_path/'drafts',skillhub_root=tmp_path/'skills'),
        planner=object(),sdk_manager=fake,embedded_provider=HealthyEmbeddedProvider())
    with TestClient(app) as client:
        reply=client.post('/api/system/sdks/codebuddy-agent-sdk/update?expectedTargetVersion=1.1.0',headers={'X-SAPBA-Action':'sdk-update'})
        assert reply.status_code==200 and reply.json()['item']['activated'] is False
        assert 'installed' not in reply.json()['message'] and 'unchanged' in reply.json()['message']
        assert calls==[('codebuddy-agent-sdk',{'expected_target_version':'1.1.0'})]
