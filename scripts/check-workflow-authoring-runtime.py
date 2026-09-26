"""No-model, no-SAP smoke test of the exact workflow v2 command boundary."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uuid

from sap_business_agents_platform.workflow_authoring_runtime import WorkflowWorkspace, workflow_preflight
from sap_business_agents_platform.codex_planner import _tool_authoring_codex, _authoring_preflight_command
from sap_business_agents_platform.runtime_execution import owned_client, command_preflight


async def main(args):
    root = Path(__file__).resolve().parents[1]
    workspace = WorkflowWorkspace(root, root / ".local-data/workflow-authoring-smoke" / uuid.uuid4().hex)
    workspace.prepare(None)
    workspace.read_only_source = True
    client = _tool_authoring_codex(workspace, full_access=args.mode == "full_access")
    async with owned_client(client, cleanup_timeout=10) as codex:
        if args.mode == "full_access":
            result = await command_preflight(codex, workspace.source)
        else:
            async def probe(ws, command, *, timeout=30):
                return await _authoring_preflight_command(codex, ws, command, timeout=timeout)
            result = await workflow_preflight(workspace, probe)
        print(json.dumps({"mode": args.mode, "checks": result, "sap_calls": 0, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["restricted", "full_access"], default="restricted")
    parser.add_argument("--confirm-trusted-local", action="store_true")
    args = parser.parse_args()
    if args.mode == "full_access" and not args.confirm_trusted_local:
        parser.error("full_access requires --confirm-trusted-local")
    asyncio.run(main(args))
