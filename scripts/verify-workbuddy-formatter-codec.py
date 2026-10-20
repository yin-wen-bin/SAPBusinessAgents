"""Offline codec check inside an existing, fully verified immutable SDK release.

No SDK client/transport is started, no model/authentication/SAP call is made,
no release/state is changed and no operation qualification is recorded.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def finish(coroutine):
    # The SDK codec callback has no I/O/awaits. Never start an event loop/client.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("offline_codec_unexpected_io")


def main():
    root = Path(__file__).resolve().parents[1]
    release = Path(sys.executable).resolve().parents[1]
    releases = (root / ".local-data/runtimes/workbuddy/releases").resolve()
    assert release.parent == releases, "workbuddy_independent_interpreter_required"
    manifest = json.loads((release / "environment.json").read_text(encoding="utf-8"))
    bound = {key: value for key, value in manifest.items() if key != "environment_digest"}
    signature = hashlib.sha256(json.dumps(bound, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode()).hexdigest()
    assert signature == manifest["environment_digest"] == release.name, "workbuddy_environment_digest_mismatch"
    expected = set(manifest["files"])
    actual = {p.relative_to(release).as_posix() for p in release.rglob("*") if p.is_file()}
    assert actual == expected | {"environment.json"}, "workbuddy_environment_inventory_changed"
    for name, expected_hash in manifest["files"].items():
        path = (release / name).resolve()
        assert path.is_relative_to(release) and sha(path) == expected_hash, "workbuddy_installation_digest_mismatch"
    assert importlib.metadata.version("codebuddy-agent-sdk") == "0.3.247"
    assert manifest["cli_version"] == "2.141.0"
    from codebuddy_agent_sdk import CodeBuddyAgentOptions, HookMatcher
    from codebuddy_agent_sdk._protocol import build_hooks_config, build_control_response
    from codebuddy_agent_sdk._internal.query import Query
    from codebuddy_agent_sdk._message_parser import parse_message
    # The production worker itself remains SDK-free until execute() is called.
    spec = importlib.util.spec_from_file_location("offline_worker_codec", release / manifest["worker"])
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    schema = {"type": "object", "additionalProperties": False, "required": ["input_kind", "input_field"],
              "properties": {"input_kind": {"type": ["string", "null"], "enum": ["secure_business_reference", None]},
                             "input_field": {"type": ["string", "null"], "enum": ["receipt_reference", None]}}}
    issues = [{"code": "runtime_report_schema_invalid", "path": "/" + name, "constraint": "enum"}
              for name in ("input_kind", "input_field")]
    async def callback(value, _tool_use_id, _context):
        assert value["tool_name"] == "StructuredOutput"
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": worker.formatter_feedback({"validation_issues": issues}, schema)}}
    options = CodeBuddyAgentOptions(hooks={"PreToolUse": [HookMatcher(matcher="^StructuredOutput$", hooks=[callback])]},
        extra_args={"json-schema": json.dumps(schema)}, tools=["StructuredOutput"], allowed_tools=["StructuredOutput"],
        setting_sources=[], persist_session=False)
    config, callbacks = build_hooks_config(options.hooks)
    assert config["PreToolUse"][0]["matcher"] == "^StructuredOutput$"
    query = Query.__new__(Query)
    query._hook_callbacks = callbacks
    hook_id = config["PreToolUse"][0]["hookCallbackIds"][0]
    rejected = {"input_kind": "private-invalid", "input_field": "private-invalid"}
    response = finish(query._execute_hook(hook_id, {"tool_name": "StructuredOutput", "tool_input": rejected}, "local-call"))
    wire = json.loads(json.dumps(build_control_response("local-request", response)))
    verdict = wire["response"]["response"]["hookSpecificOutput"]
    assert verdict["permissionDecision"] == "deny" and "literal JSON null" in verdict["permissionDecisionReason"]
    assert "private-invalid" not in json.dumps(wire)
    for value in ({"input_kind": None, "input_field": None}, rejected):
        message = parse_message({"type": "assistant", "message": {"model": "hy4-preview", "content": [
            {"type": "tool_use", "id": "local-call", "name": "StructuredOutput", "input": value}]}})
        assert message.content[0].input == value
        terminal = parse_message({"type": "result", "subtype": "success", "structured_output": value})
        assert terminal.structured_output == value
    assert json.loads(options.extra_args["json-schema"]) == schema
    assert None in schema["properties"]["input_kind"]["enum"] and None in schema["properties"]["input_field"]["enum"]
    equivalent = json.loads(json.dumps(schema))
    for definition in equivalent["properties"].values():
        alternatives = definition.pop("type")
        definition["anyOf"] = [{"type": name} for name in alternatives]
    reasons = []
    for encoded_schema in (schema, equivalent):
        async def encoding_callback(value, _tool_use_id, _context, bound_schema=encoded_schema):
            assert value["tool_name"] == "StructuredOutput"
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": worker.formatter_feedback({"validation_issues": issues}, bound_schema)}}
        configuration, hooks = build_hooks_config({"PreToolUse": [HookMatcher(matcher="^StructuredOutput$", hooks=[encoding_callback])]})
        encoding_query = Query.__new__(Query)
        encoding_query._hook_callbacks = hooks
        hook_id = configuration["PreToolUse"][0]["hookCallbackIds"][0]
        encoded_response = finish(encoding_query._execute_hook(hook_id,
            {"tool_name": "StructuredOutput", "tool_input": rejected}, "encoding-call"))
        encoded_wire = json.loads(json.dumps(build_control_response("encoding-request", encoded_response)))
        encoded_verdict = encoded_wire["response"]["response"]["hookSpecificOutput"]
        assert encoded_verdict["permissionDecision"] == "deny"
        reasons.append(encoded_verdict["permissionDecisionReason"])
        assert "private-invalid" not in json.dumps(encoded_wire)
    assert reasons[0] == reasons[1] and "literal JSON null" in reasons[1]
    # Read the exact frozen full-schema probe input, not an abbreviated example.
    # Instantiate transport options/argv only; never connect or start a client.
    from codebuddy_agent_sdk.transport import SubprocessTransport
    full_path = root / ".local-data/workbuddy-schema-projection-offline-20261012/input.json"
    if full_path.exists():
        fixture = json.loads(full_path.read_text(encoding="utf-8"))
        full = fixture["schema"]
        argv_options = CodeBuddyAgentOptions(codebuddy_code_path=str(release / manifest["cli"]),
            extra_args={"json-schema": json.dumps(full, ensure_ascii=False), "print": None, "strict-mcp-config": None},
            system_prompt=fixture["instructions"], tools=["StructuredOutput"], allowed_tools=["StructuredOutput"],
            setting_sources=[], persist_session=False, model="hy4-preview")
        transport = SubprocessTransport(options=argv_options)
        args = transport._build_args()
        forwarded = json.loads(args[args.index("--json-schema") + 1])
        assert forwarded == full and args[args.index("--system-prompt") + 1] == fixture["instructions"]
        assert set(forwarded["required"]) == set(forwarded["properties"]) and len(forwarded["properties"]) == 12
        parsed = parse_message({"type": "assistant", "message": {"model": "hy4-preview", "content": [
            {"type": "tool_use", "id": "local-call", "name": "StructuredOutput", "input": fixture["example"]}]}})
        assert parsed.content[0].input == fixture["example"]
        assert {name: data["enum"] for name, data in forwarded["properties"].items() if "enum" in data} == {
            name: data["enum"] for name, data in full["properties"].items() if "enum" in data}
        contrast = fixture.get("contrast_schema")
        if contrast:
            argv_options.extra_args["json-schema"] = json.dumps(contrast, ensure_ascii=False)
            contrast_args = SubprocessTransport(options=argv_options)._build_args()
            contrast_forwarded = json.loads(contrast_args[contrast_args.index("--json-schema") + 1])
            assert contrast_forwarded == contrast
            assert contrast_forwarded["required"] == full["required"]
            assert set(contrast_forwarded["properties"]) == set(full["properties"])
            assert contrast_forwarded["additionalProperties"] == full["additionalProperties"]
    # Repeat full inventory/content checks: no import cache may alter the release.
    assert {p.relative_to(release).as_posix() for p in release.rglob("*") if p.is_file()} == actual
    assert all(sha(release / name) == value for name, value in manifest["files"].items())
    print(json.dumps({"phase": "passed", "sdk": "0.3.247", "cli": "2.141.0", "environment_digest": release.name,
        "hook_config_codec": "passed", "deny_feedback_codec": "passed", "literal_null_roundtrip": "passed",
        "equivalent_nullable_feedback_codec": "passed", "installed_worker_checked": True,
        "invalid_strings_not_repaired": True, "installation_inventory_unchanged": True,
        "full_schema_transport_argv_checked": full_path.exists(),
        "equivalent_schema_transport_argv_checked": full_path.exists() and bool(fixture.get("contrast_schema")),
        "model_calls": 0, "authentication_calls": 0, "sap_calls": 0, "operation_qualification_granted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
