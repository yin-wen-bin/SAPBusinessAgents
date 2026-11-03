"""Attest the existing context-only workflow SDK client configuration.

This does not attest Windows filesystem isolation or grant root read access.
Only a successful real product turn establishes live SDK integration.
"""
import re
from dataclasses import replace


REQUIRED_DISABLED = frozenset({
    'shell_tool', 'apply_patch_streaming_events', 'browser_use', 'computer_use',
    'image_generation', 'multi_agent', 'plugins', 'apps', 'hooks', 'view_image', 'goals',
})


def context_client(source):
    """Reuse the product client, disabling remaining local-image/goal tools."""
    from .codex_planner import _agent_authoring_codex
    client = _agent_authoring_codex(source)
    config = client._client._sync.config
    args = list(config.launch_args_override)
    index = args.index('app-server')
    args[index:index] = ['--disable', 'view_image', '--disable', 'goals']
    client._client._sync.config = replace(config, launch_args_override=tuple(args))
    return client


class ContextPolicyError(ValueError):
    pass


def attest_context_client(client):
    args = list(client._client._sync.config.launch_args_override)
    disabled = {args[i+1] for i, value in enumerate(args[:-1]) if value == '--disable'}
    enabled = {args[i+1] for i, value in enumerate(args[:-1]) if value == '--enable'}
    settings = {}
    for i, value in enumerate(args[:-1]):
        if value in {'-c', '--config'}:
            key, separator, content = args[i+1].partition('=')
            if separator:
                settings[key.strip()] = content.strip().strip('"\'')
    if not REQUIRED_DISABLED <= disabled or enabled & REQUIRED_DISABLED:
        raise ContextPolicyError('workflow_context_native_tools_exposed')
    if settings.get('web_search') != 'disabled':
        raise ContextPolicyError('workflow_context_web_exposed')
    if any(re.fullmatch(r'mcp_servers\.[A-Za-z0-9_-]+\.enabled', key) and value != 'false'
           for key, value in settings.items()):
        raise ContextPolicyError('workflow_context_mcp_exposed')
    return {'status':'passed', 'boundary':'provided_workflow_context_only',
            'proof_scope':'client_launch_configuration', 'os_isolation':False,
            'native_file_tools':False, 'native_shell_tools':False,
            'checks': {'required_builtin_tools_disabled':True,
                       'web_disabled':True, 'configured_mcp_overrides_disabled':True}}
