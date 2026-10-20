"""One guarded no-SAP terminal check of the tested follow-up worker release.

Installation and authentication use the public managed services. This entry
never enables a Runtime or registers a model/operation qualification.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from sap_business_agents_platform.workbuddy_site import configuration, matches
from sap_business_agents_platform.workbuddy_diagnostics import safe_progress

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = ROOT / ".local-data/workbuddy-terminal-followup-offline-20261012"
OLD_CASE = ROOT / ".local-data/workbuddy-validation/china-20261012-equivalent-feedback-release"
SPEC = importlib.util.spec_from_file_location("followup_equivalence", ROOT / "scripts/verify-workbuddy-formatter-equivalence-release.py")
previous = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(previous)
baseline = previous.baseline


def sources():
    paths = [*ROOT.joinpath("src/sap_business_agents_platform").rglob("*.py"), Path(__file__),
             ROOT / "scripts/verify-workbuddy-formatter-equivalence-release.py",
             ROOT / "scripts/verify-workbuddy-schema-terminal-contrast.py",
             ROOT / "scripts/verify-workbuddy-formatter-terminal.py",
             ROOT / "scripts/verify-workbuddy-formatter-codec.py",
             ROOT / "scripts/verify-workbuddy-operations.py"]
    return {p.relative_to(ROOT).as_posix(): file_hash(p) for p in paths}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    baseline.runner.require_idle(manager)
    state = manager.environment.state()
    site = configuration(state, required=True)
    assert site["service_site"] == "china" and state.get("default_model_id") == "hy4-preview", "verification_site_or_model_changed"
    assert state.get("authenticated") is True, "verification_authentication_required"
    summary = json.loads((OFFLINE / "result-summary.json").read_text(encoding="utf-8"))
    old = baseline.old_release_identity(manager)
    assert old["environment_digest"] == summary["environment_digest"], "verification_offline_baseline_changed"
    assert old["sdk"] == "0.3.247" and old["cli"] == "2.141.0", "verification_locked_versions_changed"
    # Recheck the saved offline boundary before any installation changes state.
    spec = importlib.util.spec_from_file_location("followup_offline_guard", ROOT / "scripts/verify-workbuddy-terminal-followup-offline.py")
    offline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(offline)
    saved = json.loads((OFFLINE / "before.json").read_text(encoding="utf-8"))
    current = offline.controls()
    assert current["dependencies"] == saved["controls"]["dependencies"]
    changed = {name for name, value in current["files"].items() if value != saved["controls"]["files"][name]}
    assert changed == offline.ALLOWED, "verification_unexpected_offline_change"
    xml = Path(summary["tests"]["xml"])
    suites = ET.parse(xml).getroot().findall(".//testsuite")
    assert suites and not sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    assert sum(int(s.get("tests", 0)) - int(s.get("skipped", 0)) for s in suites) == summary["tests"]["passed"] == 548
    request = json.loads((OLD_CASE / "request.json").read_text(encoding="utf-8"))
    schema, example = baseline.probe_contract()
    assert request["canonical_schema"] == schema, "verification_contract_changed"
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema).validate(example)
    Draft202012Validator(request["wire_schema"]).validate(example)
    history = [*(OFFLINE / n for n in ("before.json", "result-summary.json")), xml,
               *(OLD_CASE / n for n in ("before.json", "freeze.json", "request.json", "terminal-report.json", "safe-events.jsonl"))]
    atomic_json(batch / "before.json", {"scope": "no_sap_terminal_followup_release", "controls": baseline.controls(),
        "sources": sources(), "old_release": old, "site": site, "model": "hy4-preview",
        "authentication_revision": state["authentication_revision"], "budget_seconds": baseline.MODEL_CHECK_SECONDS,
        "qualifications_sha256": digest(state.get("capabilities")), "models_sha256": digest(state.get("models")),
        "history": {p.relative_to(ROOT).as_posix(): file_hash(p) for p in history},
        "request_sha256": digest(request), "offline_passed": 548})
    atomic_json(batch / "request.json", request)
    return {"phase": "prepared", "old_environment": old["environment_digest"], "offline_passed": 548, "model_calls": 0, "sap_calls": 0}


def check(manager, batch, *, new=True, authentication=True):
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert baseline.controls() == before["controls"] and sources() == before["sources"], "verification_codex_or_tested_source_changed"
    assert all(file_hash(ROOT / name) == value for name, value in before["history"].items()), "verification_historical_record_changed"
    state = manager.environment.state()
    assert not state.get("enabled") and state.get("default_model_id") == before["model"], "verification_selection_changed"
    assert configuration(state, required=True) == before["site"], "verification_site_changed"
    assert digest(state.get("capabilities")) == before["qualifications_sha256"], "verification_qualification_changed"
    assert digest(state.get("models")) == before["models_sha256"], "verification_model_records_changed"
    old = manager.environment.release(before["old_release"]["environment_digest"])
    assert file_hash(Path(old["directory"]) / "environment.json") == before["old_release"]["manifest_hash"], "verification_old_release_changed"
    current = manager.environment.release()
    if new:
        assert current["environment_digest"] != old["environment_digest"], "verification_new_release_required"
        for name in ("sdk_version", "cli_version", "dependencies", "sdk_wheel_digest", "dependency_lock_digest", "python_bootstrap_digest"):
            assert current[name] == old[name], "verification_locked_installation_changed"
        assert current["files"][current["cli"]] == old["files"][old["cli"]], "verification_cli_changed"
        assert current["files"][current["worker"]] == file_hash(ROOT / "src/sap_business_agents_platform/workbuddy_worker.py"), "verification_worker_source_mismatch"
        assert not any(v["status"] == "validated" for v in manager.snapshot()["operation_capabilities"].values()), "verification_old_qualification_reused"
    else:
        assert current["environment_digest"] == old["environment_digest"], "verification_environment_changed_before_install"
    if authentication:
        assert state.get("authenticated") is True and state.get("authentication_environment") == current["environment_digest"], "verification_new_authentication_required"
        assert matches(state.get("authentication_site") or {}, before["site"]), "verification_authentication_site_changed"
        account = json.loads((Settings.from_env(ROOT).data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))["accounts"]["workbuddy"]
        assert account.get("authenticated") and not account.get("invalidation_pending"), "verification_authentication_not_confirmed"
        assert account["revision"] == state.get("authentication_revision") == before["authentication_revision"], "verification_account_changed"
        assert matches(account, before["site"]), "verification_account_site_changed"
    request = json.loads((batch / "request.json").read_text(encoding="utf-8"))
    assert digest(request) == before["request_sha256"], "verification_request_changed"
    return before, request, {"provider_id": "workbuddy", "environment_digest": current["environment_digest"],
        "model": before["model"], "authentication_revision": state.get("authentication_revision"), **before["site"]}


async def terminal(manager, batch):
    destination = batch / "terminal-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    baseline.runner.require_idle(manager)
    before, request, binding = check(manager, batch)
    report = {"phase": "running", "task_id": "wb_terminal_followup_" + uuid.uuid4().hex[:16], "binding": binding,
        "probe_kind": "equivalent_schema_followup_worker", "operation": "model_check", "budget_seconds": before["budget_seconds"],
        "canonical_schema_sha256": digest(request["canonical_schema"]), "wire_schema_sha256": digest(request["wire_schema"]),
        "model_calls": 0, "sap_calls": 0, "operation_qualification_granted": False, "model_compatibility_registered": False, "automatic_retry": False}
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
        assert check(manager, batch)[2] == binding, "verification_binding_changed"
        checks = baseline.verify_terminal(result, request["canonical_schema"])
        job = json.loads((manager.environment.root / "jobs" / (digest(report["task_id"]) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job["cleanup_complete"] and not job["validation_job"], "verification_terminal_or_cleanup_missing"
        report.update(phase="passed", checks={**checks, "canonical_schema_validated": True})
    except Exception as error:
        report.update(phase="failed", failure_code=previous.contrast.safe_code(error))
    finally:
        try:
            await manager.supervisor.close()
        except Exception as error:
            report.update(phase="failed", cleanup_failure_code=previous.contrast.safe_code(error))
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), cleanup_pending=bool(manager.supervisor.pending()))
        try:
            assert check(manager, batch)[2] == binding, "verification_binding_changed"
            baseline.runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception as error:
            report.update(phase="failed", controls_failure_code=previous.contrast.safe_code(error))
        atomic_json(destination, report)
    return {k: report.get(k) for k in ("phase", "task_id", "elapsed_seconds", "failure_code", "cleanup_pending", "binding_and_controls_unchanged", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", "terminal"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--before-install", action="store_true")
    parser.add_argument("--before-authentication", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"phase": "dry_run", "model_calls": 0, "sap_calls": 0}))
        return 0
    batch = baseline.runner.batch_path(args.batch)
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare": result = prepare(manager, batch)
    elif args.command == "check":
        _, _, binding = check(manager, batch, new=not args.before_install, authentication=not args.before_authentication)
        baseline.runner.require_idle(manager)
        result = {"phase": "passed", "binding": binding, "model_calls": 0, "sap_calls": 0}
    else: result = asyncio.run(terminal(manager, batch))
    print(json.dumps(result, ensure_ascii=False))
    return int(result["phase"] not in {"prepared", "passed"} or result.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
