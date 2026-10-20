"""Guarded new-release verification of equivalent-Schema rejection guidance.

Freeze existing controls before the narrow worker edit, then freeze tested source.
Use the public installer/authentication paths outside this private entry. Dispatch
one no-SAP terminal probe only, without model or business qualification.
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
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_site import configuration, matches
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress

ROOT = Path(__file__).resolve().parents[1]
WORKER = "src/sap_business_agents_platform/workbuddy_worker.py"
SPEC = importlib.util.spec_from_file_location("equivalence_old_case", ROOT / "scripts/verify-workbuddy-schema-terminal-contrast.py")
contrast = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contrast)
baseline = contrast.baseline
OLD_BATCH = ROOT / ".local-data/workbuddy-validation/china-20261012-equivalent-schema-terminal"


def history():
    names = [(OLD_BATCH / name) for name in ("before.json", "request.json", "terminal-report.json", "safe-events.jsonl")]
    return {path.relative_to(ROOT).as_posix(): file_hash(path) for path in names}


def sources():
    return {str(path.relative_to(ROOT)): file_hash(path) for path in (Path(__file__),
        ROOT / "scripts/verify-workbuddy-schema-terminal-contrast.py", ROOT / "scripts/workbuddy_schema_contrast.py",
        ROOT / "scripts/verify-workbuddy-formatter-terminal.py", ROOT / "scripts/verify-workbuddy-formatter-codec.py")}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    baseline.runner.require_idle(manager)
    old_frozen, request = contrast.check_frozen(manager, OLD_BATCH)
    release = baseline.old_release_identity(manager)
    state = manager.environment.state()
    atomic_json(batch / "before.json", {"scope": "no_sap_equivalent_feedback_new_release", "controls": baseline.controls(),
        "site": configuration(state, required=True), "old_release": release, "history": history(),
        "model": "hy4-preview", "authentication_revision": old_frozen["binding"]["authentication_revision"],
        "qualifications_sha256": digest(state.get("capabilities")), "models_sha256": digest(state.get("models")),
        "request_sha256": digest(request), "budget_seconds": baseline.MODEL_CHECK_SECONDS})
    atomic_json(batch / "request.json", request)
    return {"phase": "prepared", "old_environment_digest": release["environment_digest"], "model_calls": 0, "sap_calls": 0}


def before_check(manager, batch, *, tested=False):
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    current_controls = baseline.controls()
    expected = json.loads(json.dumps(before["controls"]))
    expected["files"][WORKER] = file_hash(ROOT / WORKER)
    assert current_controls == expected, "verification_codex_or_unrelated_source_changed"
    assert history() == before["history"], "verification_historical_record_changed"
    state = manager.environment.state()
    assert not state.get("enabled") and state.get("default_model_id") == before["model"], "verification_selection_changed"
    assert configuration(state, required=True) == before["site"], "verification_site_changed"
    assert digest(state.get("capabilities")) == before["qualifications_sha256"], "verification_qualification_changed"
    assert digest(state.get("models")) == before["models_sha256"], "verification_model_records_changed"
    old = manager.environment.release(before["old_release"]["environment_digest"])
    assert file_hash(Path(old["directory"]) / "environment.json") == before["old_release"]["manifest_hash"], "verification_old_release_changed"
    request = json.loads((batch / "request.json").read_text(encoding="utf-8"))
    assert digest(request) == before["request_sha256"], "verification_request_changed"
    if tested:
        frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
        assert frozen["controls"] == current_controls and frozen["sources"] == sources(), "verification_tested_source_changed"
        assert file_hash(Path(frozen["offline_xml"])) == frozen["offline_xml_sha256"], "verification_offline_result_changed"
    return before, request


def freeze(manager, batch, xml):
    import xml.etree.ElementTree as ET
    assert not (batch / "freeze.json").exists(), "verification_already_frozen"
    baseline.runner.require_idle(manager)
    before, request = before_check(manager, batch)
    assert file_hash(ROOT / WORKER) != before["controls"]["files"][WORKER], "verification_worker_fix_missing"
    xml = xml.resolve(strict=True)
    assert xml.is_relative_to(ROOT / ".local-data"), "verification_xml_outside_workspace"
    suites = ET.parse(xml).getroot().findall(".//testsuite")
    assert suites and sum(int(s.get("tests", 0)) for s in suites) > 0, "verification_offline_tests_missing"
    assert not sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites), "verification_offline_tests_failed"
    atomic_json(batch / "freeze.json", {"controls": baseline.controls(), "sources": sources(),
        "offline_xml": str(xml), "offline_xml_sha256": file_hash(xml), "request_sha256": digest(request)})
    return {"phase": "passed", "tested_worker_sha256": file_hash(ROOT / WORKER), "model_calls": 0, "sap_calls": 0}


def check_new(manager, batch, *, authentication=True):
    before, request = before_check(manager, batch, tested=True)
    current = manager.environment.release()
    old = manager.environment.release(before["old_release"]["environment_digest"])
    assert current["environment_digest"] != old["environment_digest"], "verification_new_release_required"
    for name in ("sdk_version", "cli_version", "dependencies", "sdk_wheel_digest", "dependency_lock_digest", "python_bootstrap_digest"):
        assert current[name] == old[name], "verification_locked_installation_changed"
    assert current["sdk_version"] == "0.3.247" and current["cli_version"] == "2.141.0", "verification_sdk_or_cli_changed"
    assert current["files"][current["cli"]] == old["files"][old["cli"]], "verification_cli_bytes_changed"
    assert current["files"][current["worker"]] == file_hash(ROOT / WORKER), "verification_worker_source_changed"
    snapshot = manager.snapshot()
    assert not any(v["status"] == "validated" for v in snapshot["operation_capabilities"].values()), "verification_old_qualification_reused"
    state = manager.environment.state()
    if authentication:
        assert state.get("authenticated") is True and state.get("authentication_environment") == current["environment_digest"], "verification_new_authentication_required"
        assert matches(state.get("authentication_site") or {}, before["site"]), "verification_authentication_site_changed"
        settings = Settings.from_env(ROOT)
        account = json.loads((settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))["accounts"]["workbuddy"]
        assert account.get("authenticated") and account.get("status") == "authenticated" and not account.get("invalidation_pending"), "verification_authentication_not_confirmed"
        assert account["revision"] == state.get("authentication_revision") == before["authentication_revision"], "verification_account_changed"
        assert matches(account, before["site"]), "verification_account_site_changed"
    binding = {"provider_id": "workbuddy", "environment_digest": current["environment_digest"], "model": before["model"],
        "authentication_revision": state.get("authentication_revision"), **before["site"]}
    return before, request, binding


async def terminal(manager, batch):
    destination = batch / "terminal-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    baseline.runner.require_idle(manager)
    before, request, binding = check_new(manager, batch)
    report = {"phase": "running", "task_id": "wb_equivalent_feedback_" + uuid.uuid4().hex[:16], "binding": binding,
        "probe_kind": "equivalent_feedback_fixed_worker", "operation": "model_check", "budget_seconds": before["budget_seconds"],
        "canonical_schema_sha256": digest(request["canonical_schema"]), "wire_schema_sha256": digest(request["wire_schema"]),
        "model_calls": 0, "sap_calls": 0, "operation_qualification_granted": False, "model_compatibility_registered": False,
        "automatic_retry": False}
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
        seconds = before["budget_seconds"]
        result = await manager.supervisor.run(task_id=report["task_id"], snapshot=binding, operation="model_check", probe=True,
            mode="bounded", seconds=seconds, tool_handler=None, emit=emit,
            payload={"cwd": str(batch), "output_schema": request["wire_schema"], "timeout_ms": seconds * 1000,
                "system_prompt": request["system_prompt"], "prompt": request["prompt"]})
        assert check_new(manager, batch)[2] == binding, "verification_binding_changed"
        checks = baseline.verify_terminal(result, request["canonical_schema"])
        job = json.loads((manager.environment.root / "jobs" / (digest(report["task_id"]) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job["cleanup_complete"] and not job["validation_job"], "verification_terminal_or_cleanup_missing"
        report.update(phase="passed", checks={**checks, "canonical_schema_validated": True})
    except Exception as error:
        report.update(phase="failed", failure_code=contrast.safe_code(error))
    finally:
        try:
            await manager.supervisor.close()
        except Exception as error:
            report.update(phase="failed", cleanup_failure_code=contrast.safe_code(error))
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), cleanup_pending=bool(manager.supervisor.pending()))
        try:
            assert check_new(manager, batch)[2] == binding, "verification_binding_changed"
            baseline.runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception as error:
            report.update(phase="failed", controls_failure_code=contrast.safe_code(error))
        atomic_json(destination, report)
    return {k: report.get(k) for k in ("phase", "task_id", "elapsed_seconds", "failure_code", "cleanup_pending", "binding_and_controls_unchanged", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "freeze", "check", "terminal"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--xml")
    parser.add_argument("--before-authentication", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    batch = baseline.runner.batch_path(args.batch)
    if not args.execute:
        print(json.dumps({"phase": "dry_run", "model_calls": 0, "sap_calls": 0}))
        return 0
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare": result = prepare(manager, batch)
    elif args.command == "freeze": result = freeze(manager, batch, Path(args.xml))
    elif args.command == "check":
        _, _, binding = check_new(manager, batch, authentication=not args.before_authentication)
        baseline.runner.require_idle(manager)
        result = {"phase": "passed", "binding": binding, "model_calls": 0, "sap_calls": 0}
    else: result = asyncio.run(terminal(manager, batch))
    print(json.dumps(result, ensure_ascii=False))
    return int(result["phase"] not in {"prepared", "passed"} or result.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
