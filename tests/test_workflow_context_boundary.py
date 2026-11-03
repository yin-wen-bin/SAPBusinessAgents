"""Context-only workflow policy and actual driver cleanup, without remote AI/SAP."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.workflow_context_boundary import (
    REQUIRED_DISABLED, ContextPolicyError, attest_context_client,
)


def client_args(extra=(), omit=()):
    args = ['codex', '--config', 'web_search="disabled"']
    for feature in sorted(REQUIRED_DISABLED - set(omit)):
        args.extend(['--disable', feature])
    return args + list(extra) + ['app-server', '--listen', 'stdio://']


def fake_client(extra=(), omit=()):
    return SimpleNamespace(_client=SimpleNamespace(_sync=SimpleNamespace(
        config=SimpleNamespace(launch_args_override=tuple(client_args(extra, omit))))))


def test_context_proof_does_not_claim_filesystem_isolation():
    from sap_business_agents_platform.workflow_authoring_runtime import capabilities
    proof = attest_context_client(fake_client(['--config', 'mcp_servers.existing.enabled=false']))
    actual = capabilities('restricted', proof)
    assert proof['proof_scope'] == 'client_launch_configuration'
    assert actual['execution_boundary'] == 'provided_workflow_context_only'
    assert actual['os_isolation'] is False
    assert actual['file_edit'] == actual['shell_tests'] == 'not_exposed'
    assert actual['candidate_edit'] == 'platform_validated_workflow_json'


@pytest.mark.parametrize('feature', sorted(REQUIRED_DISABLED))
def test_reenabled_local_capability_is_refused(feature):
    with pytest.raises(ContextPolicyError):
        attest_context_client(fake_client(['--enable', feature]))


@pytest.mark.parametrize('feature', sorted(REQUIRED_DISABLED))
def test_missing_disabled_capability_is_refused(feature):
    with pytest.raises(ContextPolicyError):
        attest_context_client(fake_client(omit=[feature]))


@pytest.mark.parametrize('override', ['web_search="live"', 'mcp_servers.example.enabled=true'])
def test_web_or_mcp_enable_override_is_refused(override):
    with pytest.raises(ContextPolicyError):
        attest_context_client(fake_client(['--config', override]))


def test_existing_product_client_passes_launch_configuration_check(tmp_path):
    from sap_business_agents_platform.workflow_context_boundary import context_client
    # Construct only: never starts the SDK/model. Existing MCP entries must be off.
    proof = attest_context_client(context_client(tmp_path))
    assert proof['native_file_tools'] is proof['native_shell_tools'] is False


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'cancelled'])
def test_restricted_driver_keeps_real_binding_and_owned_cleanup(tmp_path, monkeypatch, outcome):
    from openai_codex import ApprovalMode, Sandbox
    from sap_business_agents_platform import codex_planner, workflow_authoring_runtime, workflow_context_boundary
    from sap_business_agents_platform.codex_driver import CodexSDKDriver

    calls, cleanup, events = [], {}, []
    class Client:
        id = 'context-thread'
        _client = fake_client()._client
        async def __aenter__(self):
            calls.append(('start', None))
            return self
        async def close(self):
            calls.append(('close', None))
        async def thread_start(self, **kwargs):
            calls.append(('thread', kwargs))
            return self
        async def turn(self, prompt, **kwargs):
            calls.append(('turn', (prompt, kwargs)))
            return self
    client = Client()
    monkeypatch.setattr(workflow_context_boundary, 'context_client', lambda _: client)
    def no_local_tools(*_, **__):
        pytest.fail('restricted context must not create a local-tool client')
    monkeypatch.setattr(codex_planner, '_tool_authoring_codex', no_local_tools)
    async def collect(*_):
        if outcome == 'failed':
            raise ValueError('native-turn-failed')
        if outcome == 'cancelled':
            raise asyncio.CancelledError()
        return SimpleNamespace(final_response=json.dumps({'action': 'explain'}))
    monkeypatch.setattr(workflow_authoring_runtime, 'collect_turn', collect)
    request = SimpleNamespace(permissions={'execution_mode': 'restricted'}, cwd=str(tmp_path),
                              prompt='provided context', instructions='definition-only', output_schema={'type': 'object'})
    driver = CodexSDKDriver(SimpleNamespace(model='bound-model', reasoning_effort='high'))
    operation = driver.workflow_turn(request, workspace=SimpleNamespace(source=tmp_path), task_id='owned-task',
                                     emit=lambda *item: events.append(item), cleanup_state=cleanup)
    if outcome == 'completed':
        result, available = asyncio.run(operation)
        assert result.cleanup_complete is True
        assert result.session.session_id == 'context-thread'
        assert available['os_isolation'] is False
    else:
        with pytest.raises(ValueError if outcome == 'failed' else asyncio.CancelledError):
            asyncio.run(operation)
    assert cleanup['complete'] is True and calls[-1] == ('close', None)
    thread = next(value for kind, value in calls if kind == 'thread')
    assert thread['model'] == 'bound-model' and thread['sandbox'] == Sandbox.read_only
    assert thread['approval_mode'] == ApprovalMode.deny_all
    assert 'permissions' not in thread
    turn = next(value for kind, value in calls if kind == 'turn')
    assert turn[0] == 'provided context' and turn[1]['effort'] == 'high'
    assert all('command' not in kind for kind, _ in calls)


def test_context_instructions_request_missing_context_instead_of_local_files():
    from sap_business_agents_platform.runtime_workflow_authoring import instructions
    value = instructions(context_only=True)
    assert 'provided workflow context' in value
    assert 'Inspect files and run local tests' not in value
    assert 'allowedChangePaths' in value and 'For explain intent never return revisions' in value
    assert 'Inspect files and run local tests' in instructions()
