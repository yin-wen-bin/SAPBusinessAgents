import asyncio
import json
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.authoring_harness import AuthoringHarnessError
from sap_business_agents_platform.authoring_workspace import AuthoringWorkspace
from sap_business_agents_platform.codex_planner import CodexPlanner, _authoring_preflight_command, _tool_authoring_codex


def test_tool_client_keeps_other_capabilities_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('SAP_PASSWORD', 'never-inherit')
    monkeypatch.setenv('UNRELATED_TOKEN', 'never-inherit')
    workspace = AuthoringWorkspace(tmp_path / 'repo', tmp_path / 'job')
    client = _tool_authoring_codex(workspace)
    config = client._client._sync.config
    args = list(config.launch_args_override)
    disabled = {args[i + 1] for i, value in enumerate(args[:-1]) if value == '--disable'}
    enabled = {args[i + 1] for i, value in enumerate(args[:-1]) if value == '--enable'}
    assert {'shell_tool', 'apply_patch_streaming_events'} <= enabled
    assert not enabled.intersection(disabled)
    assert {'plugins', 'apps', 'hooks', 'browser_use', 'multi_agent'} <= disabled
    assert "shell_environment_policy.inherit='none'" in args
    assert config.env['SAP_PASSWORD'] == config.env['UNRELATED_TOKEN'] == ''
    assert not any('never-inherit' in arg for arg in args)
    assert "default_permissions='sapba-authoring'" in args


def test_preflight_uses_same_named_profile(tmp_path):
    calls = []
    async def request(method, params, **kwargs):
        calls.append((method, params))
        return SimpleNamespace(exit_code=0, stdout='ok', stderr='')
    result = asyncio.run(_authoring_preflight_command(SimpleNamespace(_client=SimpleNamespace(request=request)),
        SimpleNamespace(source=tmp_path), ['fixed-probe'], timeout=2))
    assert result == (0, b'ok', b'')
    assert calls == [('command/exec', {'command': ['fixed-probe'], 'cwd': str(tmp_path),
                                     'permissionProfile': 'sapba-authoring', 'timeoutMs': 2000})]


def test_preflight_has_controller_timeout(monkeypatch, tmp_path):
    async def request(*args, **kwargs):
        return None
    async def expire(coro, *, timeout):
        coro.close()
        assert timeout == 32
        raise TimeoutError()
    monkeypatch.setattr(asyncio, 'wait_for', expire)
    with pytest.raises(AuthoringHarnessError, match='preflight_timeout'):
        asyncio.run(_authoring_preflight_command(SimpleNamespace(_client=SimpleNamespace(request=request)),
            SimpleNamespace(source=tmp_path), ['fixed-probe'], timeout=2))


@pytest.mark.parametrize('mode', ['reply', 'edit', 'ambiguous', 'platform'])
def test_tool_thread_file_results_are_controller_verified(tmp_path, monkeypatch, mode):
    import openai_codex.api
    workspace = AuthoringWorkspace(tmp_path / 'repo', tmp_path / 'job')
    workspace.source.mkdir(parents=True)
    package = {'manifest': {'slug': 'example'}, 'readme': 'before', 'rules': None, 'files': {},
               'binary_files': {'untouched.bin': 'YQ=='}}
    workspace.write_package(package)
    calls = []
    async def request(method, params, **kwargs):
        calls.append(params)
        assert method == 'thread/start'
        return SimpleNamespace(thread=SimpleNamespace(id='new-thread'))
    class Thread:
        id = 'new-thread'
        async def run(self, prompt, *, output_schema, effort):
            assert effort == 'high'
            raw = {'action': 'reply', 'summary': {'zh': '说明', 'en': 'Summary'}}
            if mode in {'edit', 'ambiguous'}:
                (workspace.agent / 'README.md').write_text('after', encoding='utf-8')
                raw['action'] = 'revise_agent'
            if mode == 'ambiguous':
                raw['edits_json'] = '[]'
            if mode == 'platform':
                (workspace.source / 'README.md').write_text('unapproved', encoding='utf-8')
            return SimpleNamespace(final_response=json.dumps(raw))
    monkeypatch.setattr(openai_codex.api, 'AsyncThread', lambda *args: Thread())
    planner = CodexPlanner(tmp_path, 'gpt-5.6-sol', reasoning_effort='high')
    async def run():
        return await planner._run_agent_feedback(SimpleNamespace(_client=SimpleNamespace(request=request)),
            'test', package, 'must-not-resume', str(workspace.source), tool_workspace=workspace)
    if mode in {'ambiguous', 'platform'}:
        with pytest.raises(AuthoringHarnessError, match='ambiguous_changes' if mode == 'ambiguous' else 'platform_approval_required'):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        assert result['thread_id'] == 'new-thread'
        if mode == 'edit':
            assert result['package']['readme'] == 'after'
            assert result['package']['binary_files'] == package['binary_files']
        else:
            assert 'package' not in result
    assert calls[0]['permissions'] == 'sapba-authoring'
    assert calls[0]['approvalPolicy'] == 'never'
    assert calls[0]['model'] == 'gpt-5.6-sol'
    assert 'sandbox' not in calls[0]


def test_arbitrary_tool_policy_is_rejected_before_workspace(tmp_path):
    with pytest.raises(AuthoringHarnessError, match='policy_invalid'):
        asyncio.run(CodexPlanner(tmp_path, 'gpt-5.6-sol').review_agent_feedback(
            feedback='test', locale='en', package={}, tool_policy={'mode': 'full_access'}))
