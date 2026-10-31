"""Small stdio MCP bridge. Intelligence stays in the native host conversation."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    {"name": "test_sap_read", "description": "Execute a frozen sample/baseline/anchor recipe through the registered GET-only SAP Provider. Task must already be started. No arbitrary URLs or credentials.",
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
    store = Store(args.repository)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError()
            value = response(store, request)
        except (ValueError, TypeError):
            value = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if value is not None:
            print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
