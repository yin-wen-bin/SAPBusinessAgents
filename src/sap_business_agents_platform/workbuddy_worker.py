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
TERMINAL_CONTRACT = "sapba.runtime-terminal/1"  # Transport negotiation, not business rules.
NATIVE_CAPTURE_INSTRUCTIONS = (
    "\nComplete the requested task and its necessary focused checks, then obtain exactly one "
    "successful StructuredOutput capture of the final object matching the supplied Schema. "
    "A rejected or unsuccessful attempt is not a capture: correct the entire object without "
    "changing the Schema and resubmit only within the existing SDK retry limits and signed deadline. "
    "Do not restart the task, extend its budget or invent missing values. After successful capture, "
    "finish the turn; do not start unrelated investigation or repeat edits. A valid candidate alone "
    "is not a final native terminal. If evidence or clarification is missing, report that within "
    "the task's allowed output contract instead of claiming success."
)
_protocol_output = sys.stdout

FAILURE_CODES = frozenset({
    "workbuddy_execution_failed", "workbuddy_upstream_empty_stream", "workbuddy_upstream_error",
    "workbuddy_result_missing", "workbuddy_structured_output_missing", "workbuddy_structured_output_invalid",
    "workbuddy_schema_check_response_invalid", "workbuddy_tool_response_invalid",
    "workbuddy_terminal_correction_stalled", "workbuddy_terminal_protocol_invalid",
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


def declared_nullable_enum(definition):
    """Recognize the two supported nullable encodings for feedback only.

    This is not a Schema validator. Unknown constraints/combinators fail closed
    rather than telling the model to supply a value that could be disallowed.
    The platform still validates the untouched candidate with the full Schema.
    """
    if not isinstance(definition, dict) or not isinstance(definition.get("enum"), list):
        return False
    if not any(value is None for value in definition["enum"]):
        return False
    annotations = {"title", "description", "$comment", "default", "examples", "deprecated", "readOnly", "writeOnly"}
    if set(definition) - annotations - {"type", "enum", "anyOf"}:
        return False
    types = definition.get("type")
    if "type" in definition:
        if not isinstance(types, list) or not types or any(not isinstance(name, str) or name not in {"string", "null"} for name in types):
            return False
        if "null" not in types:
            return False
    branches = definition.get("anyOf")
    if "anyOf" in definition:
        if not isinstance(branches, list) or not branches:
            return False
        if any(not isinstance(branch, dict) or set(branch) != {"type"}
               or branch["type"] not in ("string", "null") for branch in branches):
            return False
        if not any(branch["type"] == "null" for branch in branches):
            return False
    return types is not None or branches is not None


def formatter_issues(response, schema):
    """Declared safe paths/constraints only; never supplied values or messages."""
    constraints = {"required", "additionalProperties", "type", "enum", "anyOf", "oneOf", "allOf",
                   "minimum", "maximum", "minItems", "maxItems", "pattern", "const", "minLength",
                   "maxLength", "uniqueItems", "format", "not", "multipleOf"}
    def declared(path):
        if not isinstance(path, str) or len(path) > 512 or not path.startswith("/"):
            return False
        if path == "/":
            return True
        node = schema
        for part in path[1:].split("/"):
            if not isinstance(node, dict):
                return False
            if isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/$defs/"):
                node = schema.get("$defs", {}).get(node["$ref"][8:], {})
            part = part.replace("~1", "/").replace("~0", "~")
            if part in node.get("properties", {}):
                node = node["properties"][part]
            elif part.isdecimal() and "items" in node:
                node = node["items"]
            else:
                return False
        return True
    issues = []
    supplied = response.get("validation_issues")
    for item in supplied[:50] if isinstance(supplied, list) else []:
        if (isinstance(item, dict) and item.get("code") == "runtime_report_schema_invalid"
                and isinstance(item.get("constraint"), str) and item["constraint"] in constraints
                and declared(item.get("path"))):
            safe = {key: item[key] for key in ("code", "path", "constraint")}
            if safe not in issues:
                issues.append(safe)
    return issues


def formatter_feedback(response, schema):
    """Reject without value repair; the SDK must supply its own valid terminal."""
    issues = formatter_issues(response, schema)
    message = "StructuredOutput was not captured. Correct the full object against the unchanged Schema. "
    message += json.dumps({"code": "workbuddy_structured_output_invalid", "validation_issues": issues},
                          ensure_ascii=True, separators=(",", ":"))
    properties = schema.get("properties", {})
    fields = ("input_kind", "input_field")
    if (any(item["path"] in {"/input_kind", "/input_field"} for item in issues)
            and all(name in schema.get("required", []) and declared_nullable_enum(properties.get(name))
                    for name in fields)):
        message += (" These are secure-reference clarification fields, not SAP identifiers or columns. "
                    'When that clarification is inapplicable, include "input_kind": null and "input_field": null '
                    "in the complete object. Use literal JSON null, never a string or an omitted field. "
                    "For applicable clarification use only the existing declared enum values; do not invent values.")
    return message


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
    # No defaults here: the SDK-free platform signed/froze official routing.
    config = request.get("service_site_config") or {}
    if (config.get("service_site") not in {"china", "international"}
            or any(config.get(k) != request["snapshot"].get(k) for k in (
                "service_site", "service_site_revision", "service_site_digest"))
            or (config["service_site"], config.get("sdk_environment")) not in {
                ("china", "internal"), ("international", "external")}
            or config.get("cli_env") != ({"CODEBUDDY_INTERNET_ENVIRONMENT": "internal"}
                if config.get("sdk_environment") == "internal" else {})):
        raise ValueError("workbuddy_service_site_invalid")
    sdk_env = {"CODEBUDDY_CODE_PATH": request["cli_path"], **config["cli_env"]}
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
            tools=[], setting_sources=[], persist_session=False, env=sdk_env,
            extra_args={"print": None, "strict-mcp-config": None})
        transport = SubprocessTransport(options=options)
        send("event", event="workbuddy_authentication_started", data={"stage": "initializing"})
        try:
            await transport.connect()
            send("event", event="workbuddy_authentication_started", data={"stage": "checking_existing_login"})
            await transport.write(json.dumps({"type": "control_request", "request_id": "sapba_auth_init",
                "request": {"subtype": "initialize", "hasPrompt": False,
                            "environment": config["sdk_environment"], "endpoint": None}}))
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
                    value = {key: item for key, item in response["result"].items() if not key.startswith("_")}
                    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                        "isError": value.get("ok") is False}
            return tool(name, definition.get("description", ""), definition["inputSchema"])(handler)
        servers = {"sapba": create_sdk_mcp_server("sapba", tools=[make_tool(d) for d in payload["tools"]])}
    mode = request["mode"]
    if mode == "restricted":
        raise ValueError("workbuddy_windows_restricted_unverified")
    trusted = mode == "trusted_local"
    schema = payload.get("output_schema")
    diagnostic_schema = request.get("native_output_diagnostics") == NATIVE_DIAGNOSTICS and schema is not None
    terminal_contract = request.get("terminal_contract")
    if terminal_contract is not None and (terminal_contract != TERMINAL_CONTRACT or not diagnostic_schema):
        raise NativeTerminalFailure("workbuddy_terminal_protocol_invalid", "formatter_hook")
    expected_feedback = {}
    hook_sequence = 0
    worker_wait = None
    def progress(kind, **data):
        send("event", event=kind, data={"provider_id": "workbuddy",
            "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)), **data})
    def set_wait(state):
        nonlocal worker_wait
        if worker_wait != state:
            worker_wait = state
            progress("workbuddy_native_wait", worker_wait_state=state, wait_state_source="worker_observation")
    async def inspect_output(value, phase, *, origin="native_terminal", submission_id=None):
        if not diagnostic_schema:
            return None
        async with relay_lock:
            call = uuid.uuid4().hex
            set_wait("waiting_platform_schema_check")
            metadata = {"origin": origin, "submission_id": submission_id} if terminal_contract else {}
            send("schema_check", call_id=call, phase=phase, value=value, **metadata)
            response = await asyncio.to_thread(read_frame)
            if (response.get("kind") != "schema_check_result" or response.get("call_id") != call
                    or response.get("task_id") != request.get("task_id")
                    or response.get("attempt_id") != request.get("attempt_id")
                    or response.get("schema_sha256") != fingerprint(schema)
                    or not isinstance(response.get("schema_valid"), bool)):
                raise ValueError("workbuddy_schema_check_response_invalid")
            # The same frozen platform checker supplies diagnostics/feedback.
            # Never repair arguments, substitute capture or grant qualification.
            if terminal_contract:
                feedback = response.get("repair_feedback")
                if (not isinstance(feedback, dict) or feedback.get("version") != terminal_contract
                        or feedback.get("schema_sha256") != fingerprint(schema)
                        or feedback.get("terminal_validated") is not response["schema_valid"]
                        or not isinstance(response.get("stop"), bool)):
                    raise NativeTerminalFailure("workbuddy_terminal_protocol_invalid", "formatter_hook")
                if response["stop"]:
                    # Notify before SDK hook exception suppression or shutdown can hang.
                    failure = NativeTerminalFailure("workbuddy_terminal_correction_stalled", "formatter_hook")
                    send("error", code=failure.code, diagnostic={"failure_code": failure.code,
                        "failure_source": failure.source, "elapsed_ms": max(0, int((time.monotonic()-run_started)*1000)), **counts})
                    raise failure
            return response
    async def formatter_guard(hook_input, _tool_use_id, _context):
        # SDK hooks run before the formatter, including its needsApproval=false
        # path. A permission callback alone cannot guarantee this feedback.
        if not isinstance(hook_input, dict) or hook_input.get("tool_name") != SCHEMA_TOOL:
            return {}
        nonlocal hook_sequence
        hook_sequence += 1
        sequence = hook_sequence
        progress("workbuddy_formatter_progress", formatter_stage="hook_entered", hook_index=sequence)
        try:
            response = await inspect_output(hook_input.get("tool_input"), "native_candidate",
                origin="formatter_hook", submission_id=fingerprint(_tool_use_id) if isinstance(_tool_use_id, str) and _tool_use_id else None)
            progress("workbuddy_formatter_progress", formatter_stage="check_completed", hook_index=sequence,
                     schema_valid=response["schema_valid"])
            if response["schema_valid"]:
                set_wait("waiting_sdk_message")
                progress("workbuddy_formatter_progress", formatter_stage="hook_returned", hook_index=sequence,
                         rejection_prepared=False)
                return {}  # No extra allow; a valid candidate is not a capture.
            reason = ("StructuredOutput was not captured. " + json.dumps(response["repair_feedback"], ensure_ascii=True, separators=(",", ":"))
                if terminal_contract else formatter_feedback(response, schema))
            expected = safe_native_content([{"type": "text", "text": "Error: " + reason}])
            # Bound diagnostic correlation; never retain tool arguments or IDs in events.
            if isinstance(_tool_use_id, str) and 0 < len(_tool_use_id) <= 256:
                expected_feedback[_tool_use_id] = expected
                while len(expected_feedback) > 64:
                    expected_feedback.pop(next(iter(expected_feedback)))
            progress("workbuddy_formatter_progress", formatter_stage="rejection_prepared", hook_index=sequence,
                     rejection_prepared=True, expected_feedback_sha256=expected.get("content_sha256"))
            set_wait("waiting_sdk_message")
            progress("workbuddy_formatter_progress", formatter_stage="hook_returned", hook_index=sequence,
                     rejection_prepared=True)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": reason}}
        except Exception as error:
            code = native_failure_code(error)
            progress("workbuddy_formatter_progress", formatter_stage="hook_failed", hook_index=sequence)
            # The pinned SDK can consume callback exceptions. Notify the owner
            # before SDK shutdown or a native wait hides the actual failure.
            send("error", code=code, diagnostic={"failure_code": code, "failure_source": "formatter_hook",
                "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)), **counts})
            raise NativeTerminalFailure(code, "formatter_hook") from None
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
        system_prompt = (system_prompt or "") + NATIVE_CAPTURE_INSTRUCTIONS
    hook_options = {}
    if diagnostic_schema:
        from codebuddy_agent_sdk import HookMatcher
        hook_options["hooks"] = {"PreToolUse": [HookMatcher(matcher="^StructuredOutput$", hooks=[formatter_guard])]}
    requested_timeout_ms = int(payload.get("timeout_ms", 600000))
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
        max_turns=payload.get("max_turns", 50), request_timeout_ms=requested_timeout_ms,
        env=sdk_env,
        **hook_options,
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
    run_started = time.monotonic()
    last_activity = run_started
    first_response_ms = None
    first_assistant_ms = None
    def activity(*, force=False):
        nonlocal last_activity
        now = time.monotonic()
        if force or now - last_activity >= 1:
            send("event", event="agent_runtime_activity", data={"provider_id": "workbuddy", **counts,
                "elapsed_ms": max(0, int((now - run_started) * 1000)),
                **({"first_response_ms": first_response_ms} if first_response_ms is not None else {}),
                **({"first_assistant_ms": first_assistant_ms} if first_assistant_ms is not None else {})})
            last_activity = now
    send("event", event="agent_runtime_turn_started", data={"provider_id": "workbuddy", "resumed": False, "tools_enabled": bool(servers) or trusted})
    progress("workbuddy_native_budget", requested_timeout_ms=requested_timeout_ms,
             budget_strategy="supervisor_deadline", native_timeout_propagation="unverified")
    set_wait("sdk_connecting")
    async with FailureBoundary(CodeBuddySDKClient(options=options), send, counts) as client:
        send("event", event="agent_runtime_connected", data={"provider_id": "workbuddy", "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)), **counts})
        set_wait("query_submitting")
        await client.query(payload["prompt"])
        send("event", event="agent_runtime_query_submitted", data={"provider_id": "workbuddy", "elapsed_ms": max(0, int((time.monotonic() - run_started) * 1000)), **counts})
        set_wait("waiting_sdk_message")
        async for message in client.receive_messages():
            counts["message_count"] += 1
            if first_response_ms is None:
                first_response_ms = max(0, int((time.monotonic() - run_started) * 1000))
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
                        await inspect_output(block.input, "native_candidate", origin="message_observation",
                            submission_id=fingerprint(block.id))
                        set_wait("waiting_sdk_message")
                elif isinstance(block, ToolResultBlock):
                    pending = pending_tools.pop(block.tool_use_id, None)
                    data = {"provider_id": "workbuddy", "matched_call": pending is not None,
                            "is_error": block.is_error if isinstance(block.is_error, bool) else None}
                    if pending:
                        index, name, tool_started = pending
                        data.update(call_index=index, tool=name,
                                    elapsed_ms=max(0, int((time.monotonic() - tool_started) * 1000)))
                        if schema is not None and name == SCHEMA_TOOL:
                            structured_result_count += 1
                            data.update(safe_native_content(block.content))
                            expected = expected_feedback.pop(block.tool_use_id, None)
                            match = "unavailable" if expected is None else "matched" if (
                                expected.get("content_sha256") == data.get("content_sha256")
                                and expected.get("content_length") == data.get("content_length")) else "different"
                            progress("workbuddy_formatter_progress", formatter_stage="tool_result_observed",
                                call_index=index, feedback_match=match,
                                **({"expected_feedback_sha256": expected["content_sha256"]} if expected else {}))
                    send("event", event="agent_runtime_tool_finished", data=data)
            if isinstance(message, AssistantMessage):
                counts["assistant_message_count"] += 1
                if first_assistant_ms is None:
                    first_assistant_ms = max(0, int((time.monotonic() - run_started) * 1000))
                # System/init metadata can echo the requested route. Only the
                # SDK's assistant message field identifies the generated reply.
                reported = getattr(message, "model", None)
                if isinstance(reported, str) and reported:
                    reported_models.add(reported)
                actual_model = next(iter(reported_models)) if len(reported_models) == 1 else None
                parts.extend(block.text for block in message.content if isinstance(block, TextBlock))
            if isinstance(message, ResultMessage):
                counts["terminal_result_received"] = True
                set_wait("native_terminal_received")
                activity(force=True)
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
                    checked = await inspect_output(message.structured_output, "native_terminal")
                    progress("workbuddy_formatter_progress", formatter_stage="terminal_checked",
                             **({"schema_valid": checked["schema_valid"]} if checked is not None else {}))
                    set_wait("native_terminal_received")
                    if (message.structured_output is not None and checked is not None
                            and not checked["schema_valid"]):
                        raise NativeTerminalFailure("workbuddy_structured_output_invalid", "error_result")
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
            activity()
        if not counts["terminal_result_received"]:
            activity(force=True)
        if not result or not result["text"]:
            raise NativeTerminalFailure("workbuddy_result_missing", "message_stream_end")
        send("event", event="agent_runtime_sdk_closing", data={
            "provider_id": "workbuddy", "terminal_result_received": result is not None,
            "pending_tool_count": len(pending_tools)})
        set_wait("sdk_closing")
    send("event", event="agent_runtime_sdk_closed", data={"provider_id": "workbuddy"})
    set_wait("sdk_closed")
    if not result or not result["text"]:
        raise ValueError("workbuddy_result_missing")
    send("event", event="agent_runtime_turn_completed", data={"provider_id": "workbuddy", "session_id_present": bool(result["session_id"])})
    return result


def main():
    request = read_frame()
    binding = {key: request[key] for key in ("protocol", "task_id", "attempt_id", "operation", "environment_digest", "runtime_digest", "deadline")}
    if binding["protocol"] != PROTOCOL:
        raise ValueError("workbuddy_protocol_invalid")
    write_frame({"kind": "hello", **binding,
        **({"terminal_contract": TERMINAL_CONTRACT} if request.get("terminal_contract") == TERMINAL_CONTRACT else {})})
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
