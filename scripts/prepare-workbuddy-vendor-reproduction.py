"""Package only public/synthetic terminal evidence. No SDK/client/network calls."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import importlib.util

from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, atomic_json, digest, file_hash

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / ".local-data/workbuddy-validation/china-20261012-equivalent-feedback-release"
DESTINATION = ROOT / ".local-data/workbuddy-terminal-followup-offline-20261012/vendor-reproduction"


def main():
    assert not DESTINATION.exists(), "vendor_new_directory_required"
    release = WorkBuddyEnvironment(ROOT).release()
    assert release["environment_digest"] == "2223d96a09fb68ed853496c1c0daead9f64eae437c92f85042706676b8045cf4"
    request = json.loads((BATCH / "request.json").read_text(encoding="utf-8"))
    assert digest(request["canonical_schema"]) == "1a9ab93f7a1bee3d9830ca9f9476b3822710885207396d9d58a704343ee89d57"
    assert digest(request["wire_schema"]) == "98f6c0f99d7f54cd1c9e00573de377025b7177185879be10d75a9bd9bd1e8144"
    expected = {"status": "inconclusive", "intent": "offline formatter protocol probe", "clarification_question": "",
        "input_kind": None, "input_field": None, "summary": {"zh": "仅核对格式，不查询SAP。", "en": "Formatting only; no SAP query."},
        "source_complete": False, "business_complete": False, "missing_evidence": ["offline_formatter_probe"],
        "evidence_refs": [], "executed_plans": [], "presentation": None}
    assert json.loads(request["prompt"].rsplit("\n", 1)[1]) == expected
    worker_path = Path(release["directory"]) / release["worker"]
    original_source = worker_path.read_text(encoding="utf-8")
    execute = next(n for n in ast.parse(original_source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "execute")
    append = next(n.value.right.value for n in ast.walk(execute) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "system_prompt" for t in n.targets)
        and isinstance(n.value, ast.BinOp) and isinstance(n.value.right, ast.Constant)
        and isinstance(n.value.right.value, str) and "StructuredOutput exactly once" in n.value.right.value)
    spec = importlib.util.spec_from_file_location("vendor_pinned_worker", worker_path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    events = [json.loads(line) for line in (BATCH / "safe-events.jsonl").read_text(encoding="utf-8").splitlines()]
    checks = [e for e in events if e["event"] == "workbuddy_native_output_checked"]
    results = [e for e in events if e["event"] == "agent_runtime_tool_finished"]
    feedbacks = []
    for check, result in zip(checks[::2], results):
        reason = worker.formatter_feedback({"validation_issues": check["validation_issues"]}, request["wire_schema"])
        content = [{"type": "text", "text": "Error: " + reason}]
        assert worker.safe_native_content(content)["content_sha256"] == result["content_sha256"]
        feedbacks.append({"call_index": result["call_index"], "validation_issues": check["validation_issues"],
            "safe_platform_feedback": reason, "reconstructed_tool_result": content,
            "content_sha256": result["content_sha256"], "matches_saved_real_sdk_return": True})
    case = {"canonical_schema": request["canonical_schema"], "wire_schema": request["wire_schema"],
        "synthetic_expected_output": expected, "prompt": request["prompt"],
        "historical_full_system_prompt": request["system_prompt"] + append,
        "sdk_options": {"model": "hy4-preview", "tools": ["StructuredOutput"], "allowed_tools": ["StructuredOutput"],
            "permission_mode": "plan", "setting_sources": [], "persist_session": False,
            "max_turns": 50, "request_timeout_ms": 90000},
        "cli_extra_args": {"print": None, "strict-mcp-config": None}, "platform_tools": [], "mcp_servers": {},
        "hook": {"event": "PreToolUse", "matcher": "^StructuredOutput$", "invalid_decision": "deny", "value_repair": False}}
    metadata = {"sdk": release["sdk_version"], "cli": release["cli_version"], "service_site": "china",
        "requested_model": "hy4-preview", "successful_terminal_model_identity": "not_confirmed",
        "cli_sha256": release["files"][release["cli"]], "worker_sha256": release["files"][release["worker"]],
        "sdk_wheel_sha256": release["sdk_wheel_digest"], "dependency_lock_sha256": release["dependency_lock_digest"],
        "observed": {"formatter_calls": 2, "assistant_messages": 4, "terminal_received": False,
            "candidate_lengths": [544, 385], "elapsed_seconds": 90.06, "primary_failure_code": "workbuddy_deadline_exceeded"},
        "contains_credentials_sap_rows_or_personal_configuration": False,
        "does_not_claim_vendor_root_cause": True, "sent_to_vendor": False}
    atomic_json(DESTINATION / "case.json", case)
    atomic_json(DESTINATION / "feedback.json", {"feedbacks": feedbacks, "invalid_candidate_values": "not_retained"})
    atomic_json(DESTINATION / "metadata.json", metadata)
    readme = (
        "OFFLINE SYNTHETIC REPRODUCTION PACKAGE - NOT SENT TO VENDOR\n\n"
        "This package does not execute anything. It contains no credentials, account IDs, SAP data, personal paths or raw SDK logs.\n"
        "The expected object is synthetic. Both nullable enum fields must be literal JSON null. Do not repair values or relax validation.\n"
        "The saved historical SDK tool feedback hashes match the reconstructed safe messages. Actual remote model consumption is unknown.\n"
        "Vendor questions:\n"
        "1. Which exact tool Schema reaches hy4-preview, and which arguments are generated before CLI parsing/rewriting?\n"
        "2. Why do these nullable enums still arrive as invalid strings despite the confirmed correction feedback?\n"
        "3. After the last rejection, which model request, Stop hook or completion wait is pending?\n"
        "4. How is request_timeout_ms supported by SDK 0.3.247 / CLI 2.141.0? The Python transport option copy drops it; no verified CLI flag is assumed.\n"
        "5. Can the complete unmodified Schema yield a valid native terminal without SAP or external tools?\n\n"
        "The historical full system prompt is included exactly, including its one-total-attempt wording. Platform follow-up changes that to one successful capture, but has not been live-tested.\n"
        "Any vendor-side execution must use its own authorized environment. This package grants no access or qualification.\n"
    )
    (DESTINATION / "README.txt").write_text(readme, encoding="utf-8")
    encoded = json.dumps(case, ensure_ascii=False) + json.dumps(feedbacks, ensure_ascii=False) + json.dumps(metadata) + readme
    for forbidden in ("USERPROFILE", "APPDATA", "CODEBUDDY_API_KEY", "SAP_USERNAME", "SAP_PASSWORD", "C:\\Users", "D:\\SAPBusinessAgents"):
        assert forbidden not in encoded
    print(json.dumps({"phase": "prepared_offline_not_sent", "files": sorted(p.name for p in DESTINATION.iterdir()),
        "model_calls": 0, "authentication_calls": 0, "sap_calls": 0, "contains_only_public_or_synthetic_evidence": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
