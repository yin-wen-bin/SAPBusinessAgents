import asyncio
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.runtime_execution import (
    RuntimeExecutionError, command_preflight, execution_snapshot, public_tool_event,
)
from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
from sap_business_agents_platform.codex_planner import CodexPlanner, _tool_authoring_codex


def test_full_access_client_has_no_named_windows_profile(tmp_path):
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'job')
    client = _tool_authoring_codex(workspace, full_access=True)
    args = list(client._client._sync.config.launch_args_override)
    assert 'sandbox_mode="danger-full-access"' in args
    assert 'approval_policy="never"' in args
    assert not any('sapba-authoring' in value or 'permissions.' in value for value in args)
    disabled = {args[i + 1] for i, value in enumerate(args[:-1]) if value == '--disable'}
    assert not {'shell_tool', 'apply_patch_streaming_events'} & disabled
    assert {'plugins', 'apps', 'multi_agent', 'browser_use'} <= disabled


def test_full_access_probe_does_not_initialize_permissions(tmp_path):
    calls = []
    async def request(method, params, **kwargs):
        calls.append((method, params))
        return SimpleNamespace(exit_code=0, stdout='sapba-command-ready\n', stderr='')
    result = asyncio.run(command_preflight(SimpleNamespace(_client=SimpleNamespace(request=request)), tmp_path))
    assert result['sap_calls'] == 0 and result['os_isolation'] is False
    assert calls[0][0] == 'command/exec'
    assert calls[0][1]['sandboxPolicy'] == {'type': 'dangerFullAccess'}
    assert 'permissionProfile' not in calls[0][1]


@pytest.mark.parametrize('stdout,code', [('wrong', 0), ('sapba-command-ready', 1)])
def test_probe_requires_actual_marker_and_exit_code(tmp_path, stdout, code):
    async def request(*args, **kwargs):
        return SimpleNamespace(exit_code=code, stdout=stdout, stderr='private')
    with pytest.raises(RuntimeExecutionError, match='runtime_command_preflight_failed'):
        asyncio.run(command_preflight(SimpleNamespace(_client=SimpleNamespace(request=request)), tmp_path))


def test_probe_timeout_has_stage_specific_safe_error(tmp_path):
    async def request(*args, **kwargs):
        raise TimeoutError('private output')
    with pytest.raises(RuntimeExecutionError, match='runtime_command_preflight_timeout') as error:
        asyncio.run(command_preflight(SimpleNamespace(_client=SimpleNamespace(request=request)), tmp_path))
    assert 'private' not in str(error.value)


def test_tool_event_omits_commands_outputs_and_child_messages():
    event = public_tool_event({'type': 'commandExecution', 'status': 'completed',
                              'command': 'secret', 'aggregatedOutput': 'private', 'text': 'private'})
    assert event == {'kind': 'commandExecution', 'status': 'completed'}


def test_full_access_thread_and_turn_both_explicit(tmp_path):
    import json
    from openai_codex import Sandbox, ApprovalMode
    workspace = AuthoringWorkspace(tmp_path, tmp_path / 'job')
    workspace.source.mkdir(parents=True)
    workspace.full_access = True
    package = {'manifest': {'slug': 'example'}, 'readme': '', 'rules': None, 'files': {}}
    workspace.write_package(package)
    calls = []
    class Thread:
        id = 'test-thread'
        async def run(self, prompt, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(final_response=json.dumps({'action': 'reply', 'summary': {'zh': '说明', 'en': 'Reply'}}))
    async def start(**kwargs):
        calls.append(kwargs)
        return Thread()
    result = asyncio.run(CodexPlanner(tmp_path, 'gpt-5.6-sol', reasoning_effort='high')._run_agent_feedback(
        SimpleNamespace(thread_start=start), 'test', package, 'old-thread', str(workspace.source), tool_workspace=workspace))
    assert result['thread_id'] == 'test-thread'
    for call in calls:
        assert call['sandbox'] == Sandbox.full_access
        assert call['model'] == 'gpt-5.6-sol'
        assert call['approval_mode'] == ApprovalMode.deny_all
    assert calls[1]['effort'] == 'high'


def test_snapshot_does_not_claim_unconnected_tools():
    snapshot = execution_snapshot(model='gpt-5.6-sol', effort='high')
    assert snapshot['policy']['mode'] == 'full_access'
    assert snapshot['tool_catalog']['browser'] == 'not_connected'
    assert snapshot['tool_catalog']['subagents'] == 'not_connected'
    assert snapshot['os_isolation'] is False


def test_free_query_full_access_is_explicit_and_legacy_remains_read_only():
    from openai_codex import Sandbox
    from sap_business_agents_platform.harness import _sandbox, _developer_instructions, _custom_tool_kind
    assert _sandbox(full_access=True) == Sandbox.full_access
    assert _sandbox() == Sandbox.read_only
    assert 'Never use shell' not in _developer_instructions(full_access=True)
    assert 'SAP facts require Broker evidence' in _developer_instructions(full_access=True)
    assert 'Never use shell' in _developer_instructions()
    item = {'type': 'customToolCall', 'input': 'tools.exec_command({command: "secret"})'}
    assert _custom_tool_kind(item, full_access=True) == ('engineering', '')
    assert _custom_tool_kind(item) == ('forbidden', '')
