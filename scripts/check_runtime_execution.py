"""Explicit local SDK smoke check; no SAP, publication, global settings or Git writes.

Run with the repository virtualenv. JSON output deliberately excludes raw SDK
errors, command output and credentials. This does not certify the full Harness.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.codex_planner import _tool_authoring_codex
from sap_business_agents_platform.runtime_execution import command_preflight


async def check(*, model: str | None = None, effort: str | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="sapba-full-access-probe-") as temporary:
        client = _tool_authoring_codex(SimpleNamespace(source=Path(temporary)), full_access=True)
        stage = "sdk_initialize"
        start = asyncio.create_task(client.__aenter__())
        result = {"status": "failed", "sap_calls": 0, "model_calls": 0}
        try:
            codex = await asyncio.wait_for(asyncio.shield(start), timeout=30)
            stage = "command_preflight"
            result = await command_preflight(codex, Path(temporary))
            result["model_calls"] = 0
            if model:
                from openai_codex import Sandbox, ApprovalMode
                stage = "model_tool_test"
                result.update(status="failed", model_calls=1, model=model, reasoning_effort=effort)
                thread = await asyncio.wait_for(codex.thread_start(
                    cwd=temporary, model=model, sandbox=Sandbox.full_access,
                    approval_mode=ApprovalMode.deny_all,
                    developer_instructions="Work only in the provided temporary directory. No network, SAP, repository edits or external tools. Use file and command tools for the fixed smoke check."), timeout=30)
                schema = {"type": "object", "properties": {"done": {"type": "boolean"}},
                          "required": ["done"], "additionalProperties": False}
                response = await asyncio.wait_for(thread.run(
                    'Use a file edit to create smoke.py containing print("sdk-tool-smoke"). '
                    'Run it with Python. Then create result.txt with exactly sdk-tool-smoke. Return done=true only after the command succeeds.',
                    model=model, effort=effort, sandbox=Sandbox.full_access,
                    approval_mode=ApprovalMode.deny_all, output_schema=schema), timeout=180)
                items = [item.model_dump(mode="json", by_alias=True) for item in response.items]
                command_observed = any(item.get("type") == "commandExecution" and item.get("exitCode") == 0
                                       and "sdk-tool-smoke" in str(item.get("aggregatedOutput") or "") for item in items)
                file_passed = (Path(temporary) / "result.txt").read_text().strip() == "sdk-tool-smoke"
                passed = json.loads(response.final_response).get("done") is True and file_passed and command_observed
                result.update(status="passed" if passed else "failed", controller_file_check=file_passed,
                              successful_command_observed=command_observed)
        except Exception as error:
            result.update(failed_stage=stage, error_code=getattr(error, "code", type(error).__name__))
        finally:
            proc = getattr(client._client._sync, "_proc", None)
            if proc is not None and proc.poll() is None:
                if os.name == "nt":
                    killed = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    await asyncio.wait_for(killed.wait(), timeout=8)
                else:
                    proc.kill()
            try:
                await asyncio.wait_for(asyncio.shield(start), timeout=2)
            except Exception:
                pass
            await asyncio.wait_for(client.close(), timeout=5)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--effort")
    args = parser.parse_args()
    if bool(args.model) != bool(args.effort):
        parser.error("Supply model and effort together; implicit global configuration is not allowed.")
    output = asyncio.run(check(model=args.model, effort=args.effort))
    print(json.dumps(output))
    raise SystemExit(0 if output["status"] == "passed" else 1)
