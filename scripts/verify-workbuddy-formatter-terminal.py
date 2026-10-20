"""One no-SAP native-terminal management probe, never business qualification.

Environment installation/account checking use existing guarded public services.
This private entry checks the new release and dispatches one formatter probe.
No old qualification, SDK enablement, model-check registration or retry.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager, MODEL_CHECK_SECONDS
from sap_business_agents_platform.workbuddy_site import configuration, matches

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("formatter_preparation_guard", ROOT / "scripts/verify-workbuddy-operations.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CONTROL_FILES = ("pyproject.toml", "config/sdks.json", "config/workbuddy-requirements.lock",
    "src/sap_business_agents_platform/codex_driver.py", "src/sap_business_agents_platform/codex_planner.py",
    "src/sap_business_agents_platform/runtime_execution.py", "src/sap_business_agents_platform/workflow_authoring_runtime.py",
    "src/sap_business_agents_platform/runtime_harness_contract.py", "src/sap_business_agents_platform/workbuddy_worker.py",
    "src/sap_business_agents_platform/workbuddy_supervisor.py", "src/sap_business_agents_platform/workbuddy_diagnostics.py")


def controls():
    settings = Settings.from_env(ROOT)
    account = json.loads((settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))
    return {"files": {name: file_hash(ROOT / name) for name in CONTROL_FILES},
        "default_selection": file_hash(settings.sdk_runtime_state_path),
        "codex_account_digest": digest(account.get("accounts", {}).get("codex")),
        "dependencies": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()},
        "python": sys.executable}


def old_release_identity(manager):
    release = manager.environment.release()
    return {"environment_digest": release["environment_digest"],
        "manifest_hash": file_hash(Path(release["directory"]) / "environment.json"),
        "cli_hash": release["files"][release["cli"]], "sdk": release["sdk_version"], "cli": release["cli_version"],
        "dependencies": release["dependencies"], "sdk_wheel_digest": release["sdk_wheel_digest"],
        "dependency_lock_digest": release["dependency_lock_digest"]}


def prepare(manager, batch):
    assert not batch.exists(), "verification_new_batch_required"
    runner.require_idle(manager)
    state = manager.environment.state()
    site = configuration(state, required=True)
    assert site["service_site"] == "china" and state["default_model_id"] == "hy4-preview", "verification_site_or_model_changed"
    release = old_release_identity(manager)
    assert release["sdk"] == "0.3.247" and release["cli"] == "2.141.0", "verification_installation_changed"
    atomic_json(batch / "before.json", {"scope": "no_sap_formatter_probe", "controls": controls(),
        "site": site, "old_release": release, "historical_capabilities_digest": digest(state.get("capabilities")),
        "sdk_enabled": False, "model": "hy4-preview", "automatic_retry": False,
        "model_calls": 0, "sap_calls": 0, "fixture_hash": file_hash(Path(__file__))})
    return {"phase": "prepared", "old_environment": release["environment_digest"], "model_calls": 0, "sap_calls": 0}


def check_release(manager, batch):
    before = json.loads((batch / "before.json").read_text(encoding="utf-8"))
    assert controls() == before["controls"], "verification_codex_or_source_changed"
    assert file_hash(Path(__file__)) == before["fixture_hash"], "verification_fixture_changed"
    state = manager.environment.state()
    assert not state.get("enabled") and configuration(state, required=True) == before["site"], "verification_runtime_or_site_changed"
    assert state.get("default_model_id") == before["model"], "verification_model_changed"
    old = manager.environment.release(before["old_release"]["environment_digest"])
    assert file_hash(Path(old["directory"]) / "environment.json") == before["old_release"]["manifest_hash"], "verification_old_release_changed"
    current = manager.environment.release()
    assert current["environment_digest"] != old["environment_digest"], "verification_new_release_required"
    for key in ("sdk_version", "cli_version", "dependencies", "sdk_wheel_digest", "dependency_lock_digest"):
        assert current[key] == old[key], "verification_locked_dependency_changed"
    assert current["files"][current["cli"]] == before["old_release"]["cli_hash"], "verification_cli_bytes_changed"
    assert file_hash(Path(current["directory"]) / current["worker"]) == file_hash(ROOT / "src/sap_business_agents_platform/workbuddy_worker.py"), "verification_worker_source_mismatch"
    assert digest(state.get("capabilities")) == before["historical_capabilities_digest"], "verification_historical_qualification_changed"
    assert not any(value["status"] == "validated" for value in manager.snapshot()["operation_capabilities"].values()), "verification_old_qualification_reused"
    return current


def probe_binding(manager, batch):
    current = check_release(manager, batch)
    state = manager.environment.state()
    site = configuration(state, required=True)
    assert state.get("authenticated") is True and state.get("authentication_environment") == current["environment_digest"], "verification_current_authentication_required"
    assert matches(state.get("authentication_site") or {}, site), "verification_authentication_site_changed"
    settings = Settings.from_env(ROOT)
    account = json.loads((settings.data_root / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))["accounts"]["workbuddy"]
    assert account.get("authenticated") is True and account.get("status") == "authenticated" and not account.get("invalidation_pending"), "verification_authentication_not_confirmed"
    assert account["revision"] == state.get("authentication_revision") and matches(account, site), "verification_authentication_binding_changed"
    return {"provider_id": "workbuddy", "environment_digest": current["environment_digest"], "model": "hy4-preview",
            "authentication_revision": account["revision"], **site}


def probe_contract():
    from sap_business_agents_platform.acceptance_projection import output_schema
    from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA
    schema = output_schema(_HARNESS_OUTPUT_SCHEMA, None)
    value = {"status": "inconclusive", "intent": "offline formatter protocol probe", "clarification_question": "",
        "input_kind": None, "input_field": None, "summary": {"zh": "仅核对格式，不查询SAP。", "en": "Formatting only; no SAP query."},
        "source_complete": False, "business_complete": False, "missing_evidence": ["offline_formatter_probe"],
        "evidence_refs": [], "executed_plans": [], "presentation": None}
    return schema, value


def verify_terminal(result, schema):
    from sap_business_agents_platform.runtime_diagnostics import checked_output
    value = checked_output(result.get("text"), schema, operation="model_check", output_format=result.get("output_format"))
    assert result.get("output_format") == "native_json_schema", "verification_native_terminal_missing"
    assert result.get("actual_model") == "hy4-preview" and result.get("model_identity_source") == "assistant_message", "verification_model_identity_mismatch"
    assert value["input_kind"] is None and value["input_field"] is None, "verification_literal_null_required"
    assert (value["status"] == "inconclusive" and value["source_complete"] is False and value["business_complete"] is False
            and value["executed_plans"] == [] and value["evidence_refs"] == [] and value["presentation"] is None
            and value["missing_evidence"] == ["offline_formatter_probe"]), "verification_synthetic_scope_changed"
    return {"schema_valid": True, "literal_null_fields": ["input_kind", "input_field"],
            "no_business_evidence": True, "actual_model": result["actual_model"], "native_terminal": True}


async def terminal(manager, batch):
    destination = batch / "terminal-report.json"
    assert not destination.exists(), "verification_probe_already_attempted"
    runner.require_idle(manager)
    binding = probe_binding(manager, batch)
    schema, example = probe_contract()
    from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions
    report = {"phase": "running", "binding": binding, "operation": "model_check", "probe_kind": "full_native_envelope",
        "budget_seconds": MODEL_CHECK_SECONDS, "schema_sha256": digest(schema), "model_calls": 0, "sap_calls": 0,
        "operation_qualification_granted": False, "model_compatibility_registered": False, "automatic_retry": False}
    atomic_json(destination, report)
    task = "wb_formatter_probe_" + uuid.uuid4().hex[:16]
    report["task_id"] = task
    atomic_json(destination, report)
    def emit(kind, data):
        from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
        safe = safe_progress(kind, data)
        if safe:
            if kind == "agent_runtime_query_submitted":
                report["model_calls"] = 1
            with (batch / "safe-events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": kind, **safe}, ensure_ascii=True) + "\n")
            report["last_progress"] = {"event": kind, **safe}
            atomic_json(destination, report)
    started = time.monotonic()
    try:
        result = await manager.supervisor.run(task_id=task, snapshot=binding, operation="model_check", probe=True,
            mode="bounded", seconds=MODEL_CHECK_SECONDS, tool_handler=None, emit=emit,
            payload={"cwd": str(batch), "output_schema": schema, "timeout_ms": MODEL_CHECK_SECONDS * 1000,
                "system_prompt": "Native terminal encoding probe only. No SAP, external systems or native investigation. " + native_terminal_instructions(schema),
                "prompt": "Return the following complete synthetic object through StructuredOutput, then finish. No tools other than that local formatter. "
                    "Keep both clarification fields as literal JSON null, not strings. Do not add SAP facts, plans, evidence or a presentation.\n" + json.dumps(example, ensure_ascii=False)})
        assert probe_binding(manager, batch) == binding, "verification_binding_changed"
        checks = verify_terminal(result, schema)
        job = json.loads((manager.environment.root / "jobs" / (digest(task) + ".json")).read_text(encoding="utf-8"))
        assert job["status"] == "completed" and job.get("cleanup_complete") and not job.get("validation_job"), "verification_cleanup_or_terminal_missing"
        report.update(phase="passed", checks=checks)
    except Exception as error:
        code = getattr(error, "code", None)
        if not code and isinstance(error, AssertionError):
            code = str(error)
        report.update(phase="failed", failure_code=code if isinstance(code, str) and code.replace("_", "").isalnum() and len(code) <= 120 else "verification_probe_failed")
    finally:
        await manager.supervisor.close()
        report.update(elapsed_seconds=round(time.monotonic() - started, 2), cleanup_pending=bool(manager.supervisor.pending()))
        try:
            assert probe_binding(manager, batch) == binding, "verification_binding_changed"
            runner.require_idle(manager)
            report["binding_and_controls_unchanged"] = True
        except Exception:
            report.update(phase="failed", binding_failure=True)
        atomic_json(destination, report)
    return {key: report.get(key) for key in ("phase", "task_id", "elapsed_seconds", "failure_code", "cleanup_pending", "binding_and_controls_unchanged", "checks")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "check", "terminal"])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    batch = runner.batch_path(args.batch)
    if not args.execute:
        print(json.dumps({"command": args.command, "execute": False, "model_calls": 0, "sap_calls": 0}))
        return 0
    manager = WorkBuddyManager(ROOT)
    if args.command == "prepare":
        result = prepare(manager, batch)
    elif args.command == "check":
        value = check_release(manager, batch)
        runner.require_idle(manager)
        result = {"phase": "passed", "environment_digest": value["environment_digest"], "model_calls": 0, "sap_calls": 0}
    else:
        result = asyncio.run(terminal(manager, batch))
    print(json.dumps(result, ensure_ascii=False))
    return int(result["phase"] not in {"prepared", "passed"} or result.get("cleanup_pending", False))


if __name__ == "__main__":
    raise SystemExit(main())
