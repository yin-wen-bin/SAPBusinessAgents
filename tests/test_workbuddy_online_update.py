"""Complete online maintenance with deterministic adapters: no registry/SDK/SAP."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.sdk_manager import SDKManagerError
from sap_business_agents_platform.sdk_update import failure
from sap_business_agents_platform.workbuddy_online_update import OnlineUpdate
from tests.test_workbuddy_sdk_update import manager
from tests.test_runtime_authentication import service


def rig(tmp_path,monkeypatch,scenario='success'):
    m,a,s=manager(tmp_path)
    m.workbuddy.environment.release=lambda *_:{'sdk_version':'1.0.0' if s['environment_digest']=='old' else '1.1.0',
        'cli_version':'2.0.0','environment_digest':s['environment_digest']}
    auth,_=service(tmp_path/'auth');m.authentication_service=auth
    events=[]
    class Updater:
        def prepare(self,target,emit):
            events.append('download')
            if scenario=='target_binding':s['default_model_id']='changed'
            if scenario=='download':raise failure('sdk_download_integrity_failed','verification')
            return {'target':target}
        def prepare_candidate(self,bundle,binding,emit,**kwargs):
            events.append('install');emit(phase='installation',cleanup_complete=True)
            if scenario=='install':raise failure('sdk_update_failed','installation')
            return {'environment_digest':'new','sdk_version':'1.1.0','cli_version':'2.1.0'}
    class Backend:
        switch_safe=True
        def __init__(self,*_):pass
        def preflight(self):
            events.append('preflight')
            if scenario=='busy':raise failure('runtime_authentication_busy','waiting_maintenance')
        async def verify(self,receipt,emit,claim):
            emit(phase='authentication_verification');events.append('authentication')
            if scenario=='auth':raise failure('workbuddy_update_authentication_failed','authentication_verification')
            emit(phase='infrastructure_validation');events.append('infrastructure')
            if scenario=='infra':raise failure('workbuddy_update_infrastructure_failed','infrastructure_validation')
            claim();events.append('model')
            if scenario=='model':raise failure('workbuddy_update_model_failed','model_verification')
            if scenario=='model_unknown':raise TimeoutError()
            if scenario=='gate_missing':raise failure('workbuddy_update_evidence_required','model_verification')
            return object()
        def enter_switch(self):events.append('barrier');self.switch_safe=False;return object()
        def activate(self,*_):
            if scenario=='switch_before':raise failure('sdk_update_binding_changed','switching')
            s['environment_digest']='new';events.append('activate')
            if scenario=='switch_after':raise failure('sdk_update_failed','switching')
        def is_target(self,*_):return s['environment_digest']=='new'
        def health(self,*_):
            events.append('health')
            if scenario in {'health','rollback_fail'}:raise failure('sdk_update_not_applied','health_check')
            self.switch_safe=True
        async def rollback(self,*_):
            events.append('rollback')
            if scenario=='rollback_fail':raise failure('workbuddy_update_rollback_failed','rollback')
            s['environment_digest']='old';self.switch_safe=True
        def leave_switch(self,*_):events.append('release_barrier')
        def cleanup_complete(self):return scenario!='cleanup'
        async def close(self):events.append('close')
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_online_update.UpdateBackend',Backend)
    m._workbuddy_updater=lambda:Updater()
    return m,a,s,events


def test_full_flow_returns_operation_then_activates_once(tmp_path,monkeypatch):
    async def run():
        m,a,s,events=rig(tmp_path,monkeypatch)
        original=copy.deepcopy(s)
        result=await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0')
        assert result['maintenance_kind']=='online_upgrade' and result['current_version']=='1.0.0'
        assert result['update_status']['status']=='running' and s==original
        with pytest.raises(SDKManagerError) as caught:await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0')
        assert caught.value.code=='sdk_update_busy'
        await m.online_update.task
        status=m.maintenance_status('workbuddy')
        assert status['status']=='completed' and status['outcome']=='activated'
        assert status['observed_versions']=={'sdk':'1.1.0','cli':'2.1.0'}
        assert events.count('model')==events.count('install')==events.count('activate')==1
        assert s['default_model_id']==original['default_model_id'] and s['environment_digest']=='new'
        assert not m.authentication_service.sdk_maintenance_active and not a.updates
        before=list(events);m.maintenance_status('workbuddy');await m.check_versions('codebuddy-agent-sdk')
        assert events==before
    asyncio.run(run())


@pytest.mark.parametrize('scenario,expected_status,expected_env',[
    ('download','failed','old'),('target_binding','failed','old'),('busy','failed','old'),
    ('install','needs_review','old'),('auth','failed','old'),('infra','failed','old'),
    ('model','failed','old'),('model_unknown','needs_review','old'),('gate_missing','failed','old'),
    ('switch_before','failed','old'),('switch_after','rolled_back','old'),('health','rolled_back','old'),
    ('rollback_fail','needs_review','new'),('cleanup','cleanup_pending','old')])
def test_stage_failures_keep_old_or_restore_without_model_retry(tmp_path,monkeypatch,scenario,expected_status,expected_env):
    async def run():
        m,a,s,events=rig(tmp_path,monkeypatch,scenario)
        await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0');await m.online_update.task
        status=m.maintenance_status('workbuddy')
        assert status['status']==expected_status and s['environment_digest']==expected_env
        assert events.count('model')<=1 and events.count('install')<=1
        assert status['error']['code'] and status['error']['phase']
        if scenario in {'rollback_fail','cleanup'}:assert 'workbuddy' in m.authentication_service.sdk_maintenance_active
        else:assert not m.authentication_service.sdk_maintenance_active
        if expected_status=='needs_review':
            with pytest.raises(SDKManagerError):await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0')
        assert not a.updates
    asyncio.run(run())


def test_target_changed_is_failed_before_download(tmp_path,monkeypatch):
    async def run():
        m,a,s,events=rig(tmp_path,monkeypatch)
        await m.update('codebuddy-agent-sdk',expected_target_version='1.2.0');await m.online_update.task
        assert m.maintenance_status('workbuddy')['error']['code']=='sdk_update_binding_changed'
        assert events==[] and s['environment_digest']=='old'
    asyncio.run(run())


@pytest.mark.parametrize('phase,submitted',[('downloading',False),('installation',False),('model_verification',True),('switching',True)])
def test_restart_never_replays_ambiguous_phase_or_resets_budget(tmp_path,monkeypatch,phase,submitted):
    m,a,s,events=rig(tmp_path,monkeypatch)
    original={'version':1,'operation_id':'update_'+'a'*32,'status':'running','phase':phase,
        'model_submitted':submitted,'cleanup_complete':True,'deadline_at':123456,'started_at':'2026-10-27T00:00:00Z'}
    m.online_update.path.parent.mkdir(parents=True,exist_ok=True)
    m.online_update.path.write_text(json.dumps(original))
    recovered=OnlineUpdate(m)
    assert recovered.record['status']=='needs_review'
    assert recovered.record['deadline_at']==123456 and recovered.record['model_submitted']==submitted
    assert recovered.task is None and events==[]
    recovered.bind_authentication_service()
    if submitted or phase=='installation':
        assert recovered.record['cleanup_complete'] is False
        assert 'workbuddy' in m.authentication_service.sdk_maintenance_active
    m.online_update=recovered
    with pytest.raises(SDKManagerError) as caught:asyncio.run(m.update('codebuddy-agent-sdk',expected_target_version='1.1.0'))
    assert caught.value.code=='sdk_update_outcome_unconfirmed'


def test_version_check_survives_missing_installation_and_never_authenticates(tmp_path,monkeypatch):
    m,a,s=manager(tmp_path)
    def missing(*_):raise failure('workbuddy_environment_not_installed','version_check')
    m.workbuddy.environment.release=missing
    m.authentication_service=SimpleNamespace(check=lambda *_:pytest.fail('auth dispatched'))
    result=asyncio.run(m.check_versions('codebuddy-agent-sdk'))
    assert result['latest_version']=='1.1.0' and result['current_version'] is None
    assert result['version_check']['status']=='checked' and result['version_check_supported'] is True
    assert a.updates==[]


def test_record_never_returns_internal_binding_or_paths(tmp_path,monkeypatch):
    async def run():
        m,a,s,events=rig(tmp_path,monkeypatch)
        await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0');await m.online_update.task
        status=m.maintenance_status('workbuddy')
        assert 'source_binding' not in status and 'deadline_at' not in status
        assert str(tmp_path) not in json.dumps(status)
        assert m.online_update.record['model_submitted'] is True
    asyncio.run(run())


def test_rest_update_returns_202_operation_without_claiming_install(tmp_path):
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_sdk_manager import FakeSDKManager,HealthyEmbeddedProvider
    class Registry(FakeSDKManager):
        async def update(self,sdk_id,**kwargs):
            assert kwargs['expected_target_version']=='1.1.0'
            return {'maintenance_kind':'online_upgrade','operation_id':'update_'+'a'*32,
                'current_version':'1.0.0','update_status':{'status':'running','phase':'downloading'}}
    app=create_app(Settings(repository_root=tmp_path,data_root=tmp_path/'data'),sdk_manager=Registry(),embedded_provider=HealthyEmbeddedProvider())
    with TestClient(app) as client:
        denied=client.post('/api/system/sdks/codebuddy-agent-sdk/update?expectedTargetVersion=1.1.0')
        assert denied.status_code==403
        response=client.post('/api/system/sdks/codebuddy-agent-sdk/update?expectedTargetVersion=1.1.0',headers={'X-SAPBA-Action':'sdk-update'})
        assert response.status_code==202 and response.json()['operation_id'].startswith('update_')
        assert response.json()['item']['current_version']=='1.0.0'


@pytest.mark.parametrize('fail_health',[False,True])
def test_real_candidate_auth_audit_activation_and_rollback_with_synthetic_sdk(tmp_path,monkeypatch,fail_health):
    """Real state/account/audit boundaries; only provider, model and OS inventory are synthetic."""
    from sap_business_agents_platform.runtime_authentication import RuntimeAuthenticationService
    from sap_business_agents_platform.workbuddy_online_update import UpdateBackend
    from sap_business_agents_platform.workbuddy_environment import digest
    from sap_business_agents_platform.workbuddy_site import configuration
    from tests.test_runtime_authentication import Adapter
    from tests.test_workbuddy_three_layers import fixture
    from tests.workbuddy_integration_fakes import probe_result
    import time
    m,a,_=manager(tmp_path)
    wm,old=fixture(tmp_path,monkeypatch);m.workbuddy=wm
    old.update(sdk_version='1.0.0',cli_version='2.0.0')
    from tests.workbuddy_integration_fakes import install_certificate
    install_certificate(wm,old)
    new={**old,'environment_digest':'b'*64,'sdk_version':'1.1.0','cli_version':'2.1.0'}
    monkeypatch.setattr(wm.environment,'release',lambda key=None:new if (key or wm.environment.state()['environment_digest'])==new['environment_digest'] else old)
    class Official(Adapter):
        def __init__(self,root=None,provider=None,workbuddy=None):
            super().__init__();self.workbuddy=workbuddy
        def context(self):return configuration(self.workbuddy.environment.state(),required=True)
        def observation(self):return {'context_digest':'c'*64,'cache_digest':'d'*64,'source_digest':'e'*64}
        async def check(self,**kwargs):return await super().check()
    adapter=Official(workbuddy=wm)
    auth=RuntimeAuthenticationService(tmp_path/'accounts',{'workbuddy':adapter},changed=m.apply_authentication_state)
    m.authentication_service=auth
    monkeypatch.setattr('sap_business_agents_platform.runtime_authentication_adapters.OfficialAuthenticationAdapter',Official)
    async def model(self,**kwargs):
        result=await probe_result(SimpleNamespace(environment=self.environment),kwargs)
        from sap_business_agents_platform.workbuddy_environment import atomic_json
        path=self.environment.root/'jobs'/(digest(kwargs['task_id'])+'.json')
        job=json.loads(path.read_bytes());job.update(environment_digest=kwargs['snapshot']['environment_digest'],
            authentication_revision=kwargs['snapshot']['authentication_revision'],management_digest='observed-host-digest',
            process_identity='synthetic-owned-process',protocol='sapba.workbuddy.worker/1',**configuration(self.environment.state()))
        atomic_json(path,job)
        return result
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_supervisor.WorkBuddySupervisor.run',model)
    def clearance(self):
        return {'all_writers_checked':True,'old_writers':[],'active_jobs':[],'cleanup_pending':[],
            'state_digest':digest(wm.environment.state()),'process_inventory_digest':'synthetic-inventory','checked_at':time.time()}
    monkeypatch.setattr(UpdateBackend,'clearance',clearance)
    async def run():
        await auth.check('workbuddy')
        await wm.check_model('new-model',request_id='existing-old-check')
        from tests.workbuddy_integration_fakes import certificate as source_certificate
        extra=source_certificate(wm,old,{'review_agent_feedback':['trusted_local']})
        wm.environment.mutate(lambda state:state['sdk_integration_records'].__setitem__(extra['id'],extra))
        before=copy.deepcopy(wm.environment.state())
        record={'operation_id':'update_'+'f'*32,'source_binding':__import__('sap_business_agents_platform.workbuddy_sdk_update',fromlist=['source_binding']).source_binding(before)}
        backend=UpdateBackend(m,record)
        # The core check runner is real and only executes owned synthetic processes.
        receipt={'environment_digest':new['environment_digest'],'sdk_version':'1.1.0','cli_version':'2.1.0','source_binding':record['source_binding']}
        claims=[]
        audit=await backend.verify(receipt,lambda **_:None,lambda:claims.append('model'))
        assert claims==['model'] and wm.environment.state()['environment_digest']==old['environment_digest']
        backend.activate(receipt,audit)
        assert wm.environment.state()['environment_digest']==new['environment_digest']
        current=wm.integration(wm.environment.state(),new)
        assert 'trusted_local' in current['operations']['review_agent_feedback']
        if fail_health:
            await backend.rollback(lambda **_:None)
            assert wm.environment.state()['environment_digest']==old['environment_digest']
        else:
            backend.health(receipt,audit)
            assert auth.view('workbuddy')['authenticated'] is True
        assert wm.environment.state()['default_model_id']==before['default_model_id']
        assert wm.environment.state().get('qualification_records')==before.get('qualification_records')
        await backend.close();await auth.close()
    asyncio.run(run())


def test_submission_timestamp_preserves_milliseconds_for_response_loss(tmp_path,monkeypatch):
    from datetime import datetime
    async def run():
        m,a,s,events=rig(tmp_path,monkeypatch)
        now=1793059200.789
        monkeypatch.setattr('sap_business_agents_platform.workbuddy_online_update.time.time',lambda:now)
        result=await m.update('codebuddy-agent-sdk',expected_target_version='1.1.0')
        assert datetime.fromisoformat(result['update_status']['started_at']).timestamp()==now
        await m.online_update.task
        assert m.maintenance_status('workbuddy')['status']=='completed'
    asyncio.run(run())
