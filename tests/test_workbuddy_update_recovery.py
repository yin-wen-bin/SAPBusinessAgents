"""Recovery dispatch/durability tests; all provider and installation effects forbidden."""
import asyncio
import copy
import hashlib
import json
import time

import pytest

from sap_business_agents_platform.sdk_manager import SDKManagerError
from sap_business_agents_platform.workbuddy_online_update import OnlineUpdate
from sap_business_agents_platform.workbuddy_sdk_update import source_binding
from tests.test_workbuddy_online_update import rig

PARENT = 'update_' + 'a' * 32


def setup(tmp_path, monkeypatch, scenario='success'):
    manager, adapter, state, events = rig(tmp_path, monkeypatch, scenario)
    root = manager.online_update.root
    root.mkdir(parents=True, exist_ok=True)
    parent = {'version':1,'operation_id':PARENT,'status':'failed','phase':'authentication_verification',
              'before_version':'1.0.0','target_version':'1.1.0','model_submitted':True,'cleanup_complete':True,
              'source_environment_digest':'old','source_binding':source_binding(state),
              'candidate_environment_digest':'new'}
    path = root / (PARENT+'.json')
    path.write_text(json.dumps(parent))
    raw = path.read_bytes()
    manager.online_update.record = copy.deepcopy(parent)
    manager.online_update.publish()
    receipt = {'environment_digest':'new','sdk_version':'1.1.0','cli_version':'2.1.0'}
    def inputs(*_):
        events.append('verify_inputs')
        return {'receipt':receipt,'input_digest':'synthetic-input',
                'references':{'parent':{'sha256':hashlib.sha256(raw).hexdigest()}}}
    async def verify(owner,backend,values):
        events.append('reuse_existing_proof')
        return object()
    def forbidden(*_):
        pytest.fail('recovery must not install or call models')
    manager._workbuddy_updater = forbidden
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_update_recovery.load_parent', inputs)
    monkeypatch.setattr('sap_business_agents_platform.workbuddy_update_recovery.verify_reused', verify)
    return manager,state,events,path,raw


def test_recovery_single_submission_and_duplicate_after_activation_or_restart(tmp_path,monkeypatch):
    async def run():
        m,state,events,path,raw=setup(tmp_path,monkeypatch)
        accepted=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert events==[] and accepted['update_status']['model_check_limit']==0
        again=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert again['operation_id']==accepted['operation_id']
        deadline=m.online_update.record['deadline_at']
        await m.online_update.task
        assert m.online_update.record['status']=='completed'
        assert state['environment_digest']=='new' and events.count('activate')==1
        assert not set(events)&{'download','install','model'}
        assert path.read_bytes()==raw
        m.online_update=OnlineUpdate(m)
        reply=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert reply['operation_id']==accepted['operation_id'] and reply['update_status']['status']=='completed'
        assert m.online_update.record['deadline_at']==deadline and events.count('activate')==1
        # current.json may later describe another operation; idempotency is still per parent.
        m.online_update.record={'version':1,'operation_id':'update_'+'b'*32,'status':'failed'}
        reply=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert reply['operation_id']==accepted['operation_id'] and events.count('activate')==1
    asyncio.run(run())


@pytest.mark.parametrize('change,code',[
    ('target','sdk_update_binding_changed'),('binding','workbuddy_update_recovery_unavailable'),
    ('cleanup','workbuddy_update_recovery_unavailable'),('probe','workbuddy_update_recovery_unavailable')])
def test_invalid_recovery_never_dispatches(tmp_path,monkeypatch,change,code):
    async def run():
        m,state,events,path,raw=setup(tmp_path,monkeypatch)
        target='1.2.0' if change=='target' else '1.1.0'
        if change=='binding':state['default_model_id']='different'
        if change in {'cleanup','probe'}:
            value=json.loads(raw);value['cleanup_complete' if change=='cleanup' else 'model_submitted']=False
            path.write_text(json.dumps(value))
        with pytest.raises(SDKManagerError) as exc:
            await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version=target)
        assert exc.value.code==code and events==[]
    asyncio.run(run())


@pytest.mark.parametrize('scenario,status', [('health','rolled_back'),('rollback_fail','needs_review'),('busy','failed')])
def test_recovery_failure_and_rollback_have_no_model_retry(tmp_path,monkeypatch,scenario,status):
    async def run():
        m,state,events,path,raw=setup(tmp_path,monkeypatch,scenario)
        await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        await m.online_update.task
        assert m.online_update.record['status']==status
        assert not set(events)&{'download','install','model'} and path.read_bytes()==raw
        if scenario=='rollback_fail':assert m.authentication_service.sdk_maintenance_active
    asyncio.run(run())


def test_lost_response_restart_never_dispatches_or_resets_deadline(tmp_path,monkeypatch):
    async def run():
        m,state,events,path,raw=setup(tmp_path,monkeypatch)
        async def pending(*_):await asyncio.Future()
        monkeypatch.setattr('sap_business_agents_platform.workbuddy_update_recovery.run',pending)
        reply=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        old=m.online_update
        deadline=old.record['deadline_at']
        old.task.cancel()
        await asyncio.gather(old.task,return_exceptions=True)
        m.online_update=OnlineUpdate(m);m.online_update.bind_authentication_service()
        repeated=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert repeated['operation_id']==reply['operation_id']
        assert repeated['update_status']['status']=='needs_review'
        assert m.online_update.record['deadline_at']==deadline and m.online_update.task is None
        assert events==[] and m.authentication_service.sdk_maintenance_active
    asyncio.run(run())


def test_recovery_api_requires_confirmation_and_returns_202(tmp_path):
    from fastapi.testclient import TestClient
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from tests.test_sdk_manager import FakeSDKManager,HealthyEmbeddedProvider
    registry=FakeSDKManager()
    calls=[]
    async def recover(sdk_id,parent_id,*,expected_target_version=None):
        calls.append((sdk_id,parent_id,expected_target_version))
        return {'operation_id':'recovery_test','maintenance_kind':'online_upgrade_recovery'}
    registry.recover_update=recover
    app=create_app(Settings(repository_root=tmp_path,data_root=tmp_path/'data'),sdk_manager=registry,embedded_provider=HealthyEmbeddedProvider())
    client=TestClient(app)
    url=f'/api/system/sdks/codebuddy-agent-sdk/update-operations/{PARENT}/recover?expectedTargetVersion=1.1.0'
    assert client.post(url).status_code==403 and calls==[]
    result=client.post(url,headers={'X-SAPBA-Action':'sdk-update'})
    assert result.status_code==202 and result.json()['operation_id']=='recovery_test'
    assert calls==[('codebuddy-agent-sdk',PARENT,'1.1.0')]


@pytest.mark.parametrize('phase,status',[('environment_commit','running'),('completed','completed')])
def test_authoritative_operation_survives_crash_before_current_projection(tmp_path,monkeypatch,phase,status):
    m,state,events,parent_path,raw=setup(tmp_path,monkeypatch)
    operation='recovery_'+'c'*32
    stale={'version':1,'operation_id':operation,'status':'running','phase':'recovery_verification',
           'model_submitted':True,'deadline_at':12345,'cleanup_complete':True}
    authoritative={**stale,'status':status,'phase':phase,'checkpoints':{'environment_commit':'completed'},
                   'durable_test_marker':'must-survive'}
    root=m.online_update.root
    (root/'current.json').write_text(json.dumps(stale))
    (root/(operation+'.json')).write_text(json.dumps(authoritative))
    recovered=OnlineUpdate(m)
    assert recovered.record['durable_test_marker']=='must-survive'
    assert recovered.record['checkpoints']==authoritative['checkpoints']
    assert recovered.record['status']==('completed' if status=='completed' else 'needs_review')
    assert json.loads((root/'current.json').read_bytes())==json.loads((root/(operation+'.json')).read_bytes())
    assert parent_path.read_bytes()==raw


def test_orphan_accepted_record_is_reviewed_not_replayed(tmp_path,monkeypatch):
    async def run():
        from sap_business_agents_platform.workbuddy_environment import digest
        m,state,events,path,raw=setup(tmp_path,monkeypatch)
        key=digest([PARENT,hashlib.sha256(raw).hexdigest(),'new'])
        operation='recovery_'+key[:32]
        record={'version':1,'operation_id':operation,'recovery_key':key,'parent_operation_id':PARENT,
                'status':'running','phase':'recovery_verification','deadline_at':12345,'cleanup_complete':True}
        (m.online_update.root/(operation+'.json')).write_text(json.dumps(record))
        reply=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert reply['update_status']['status']=='needs_review' and m.online_update.task is None
        assert json.loads((m.online_update.root/(operation+'.json')).read_bytes())['deadline_at']==12345
        assert events==[] and path.read_bytes()==raw
    asyncio.run(run())


def test_explicit_successor_preserves_rollback_and_reuses_proof_without_model(tmp_path,monkeypatch):
    async def run():
        from sap_business_agents_platform import workbuddy_online_update as module
        from sap_business_agents_platform.workbuddy_integration import manifest,INFRASTRUCTURE_FILES
        from sap_business_agents_platform.workbuddy_update_recovery import recovery_offer
        m,state,events,path,raw=setup(tmp_path,monkeypatch,'health')
        original_infrastructure=manifest(INFRASTRUCTURE_FILES)
        def anchor(self,receipt,audit):
            return {'operation_id':m.online_update.record['operation_id'],
                    'source_environment_digest':'old','target_environment_digest':'new',
                    'target_certificate':{'binding':{'infrastructure':original_infrastructure}}}
        monkeypatch.setattr(module.UpdateBackend,'anchor',anchor)
        first=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        await m.online_update.task
        assert m.online_update.record['status']=='rolled_back' and state['environment_digest']=='old'
        prior=m.online_update.root/(first['operation_id']+'.json');prior_bytes=prior.read_bytes()
        deadline=m.online_update.record['deadline_at']
        assert not recovery_offer(m,m.online_update.record)['available']
        with pytest.raises(SDKManagerError):
            await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0',retry_of=first['operation_id'])
        monkeypatch.setattr('sap_business_agents_platform.workbuddy_integration.manifest',lambda *_:{'fixed.py':'new'})
        def health(self,*_):self.switch_safe=True;events.append('health')
        monkeypatch.setattr(module.UpdateBackend,'health',health)
        assert recovery_offer(m,m.online_update.record)['retry_of']==first['operation_id']
        second=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0',retry_of=first['operation_id'])
        assert second['operation_id']!=first['operation_id']
        await m.online_update.task
        assert m.online_update.record['status']=='completed' and state['environment_digest']=='new'
        repeated=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0',retry_of=first['operation_id'])
        assert repeated['operation_id']==second['operation_id']
        original=await m.recover_update('codebuddy-agent-sdk',PARENT,expected_target_version='1.1.0')
        assert original['update_status']['status']=='rolled_back'
        assert prior.read_bytes()==prior_bytes and path.read_bytes()==raw
        assert json.loads(prior_bytes)['deadline_at']==deadline
        assert events.count('activate')==2 and not set(events)&{'model','install','download'}
    asyncio.run(run())
