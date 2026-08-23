from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **properties,
            "tool_call_id": {"type": "string", "description": "Optional idempotency key."},
        },
        "required": required or [],
    }


_ADT_FILTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "field": {"type": "string"},
        "sign": {"type": "string", "enum": ["I", "E"]},
        "operator": {
            "type": "string",
            "enum": ["EQ", "NE", "GT", "GE", "LT", "LE", "BT", "IN"],
        },
        "value": {},
        "low": {},
        "high": {},
        "values": {"type": "array", "minItems": 1, "maxItems": 100},
    },
    "required": ["field", "operator"],
}

_ADT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "Declarative bounded ADT read. SAPSkillhub owns its connection; never pass a profile, "
        "URL, client, credentials or SQL."
    ),
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "source_type": {"type": "string", "enum": ["table", "cds"]},
        "object": {"type": "string", "description": "Table or CDS identifier."},
        "fields": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "uniqueItems": True,
            "items": {"type": "string"},
        },
        "filters": {"type": "array", "minItems": 1, "maxItems": 20, "items": _ADT_FILTER_SCHEMA},
        "order_by": {
            "type": "array",
            "maxItems": 10,
            "description": "Ascending stable-key field names confirmed by SAPSkillhub live metadata.",
            "items": {"type": "string"},
        },
        "max_rows": {"type": "integer", "minimum": 1, "maximum": 30000},
    },
    "required": ["schema_version", "source_type", "object", "fields", "filters", "max_rows"],
}


_SAP_TOOLS = [
    {
        "name": "sap_catalog_search",
        "description": "Search the advisory bilingual SAP OData catalog. Live metadata is still required before execution.",
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "locale": {"type": "string", "enum": ["zh", "en", "auto"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["query"],
        ),
    },
    {
        "name": "sap_schema_get",
        "description": "Read authoritative live $metadata for a registered service/version and entity set list.",
        "inputSchema": _schema(
            {
                "service_name": {"type": "string"},
                "odata_version": {"type": "string", "enum": ["2.0", "4.0"]},
                "entity_sets": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "query": {"type": "string"},
                "max_fields": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            ["service_name", "odata_version", "entity_sets"],
        ),
    },
    {
        "name": "sap_query_validate",
        "description": "Validate a strict GET-only SAP query plan against the registered service and live schema.",
        "inputSchema": _schema(
            {"plan": {"type": "object"}, "query": {"type": "string"}}, ["plan"]
        ),
    },
    {
        "name": "sap_query_execute",
        "description": "Execute a validated GET-only SAP plan and return a bounded preview plus a platform evidence reference.",
        "inputSchema": _schema(
            {"plan": {"type": "object"}, "query": {"type": "string"}}, ["plan"]
        ),
    },
    {
        "name": "sap_evidence_read",
        "description": "Read a bounded page of normalized rows from an evidence reference already stored by the platform.",
        "inputSchema": _schema(
            {
                "evidence_ref": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string"}},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            ["evidence_ref"],
        ),
    },
    {
        "name": "sap_evidence_assess",
        "description": "Deterministically assess evidence completeness and issue an ADT gap token only after the OData-first gate.",
        "inputSchema": _schema(
            {
                "question": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string"}},
                "missing_evidence": {"type": "array", "items": {"type": "string"}},
            },
            ["question", "evidence_refs", "missing_evidence"],
        ),
    },
    {
        "name": "sap_skill_execute",
        "description": "Execute one approved SAPSkillhub read-only skill with a single-use deterministic evidence-gap token. ADT order_by is optional and must be omitted unless a trusted live-DDIC result supplied the exact stable key.",
        "inputSchema": _schema(
            {
                "skill_id": {"type": "string", "enum": ["sap-adt-table-export"]},
                "gap_token": {"type": "string"},
                "input": _ADT_INPUT_SCHEMA,
            },
            ["skill_id", "gap_token", "input"],
        ),
    },
    {
        "name": "sap_final_report_validate",
        "description": "Validate that customer-business claims cite run-scoped SAP evidence references.",
        "inputSchema": _schema(
            {"report": {"type": "object"}}, ["report"]
        ),
    },
]

_TOOL_TOOLS = [
    {
        "name": "tool_discovery_search",
        "description": "Search safe built-ins and configured tools; optionally inspect a public credential-free OpenAPI JSON manifest discovered by Web Search.",
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "capability": {"type": "string"},
                "manifest_url": {"type": "string", "format": "uri"},
            },
            ["query"],
        ),
    },
    {
        "name": "tool_discovery_inspect",
        "description": "Inspect the schema, provenance, hash and admission decision of a run-scoped tool candidate.",
        "inputSchema": _schema(
            {"candidate_id": {"type": "string"}}, ["candidate_id"]
        ),
    },
    {
        "name": "tool_discovery_activate",
        "description": "Temporarily activate a candidate only when the platform can enforce its read-only contract.",
        "inputSchema": _schema(
            {"candidate_id": {"type": "string"}}, ["candidate_id"]
        ),
    },
    {
        "name": "external_tool_execute",
        "description": "Execute one activated credential-free public HTTPS GET/HEAD OpenAPI operation through the admission gateway.",
        "inputSchema": _schema(
            {
                "candidate_id": {"type": "string"},
                "operation_id": {"type": "string"},
                "parameters": {"type": "object"},
            },
            ["candidate_id", "operation_id", "parameters"],
        ),
    },
    {
        "name": "safe_compute",
        "description": "Evaluate a bounded pure Python expression over supplied JSON inputs using an AST allowlist and no I/O.",
        "inputSchema": _schema(
            {
                "language": {"type": "string", "enum": ["python"]},
                "code": {"type": "string"},
                "inputs": {"type": "object"},
            },
            ["language", "code", "inputs"],
        ),
    },
]


def _result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": value,
        "isError": value.get("ok") is False,
    }


def _call_platform(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("SAPBA_INTERNAL_API_URL", "").rstrip("/")
    run_id = os.environ.get("SAPBA_HARNESS_RUN_ID", "")
    capability = os.environ.get("SAPBA_HARNESS_CAPABILITY", "")
    if not base_url or not run_id or not capability:
        return {"ok": False, "code": "harness_broker_unconfigured"}
    body = json.dumps({"arguments": arguments}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/internal/harness/tools/{urllib.parse.quote(tool_name, safe='')}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-SAPBA-Run": run_id,
            "X-SAPBA-Capability": capability,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=330) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = {"ok": False, "code": "harness_broker_http_error", "status": exc.code}
    except (OSError, ValueError) as exc:
        payload = {"ok": False, "code": "harness_broker_unavailable", "message": str(exc)}
    return payload if isinstance(payload, dict) else {"ok": False, "code": "invalid_broker_output"}


def serve(mode: str) -> None:
    tools = _SAP_TOOLS if mode == "sap" else _TOOL_TOOLS
    allowed = {item["name"] for item in tools}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            if method == "initialize":
                protocol = str((request.get("params") or {}).get("protocolVersion") or "2025-06-18")
                result = {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": f"sapbusinessagents-{mode}", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            **item,
                            "annotations": {
                                "readOnlyHint": True,
                                "destructiveHint": False,
                                "idempotentHint": True,
                                "openWorldHint": mode == "tools",
                            },
                        }
                        for item in tools
                    ]
                }
            elif method == "tools/call":
                params = request.get("params") or {}
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if name not in allowed or not isinstance(arguments, dict):
                    result = _result({"ok": False, "code": "unregistered_tool_rejected"})
                else:
                    result = _result(_call_platform(name, arguments))
            elif method and str(method).startswith("notifications/"):
                continue
            else:
                result = {}
            if request_id is not None:
                sys.stdout.write(
                    json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n"
                )
                sys.stdout.flush()
        except Exception as exc:  # protocol errors stay on stdout as JSON-RPC errors
            if isinstance(locals().get("request"), dict) and request.get("id") is not None:
                sys.stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request.get("id"),
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["sap", "tools"], required=True)
    args = parser.parse_args()
    serve(args.mode)


if __name__ == "__main__":
    main()
