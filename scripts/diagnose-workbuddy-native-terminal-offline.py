"""Pinned-release offline evidence only. Never starts SDK clients or the CLI.

Checks real SDK argument/control codecs, reconstructs safe saved feedback hashes,
and replays the worker timer with an in-memory fake SDK. No production mutation,
value repair, qualification, account check, network, SAP or model dispatch.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / ".local-data/workbuddy-validation/china-20261012-equivalent-feedback-release"
DESTINATION = ROOT / ".local-data/workbuddy-native-terminal-offline-20261012"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def controls():
    freeze = json.loads((BATCH / "freeze.json").read_text(encoding="utf-8"))
    paths = [ROOT / name for name in freeze["controls"]["files"]]
    paths += [ROOT / ".local-data/runtimes/workbuddy/state.json", ROOT / ".local-data/sdk-runtimes/authentication.json"]
    paths += [BATCH / name for name in ("before.json", "freeze.json", "request.json", "terminal-report.json", "safe-events.jsonl", "result-summary.json")]
    return {str(path.relative_to(ROOT)): sha(path) for path in paths}


def verify_release(release, manifest):
    assert signature({k: v for k, v in manifest.items() if k != "environment_digest"}) == manifest["environment_digest"] == release.name
    assert {p.relative_to(release).as_posix() for p in release.rglob("*") if p.is_file()} == set(manifest["files"]) | {"environment.json"}
    for name, expected in manifest["files"].items():
        path = (release / name).resolve()
        assert path.is_relative_to(release) and sha(path) == expected


def feedback_checks(worker, request, events):
    candidates = [x for x in events if x["event"] == "workbuddy_native_output_checked"]
    results = [x for x in events if x["event"] == "agent_runtime_tool_finished"]
    assert len(candidates) == 4 and len(results) == 2
    matches = []
    for check, result in zip(candidates[::2], results):
        feedback = worker.formatter_feedback({"validation_issues": check["validation_issues"]}, request["wire_schema"])
        # Public CLI returns a denied native tool as this text-content list.
        # Reconstruct only our own safe feedback, never rejected candidate values.
        expected = worker.safe_native_content([{"type": "text", "text": "Error: " + feedback}])
        matched = (expected["content_length"] == result["content_length"]
            and expected["content_sha256"] == result["content_sha256"])
        assert matched and "Use literal JSON null" in feedback
        matches.append({"call_index": result["call_index"], "length": expected["content_length"],
            "sha256": expected["content_sha256"], "saved_tool_result_exact_match": matched,
            "literal_null_guidance_present": True, "model_consumption_proven": False})
    return matches


def sdk_checks(release, manifest, worker, request):
    from codebuddy_agent_sdk import CodeBuddyAgentOptions, HookMatcher
    from codebuddy_agent_sdk.transport import SubprocessTransport
    from codebuddy_agent_sdk._internal.query import Query
    from codebuddy_agent_sdk._protocol import build_hooks_config
    from codebuddy_agent_sdk._message_parser import parse_message
    options = CodeBuddyAgentOptions(codebuddy_code_path=str(release / manifest["cli"]), model="hy4-preview",
        extra_args={"json-schema": json.dumps(request["wire_schema"], ensure_ascii=False), "print": None, "strict-mcp-config": None},
        tools=["StructuredOutput"], allowed_tools=["StructuredOutput"], setting_sources=[], persist_session=False,
        system_prompt=request["system_prompt"], request_timeout_ms=90000, max_turns=50)
    transport = SubprocessTransport(options=options)  # Options/argv only: no connect.
    args = transport._build_args()
    assert json.loads(args[args.index("--json-schema") + 1]) == request["wire_schema"]
    assert args[args.index("--system-prompt") + 1] == request["system_prompt"]
    assert transport.options.model == "hy4-preview" and transport.options.tools == ["StructuredOutput"]
    assert options.request_timeout_ms == 90000 and transport.options.request_timeout_ms is None
    assert "--request-timeout-ms" not in args
    for raw in ({"input_kind": None, "input_field": None}, {"input_kind": "synthetic-invalid", "input_field": "synthetic-invalid"}):
        parsed = parse_message({"type": "assistant", "message": {"model": "hy4-preview", "content": [
            {"type": "tool_use", "id": "synthetic-call", "name": "StructuredOutput", "input": raw}]}})
        assert parsed.content[0].input == raw
    written = []
    async def callback(value, _id, _context):
        assert value["tool_name"] == "StructuredOutput"
        issues = [{"code": "runtime_report_schema_invalid", "path": "/" + name, "constraint": "enum"}
                  for name in ("input_kind", "input_field")]
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": worker.formatter_feedback({"validation_issues": issues}, request["wire_schema"])}}
    config, callbacks = build_hooks_config({"PreToolUse": [HookMatcher(matcher="^StructuredOutput$", hooks=[callback])]})
    query = Query.__new__(Query)
    query._hook_callbacks = callbacks
    async def write(message): written.append(json.loads(message))
    query.transport = SimpleNamespace(write=write, is_closed=False)
    asyncio.run(query._handle_control_request({"type": "control_request", "request_id": "offline-hook",
        "request": {"subtype": "hook_callback", "callback_id": config["PreToolUse"][0]["hookCallbackIds"][0],
            "input": {"tool_name": "StructuredOutput", "tool_input": {"input_kind": "synthetic-invalid", "input_field": "synthetic-invalid"}},
            "tool_use_id": "synthetic-call"}}))
    response = written[0]["response"]["response"]["hookSpecificOutput"]
    assert response["permissionDecision"] == "deny" and "literal JSON null" in response["permissionDecisionReason"]
    assert "synthetic-invalid" not in json.dumps(written)
    return {"full_schema_forwarded": True, "prompt_forwarded": True, "tool_and_result_parser_null_preserved": True,
        "invalid_strings_not_repaired": True, "real_sdk_hook_dispatch_and_control_write": "passed",
        "requested_timeout_ms": options.request_timeout_ms, "transport_timeout_ms": transport.options.request_timeout_ms,
        "cli_timeout_flag_forwarded": False, "timeout_loss_proves_enum_root_cause": False}


def timer_replay(worker, request):
    clock = SimpleNamespace(now=100.0)
    emitted, frames = [], []
    value = {"status": "inconclusive", "intent": "offline formatter protocol probe", "clarification_question": "",
        "input_kind": None, "input_field": None, "summary": {"zh": "仅核对格式，不查询SAP。", "en": "Formatting only; no SAP query."},
        "source_complete": False, "business_complete": False, "missing_evidence": ["offline_formatter_probe"],
        "evidence_refs": [], "executed_plans": [], "presentation": None}
    class ToolUse:
        name, id, input = "StructuredOutput", "synthetic-call", value
    class ToolResult:
        tool_use_id, is_error, content = "synthetic-call", False, "synthetic local result"
    class Assistant:
        model = "hy4-preview"
        def __init__(self, content): self.content = content
    class User:
        content = [ToolResult()]
    class Result:
        is_error, session_id, result, structured_output = False, "synthetic-session", "", value
    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def query(self, _): pass
        async def receive_messages(self):
            clock.now = 105; yield Assistant([ToolUse()])
            clock.now = 125; yield User()
            clock.now = 140; yield Assistant([])
            clock.now = 145; yield Result()
    fake_sdk = SimpleNamespace(CodeBuddyAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs), CodeBuddySDKClient=Client,
        AssistantMessage=Assistant, ResultMessage=Result, TextBlock=type("Text", (), {}), ToolUseBlock=ToolUse,
        ToolResultBlock=ToolResult, PermissionResultAllow=SimpleNamespace, PermissionResultDeny=SimpleNamespace,
        HookMatcher=SimpleNamespace)
    real_sdk = sys.modules.get("codebuddy_agent_sdk")
    original_time, original_read = worker.time, worker.read_frame
    worker.time = SimpleNamespace(monotonic=lambda: clock.now)
    worker.read_frame = lambda: frames.pop(0)
    def send(kind, **data):
        if kind == "schema_check":
            frames.append({"kind": "schema_check_result", "call_id": data["call_id"], "task_id": "offline-timer",
                "attempt_id": "offline-attempt", "schema_sha256": worker.fingerprint(request["wire_schema"]),
                "schema_valid": True, "validation_issues": []})
        if kind == "event" and data.get("event") == "agent_runtime_activity":
            emitted.append({"observed_elapsed_ms": data["data"]["elapsed_ms"], "expected_run_elapsed_ms": int((clock.now - 100) * 1000)})
    site = {"service_site": "china", "service_site_revision": 1,
        "service_site_digest": "35ab077b99cc8ffcdf2516eecc24280987b5e8e3767d02a2f82c21bbcc803729"}
    try:
        sys.modules["codebuddy_agent_sdk"] = fake_sdk
        asyncio.run(worker.execute({"operation": "model_check", "mode": "bounded", "task_id": "offline-timer",
            "attempt_id": "offline-attempt", "cli_path": "not-executed", "snapshot": {"model": "hy4-preview", **site},
            "service_site_config": {**site, "sdk_environment": "internal", "cli_env": {"CODEBUDDY_INTERNET_ENVIRONMENT": "internal"}},
            "native_output_diagnostics": worker.NATIVE_DIAGNOSTICS,
            "payload": {"cwd": str(DESTINATION), "prompt": "synthetic clock replay", "output_schema": request["wire_schema"]}}, send))
    finally:
        worker.time, worker.read_frame = original_time, original_read
        if real_sdk is None: sys.modules.pop("codebuddy_agent_sdk", None)
        else: sys.modules["codebuddy_agent_sdk"] = real_sdk
    assert emitted[-1] == {"observed_elapsed_ms": 40000, "expected_run_elapsed_ms": 45000}
    return {"reproduced": True, "samples": emitted, "cause": "ToolResult tuple assignment overwrites the run-start variable",
        "changes_supervisor_deadline": False, "synthetic_fake_sdk_only": True}


def main():
    assert not DESTINATION.exists(), "offline_fresh_directory_required"
    release = Path(sys.executable).resolve().parents[1]
    assert release.parent == (ROOT / ".local-data/runtimes/workbuddy/releases").resolve()
    manifest = json.loads((release / "environment.json").read_text(encoding="utf-8"))
    assert release.name == "2223d96a09fb68ed853496c1c0daead9f64eae437c92f85042706676b8045cf4"
    verify_release(release, manifest)
    before = controls()
    assert importlib.metadata.version("codebuddy-agent-sdk") == "0.3.247" and manifest["cli_version"] == "2.141.0"
    request = json.loads((BATCH / "request.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (BATCH / "safe-events.jsonl").read_text(encoding="utf-8").splitlines()]
    spec = importlib.util.spec_from_file_location("offline_pinned_worker", release / manifest["worker"])
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    cli = (release / manifest["cli"]).read_bytes()
    anchors = {
        "formatter_dynamic_plain_schema": b'let rM=class extends X$.BaseTool',
        "hook_denial_reason_mapping": b'convertSdkOutputToResult(v$){',
        "argument_rewrite_null_passthrough": b'a0=class{process(LH){',
        "structured_output_completion_wait": b'cD=class{constructor(){this.event=HH.HookEvent.STOP',
        "chat_completions_tool_schema_passthrough": b'function Du(U$){return{type:"function",function:{name:U$.toolName',
    }
    offsets = {name: cli.find(anchor) for name, anchor in anchors.items()}
    assert all(value >= 0 for value in offsets.values())
    report = {"phase": "offline_diagnosis_completed_live_root_cause_not_fully_known", "sdk": "0.3.247", "cli": "2.141.0",
        "environment_digest": release.name, "model_calls": 0, "authentication_calls": 0, "sap_calls": 0,
        "feedback_hash_checks": feedback_checks(worker, request, events),
        "sdk_parameter_and_hook_checks": sdk_checks(release, manifest, worker, request),
        "worker_timer_replay": timer_replay(worker, request),
        "public_cli_static_boundaries": {"sha256": sha(release / manifest["cli"]), "anchor_byte_offsets": offsets,
            "exact_request_timeout_ms_flag_literal_present": b'--request-timeout-ms' in cli,
            "not_live_provider_request_introspection": True},
        "unknowns": ["actual invalid string contents were not retained", "remote tool-schema projection and model generation",
            "actual model consumption of the confirmed tool feedback", "native waiting phase after the last rejection"],
        "canonical_contract_changed": False, "production_source_changed": False, "release_or_qualification_changed": False}
    assert controls() == before
    verify_release(release, manifest)
    report["controls_and_complete_installation_unchanged"] = True
    DESTINATION.mkdir()
    (DESTINATION / "result-summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
