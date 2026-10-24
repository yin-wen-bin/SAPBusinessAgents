"""No model/SAP: negative checks, same-account receipts and new draft binding."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sap_business_agents_platform import workbuddy_auth_recovery as recovery
from sap_business_agents_platform import workbuddy_model_protocol as protocol
from sap_business_agents_platform.workbuddy_environment import digest, file_hash
from sap_business_agents_platform.workbuddy_site import make_configuration, model_matches
from sap_business_agents_platform.workbuddy_qualification import effective_model_check
from sap_business_agents_platform.runtime import RuntimeRouter
from sap_business_agents_platform.plugins import AgentRuntimeCapability
from tests.test_workbuddy_auth_hardening import auth_service


def test_negative_workbuddy_check_does_not_destroy_identity_or_invalidate_model(tmp_path):
    service,adapter,changed=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'))
    before=copy.deepcopy(service.state['accounts']['workbuddy'])
    async def negative(**kwargs):
        return {'authenticated':False,'method':'browser','source':'official_cli','authentication_evidence':'initialize_account_absent'}
    adapter.check=negative
    result=asyncio.run(service.check('workbuddy'))
    account=service.state['accounts']['workbuddy']
    assert result['status']=='check_failed' and result['authenticated'] is None
    assert result['failure_code']=='workbuddy_authentication_confirmation_required'
    for key in ('revision','confirmed_binding','last_successful_check','last_confirmed_binding'):
        assert account[key]==before[key]
    assert changed==[('workbuddy',False),('workbuddy',False)]
    with pytest.raises(Exception):service.assert_binding('workbuddy',service.bind('workbuddy',{}))


def test_same_account_recovery_after_negative_check_keeps_revision(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'));before=service.bind('workbuddy',{})
    service._accept('workbuddy',{'authenticated':False},context=adapter.context())
    asyncio.run(service.check('workbuddy'))
    assert service.bind('workbuddy',{})==before


def test_confirmed_logout_still_invalidates_binding(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    asyncio.run(service.check('workbuddy'));before=service.bind('workbuddy',{})
    service._accept('workbuddy',{'authenticated':False},change=True,context=adapter.context())
    assert service.view('workbuddy')['status']=='signed_out'
    assert service.bind('workbuddy',{})['authentication_binding_revision']>before['authentication_binding_revision']


def test_cache_changed_during_check_cannot_bless_unobserved_credentials(tmp_path):
    service,adapter,_=auth_service(tmp_path)
    check=adapter.check
    async def updating(**kwargs):
        result=await check(**kwargs);adapter.stamp['cache_digest']='updated-during-check';return result
    adapter.check=updating
    result=asyncio.run(service.check('workbuddy'))
    assert result['authentication_context_status']=='check_required' and result['authenticated'] is None


def make_receipt(tmp_path):
    root=tmp_path/'workbuddy';root.mkdir();(root/'jobs').mkdir()
    env=SimpleNamespace(root=root)
    site=make_configuration('china',1);model='fixture-model';release={'environment_digest':'e'*64}
    identity='f'*64
    def document(name,value):
        path=root/name;path.write_text(json.dumps(value),encoding='utf-8')
        return {'path':name,'sha256':file_hash(path)}
    job={'operation':'model_check','runtime_model':model,'status':'completed','cleanup_complete':True,
        'environment_digest':release['environment_digest'],'authentication_revision':6,'management_digest':'m',**site,
        'result_metadata':{'structured_terminal':True,'output_sha256':'o','actual_model':model,'identity_source':'assistant_message'}}
    job_id='source-probe';document('jobs/'+digest(job_id)+'.json',job)
    check={'compatible':True,'status':'compatible','model_id':model,'authentication_revision':6,**site,
        'environment_digest':release['environment_digest'],'source_job_id':job_id,'source_job_digest':digest(job),
        'probe_contract':protocol.CONTRACT,'model_protocol_digest':protocol.protocol_digest(),
        'proof':{'tool_calls':1},'schema_sha256':digest(protocol.SCHEMA),'output_sha256':'o',
        'actual_model':model,'model_identity_source':'assistant_message','identity_known':True}
    check['check_digest']=digest(check)
    receipt={'contract':recovery.CONTRACT,'source_revision':6,'target_revision':8,**site,
        'environment_digest':release['environment_digest'],'identity_digest':identity,
        'original_identity':document('original.json',{'authenticated':True,'identity_digest':identity}),
        'current_identity':document('current.json',{'authenticated':True,'identity_digest':identity,'cleanup_complete':True}),
        'model_checks':{model:document('check.json',check)}}
    receipt['id']=digest(receipt)
    state={'authenticated':True,'authentication_revision':8,'authentication_identity_digest':identity,
        'environment_digest':release['environment_digest'],**site,
        'models':{model:{**check,'compatible':False,'status':'authentication_changed'}},
        'model_check_history':[copy.deepcopy(check)],'authentication_recoveries':{receipt['id']:receipt}}
    return env,state,release,check,receipt


def test_receipt_retains_original_check_and_requires_real_source_job(tmp_path):
    env,state,release,check,receipt=make_receipt(tmp_path);before=copy.deepcopy(state)
    result=effective_model_check(env,state,release,check['model_id'])
    assert result==check and protocol.applicable(env,result)
    assert model_matches(check,state,env) and not model_matches(check,state)
    assert state==before
    (env.root/'jobs'/(digest(check['source_job_id'])+'.json')).unlink()
    assert not model_matches(check,state,env)


@pytest.mark.parametrize('field,value',[('authenticated',False),('authentication_identity_digest','other'),
    ('authentication_revision',9),('environment_digest','other'),('service_site','international'),('service_site_revision',2)])
def test_receipt_cannot_cross_binding(tmp_path,field,value):
    env,state,_,check,_=make_receipt(tmp_path);state[field]=value
    if field in {'service_site','service_site_revision'}:
        state.update(make_configuration(state['service_site'],state['service_site_revision']))
    assert not model_matches(check,state,env)


def test_modified_or_missing_evidence_cannot_restore(tmp_path):
    env,state,_,check,receipt=make_receipt(tmp_path)
    (env.root/receipt['current_identity']['path']).write_text('{}',encoding='utf-8')
    assert not model_matches(check,state,env)


def test_new_authoring_request_restores_only_same_model_environment(tmp_path):
    env,state,_,check,receipt=make_receipt(tmp_path)
    env.state=lambda:copy.deepcopy(state)
    old={'provider_id':'workbuddy','model':check['model_id'],'actual_model':check['model_id'],
        'authentication_revision':6,'authentication_binding_revision':6,'model_check_digest':check['check_digest'],
        'environment_digest':state['environment_digest'],'version':'0.3.247','cli_version':'2.141.0','sdk_fingerprint':'f',
        **make_configuration('china',1)}
    class Auth:
        def bind(self,provider,snapshot):return {**snapshot,'authentication_binding_revision':8}
        def assert_binding(self,provider,snapshot):
            if snapshot.get('authentication_binding_revision')!=8:
                from sap_business_agents_platform.runtime_authentication import error
                raise error('runtime_authentication_binding_changed')
    router=RuntimeRouter.__new__(RuntimeRouter)
    router.manager=SimpleNamespace(authentication_service=Auth(),workbuddy=SimpleNamespace(environment=env,
        runtime_snapshot=lambda model:{**old,'authentication_revision':8}))
    facade=AgentRuntimeCapability(SimpleNamespace(_providers={'codex-runtime':SimpleNamespace(planner=router)}))
    from tests.test_agent_authoring import _service
    service,_,_=_service(tmp_path/'authoring')
    service.runtime=facade
    result=service._feedback_runtime_snapshot({'metadata':{'runtime_snapshot':old}})
    assert result['authentication_revision']==8 and result['authentication_binding_revision']==8
    assert result['authentication_recovery_id']==receipt['id']
    assert old['authentication_revision']==6  # Historical source is untouched.
    router.manager.workbuddy.runtime_snapshot=lambda model:{**old,'authentication_revision':8,'model':'other'}
    with pytest.raises(Exception):facade.authoring_snapshot_for_binding(old)
    with pytest.raises(Exception):facade.snapshot_for_binding(old)  # In-flight path stays strict.


def test_authoring_facade_preserves_legacy_and_codex_binding_checks():
    calls=[]
    def strict(snapshot):
        calls.append(snapshot)
        return {**snapshot,'checked':True}
    facade=AgentRuntimeCapability(SimpleNamespace(_providers={'codex-runtime':SimpleNamespace(planner=
        SimpleNamespace(snapshot_for_binding=strict))}))
    original={'provider_id':'codex','model':'unchanged-model'}
    assert facade.authoring_snapshot_for_binding(original)=={**original,'checked':True}
    assert calls==[original] and original=={'provider_id':'codex','model':'unchanged-model'}


def test_business_output_failure_preserves_safe_code_without_relaxing_validation():
    from sap_business_agents_platform.manifests import ManifestError
    from sap_business_agents_platform.authoring_checks import candidate_business_output_issues
    manifest={'execution':{'steps':[{'executor':'sap_read'},{'executor':'rule','operation':'evidence_summary'}],
        'outputSchema':{'type':'object','properties':{'status':{'type':'string'}}}}}
    issue=candidate_business_output_issues(manifest)[0]
    assert issue['code']=='agent_business_output_contract_missing'
    failure=ManifestError('Private diagnostic must not be projected',issue_code=issue['code'],path=issue['path'])
    assert failure.public_issue()=={'code':'agent_business_output_contract_missing','path':'/manifest/execution/outputSchema'}
    assert candidate_business_output_issues(manifest)==[issue]  # Original strict gate still rejects it.
    assert ManifestError('private',issue_code='untrusted').public_issue()['code']=='definition_invalid'


def test_original_positive_model_check_is_not_modified(tmp_path):
    env,state,release,check,_=make_receipt(tmp_path)
    before=json.dumps(state['model_check_history'],sort_keys=True)
    effective_model_check(env,state,release,check['model_id'])
    assert json.dumps(state['model_check_history'],sort_keys=True)==before


def test_model_picker_uses_audited_check_instead_of_showing_it_as_incompatible(tmp_path):
    from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
    env,state,release,check,_=make_receipt(tmp_path)
    env.state=lambda:copy.deepcopy(state);env.release=lambda *args:release
    state['default_model_id']=check['model_id']
    # The SDK directory deliberately excludes arbitrary historical model IDs.
    # This test exercises an audited check for a genuinely discovered model.
    state['model_discovery']={'models':[check['model_id']], 'source':'locked_cli_help',
        'environment_digest':state['environment_digest'], **make_configuration('china',1)}
    manager=WorkBuddyManager.__new__(WorkBuddyManager);manager.environment=env
    manager.integration=lambda *args:{'status':'certified'}
    items=manager.models(state=state)['items']
    assert len(items)==1 and items[0]['selectable'] is True
    assert items[0]['check']==check


def test_recovery_cannot_change_identity_under_an_original_check_digest(tmp_path):
    env,state,_,check,_=make_receipt(tmp_path)
    forged={**check,'actual_model':'different-model'}
    assert not model_matches(forged,state,env)


def test_recovered_draft_binding_is_saved_atomically_with_a_single_revision(tmp_path):
    from contextlib import contextmanager
    from sap_business_agents_platform.models import AgentFeedbackRequest
    from tests.test_agent_authoring import _service,create,Runtime,send_and_wait
    service,store,_=_service(tmp_path)
    draft=create(service)
    saved=store.get_agent_authoring_draft(draft['draft_id'])
    old={'provider_id':'workbuddy','model':'fixture-model','authentication_revision':6,'authentication_binding_revision':6}
    saved['metadata']['runtime_snapshot']=copy.deepcopy(old)
    store.save_agent_authoring_draft(saved,expected_revision=1)
    def revise(package):package['manifest']['title']['zh']='恢复后名称'
    class RecoveredRuntime(Runtime):
        def workbuddy_reserved(self): return True  # No real SDK process in this transaction fixture.
        def authoring_snapshot_for_binding(self,previous):
            assert previous==old
            return {**previous,'authentication_revision':8,'authentication_binding_revision':8,'authentication_recovery_id':'audited-receipt'}
        @contextmanager
        def pin_snapshot(self,snapshot):
            assert snapshot['authentication_revision']==8
            yield
    runtime=RecoveredRuntime('revise_agent',revise);service.runtime=runtime
    payload=AgentFeedbackRequest(baseTurn=len(draft['conversation']),baseRevision=1,feedback='修改名称',
        intent='revise',requestId='recover-one',executionMode='trusted_local',trustedLocalConfirmed=True)
    _,result=asyncio.run(send_and_wait(service,draft,payload))
    assert result['revision']==2
    turn=result['conversation'][-1]
    assert turn['status']=='completed' and turn['decision']['runtime_snapshot']['authentication_revision']==8
    assert store.get_agent_authoring_draft(draft['draft_id'])['metadata']['runtime_snapshot']['authentication_recovery_id']=='audited-receipt'
    assert old['authentication_revision']==6 and len(runtime.calls)==1
