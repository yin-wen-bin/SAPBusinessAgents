"""Automatic default intent, once-only capture and immutable task recovery."""
import asyncio
import copy
import json

import pytest
from pydantic import ValidationError
from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentSampleDiscoveryRequest
from sap_business_agents_platform.sdk_manager import SDKManagerError
from unittest.mock import Mock
from tests.test_agent_discovery_api import setup_jobs, request
from tests.test_sample_runtime_selection import setup_current


def automatic(**kwargs):
    return request(runtimePolicy='system_default', selectedFields=['customer'], requestId=kwargs.pop('requestId','automatic'), **kwargs)


def test_old_draft_automatically_uses_verified_default_and_keeps_history(tmp_path):
    async def scenario():
        jobs,store,sdk,service,current,metadata=setup_current(tmp_path)
        original=copy.deepcopy(current)
        result=jobs.start('draft_sample',automatic())
        accepted=jobs.get('draft_sample',result['run_id'])
        assert accepted['runtime_binding_source']=='system_default'
        assert accepted['runtime_snapshot']['model']=='glm-5.3-flash'
        assert accepted['runtime_snapshot']['authentication_binding_revision']==9
        assert accepted['runtime_snapshot']['environment_digest']==current['environment_digest']
        assert accepted['runtime_usage']['model_id']=='glm-5.3-flash'
        assert accepted['runtime_selection'] is None
        assert store.get_agent_authoring_draft('draft_sample')['metadata']==metadata
        assert current==original
        sdk.workbuddy.bound_snapshot.assert_not_called()
        await jobs.tasks[result['run_id']]
        assert service.discover.await_count==1
        assert sdk.workbuddy.environment.assert_operation.call_count>=2
        assert store.get_run(result['run_id']).runtime.model=='glm-5.3-flash'
    asyncio.run(scenario())


def test_resubmission_with_changed_default_does_not_check_or_dispatch_again(tmp_path):
    async def scenario():
        jobs,store,sdk,service,current,_=setup_current(tmp_path)
        payload=automatic(prepareRuntime=True)
        result=jobs.start('draft_sample',payload)
        calls=sdk.runtime_snapshot.call_count
        sdk.default_provider_id='codex'
        sdk.runtime_snapshot.side_effect=AssertionError('must_not_re_read_defaults')
        # Even prepareRuntime on a POST retransmission is not a new preflight.
        recovered=jobs.start('draft_sample',payload)
        assert recovered['run_id']==result['run_id']
        assert sdk.runtime_snapshot.call_count==calls
        sdk.runtime_snapshot_for_model.assert_not_called()
        await jobs.tasks[result['run_id']]
        assert service.discover.await_count==1
        assert service.discover.call_args.kwargs['model']=='glm-5.3-flash'
        assert jobs.start('draft_sample',payload)['run_id']==result['run_id']
        assert sdk.runtime_snapshot.call_count==calls
    asyncio.run(scenario())


def test_new_request_captures_new_default_but_old_task_stays_frozen(tmp_path):
    async def scenario():
        jobs,store,sdk,service,current,_=setup_current(tmp_path)
        first=jobs.start('draft_sample',automatic())
        await jobs.tasks[first['run_id']]
        current.update(model='next-model',actual_model='next-model',model_check_digest='5'*64)
        second=jobs.start('draft_sample',automatic(requestId='new-intent'))
        await jobs.tasks[second['run_id']]
        assert store.get_run(first['run_id']).runtime.model=='glm-5.3-flash'
        assert store.get_run(second['run_id']).runtime.model=='next-model'
        assert service.discover.await_count==2
    asyncio.run(scenario())


def test_actual_default_at_acceptance_is_used_not_an_old_preview(tmp_path):
    async def scenario():
        jobs,store,sdk,service,current,_=setup_current(tmp_path)
        preview=jobs.preflight('draft_sample',automatic(prepareRuntime=True))['runtime_selection']
        assert preview['model_id']=='glm-5.3-flash'
        current.update(model='accepted-model',actual_model='accepted-model')
        result=jobs.start('draft_sample',automatic())
        await jobs.tasks[result['run_id']]
        assert result['runtime_snapshot']['model']=='accepted-model'
        assert service.discover.call_args.kwargs['model']=='accepted-model'
    asyncio.run(scenario())


@pytest.mark.parametrize('code',['runtime_authentication_binding_changed','runtime_model_check_required','workbuddy_cleanup_pending','workbuddy_sdk_operation_not_certified'])
def test_failed_admission_is_specific_and_never_retried(tmp_path,code):
    jobs,store,sdk,service,_,metadata=setup_current(tmp_path)
    sdk.runtime_snapshot.side_effect=SDKManagerError('PRIVATE',code=code)
    with pytest.raises(AgentLifecycleError) as failure:jobs.start('draft_sample',automatic())
    assert failure.value.code==code
    value=failure.value.detail['sample_discovery']
    assert value['codes']==[code] and value['execution_started'] is False
    assert value['runtime_binding_source']=='system_default'
    assert 'PRIVATE' not in json.dumps(value)
    calls=sdk.runtime_snapshot.call_count
    assert jobs.start('draft_sample',automatic())['run_id']==value['run_id']
    assert sdk.runtime_snapshot.call_count==calls
    service.discover.assert_not_awaited()
    assert store.get_agent_authoring_draft('draft_sample')['metadata']==metadata


def test_permission_and_dispatch_gates_still_apply_to_automatic_tasks(tmp_path):
    async def scenario():
        jobs,store,sdk,service,_,_=setup_current(tmp_path)
        result=jobs.start('draft_sample',automatic())
        sdk.authentication_service.assert_binding.side_effect=SDKManagerError('PRIVATE',code='runtime_authentication_binding_changed')
        await jobs.tasks[result['run_id']]
        service.discover.assert_not_awaited()
        assert jobs.get('draft_sample',result['run_id'])['codes']==['runtime_authentication_binding_changed']
    asyncio.run(scenario())


def test_idempotency_distinguishes_default_intent_from_legacy_request(tmp_path):
    jobs,store,sdk,service,_,_=setup_current(tmp_path)
    sdk.runtime_snapshot.side_effect=SDKManagerError('PRIVATE',code='runtime_model_check_required')
    with pytest.raises(AgentLifecycleError):jobs.start('draft_sample',automatic())
    with pytest.raises(AgentLifecycleError) as failure:
        jobs.start('draft_sample',request(requestId='automatic',selectedFields=['customer']))
    assert failure.value.code=='agent_request_conflict'
    service.discover.assert_not_awaited()


def test_failure_after_run_creation_keeps_actual_frozen_usage(tmp_path):
    jobs,store,sdk,service,_,_=setup_current(tmp_path)
    store.save_agent_run_snapshot=Mock(side_effect=OSError('PRIVATE_PATH'))
    with pytest.raises(AgentLifecycleError) as failure:jobs.start('draft_sample',automatic())
    value=failure.value.detail['sample_discovery']
    assert value['model']=='glm-5.3-flash' and value['runtime_snapshot']['model']=='glm-5.3-flash'
    assert value['execution_started'] is False and value['codes']==['sample_discovery_start_failed']
    assert 'PRIVATE_PATH' not in json.dumps(value)
    assert jobs.start('draft_sample',automatic())['run_id']==value['run_id']
    service.discover.assert_not_awaited()


def test_codex_automatic_default_preserves_existing_sample_model_and_effort(tmp_path):
    async def scenario():
        jobs,store,sdk,service,_=setup_jobs(tmp_path)
        sdk.default_provider_id='codex'
        result=jobs.start('draft_sample',automatic())
        await jobs.tasks[result['run_id']]
        sdk.runtime_snapshot_for_model.assert_called_once_with('codex','gpt-5.6-sol')
        assert store.get_run(result['run_id']).runtime.reasoning_effort=='medium'
        assert service.discover.call_args.kwargs['model']=='gpt-5.6-sol'
    asyncio.run(scenario())


def test_ambiguous_or_unknown_runtime_policy_is_rejected():
    with pytest.raises(ValidationError):
        AgentSampleDiscoveryRequest(expectedRevision=1,runtimePolicy='system_default',runtimeSelection={
            'providerId':'workbuddy','modelId':'glm-5.3-flash','bindingDigest':'0'*64})
    with pytest.raises(ValidationError):AgentSampleDiscoveryRequest(expectedRevision=1,runtimePolicy='arbitrary_fallback')
