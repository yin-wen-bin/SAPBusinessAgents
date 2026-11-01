"""Run-bound shell transport to the existing Broker, when native MCP is absent.

No SAP connection, login or independent execution engine exists here. The host
keeps the capability outside the engineering copy and revokes it on cleanup.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def create(settings, broker, run_id, capability):
    from .mcp_server import _SAP_TOOLS, _TOOL_TOOLS
    from .runtime_report_contract import tools_for_contract
    from .runtime_input_control import tools_for_task
    from .runtime_validation_store import atomic
    contract = broker.report_contract(run_id)
    state = broker.store.get_harness_state(run_id)
    tools = tools_for_task(tools_for_contract(_SAP_TOOLS, contract),
        formal=contract.get('acceptance_spec') is not None, version=state.get('terminal_protocol', 1)) + _TOOL_TOOLS
    path = settings.data_root / 'harness' / run_id / 'tool-bridge' / 'binding.json'
    atomic(path, {'version': 1, 'api_url': settings.internal_api_url, 'run_id': run_id,
                  'capability': capability, 'tools': tools})
    command = f'"{sys.executable}" -I -B "{Path(__file__).resolve()}" "{path.resolve()}"'
    instructions = ('\nThis run has an approved native-shell transport to the SAME platform Broker. '
        'Use it whenever the named MCP tools are unavailable; do not search the repository or invoke '
        'mcp_server._call_platform directly. First run ' + command + ' --list. '
        'For a call, pipe one UTF-8 JSON object of arguments to ' + command + ' --tool TOOL_NAME. '
        'The host supplies run binding; never read, print, modify or copy binding.json, credentials or connection settings. '
        'The command only supports the listed read-only/platform-control tools and retains all live schema, scope, '
        'permission and report-validation checks. Missing business identifiers require request_query_clarification '
        'through this transport, not a diagnostic terminal. The same tool names and schemas apply.\n')
    return path, instructions


def invoke(binding, name, arguments, opener=None):
    url = urlsplit(binding['api_url'])
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost'} or not url.port
            or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment):
        raise ValueError('bridge_target_not_loopback')
    if name not in {tool['name'] for tool in binding['tools']} or not isinstance(arguments, dict):
        raise ValueError('bridge_tool_not_registered')
    request = Request(binding['api_url'].rstrip('/') + '/api/internal/harness/tools/' + quote(name, safe=''),
        data=json.dumps({'arguments': arguments}, ensure_ascii=False).encode('utf8'), method='POST',
        headers={'Content-Type': 'application/json', 'X-SAPBA-Run': binding['run_id'],
                 'X-SAPBA-Capability': binding['capability']})
    with (opener or build_opener(ProxyHandler({}), NoRedirect())).open(request, timeout=330) as response:
        value = json.loads(response.read().decode('utf8'))
    if not isinstance(value, dict):
        raise ValueError('bridge_response_invalid')
    return value


def main():
    parser = argparse.ArgumentParser(description='Call this run\'s existing platform Broker.')
    parser.add_argument('binding', type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--list', action='store_true')
    actions.add_argument('--tool')
    args = parser.parse_args()
    try:
        binding = json.loads(args.binding.read_text(encoding='utf8'))
        value = {'tools': binding['tools']} if args.list else invoke(binding, args.tool,
            json.loads(sys.stdin.buffer.read().decode('utf-8-sig')))
    except Exception as error:
        value = {'ok': False, 'code': 'platform_tool_bridge_failed', 'error_type': type(error).__name__}
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + '\n').encode('utf8'))


if __name__ == '__main__':
    main()
