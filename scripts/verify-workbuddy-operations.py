"""Reviewed live cases for the current operation registry. Default is import-only.

Each execute command is once-only in a fresh batch. Historical fixtures and
reports are preserved; qualification still belongs to the existing manager.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.runtime_policy import OPERATIONS, orchestration_digest
from sap_business_agents_platform.workbuddy_environment import atomic_json, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager
from workbuddy_validation_cases import frozen_runtime_binding
from workbuddy_live_cases import live_guard

ROOT = Path(__file__).resolve().parents[1]
CASES = Path(__file__).resolve().parent / "workbuddy_live_cases"
FAMILIES = {
    "author": ("author.py", [], {"author_draft"}, {"bounded"}),
    "feedback": ("free_feedback.py", [], {"review_free_query_feedback", "revise_free_query_presentation"}, {"bounded"}),
    "workflows": ("workflows.py", [], {"compose_workflow", "review_workflow", "repair_workflow", "review_workflow_feedback"}, {"bounded"}),
    "agent_explain": ("agent_feedback.py", ["--explain-only", "--production-budget"], {"review_agent_feedback"}, {"bounded"}),
    "agent_modify": ("agent_feedback.py", ["--exact-suffix", "--feedback-native"], {"review_agent_feedback"}, {"trusted_local"}),
    "workflow_v2": ("workflow_v2.py", ["--production-budget"], {"workflow_authoring.v2"}, {"trusted_local"}),
    "roles": ("roles.py", [], {"analyze_role_matching", "review_role_matching_feedback"}, {"bounded"}),
    "sample": ("sample.py", ["--sample-only"], {"sample_discovery"}, {"bounded"}),
    "acceptance": ("acceptance.py", [], {"acceptance_baseline", "acceptance_free_query"}, {"bounded"}),
}
NON_SAP = set(OPERATIONS) - {"free_query", "sample_discovery", "acceptance_baseline", "acceptance_free_query"}


def batch_path(value):
    path = Path(value).resolve()
    allowed = (ROOT / ".local-data/workbuddy-validation").resolve()
    if path == allowed or not path.is_relative_to(allowed):
        raise ValueError("verification_batch_directory_invalid")
    return path


def controls(manager):
    settings = Settings.from_env(ROOT)
    paths = [ROOT / "pyproject.toml", ROOT / "config/sdks.json", settings.sdk_runtime_state_path,
             ROOT / "src/sap_business_agents_platform/codex_planner.py",
             ROOT / "src/sap_business_agents_platform/codex_driver.py",
             ROOT / "src/sap_business_agents_platform/workflow_authoring_runtime.py",
             ROOT / "src/sap_business_agents_platform/runtime_execution.py"]
    release = manager.environment.release()
    worker = file_hash(Path(release["directory"]) / release["worker"])
    assert worker == file_hash(ROOT / "src/sap_business_agents_platform/workbuddy_worker.py")
    account = json.loads((settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))
    from sap_business_agents_platform.workbuddy_environment import digest
    return {"files": {str(path.relative_to(ROOT)): file_hash(path) for path in paths if path.exists()},
            "codex_account_digest": digest(account.get("accounts", {}).get("codex")),
            "dependencies": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()},
            "executable": sys.executable, "environment_digest": release["environment_digest"],
            "worker_digest": worker, "release_manifest": file_hash(Path(release["directory"]) / "environment.json")}


def require_idle(manager):
    assert not manager.snapshot()["enabled"] and not manager.supervisor.reconcile(), "verification_runtime_not_idle"
    for path in (manager.environment.root / "jobs").glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8")).get("status") not in {"running", "cleanup_pending"}
    database = Settings.from_env(ROOT).database_path
    if not database.exists():
        return
    statuses = ("queued", "running", "planning", "executing", "starting", "cancelling", "cleanup_pending", "preparing")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            assert re.fullmatch(r"[A-Za-z0-9_]+", table)
            if "status" not in {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}:
                continue
            slots = ",".join("?" for _ in statuses)
            query = (f'SELECT t.status FROM role_matching_turns t JOIN role_matching_sessions s ON t.session_id=s.session_id WHERE t.status IN ({slots}) AND NOT(s.status="cancelled" AND s.active_job_id IS NULL)'
                     if table == "role_matching_turns" else f'SELECT status FROM "{table}" WHERE status IN ({slots})')
            assert not list(db.execute(query, statuses)), "verification_active_platform_operation"


def load_case(family, batch):
    filename, arguments, _, _ = FAMILIES[family]
    argv = sys.argv
    try:
        sys.argv = [filename, *arguments]
        spec = importlib.util.spec_from_file_location("wb_current_" + family, CASES / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    module.ROOT, module.HERE = ROOT, batch
    module.RUN_ROOT = batch / (family + "-data")
    module.REPORT = module.RUN_ROOT / "report.json"
    if hasattr(module, "RUN_ID"):
        module.RUN_ID = "wb_case_" + uuid.uuid4().hex[:16]
    return module


def check_cases(batch):
    assert set.union(*(value[2] for value in FAMILIES.values())).issubset(OPERATIONS)
    assert set.union(*(value[2] for key, value in FAMILIES.items() if key not in {"sample", "acceptance"})) == NON_SAP
    for family in FAMILIES:
        load_case(family, batch)
    return {"phase": "passed", "fixture_imports": len(FAMILIES), "operation_count": len(OPERATIONS),
            "retired_operations": [], "model_calls": 0, "sap_calls": 0}


def fixture_digests():
    paths = [*CASES.glob("*.py"), Path(__file__), ROOT / "scripts/verify-runtime-role-matching.py",
             ROOT / "scripts/workbuddy_validation_cases.py"]
    return {path.relative_to(ROOT / "scripts").as_posix(): file_hash(path) for path in paths}


def check_frozen(manager, batch):
    frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
    assert controls(manager) == frozen["controls"], "verification_codex_or_environment_changed"
    assert fixture_digests() == frozen["fixture_digests"], "verification_fixture_changed"
    assert all(orchestration_digest(op) == value for op, value in frozen["operation_digests"].items()), "verification_orchestration_changed"
    if frozen.get("scope") == "china_non_sap":
        live_guard.check_batch(manager, batch)
    return frozen


def freeze_current(manager, batch, model_id="hy4-preview"):
    """Freeze already successful checks without calling their execution paths."""
    require_idle(manager)
    assert not (batch / "freeze.json").exists(), "verification_batch_already_frozen"
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert controls(manager) == before["controls"], "verification_codex_or_environment_changed"
    assert fixture_digests() == before["fixture_digests"], "verification_fixture_changed"
    assert all(orchestration_digest(op) == value for op, value in before["operation_digests"].items()), "verification_orchestration_changed"
    snapshot = live_guard.current_binding(manager, model_id)
    catalog = live_guard.catalog_snapshot(ROOT)
    from sap_business_agents_platform.workbuddy_environment import digest
    roles = load_case("roles", batch).role_case
    parameters = {family: {"fixture": values[0], "arguments": values[1],
                          "operations": sorted(values[2]), "modes": sorted(values[3])}
                  for family, values in FAMILIES.items() if values[2].issubset(NON_SAP)}
    parameters["roles"].update(description=roles.DESCRIPTION, feedback=roles.FEEDBACK)
    frozen = {**before, "phase": "frozen", "scope": "china_non_sap", "allowed_operations": sorted(NON_SAP),
              "validation_model_id": model_id,
              "environment_digest": snapshot["environment_digest"], "operation_count": len(OPERATIONS),
              "case_parameters": parameters, "catalog_sha256": digest(catalog),
              "catalog_digest": catalog["digest"], "checks_source": "existing_platform_checks"}
    with manager.environment.maintenance():
        assert live_guard.binding_identity(live_guard.current_binding(manager, model_id)) == live_guard.binding_identity(snapshot), "verification_runtime_identity_changed"
        atomic_json(batch / "runtime-snapshot.json", snapshot)
        atomic_json(batch / "catalog-snapshot.json", catalog)
        atomic_json(batch / "freeze.json", frozen)
    return {"phase": "passed", "binding": {k: snapshot[k] for k in (
        "service_site", "service_site_revision", "authentication_revision", "model", "actual_model", "environment_digest")},
        "reused_checks": True, "model_calls": 0, "sap_calls": 0, "target_operations": len(NON_SAP)}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    require_idle(manager)
    imports = check_cases(batch)
    before = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "controls": controls(manager), "fixture_digests": fixture_digests(), "offline_import_check": imports,
              "operation_digests": {op: orchestration_digest(op) for op in OPERATIONS}}
    atomic_json(batch / "before.json", before)
    return {"phase": "prepared", "operation_count": len(OPERATIONS), "model_calls": 0, "sap_calls": 0}


async def probes(manager, batch):
    destination = batch / "probes-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert controls(manager) == before["controls"] and fixture_digests() == before["fixture_digests"]
    require_idle(manager)
    report = {"phase": "running", "stages": [], "sap_calls": 0, "automatic_retry": False}
    atomic_json(destination, report)
    try:
        for operation in ("authentication", "model_check"):
            started = time.monotonic()
            result = await manager.check() if operation == "authentication" else await manager.check_model("hy4-preview")
            value = result if operation == "authentication" else result["check"]
            passed = value.get("authenticated") is True if operation == "authentication" else (
                value.get("compatible") is True and value.get("identity_known") is True and value.get("actual_model") == "hy4-preview")
            entry = {"operation": operation, "phase": "passed" if passed else "failed",
                     "elapsed_seconds": round(time.monotonic() - started, 2),
                     **{key: value.get(key) for key in ("status", "authenticated", "actual_model", "identity_known", "error")}}
            if operation == "model_check":
                entry.update({key: value.get(key) for key in ("probe_contract", "schema_sha256", "diagnostics")})
            report["stages"].append(entry)
            atomic_json(destination, report)
            print(json.dumps(entry), flush=True)
            if not passed:
                break
    finally:
        await manager.supervisor.close()
        report.update(phase="passed" if len(report["stages"]) == 2 and all(item["phase"] == "passed" for item in report["stages"]) else "failed",
                      cleanup_pending=bool(manager.supervisor.pending()), controls_unchanged=controls(manager) == before["controls"])
        atomic_json(destination, report)
    if report["phase"] == "passed" and not report["cleanup_pending"] and report["controls_unchanged"]:
        snapshot = frozen_runtime_binding(manager.runtime_snapshot(require_enabled=False, formal=True))
        frozen = {**before, "phase": "frozen", "environment_digest": snapshot["environment_digest"], "operation_count": len(OPERATIONS)}
        atomic_json(batch / "runtime-snapshot.json", snapshot)
        atomic_json(batch / "freeze.json", frozen)
    return report


async def dispatch(manager, family, batch):
    destination = batch / (family + "-summary.json")
    assert not destination.exists(), "verification_family_already_attempted"
    frozen = check_frozen(manager, batch)
    require_idle(manager)
    _, arguments, expected, modes = FAMILIES[family]
    assert expected.issubset(frozen.get("allowed_operations", OPERATIONS)), "verification_operation_out_of_scope"
    if family == "agent_modify":
        initial = json.loads((batch / "agent_explain-summary.json").read_text(encoding="utf-8"))
        assert initial["phase"] == "passed", "verification_prerequisites_not_ready"
    if family in {"sample", "acceptance"}:
        required = set(OPERATIONS) - expected - ({"acceptance_baseline", "acceptance_free_query"} if family == "sample" else set())
        admission = manager.snapshot()["operation_capabilities"]
        assert all(admission[op]["status"] == "validated" for op in required), "verification_prerequisites_not_ready"
    module = load_case(family, batch)
    live_guard.configure(batch, expected, modes)
    report = {"family": family, "phase": "running", "expected_operations": sorted(expected),
              "report": str(module.REPORT), "automatic_retry": False}
    atomic_json(destination, report)
    started, argv = time.monotonic(), sys.argv
    try:
        sys.argv = [family, *arguments]
        await module.main()
        saved = json.loads(module.REPORT.read_text(encoding="utf-8"))
        stages = saved.get("stages", [])
        assert saved.get("phase") in {"passed", "completed"} and stages and all(item.get("phase") == "passed" for item in stages), "verification_family_contract_failed"
        passed = set(saved.get("qualified_operations") or saved.get("business_operations_validated") or
                     (["workflow_authoring.v2"] if family == "workflow_v2" and saved["phase"] == "passed" else []))
        assert passed == expected, "verification_family_qualification_mismatch"
        report.update(phase="passed", operations=sorted(passed))
    except BaseException as error:
        code = getattr(error, "code", str(error))
        report.update(phase="failed", failure_code=code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 120 else type(error).__name__)
        if module.REPORT.exists():
            saved = json.loads(module.REPORT.read_text(encoding="utf-8"))
            report["case_failure_code"] = saved.get("failure_code")
            report["case_stages"] = saved.get("stages", [])
    finally:
        sys.argv = argv
        await manager.supervisor.close()
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), enabled=manager.snapshot()["enabled"],
                      cleanup_pending=bool(manager.supervisor.pending()), controls_unchanged=controls(manager) == frozen["controls"])
        try:
            check_frozen(manager, batch)
        except Exception as exc:
            report.update(phase="failed", binding_failure=str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
        atomic_json(destination, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "prepare", "freeze-current", "probes", *FAMILIES])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--manifest", help="Use the shared case runner with an explicitly authorized frozen manifest")
    parser.add_argument("--model-id", help="Explicit checked model for freeze-current only; does not change defaults.")
    args = parser.parse_args()
    if args.model_id and args.command != "freeze-current":
        parser.error("--model-id applies only to freeze-current")
    if args.execute:
        from runtime_validation_delegate import delegate
        delegated = delegate(ROOT, args.batch, args.manifest)
        if delegated is not None:
            return delegated
    batch = batch_path(args.batch)
    if args.command == "check" or not args.execute:
        print(json.dumps(check_cases(batch)))
        return 0
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare":
        report = prepare(manager, batch)
    elif args.command == "freeze-current":
        report = freeze_current(manager, batch, args.model_id or "hy4-preview")
    elif args.command == "probes":
        report = asyncio.run(probes(manager, batch))
    else:
        report = asyncio.run(dispatch(manager, args.command, batch))
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(report["phase"] not in {"passed", "prepared"} or report.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
