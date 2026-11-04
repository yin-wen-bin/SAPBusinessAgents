"""Small stdio MCP bridge. Intelligence stays in the native host conversation."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jsonschema import Draft202012Validator

from .common import Store, TestAgentError


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
TOOLS = [
    {"name": "test_inventory", "description": "Discover current source-backed SAPBusinessAgents features. No SAP/model calls.", "inputSchema": schema({}), "annotations": {"readOnlyHint": True}},
    {"name": "test_prepare", "description": "Persist selected native test cases and generate scripts; does not execute them.",
     "inputSchema": schema({"selected": {"type": "array", "items": STRING, "minItems": 1}, "inventory_digest": STRING,
                            "cases": {"type": "array", "items": {"type": "object"}, "minItems": 1}}, ("selected", "inventory_digest", "cases"))},
    {"name": "test_start", "description": "Start one user-requested test plan in an isolated working-tree snapshot. Retry identical request IDs safely.",
     "inputSchema": schema({"plan_id": STRING, "plan_digest": STRING, "request_id": STRING}, ("plan_id", "plan_digest", "request_id"))},
    {"name": "test_status", "description": "Read persisted task status and failure groups without replaying execution.", "inputSchema": schema({"task_id": STRING}, ("task_id",)), "annotations": {"readOnlyHint": True}},
    {"name": "test_sap_read", "description": "Execute a frozen sample/baseline/anchor recipe through registered GET-only SAP OData or approved read-only Skills. Task must already be started. No arbitrary URLs or credentials.",
     "inputSchema": schema({"task_id": STRING, "case_id": STRING, "role": {"enum": ["sample", "baseline", "anchor"]}}, ("task_id", "case_id", "role")), "annotations": {"readOnlyHint": True}},
    {"name": "test_cancel", "description": "Stop future dispatch and request cancellation of only this test task's owned execution.",
     "inputSchema": schema({"task_id": STRING}, ("task_id",))},
]


def invoke(store, name, args):
    from .inventory import discover
    from .plans import prepare
    from .state import start, cancel
    from .report import report
    if name == "test_inventory":
        return discover(store.repository)
    if name == "test_prepare":
        return prepare(store, args["selected"], args["cases"], args["inventory_digest"])
    if name == "test_start":
        return start(store, args["plan_id"], args["plan_digest"], args["request_id"])
    if name == "test_status":
        return report(store, args["task_id"])
    if name == "test_sap_read":
        from .sap import read
        return asyncio.run(read(store, args["task_id"], args["case_id"], args["role"]))
    if name == "test_cancel":
        return cancel(store, args["task_id"])
    raise TestAgentError("tool_unknown")


def response(store, request):
    request_id, method = request.get("id"), request.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (request.get("params") or {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "sapba-auto-test", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments", {})
        tool = next((tool for tool in TOOLS if tool["name"] == name), None)
        try:
            if tool is None:
                raise TestAgentError("tool_unknown")
            if not Draft202012Validator(tool["inputSchema"]).is_valid(arguments):
                raise TestAgentError("tool_arguments_invalid")
            value = invoke(store, name, arguments)
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": False}
        except Exception as error:
            code = getattr(error, "code", "test_tool_failed")
            result = {"content": [{"type": "text", "text": json.dumps({"code": code})}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    args = parser.parse_args()
    # Avoid the platform package's eager FastAPI import. This pure coordination
    # module has the same source/lock contract as the verified launcher.
    source = Path(__file__).resolve().parents[1] / 'sap_business_agents_platform/startup_control.py'
    spec = importlib.util.spec_from_file_location('sapba_mcp_coordination', source)
    coordination = importlib.util.module_from_spec(spec)
    exec(compile(source.read_bytes(), str(source), 'exec'), coordination.__dict__)
    output_lock = threading.Lock()
    def send(value):
        if value is not None:
            with output_lock:
                print(json.dumps(value, ensure_ascii=False), flush=True)
    def blocked(request, error):
        value = {'code': str(error), 'accepted': False, 'retryable': True}
        send({'jsonrpc': '2.0', 'id': request['id'], 'result': {
            'content': [{'type': 'text', 'text': json.dumps(value)}],
            'isError': True, 'structuredContent': value}})
    def dispatch(request, operation):
        try:
            # Store initialization also writes. Do it only after admission.
            send(response(Store(args.repository), request))
        except Exception:
            send({'jsonrpc': '2.0', 'id': request['id'], 'error': {
                'code': -32603, 'message': 'test_tool_failed'}})
        finally:
            operation.__exit__(None, None, None)
    # A long SAP read must not block status or the explicit cancellation tool.
    with coordination.MCPParticipant(args.repository, 'sap_business_agents_test_agent.mcp', __file__) as participant, ThreadPoolExecutor(max_workers=4, thread_name_prefix="sapba-test-tool") as workers, ThreadPoolExecutor(max_workers=1, thread_name_prefix='sapba-test-cancel') as cancellations:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError()
                if request.get("method") == "tools/call":
                    if request.get('id') is None: continue
                    cancel = (request.get('params') or {}).get('name') == 'test_cancel'
                    operation = participant.operation(cancel=cancel)
                    try:
                        # Admission precedes the worker queue. Queued calls are
                        # already accepted and must drain before any switch.
                        operation.__enter__()
                    except coordination.StartupBlocked as error:
                        blocked(request, error)
                        continue
                    try:
                        (cancellations if cancel else workers).submit(dispatch, request, operation)
                    except BaseException:
                        operation.__exit__(None, None, None)
                        raise
                else:
                    send(response(None, request))
            except (ValueError, TypeError):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})


if __name__ == "__main__":
    main()
