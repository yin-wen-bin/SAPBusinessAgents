"""Independent workflow_authoring.v2 adapter; legacy Codex paths are untouched."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
import subprocess
import sys

from .authoring_workspace import AuthoringWorkspace
from .authoring_harness import AuthoringHarnessError


from .runtime_workflow_authoring import WorkflowWorkspace

from .runtime_workflow_authoring import OUTPUT_SCHEMA, instructions as authoring_instructions, prompt as authoring_prompt, decode as decode_authoring


def capabilities(mode: str, preflight: dict | None = None) -> dict[str, Any]:
    verified = bool(preflight and preflight.get("status") == "passed")
    return {
        "contract": "workflow_authoring.v2", "mode": mode,
        "file_edit": "available" if verified else "preflight_required",
        "shell_tests": "available" if verified else "preflight_required",
        "web": "preflight_required" if mode == "full_access" else "network_authorization_required",
        "mcp": "not_connected", "subagents": "not_verified",
        "resume": "platform_history", "steer": "queue_only",
        "stream_events": "sdk_tool_events", "image_input": "not_verified",
        "os_isolation": False if mode == "full_access" else True if verified else "unverified_until_preflight",
    }


async def workflow_preflight(workspace: Any, command_runner: Any) -> dict:
    """Verify the additional v2 source-read/candidate-write split with canaries."""
    from .authoring_workspace import sandbox_preflight, _permission_denied
    source_root = workspace.source / "src"
    if not source_root.is_dir():
        raise AuthoringHarnessError("workflow_harness_source_unavailable")
    canary = source_root / ".workflow-readonly-canary"
    canary.write_text("readonly-canary", encoding="utf-8")
    result = await sandbox_preflight(workspace, command_runner=command_runner)
    code = (
        "import json\nfrom pathlib import Path\n"
        f"p=Path({str(canary)!r})\n"
        "result={'source_read':p.read_text()=='readonly-canary'}\n"
        "try:\n p.write_text('changed'); result['source_write_error']=None\n"
        "except OSError as e:\n result['source_write_error']={'errno':e.errno,'winerror':getattr(e,'winerror',None)}\n"
        "print(json.dumps(result))\n"
    )
    status, stdout, _ = await command_runner(workspace, [sys.executable, "-I", "-c", code], timeout=30)
    try:
        observation = json.loads(stdout.decode("utf-8").strip())
    except (ValueError, UnicodeError):
        raise AuthoringHarnessError("workflow_harness_source_boundary_failed") from None
    if (status or not isinstance(observation, dict)
            or set(observation) != {"source_read", "source_write_error"}
            or observation["source_read"] is not True
            or not _permission_denied(observation["source_write_error"])
            or canary.read_text(encoding="utf-8") != "readonly-canary"):
        raise AuthoringHarnessError("workflow_harness_source_boundary_failed")
    result["checks"].update(source_read=True, source_write_denied=True)
    return result


async def collect_turn(handle: Any, emit: Any) -> Any:
    """Use the installed SDK's turn stream; publish metadata, never tool bodies."""
    from openai_codex.api import _collect_async_turn_result
    from .runtime_execution import public_tool_event
    stream = handle.stream()
    async def observed():
        async for notification in stream:
            if notification.method in {"item/started", "item/completed"}:
                item = getattr(notification.payload, "item", None)
                if item is not None:
                    root = getattr(item, "root", item)
                    data = root.model_dump(mode="json", by_alias=True) if hasattr(root, "model_dump") else {}
                    safe = public_tool_event(data)
                    if safe["kind"] != "unknown":
                        emit("tool_progress", safe)
            yield notification
    try:
        return await _collect_async_turn_result(observed(), turn_id=handle.id)
    finally:
        await stream.aclose()


async def run(planner: Any, **kwargs: Any) -> dict:
    """Compatibility entry; candidate orchestration is SDK-independent."""
    from .runtime_workflow_authoring import run as shared_run
    return await shared_run(planner, **kwargs)
