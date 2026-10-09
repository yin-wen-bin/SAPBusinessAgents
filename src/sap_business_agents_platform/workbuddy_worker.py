"""Standalone worker; imports only stdlib and the isolated WorkBuddy SDK.

Launched with -I. Never imports the platform, Codex or its SDK. No database writes.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
import contextlib
import re
import time
import hashlib
from pathlib import Path

PROTOCOL = "sapba.workbuddy.worker/1"
FRAME_LIMIT = 1024 * 1024
NATIVE_TOOLS = frozenset({"Bash", "Read", "Write", "Edit", "Glob", "Grep", "MultiEdit", "TodoWrite"})
SCHEMA_TOOL = "StructuredOutput"  # Verified pinned CLI's local output-capture tool.
NATIVE_DIAGNOSTICS = "sapba.workbuddy.native-output/1"
_protocol_output = sys.stdout

FAILURE_CODES = frozenset({
    "workbuddy_execution_failed", "workbuddy_upstream_empty_stream", "workbuddy_upstream_error",
    "workbuddy_result_missing", "workbuddy_structured_output_missing", "workbuddy_structured_output_invalid",
    "workbuddy_schema_check_response_invalid", "workbuddy_tool_response_invalid",
})


class NativeTerminalFailure(ValueError):
    def __init__(self, code, source):
        self.code, self.source = code, source
        super().__init__(code)


def native_failure_code(error):
    """Recognize explicit native signatures without exporting exception text."""
    text = str(error)
    if re.search(r"\bEmptyStreamError\b", text):
        return "workbuddy_upstream_empty_stream"
    if "Error streaming response" in text:
        return "workbuddy_upstream_error"
    return text if text in FAILURE_CODES else "workbuddy_execution_failed"


class FailureBoundary:
    """Notify the owner before SDK disconnect, which itself can hang."""
    def __init__(self, client, send, counts):
        self.client, self.send, self.counts = client, send, counts
        self.started = time.monotonic()

    def failed(self, error):
        if isinstance(error, asyncio.CancelledError):
            return
        code = getattr(error, "code", None) or native_failure_code(error)
        source = getattr(error, "source", "sdk_exception")
        self.send("error", code=code, diagnostic={
            "failure_code": code, "failure_source": source,
            "elapsed_ms": max(0, int((time.monotonic() - self.started) * 1000)),
            **self.counts,
        })

    async def __aenter__(self):
        try:
            return await self.client.__aenter__()
        except Exception as error:
            self.failed(error)
            raise

    async def __aexit__(self, kind, error, traceback):
        if error is not None:
            self.failed(error)
            self.send("event", event="agent_runtime_sdk_closing", data={"provider_id": "workbuddy"})
        result = await self.client.__aexit__(kind, error, traceback)
        if error is not None:
            self.send("event", event="agent_runtime_sdk_closed", data={"provider_id": "workbuddy"})
        return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_native_content(content):
    """Fingerprint native tool content, never expose its text or business values."""
    try:
        encoded = content.encode("utf-8") if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return {}
    return {"content_length": len(encoded), "content_sha256": hashlib.sha256(encoded).hexdigest()}


def read_frame():
    value = sys.stdin.buffer.readline(FRAME_LIMIT + 1)
    if not value or len(value) > FRAME_LIMIT:
        raise ValueError("workbuddy_message_limit")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("workbuddy_protocol_invalid")
    return result


def write_frame(value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    if len(data) > FRAME_LIMIT:
        raise ValueError("workbuddy_message_limit")
    _protocol_output.buffer.write(data)
    _protocol_output.buffer.flush()


async def execute(request, send):
    import codebuddy_agent_sdk as sdk
    from codebuddy_agent_sdk import CodeBuddyAgentOptions, CodeBuddySDKClient, ResultMessage, AssistantMessage, TextBlock
    from codebuddy_agent_sdk import PermissionResultAllow, PermissionResultDeny
    operation = request["operation"]
    payload = request["payload"]
    if operation == "authentication":
        # Existing-login check only. No credential or login URL crosses the
        # worker boundary. The locked CLI documents --print for stream-json;
        # explicitly suppress personal MCP and session persistence as well.
        from codebuddy_agent_sdk.transport import SubprocessTransport
        options = CodeBuddyAgentOptions(codebuddy_code_path=request["cli_path"],
            tools=[], setting_sources=[], persist_session=False,
            extra_args={"print": None, "strict-mcp-config": None})
        transport = SubprocessTransport(options=options)
        send("event", event="workbuddy_authentication_started", data={"stage": "initializing"})
        try:
            await transport.connect()
            send("event", event="workbuddy_authentication_started", data={"stage": "checking_existing_login"})
            await transport.write(json.dumps({"type": "control_request", "request_id": "sapba_auth_init",
                "request": {"subtype": "initialize", "hasPrompt": False}}))
            async for message in transport.read_messages():
                response = message.get("response") or {}
                if message.get("type") != "control_response" or response.get("request_id") != "sapba_auth_init":
                    continue
                if response.get("subtype") == "error":
                    raise ValueError("workbuddy_authentication_initialization_failed")
                account = (response.get("response") or {}).get("account") or {}
                authenticated = bool(account.get("userId") and account.get("token"))
                break
            else:
                raise ValueError("workbuddy_authentication_response_missing")
            return {"authenticated": authenticated, "status": "existing_login" if authenticated else "login_required",
                    **({} if authenticated else {"error": {"code": "workbuddy_existing_login_unavailable"}})}
        finally:
            await transport.close()
    if operation == "models":
        process = await asyncio.create_subprocess_exec(request["cli_path"], "--help",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0)
        data = await process.stdout.read(FRAME_LIMIT + 1)
        await process.wait()
        if process.returncode or len(data) > FRAME_LIMIT:
            raise ValueError("workbuddy_model_discovery_failed")
        text = data.decode("utf-8", errors="replace")
        match = re.search(r"--model\s+<model>[^\n]*Currently supported:\s*\(([^\n)]+)\)", text)
        ids = [] if not match else [item.strip() for item in match[1].split(",")]
        if not ids or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item) for item in ids):
            raise ValueError("workbuddy_model_discovery_failed")
        return {"models": list(dict.fromkeys(ids)), "model_catalog_complete": False,
                "source": "locked_cli_help", "availability_verified": False}
    from codebuddy_agent_sdk import ToolUseBlock, ToolResultBlock
    tool_names = set()
    servers = {}
    relay_lock = asyncio.Lock()
    if payload.get("tools"):
        from codebuddy_agent_sdk import create_sdk_mcp_server, tool
        def make_tool(definition):
            name = definition["name"]
            tool_names.add("mcp__sapba__" + name)
            async def handler(arguments):
                async with relay_lock:
                    call = uuid.uuid4().hex
                    send("tool_call", tool=name, arguments=arguments, call_id=call)
                    response = await asyncio.to_thread(read_frame)
                    if response.get("kind") != "tool_result" or response.get("call_id") != call:
                        raise ValueError("workbuddy_tool_response_invalid")
                    return {"content": [{"type": "text", "text": json.dumps(response["result"], ensure_ascii=False)}]}
            return tool(name, definition.get("description", ""), definition["inputSchema"])(handler)
        servers = {"sapba": create_sdk_mcp_server("sapba", tools=[make_tool(d) for d in payload["tools"]])}
    mode = request["mode"]
    if mode == "restricted":
        raise ValueError("workbuddy_windows_restricted_unverified")
    trusted = mode == "trusted_local"
    schema = payload.get("output_schema")
    diagnostic_schema = request.get("native_output_diagnostics") == NATIVE_DIAGNOSTICS and schema is not None
    async def inspect_output(value, phase):
        if not diagnostic_schema:
            return
        async with relay_lock:
            call = uuid.uuid4().hex
            send("schema_check", call_id=call, phase=phase, value=value)
            response = await asyncio.to_thread(read_frame)
            if (response.get("kind") != "schema_check_result" or response.get("call_id") != call
                    or response.get("task_id") != request.get("task_id")
                    or response.get("attempt_id") != request.get("attempt_id")
                    or response.get("schema_sha256") != fingerprint(schema)
                    or not isinstance(response.get("schema_valid"), bool)):
                raise ValueError("workbuddy_schema_check_response_invalid")
            # Schema diagnostics do not change arguments, tool permission,
            # native SDK capture or whether this job is qualified.
    async def admission(name, arguments, _context):
        if name in tool_names or trusted and name in NATIVE_TOOLS or schema is not None and name == SCHEMA_TOOL:
            return PermissionResultAllow(updated_input=arguments)
        return PermissionResultDeny(message="tool_not_enabled_for_task", interrupt=False)
    directory = payload.get("cwd")
    if not directory:
        raise ValueError("workbuddy_workspace_missing")
    # This is a platform-frozen output contract, not model-supplied context.
    # Keep other operations on their existing SDK options and output path.
    extra_args = {"print": None, "strict-mcp-config": None}
    if schema is not None:
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("workbuddy_output_schema_invalid")
        extra_args["json-schema"] = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    system_prompt = payload.get("system_prompt")
    if schema is not None:
        # The pinned CLI's formatter captures the terminal contract. It does not
        # authorize SAP, native tools or production writes. Preserve task policy.
        system_prompt = (system_prompt or "") + (
            "\nComplete the requested task and its necessary focused checks, then call "
            "StructuredOutput exactly once with the final object matching the supplied Schema. "
            "After successful capture, finish the turn; do not start unrelated investigation "
            "or repeat edits. If evidence or clarification is missing, report that within "
            "the task's allowed output contract instead of claiming success."
        )
    options = CodeBuddyAgentOptions(
        # The pinned CLI implements Schema capture using StructuredOutput.
        # tools=[] would hide that formatter, causing retries and no capture.
        tools=None if trusted else [SCHEMA_TOOL] if schema is not None else [],
        allowed_tools=sorted(tool_names | ({SCHEMA_TOOL} if schema is not None else set())),
        disallowed_tools=["WebSearch", "WebFetch", "Agent", "Skill"],
        model=request["snapshot"].get("model"),
        codebuddy_code_path=request["cli_path"],
        system_prompt=system_prompt,
        permission_mode="bypassPermissions" if trusted else "plan", setting_sources=[],
        mcp_servers=servers, cwd=directory, can_use_tool=admission,
        extra_args=extra_args,
        resume=None, persist_session=False,
        # Platform context is the only validated continuation contract.
        max_turns=payload.get("max_turns", 50), request_timeout_ms=int(payload.get("timeout_ms", 600000)),
        env={"CODEBUDDY_CODE_PATH": request["cli_path"]},
    )
    parts = []
    result = None
    actual_model = None
    reported_models = set()
    pending_tools = {}
    call_count = 0
    structured_call_count = structured_result_count = 0
    counts = {"message_count": 0, "assistant_message_count": 0, "stream_event_count": 0,
              "terminal_result_received": False}
    send("event", event="agent_runtime_turn_started", data={"provider_id": "workbuddy", "resumed": False, "tools_enabled": bool(servers) or trusted})
    async with FailureBoundary(CodeBuddySDKClient(options=options), send, counts) as client:
        await client.query(payload["prompt"])
        async for message in client.receive_messages():
            counts["message_count"] += 1
            if isinstance(message, getattr(sdk, "StreamEvent", ())):
                counts["stream_event_count"] += 1
            if isinstance(message, getattr(sdk, "ErrorMessage", ())):
                raise NativeTerminalFailure(native_failure_code(message.error), "error_message")
            # Only safe tool identity and progress cross the diagnostic boundary.
            # Arguments, shell commands, paths, tool output and SDK IDs never do.
            blocks = getattr(message, "content", None)
            for block in blocks if isinstance(blocks, list) else []:
                if isinstance(block, ToolUseBlock):
                    call_count += 1
                    name = block.name if (block.name in NATIVE_TOOLS or block.name in tool_names
                        or schema is not None and block.name == SCHEMA_TOOL) else "unrecognized"
                    pending_tools[block.id] = (call_count, name, time.monotonic())
                    send("event", event="agent_runtime_tool_started", data={
                        "provider_id": "workbuddy", "call_index": call_count, "tool": name,
                        "platform_tool": block.name in tool_names})
                    if schema is not None and block.name == SCHEMA_TOOL:
                        structured_call_count += 1
                        await inspect_output(block.input, "native_candidate")
                elif isinstance(block, ToolResultBlock):
                    pending = pending_tools.pop(block.tool_use_id, None)
                    data = {"provider_id": "workbuddy", "matched_call": pending is not None,
                            "is_error": block.is_error if isinstance(block.is_error, bool) else None}
                    if pending:
                        index, name, started = pending
                        data.update(call_index=index, tool=name,
                                    elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
                        if schema is not None and name == SCHEMA_TOOL:
                            structured_result_count += 1
                            data.update(safe_native_content(block.content))
                    send("event", event="agent_runtime_tool_finished", data=data)
            if isinstance(message, AssistantMessage):
                counts["assistant_message_count"] += 1
                # System/init metadata can echo the requested route. Only the
                # SDK's assistant message field identifies the generated reply.
                reported = getattr(message, "model", None)
                if isinstance(reported, str) and reported:
                    reported_models.add(reported)
                actual_model = next(iter(reported_models)) if len(reported_models) == 1 else None
                parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
                send("event", event="agent_runtime_response_received", data={"provider_id": "workbuddy", "message_type": "assistant"})
            if isinstance(message, ResultMessage):
                counts["terminal_result_received"] = True
                send("event", event="agent_runtime_result_received", data={
                    "provider_id": "workbuddy", "is_error": bool(message.is_error),
                    "pending_tool_count": len(pending_tools),
                    **({"schema_sha256": fingerprint(schema),
                        "structured_output_present": message.structured_output is not None,
                        "structured_call_count": structured_call_count,
                        "structured_result_count": structured_result_count,
                        "result_subtype": getattr(message, "subtype", None) if getattr(message, "subtype", None) in {
                            "success", "error_max_turns", "error_during_execution", "error_max_budget_usd",
                            "error_max_structured_output_retries"} else "unknown",
                        "stop_reason": getattr(message, "stop_reason", None) if getattr(message, "stop_reason", None) in {
                            "end_turn", "max_tokens", "stop_sequence", "tool_use", "cancelled"} else "unknown"}
                       if schema is not None else {})})
                if message.is_error:
                    raise NativeTerminalFailure(native_failure_code(getattr(message, "errors", None)), "error_result")
                if schema is not None:
                    await inspect_output(message.structured_output, "native_terminal")
                if schema is not None and message.structured_output is None:
                    # Never silently downgrade native constrained output to an
                    # unconstrained reply or an edited workspace file.
                    raise NativeTerminalFailure("workbuddy_structured_output_missing", "error_result")
                result = {"text": json.dumps(message.structured_output, ensure_ascii=False) if message.structured_output is not None
                          else message.result or "\n".join(parts),
                          "session_id": str(message.session_id or ""), "actual_model": actual_model,
                          "model_identity_source": "assistant_message" if actual_model else None}
                if schema is not None:
                    result["output_format"] = "native_json_schema"
                break
        if not result or not result["text"]:
            raise NativeTerminalFailure("workbuddy_result_missing", "message_stream_end")
        send("event", event="agent_runtime_sdk_closing", data={
            "provider_id": "workbuddy", "terminal_result_received": result is not None,
            "pending_tool_count": len(pending_tools)})
    send("event", event="agent_runtime_sdk_closed", data={"provider_id": "workbuddy"})
    if not result or not result["text"]:
        raise ValueError("workbuddy_result_missing")
    send("event", event="agent_runtime_turn_completed", data={"provider_id": "workbuddy", "session_id_present": bool(result["session_id"])})
    return result


def main():
    request = read_frame()
    binding = {key: request[key] for key in ("protocol", "task_id", "attempt_id", "operation", "environment_digest", "runtime_digest", "deadline")}
    if binding["protocol"] != PROTOCOL:
        raise ValueError("workbuddy_protocol_invalid")
    write_frame({"kind": "hello", **binding})
    def send(kind, **kwargs):
        nonlocal failure_sent
        if kind == "error":
            if failure_sent:
                return
            failure_sent = True
        write_frame({"kind": kind, "task_id": binding["task_id"], "attempt_id": binding["attempt_id"], **kwargs})
    failure_sent = False
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(execute(request, send))
        send("result", result=result)
    except Exception as exc:
        code = str(exc)
        if not code.startswith("workbuddy_") or not code.replace("_", "").isalnum():
            code = "workbuddy_execution_failed"
        send("error", code=code)


if __name__ == "__main__":
    main()
