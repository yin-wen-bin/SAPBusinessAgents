"""No external SDK, installation, model or SAP. All package contents are synthetic."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import httpx
import pytest

from sap_business_agents_platform.sdk_manager import SDKManager, SDKManagerError
from sap_business_agents_platform.sdk_update import CodexPackageUpdate
from tests.test_sdk_manager import FakeAdapter, FakeRuntimeProbe, _write_runtime_registry
from tests.test_runtime_authentication import service


def wheel(name,version='1.1.0',requires=()):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as archive:
        archive.writestr(f'{name}-{version}.dist-info/METADATA',
            f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n'+''.join(f'Requires-Dist: {r}\n' for r in requires))
    return f'{name}-{version}-py3-none-any.whl',data.getvalue()


@pytest.fixture
def pipeline(tmp_path):
    names=[wheel('openai_codex',requires=['openai-codex-cli-bin==1.1.0']),
           wheel('openai_codex_cli_bin'),wheel('existing_dependency','9.0.0')]
    contents=dict(names); calls=[]; network=[]
    def request(req):
        network.append(str(req.url))
        package,version=req.url.path.split('/')[-3:-1]
        files=[{'filename':name,'size':len(data),'digests':{'sha256':hashlib.sha256(data).hexdigest()},'yanked':False}
               for name,data in names if name.startswith(package.replace('-','_')+'-'+version+'-')]
        return httpx.Response(200,json={'urls':files})
    def run(arguments,**kwargs):
        calls.append((arguments,kwargs))
        if arguments[0]=='download':
            directory=Path(arguments[arguments.index('--dest')+1])
            for name,data in contents.items(): (directory/name).write_bytes(data)
            kwargs['progress'](phase='downloading',filename=names[1][0],downloaded_bytes=50,total_bytes=100)
        elif '--dry-run' in arguments:
            directory=Path(arguments[arguments.index('--find-links')+1])
            # Existing dependency is already sufficient: pip must not eagerly
            # upgrade it just because a newer wheel was prefetched.
            items=[]
            for name,data in names[:2]:
                items.append({'download_info':{'url':(directory/name).as_uri(),'archive_info':{'hashes':{'sha256':hashlib.sha256(data).hexdigest()}}},
                              'metadata':{'name':name.split('-')[0],'version':'1.1.0'}})
            Path(arguments[arguments.index('--report')+1]).write_text(json.dumps({'install':items}))
    instance=CodexPackageUpdate(tmp_path/'cache with spaces',run=run,
        client_factory=lambda:httpx.Client(transport=httpx.MockTransport(request)))
    return instance,calls,network,contents


def test_separate_download_and_hash_verified_local_minimal_install(pipeline):
    instance,calls,network,_=pipeline; events=[]
    bundle=instance.prepare('1.1.0',lambda **value:events.append(value))
    assert len(network)==3 and bundle['cli_version']=='1.1.0'
    receipt=instance.install(bundle,lambda **value:events.append(value))
    download,resolution,install=calls
    assert download[1]['seconds']==1800 and download[1]['idle_seconds']==60
    assert '--only-binary=:all:' in download[0]
    assert '--dry-run' in resolution[0] and '--no-index' in resolution[0]
    assert 0<resolution[1]['seconds']<=300 and 0<install[1]['seconds']<=resolution[1]['seconds']
    assert '--require-hashes' in install[0] and '--no-deps' in install[0] and '--no-index' in install[0]
    locked=Path(install[0][install[0].index('-r')+1]).read_text()
    assert 'openai-codex==1.1.0' in locked and 'openai-codex-cli-bin==1.1.0' in locked
    assert 'existing-dependency' not in locked
    assert receipt=={'expected_sdk':'1.1.0','expected_cli':'1.1.0'}


def test_verified_cache_reuses_without_network_or_download(pipeline):
    instance,calls,network,_=pipeline
    first=instance.prepare('1.1.0',lambda **v:None)
    previous=(len(calls),len(network));events=[]
    assert instance.prepare('1.1.0',lambda **v:events.append(v))==first
    assert previous==(len(calls),len(network)) and events[0]['cache_reused']


def test_bad_remote_hash_and_partial_download_never_install(pipeline):
    instance,calls,network,contents=pipeline
    contents[next(iter(contents))]=b'not-an-authentic-wheel'
    with pytest.raises(SDKManagerError) as caught:instance.prepare('1.1.0',lambda **v:None)
    assert caught.value.code=='sdk_download_integrity_failed'
    assert len(calls)==1 and not (instance._cache('1.1.0')/'verified.json').exists()


@pytest.mark.parametrize('tamper',['file','manifest','cli','package'])
def test_cache_tamper_rejected_before_local_install(pipeline,tamper):
    instance,calls,_,_=pipeline;bundle=instance.prepare('1.1.0',lambda **v:None)
    if tamper=='file':
        (Path(bundle['directory'])/next(iter(bundle['files']))).write_bytes(b'changed')
    elif tamper=='manifest':bundle['files'][next(iter(bundle['files']))]['sha256']='0'*64
    elif tamper=='cli':bundle['cli_version']='2.0.0'
    else:bundle['files'][next(iter(bundle['files']))]['name']='another-package'
    with pytest.raises(SDKManagerError):instance.install(bundle,lambda **v:None)
    assert len(calls)==1


def test_remote_or_changed_transaction_is_refused(pipeline):
    instance,calls,_,_=pipeline;bundle=instance.prepare('1.1.0',lambda **v:None)
    original=instance.run
    def tamper(arguments,**kwargs):
        original(arguments,**kwargs)
        if '--report' in arguments:
            report=Path(arguments[arguments.index('--report')+1]);data=json.loads(report.read_bytes())
            data['install'][0]['download_info']['url']='https://untrusted.invalid/other.whl'
            report.write_text(json.dumps(data))
    instance.run=tamper
    with pytest.raises(SDKManagerError) as caught:instance.install(bundle,lambda **v:None)
    assert caught.value.code=='sdk_update_plan_invalid' and len(calls)==2


def test_install_budget_is_one_shared_deadline(pipeline):
    instance,calls,_,_=pipeline;bundle=instance.prepare('1.1.0',lambda **v:None)
    now=[0.0];instance.clock=lambda:now[0]
    original=instance.run
    def slow(arguments,**kwargs):
        original(arguments,**kwargs)
        if '--dry-run' in arguments:now[0]=301
    instance.run=slow
    with pytest.raises(SDKManagerError) as caught:instance.install(bundle,lambda **v:None)
    assert caught.value.code=='sdk_update_timeout' and len(calls)==2


class StagedAdapter(FakeAdapter):
    def __init__(self):super().__init__();self.cli='1.0.0';self.events=[]
    async def prepare_update(self,definition,target_version,*,cache_root,progress):
        self.events.append('download');progress(phase='verification',expected_cli=target_version)
        return target_version
    async def install_prepared(self,definition,prepared,*,progress):
        self.events.append('install');progress(phase='installation');self.installed=prepared;self.cli=prepared
        return {'expected_sdk':prepared,'expected_cli':prepared}
    def installed_runtime_version(self,definition):return self.cli
    def installed_dependencies_consistent(self,definition):return self.cli==self.installed


def managed(tmp_path,adapter):
    registry=tmp_path/'sdks.json';_write_runtime_registry(registry)
    return SDKManager(registry,tmp_path,adapters={'python':adapter},runtime_probes={'codex':FakeRuntimeProbe()})


def test_download_outside_maintenance_install_inside_and_no_configuration_write(tmp_path):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter);auth,_=service(tmp_path/'auth');manager.authentication_service=auth
        original=manager._runtime_config.copy()
        prepare=adapter.prepare_update;install=adapter.install_prepared
        async def download(*a,**kw):
            assert not auth.sdk_maintenance_active
            assert not manager._locks['codex-python-sdk'].locked()
            return await prepare(*a,**kw)
        async def apply(*a,**kw):
            assert 'codex' in auth.sdk_maintenance_active
            return await install(*a,**kw)
        adapter.prepare_update=download;adapter.install_prepared=apply
        result=await manager.update('codex-python-sdk')
        assert result['updated_to']=='1.1.0' and result['update_status']['phase']=='restart_pending'
        assert manager._runtime_config==original and not auth.sdk_maintenance_active
        assert list((tmp_path/'.local-data/sdk-update-diagnostics/codex-python-sdk').glob('*.json'))
    asyncio.run(scenario())


@pytest.mark.parametrize('block',['business','authentication','cleanup'])
def test_gate_rechecked_after_download_without_installing(tmp_path,block):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter)
        auth,_=service(tmp_path/'auth',busy=lambda provider:[{'code':block}]);manager.authentication_service=auth
        with pytest.raises(SDKManagerError):await manager.update('codex-python-sdk')
        assert adapter.events==['download'] and adapter.installed=='1.0.0'
        assert manager.maintenance_status('codex')['outcome']=='not_installed'
    asyncio.run(scenario())


def test_unknown_install_blocks_repeat_until_explicit_version_review(tmp_path):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter)
        async def fail(*a,**kw):
            kw['progress'](phase='installation')
            raise SDKManagerError('fake timeout',code='sdk_update_timeout',detail={'phase':'installation'})
        adapter.install_prepared=fail
        with pytest.raises(SDKManagerError):await manager.update('codex-python-sdk')
        assert manager.maintenance_status('codex')['status']=='needs_review'
        with pytest.raises(SDKManagerError) as caught:await manager.update('codex-python-sdk')
        assert caught.value.code=='sdk_update_outcome_unconfirmed'
        checked=await manager.check_versions('codex-python-sdk')
        assert checked['update_status']['status']=='failed' and checked['update_status']['outcome']=='reviewed'
    asyncio.run(scenario())


def test_duplicate_and_cancel_do_not_release_maintenance_early(tmp_path):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter);auth,_=service(tmp_path/'auth');manager.authentication_service=auth
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow(*a,**kw):
            kw['progress'](phase='installation');entered.set();await release.wait()
            return {'expected_sdk':'1.1.0','expected_cli':'1.1.0'}
        adapter.install_prepared=slow
        task=asyncio.create_task(manager.update('codex-python-sdk'));await entered.wait()
        with pytest.raises(SDKManagerError) as caught:await manager.update('codex-python-sdk')
        assert caught.value.code=='sdk_update_busy'
        task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
        assert 'codex' in auth.sdk_maintenance_active and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert not auth.sdk_maintenance_active and manager.maintenance_status('codex')['status']=='needs_review'
    asyncio.run(scenario())


def test_wrong_cli_cannot_claim_update_success(tmp_path):
    adapter=StagedAdapter();manager=managed(tmp_path,adapter)
    adapter.installed_runtime_version=lambda definition:'0.0.0'
    with pytest.raises(SDKManagerError) as caught:asyncio.run(manager.update('codex-python-sdk'))
    assert caught.value.code=='sdk_update_not_applied' and manager.maintenance_status('codex')['status']=='needs_review'


def test_explicit_review_keeps_original_error_but_not_false_current_failure(tmp_path):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter)
        install=adapter.install_prepared
        async def lost(*args,**kwargs):
            await install(*args,**kwargs)
            raise SDKManagerError('synthetic response loss',code='sdk_update_timeout')
        adapter.install_prepared=lost
        with pytest.raises(SDKManagerError):await manager.update('codex-python-sdk')
        checked=await manager.check_versions('codex-python-sdk')
        state=checked['update_status']
        assert state['status']=='completed' and state['phase']=='restart_pending' and state['error'] is None
        assert state['previous_error']['code']=='sdk_update_timeout'
    asyncio.run(scenario())


@pytest.mark.skipif(os.name!='nt',reason='Codex staged installer is Windows-only')
@pytest.mark.parametrize('timeout_kind',['budget','stall'])
def test_owned_job_cleans_synthetic_descendants_on_timeout(tmp_path,monkeypatch,timeout_kind):
    import subprocess
    from sap_business_agents_platform.sdk_update import OwnedPip
    from sap_business_agents_platform.workbuddy_job import process_identity
    import sap_business_agents_platform.sdk_update as module
    original=subprocess.Popen
    fixture=Path(__file__).parent/'fixtures/sdk_package_worker.py'
    def spawn(command,*args,**kwargs):
        assert Path(command[3]).name=='sdk-package-helper.py'
        return original([*command[:3],str(fixture)],*args,**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',spawn)
    events=[];record=tmp_path/'child.json'
    # Controlled readiness clock: under a concurrent build Windows can take
    # >0.8s just to create the fixture. Exercise the same 0.8s timeout *after*
    # its child exists, rather than accidentally testing "no child started".
    # Production uses the unmodified monotonic clock, including startup time.
    import time
    started=time.monotonic();ready=[None]
    def clock():
        now=time.monotonic()
        if ready[0] is None and record.is_file():ready[0]=now
        return now-ready[0] if ready[0] is not None else (20 if now-started>10 else 0)
    with pytest.raises(SDKManagerError) as caught:
        OwnedPip(clock=clock).run(['fixture-hang',str(record)],phase='downloading',seconds=.8 if timeout_kind=='budget' else 10,
                       idle_seconds=.8 if timeout_kind=='stall' else None,progress=lambda **v:events.append(v))
    assert caught.value.code==('sdk_download_timeout' if timeout_kind=='budget' else 'sdk_download_stalled')
    assert caught.value.detail['cleanup_complete']
    assert record.is_file() and process_identity(json.loads(record.read_bytes())['child']) is None
    assert events[-1]['cleanup_complete'] is True


@pytest.mark.skipif(os.name!='nt',reason='Codex staged installer is Windows-only')
def test_owned_output_unknown_size_is_progress_and_raw_text_is_not_retained(monkeypatch):
    import subprocess
    import sap_business_agents_platform.sdk_update as module
    original=subprocess.Popen
    fixture=Path(__file__).parent/'fixtures/sdk_package_worker.py'
    def spawn(command,*args,**kwargs):
        assert Path(command[3]).name=='sdk-package-helper.py'
        assert not any(k.upper().startswith(('SAP_','OPENAI_','WORKBUDDY_')) for k in kwargs['env'])
        return original([*command[:3],str(fixture)],*args,**kwargs)
    monkeypatch.setenv('OPENAI_API_KEY','never-forward-this')
    monkeypatch.setenv('SAP_PASSWORD','never-forward-this')
    monkeypatch.setattr(module.subprocess,'Popen',spawn)
    events=[]
    module.OwnedPip().run(['fixture-stream'],phase='downloading',seconds=10,progress=lambda **v:events.append(v))
    assert any(e.get('downloaded_bytes')==50 and e.get('total_bytes') is None for e in events)
    assert 'synthetic-secret' not in json.dumps(events) and events[-1]['cleanup_complete']


def test_unconfirmed_cleanup_keeps_install_reservation(tmp_path):
    async def scenario():
        adapter=StagedAdapter();manager=managed(tmp_path,adapter);auth,_=service(tmp_path/'auth');manager.authentication_service=auth
        pending,release=asyncio.Event(),asyncio.Event()
        async def install(*a,**kw):
            kw['progress'](phase='installation')
            kw['progress'](phase='cleanup',status='cleanup_pending',cleanup_complete=False)
            pending.set();await release.wait()
            kw['progress'](phase='installation',status='running',cleanup_complete=True)
            raise SDKManagerError('fake timeout',code='sdk_update_timeout',detail={'phase':'installation'})
        adapter.install_prepared=install
        task=asyncio.create_task(manager.update('codex-python-sdk'));await pending.wait()
        assert manager.maintenance_status('codex')['status']=='cleanup_pending'
        assert 'codex' in auth.sdk_maintenance_active and not task.done()
        with pytest.raises(SDKManagerError):await manager.update('codex-python-sdk')
        release.set()
        with pytest.raises(SDKManagerError):await task
        assert not auth.sdk_maintenance_active
    asyncio.run(scenario())


def test_summary_overlays_live_update_without_full_runtime_checks(tmp_path):
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_sdk_manager import FakeSDKManager,HealthyEmbeddedProvider
    sdk=FakeSDKManager();sdk.maintenance_status=lambda provider:{'status':'running','phase':'downloading','downloaded_bytes':55,'total_bytes':100}
    settings=Settings(repository_root=Path(__file__).parents[1],data_root=tmp_path/'data',draft_root=tmp_path/'draft',skillhub_root=tmp_path/'skills')
    app=create_app(settings,planner=object(),embedded_provider=HealthyEmbeddedProvider(),sdk_manager=sdk)
    with TestClient(app) as client:
        item=client.get('/api/system/sdk-runtimes?view=summary').json()['items'][0]
    assert item['update_status']['downloaded_bytes']==55
