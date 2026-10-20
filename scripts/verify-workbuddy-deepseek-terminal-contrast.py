"""Once-only no-SAP model contrast using the frozen full formatter contract.

Reuse the current immutable release and site. No login, installation, model
check registration, operation qualification, default change or retry occurs.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import time
import uuid
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.runtime_diagnostics import checked_output
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_site import configuration, matches

ROOT = Path(__file__).resolve().parents[1]
MODEL = "deepseek-v4.1-flash"
REFERENCE = ROOT / ".local-data/workbuddy-validation/china-20261012-terminal-followup-release"
SPEC = importlib.util.spec_from_file_location("deepseek_followup_reference", ROOT / "scripts/verify-workbuddy-terminal-followup-release.py")
followup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(followup)
baseline = followup.baseline


def sources():
    return {**followup.sources(), Path(__file__).relative_to(ROOT).as_posix(): file_hash(Path(__file__))}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    baseline.runner.require_idle(manager)
    previous = json.loads((REFERENCE / "before.json").read_text(encoding="utf-8"))
    report = json.loads((REFERENCE / "terminal-report.json").read_text(encoding="utf-8"))
    assert baseline.controls() == previous["controls"] and followup.sources() == previous["sources"], "verification_reference_code_changed"
    assert report["phase"] == "failed" and report["failure_code"] == "workbuddy_deadline_exceeded", "verification_reference_case_changed"
    assert report["binding_and_controls_unchanged"] and not report["cleanup_pending"], "verification_reference_cleanup_missing"
    release = manager.environment.release()
    assert release["environment_digest"] == report["binding"]["environment_digest"], "verification_same_release_required"
    assert release["sdk_version"] == "0.3.247" and release["cli_version"] == "2.141.0", "verification_versions_changed"
    request = json.loads((REFERENCE / "request.json").read_text(encoding="utf-8"))
    assert digest(request) == previous["request_sha256"], "verification_reference_input_changed"
    assert digest(request["canonical_schema"]) == report["canonical_schema_sha256"], "verification_contract_changed"
    assert digest(request["wire_schema"]) == report["wire_schema_sha256"], "verification_wire_contract_changed"
    schema, example = baseline.probe_contract()
    assert schema == request["canonical_schema"], "verification_current_contract_changed"
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema).validate(example)
    Draft202012Validator(request["wire_schema"]).validate(example)
    state = manager.environment.state()
    assert configuration(state, required=True) == previous["site"], "verification_site_changed"
    assert not state.get("enabled") and state.get("default_model_id") == "hy4-preview", "verification_selection_changed"
    binding = manager.runtime_snapshot(MODEL, require_enabled=False, formal=True)
    manager.environment.assert_verification_binding(binding, formal=True)
    assert binding["actual_model"] == MODEL, "verification_model_identity_mismatch"
    history = [REFERENCE / n for n in ("before.json", "request.json", "terminal-report.json", "safe-events.jsonl", "result-summary.json")]
    atomic_json(batch / "before.json", {"scope": "no_sap_full_schema_model_contrast", "binding": binding,
        "controls": baseline.controls(), "sources": sources(), "budget_seconds": baseline.MODEL_CHECK_SECONDS,
        "manifest_sha256": file_hash(Path(release["directory"]) / "environment.json"),
        "models_sha256": digest(state.get("models")), "qualifications_sha256": digest(state.get("capabilities")),
        "default_model_id": state["default_model_id"], "site": previous["site"],
        "history": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in history}, "request_sha256": digest(request),
        "reference_task_id": report["task_id"], "reference_requested_model": report["binding"]["model"],
        "model_calls": 0, "sap_calls": 0, "automatic_retry": False})
    atomic_json(batch / "request.json", request)
    return {"phase": "prepared", "environment_digest": release["environment_digest"], "model": MODEL,
        "canonical_schema_sha256": digest(schema), "wire_schema_sha256": digest(request["wire_schema"]), "model_calls": 0, "sap_calls": 0}


def check(manager, batch):
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert baseline.controls() == before["controls"] and sources() == before["sources"], "verification_codex_or_source_changed"
    assert all(file_hash(ROOT / name) == value for name, value in before["history"].items()), "verification_history_changed"
    state = manager.environment.state()
    assert not state.get("enabled") and state.get("default_model_id") == before["default_model_id"], "verification_selection_changed"
    assert configuration(state, required=True) == before["site"], "verification_site_changed"
    assert digest(state.get("models")) == before["models_sha256"], "verification_model_records_changed"
    assert digest(state.get("capabilities")) == before["qualifications_sha256"], "verification_qualification_changed"
    assert state.get("environment_digest") == before["binding"]["environment_digest"], "verification_environment_changed"
    release = manager.environment.assert_verification_binding(before["binding"], formal=True)
    assert file_hash(Path(release["directory"]) / "environment.json") == before["manifest_sha256"], "verification_release_changed"
    account = json.loads((Settings.from_env(ROOT).data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))["accounts"]["workbuddy"]
    assert account.get("authenticated") and not account.get("invalidation_pending"), "verification_authentication_not_confirmed"
    assert account["revision"] == before["binding"]["authentication_revision"] and matches(account, before["site"]), "verification_account_binding_changed"
    request = json.loads((batch / "request.json").read_text(encoding="utf-8"))
    assert digest(request) == before["request_sha256"], "verification_request_changed"
    return before, request, before["binding"]


def verify_terminal(result, schema):
    # Same canonical checker and synthetic/null requirements as the reference;
    # only the expected native model identity changes for this model contrast.
    value = checked_output(result.get("text"), schema, operation="model_check", output_format=result.get("output_format"))
    assert result.get("output_format") == "native_json_schema", "verification_native_terminal_missing"
    assert result.get("actual_model") == MODEL and result.get("model_identity_source") == "assistant_message", "verification_model_identity_mismatch"
    assert value["input_kind"] is None and value["input_field"] is None, "verification_literal_null_required"
    assert (value["status"] == "inconclusive" and value["source_complete"] is False and value["business_complete"] is False
        and value["executed_plans"] == [] and value["evidence_refs"] == [] and value["presentation"] is None
        and value["missing_evidence"] == ["offline_formatter_probe"]), "verification_synthetic_scope_changed"
    return {"schema_valid": True, "literal_null_fields": ["input_kind", "input_field"], "no_business_evidence": True,
        "actual_model": result["actual_model"], "native_terminal": True, "canonical_schema_validated": True}


async def terminal(manager, batch):
    destination = batch / "terminal-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    baseline.runner.require_idle(manager)
    before, request, binding = check(manager, batch)
    report = {"phase": "running", "task_id": "wb_deepseek_contrast_" + uuid.uuid4().hex[:16], "binding": binding,
        "probe_kind": "same_full_schema_model_contrast", "operation": "model_check", "budget_seconds": before["budget_seconds"],
        "canonical_schema_sha256": digest(request["canonical_schema"]), "wire_schema_sha256": digest(request["wire_schema"]),
        "reference_task_id": before["reference_task_id"], "model_calls": 0, "sap_calls": 0,
        "operation_qualification_granted": False, "model_compatibility_registered": False, "automatic_retry": False}
    atomic_json(destination, report)
    def emit(kind, data):
        safe = safe_progress(kind, data)
        if safe:
            if kind == "agent_runtime_query_submitted": report["model_calls"] = 1
            with (batch / "safe-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": kind, **safe}, ensure_ascii=True) + "\n")
            report["last_progress"] = {"event": kind, **safe}
            atomic_json(destination, report)
    started = time.monotonic()
    try:
        result = await manager.supervisor.run(task_id=report["task_id"], snapshot=binding, operation="model_check", probe=True,
            mode="bounded", seconds=before["budget_seconds"], tool_handler=None, emit=emit,
            payload={"cwd": str(batch), "output_schema": request["wire_schema"], "timeout_ms": before["budget_seconds"] * 1000,
                     "system_prompt": request["system_prompt"], "prompt": request["prompt"]})
        assert check(manager, batch)[2] == binding, "verification_binding_changed"
        checks = verify_terminal(result, request["canonical_schema"])
        job = json.loads((manager.environment.root / "jobs" / (digest(report["task_id"]) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job["cleanup_complete"] and not job["validation_job"], "verification_terminal_or_cleanup_missing"
        report.update(phase="passed", checks=checks)
    except Exception as error:
        report.update(phase="failed", failure_code=followup.previous.contrast.safe_code(error))
    finally:
        try:
            await manager.supervisor.close()
        except Exception as error:
            report.update(phase="failed", cleanup_failure_code=followup.previous.contrast.safe_code(error))
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), cleanup_pending=bool(manager.supervisor.pending()))
        try:
            assert check(manager, batch)[2] == binding, "verification_binding_changed"
            baseline.runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception as error:
            report.update(phase="failed", controls_failure_code=followup.previous.contrast.safe_code(error))
        atomic_json(destination, report)
    return {k: report.get(k) for k in ("phase", "task_id", "elapsed_seconds", "failure_code", "cleanup_pending", "binding_and_controls_unchanged", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", "terminal"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"phase": "dry_run", "model_calls": 0, "sap_calls": 0}))
        return 0
    batch = baseline.runner.batch_path(args.batch)
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare": result = prepare(manager, batch)
    elif args.command == "check":
        _, _, binding = check(manager, batch)
        baseline.runner.require_idle(manager)
        result = {"phase": "passed", "model": binding["model"], "model_calls": 0, "sap_calls": 0}
    else: result = asyncio.run(terminal(manager, batch))
    print(json.dumps(result, ensure_ascii=False))
    return int(result["phase"] not in {"prepared", "passed"} or result.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
