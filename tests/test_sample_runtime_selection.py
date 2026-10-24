"""Explicit new-task bindings. All execution is mocked; no SDK/SAP access."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sap_business_agents_platform.agent_lifecycle import AgentLifecycleError
from sap_business_agents_platform.models import AgentSampleDiscoveryRequest
from sap_business_agents_platform.sdk_manager import SDKManagerError
from tests.test_agent_discovery_api import setup_jobs, package, request


def setup_current(tmp_path):
    jobs, store, sdk, service, settings = setup_jobs(tmp_path)
    old = {"provider_id": "workbuddy", "model": "deepseek-v4.1-flash", "environment_digest": "a" * 64,
           "authentication_revision": 8, "authentication_binding_revision": 8}
    draft = store.get_agent_authoring_draft('draft_sample')
    draft['metadata']['runtime_snapshot'] = old
    store.save_agent_authoring_draft(draft, package=package())
    current = {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "version": "0.3.247", "cli_version": "2.141.0",
        "model": "glm-5.3-flash", "actual_model": "glm-5.3-flash", "model_identity_known": True,
        "model_identity_source": "assistant_message", "model_source": "user_verified", "reasoning_effort": None,
        "reasoning_effort_source": "sdk_default", "environment_digest": "b" * 64,
        "model_check_digest": "c" * 64, "sdk_fingerprint": "d" * 64, "sdk_integration_id": "e" * 64,
        "authentication_revision": 9, "authentication_binding_revision": 9, "service_site": "china",
        "service_site_revision": 1, "service_site_digest": "f" * 64, "configuration_digest": "1" * 64,
        "runtime_configuration_revision": 0, "selected_at": "2026-10-21T00:00:00+00:00"}
    sdk.default_provider_id = 'workbuddy'
    sdk.runtime_snapshot = Mock(side_effect=lambda provider: copy.deepcopy(current))
    def assertion(provider, snapshot):
        if snapshot.get('authentication_binding_revision') != 9:
            raise SDKManagerError('PRIVATE ACCOUNT', code='runtime_authentication_binding_changed')
    auth = SimpleNamespace(assert_binding=Mock(side_effect=assertion), bind=Mock(side_effect=lambda p, s: copy.deepcopy(s)))
    sdk.authentication_service = auth
    sdk.workbuddy = SimpleNamespace(environment=SimpleNamespace(assert_operation=Mock()),
        bound_snapshot=Mock(side_effect=AssertionError('must_not_use_old_draft_binding')))
    return jobs, store, sdk, service, current, copy.deepcopy(draft['metadata'])


def selected_request(jobs, *, request_id='selected-runtime'):
    result = jobs.preflight('draft_sample', request(prepareRuntime=True))
    choice = result['runtime_selection']
    return request(requestId=request_id, selectedFields=['customer'], runtimeSelection={
        'providerId':choice['provider_id'], 'modelId':choice['model_id'], 'bindingDigest':choice['binding_digest']})


def test_explicit_current_binding_bypasses_only_old_draft_binding_not_gates(tmp_path):
    async def scenario():
        jobs, store, sdk, service, current, metadata = setup_current(tmp_path)
        before = copy.deepcopy(current)
        payload = selected_request(jobs)
        # Observation timestamp is not an execution binding change.
        current['selected_at'] = '2026-10-21T00:01:00+00:00'
        started = jobs.start('draft_sample', payload)
        replay = jobs.start('draft_sample', payload)
        assert replay['run_id'] == started['run_id']
        await jobs.tasks[started['run_id']]
        saved = store.get_run(started['run_id']).runtime
        assert saved.model == 'glm-5.3-flash' and saved.environment_digest == before['environment_digest']
        assert saved.authentication_revision == 9
        assert service.discover.await_count == 1 and service.discover.call_args.kwargs['model'] == saved.model
        sdk.workbuddy.bound_snapshot.assert_not_called()
        assert sdk.workbuddy.environment.assert_operation.call_count >= 2
        assert store.get_agent_authoring_draft('draft_sample')['metadata'] == metadata
        assert store.get_agent_authoring_draft('draft_sample')['revision'] == 1
        assert current['model'] == before['model'] and sdk.default_provider_id == 'workbuddy'
        # A lost response restored after a later default change returns the same
        # finished task, not a new admission or model call.
        calls = sdk.runtime_snapshot.call_count
        current['model'] = 'another-model'
        assert jobs.start('draft_sample', payload)['run_id'] == started['run_id']
        assert sdk.runtime_snapshot.call_count == calls and service.discover.await_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('field,value', [('model','another-model'), ('environment_digest','2'*64),
    ('service_site_revision',2), ('sdk_integration_id','3'*64), ('model_check_digest','4'*64)])
def test_changed_selection_is_rejected_before_run_and_preserves_reason(tmp_path, field, value):
    jobs, store, sdk, service, current, metadata = setup_current(tmp_path)
    payload = selected_request(jobs)
    current[field] = value
    with pytest.raises(AgentLifecycleError) as failure:
        jobs.start('draft_sample', payload)
    assert failure.value.code == 'sample_runtime_selection_changed'
    op = store.latest_agent_operation('draft_sample','sample_discovery')
    value = jobs.get('draft_sample', op['operation_id'])
    assert value['codes'] == [failure.value.code] and value['execution_started'] is False
    assert value['timeout_seconds'] == 600 and value['startup_elapsed_seconds'] >= 0
    assert value['request_id'] == payload.request_id and value['requested_runtime']['model_id'] == 'glm-5.3-flash'
    assert failure.value.detail['sample_discovery'] == value
    with pytest.raises(KeyError): store.get_run(op['operation_id'])
    service.discover.assert_not_awaited()
    calls = sdk.runtime_snapshot.call_count
    assert jobs.start('draft_sample', payload)['run_id'] == op['operation_id']
    assert sdk.runtime_snapshot.call_count == calls
    assert store.get_agent_authoring_draft('draft_sample')['metadata'] == metadata


def test_authentication_change_is_not_automatically_rebound(tmp_path):
    jobs, store, sdk, service, current, _ = setup_current(tmp_path)
    payload = selected_request(jobs)
    current['authentication_binding_revision'] = 10
    with pytest.raises(AgentLifecycleError) as failure: jobs.start('draft_sample', payload)
    assert failure.value.code == 'runtime_authentication_binding_changed'
    op = store.latest_agent_operation('draft_sample','sample_discovery')
    assert op['detail']['codes'] == [failure.value.code] and 'PRIVATE ACCOUNT' not in json.dumps(op)
    service.discover.assert_not_awaited()


def test_legacy_client_does_not_silently_migrate_old_binding(tmp_path):
    jobs, store, sdk, service, _, _ = setup_current(tmp_path)
    with pytest.raises(AgentLifecycleError) as failure:jobs.start('draft_sample', request(requestId='legacy'))
    assert failure.value.code == 'runtime_authentication_binding_changed'
    sdk.runtime_snapshot.assert_not_called();service.discover.assert_not_awaited()
    assert store.latest_agent_operation('draft_sample','sample_discovery')['detail']['codes'] == [failure.value.code]


def test_queued_task_keeps_frozen_model_even_if_default_changes(tmp_path):
    async def scenario():
        jobs, store, sdk, service, current, _ = setup_current(tmp_path)
        payload = selected_request(jobs);result = jobs.start('draft_sample',payload)
        current['model'] = 'later-default'
        await jobs.tasks[result['run_id']]
        assert service.discover.call_args.kwargs['model'] == 'glm-5.3-flash'
        assert store.get_run(result['run_id']).runtime.model == 'glm-5.3-flash'
    asyncio.run(scenario())


def test_dispatch_rechecks_account_without_calling_service(tmp_path):
    async def scenario():
        jobs, store, sdk, service, _, _ = setup_current(tmp_path)
        payload = selected_request(jobs);result = jobs.start('draft_sample',payload)
        sdk.authentication_service.assert_binding.side_effect = SDKManagerError('PRIVATE',code='runtime_authentication_binding_changed')
        await jobs.tasks[result['run_id']]
        service.discover.assert_not_awaited()
        value = jobs.get('draft_sample',result['run_id'])
        assert value['codes'] == ['runtime_authentication_binding_changed'] and value['execution_started'] is False
    asyncio.run(scenario())


def test_forged_selection_and_same_request_different_runtime_cannot_dispatch(tmp_path):
    jobs, store, sdk, service, _, _ = setup_current(tmp_path)
    payload = selected_request(jobs);original = payload.model_copy(deep=True)
    payload.runtime_selection.binding_digest = '0'*64
    with pytest.raises(AgentLifecycleError) as rejection:jobs.start('draft_sample',payload)
    assert rejection.value.code == 'sample_runtime_selection_changed'
    with pytest.raises(AgentLifecycleError) as conflict:jobs.start('draft_sample',original)
    assert conflict.value.code == 'agent_request_conflict'
    service.discover.assert_not_awaited()


def test_readiness_unavailable_is_specific_and_never_dispatches(tmp_path):
    jobs, store, sdk, service, _, metadata = setup_current(tmp_path)
    sdk.runtime_snapshot.side_effect = SDKManagerError('PRIVATE',code='workbuddy_cleanup_pending')
    result = jobs.preflight('draft_sample',request(prepareRuntime=True))
    assert result['runtime_selection'] == {'status':'unavailable','code':'workbuddy_cleanup_pending'}
    assert store.latest_agent_operation('draft_sample','sample_discovery') is None
    assert store.get_agent_authoring_draft('draft_sample')['metadata'] == metadata
    service.discover.assert_not_awaited()


def test_codex_explicit_selection_retains_original_sample_policy(tmp_path):
    async def scenario():
        jobs, store, sdk, service, _ = setup_jobs(tmp_path)
        sdk.default_provider_id = 'codex'
        payload = selected_request(jobs)
        result = jobs.start('draft_sample',payload)
        await jobs.tasks[result['run_id']]
        assert all(c.args == ('codex','gpt-5.6-sol') for c in sdk.runtime_snapshot_for_model.call_args_list)
        assert service.discover.call_args.kwargs['model'] == 'gpt-5.6-sol'
        assert store.get_run(result['run_id']).runtime.reasoning_effort == 'medium'
    asyncio.run(scenario())


def test_selection_rejects_extra_fields_and_invalid_digests():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentSampleDiscoveryRequest(expectedRevision=1,runtimeSelection={'providerId':'workbuddy','modelId':'glm-5.3-flash','bindingDigest':'0'*64,'skipAuth':True})
