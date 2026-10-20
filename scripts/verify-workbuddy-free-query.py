"""Opt-in, isolated Broker-only validation. Import/dry-run performs no I/O.

Use a new directory per attempt. Never reclassify saved runs or use tool
arguments as terminal output. No automatic retry, enabling or publication.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from workbuddy_validation_cases import CASES, checked_plan, checked_result, failed_attempt, frozen_runtime_binding, query
from workbuddy_validation_diagnostics import rejected_plan_diagnostic

ROOT = Path(__file__).resolve().parents[1]


async def verify(arguments):
    from sap_business_agents_platform.app import create_app
    from sap_business_agents_platform.config import Settings
    from sap_business_agents_platform.models import RunCreate, RunMode
    from sap_business_agents_platform.runtime_policy import OPERATIONS
    from sap_business_agents_platform.workbuddy_environment import atomic_json, digest, file_hash

    directory = Path(arguments.data_root).resolve()
    from runtime_validation_cases import active_adapter
    scoped = active_adapter()
    allowed = (ROOT / ".local-data/workbuddy-validation").resolve()
    if scoped:
        allowed = scoped.batch.parent.resolve()
    if directory == allowed or not directory.is_relative_to(allowed) or directory.exists():
        raise ValueError("verification_fresh_isolated_directory_required")
    batch = getattr(arguments, "batch", None)
    if batch:
        batch = Path(batch).resolve()
        if batch == allowed or not batch.is_relative_to(allowed) or not directory.is_relative_to(batch):
            raise ValueError("verification_batch_directory_invalid")
        from workbuddy_live_cases.live_guard import check_batch, configure, attach_validation_monitor
    directory.mkdir(parents=True)
    destination = directory / "report.json"
    report = {"phase": "preflight", "case": arguments.case, "sap_calls": 0,
              "qualified": False, "platform_status_overridden": False}
    atomic_json(destination, report)
    settings = replace(Settings.from_env(ROOT), data_root=directory / "data", draft_root=directory / "drafts")
    if batch:
        from workbuddy_validation_auth import seed_isolated_authentication
        report["authentication_proof"] = seed_isolated_authentication(settings, batch)
        atomic_json(destination, report)
    app = create_app(settings)
    manager, store = app.state.sdk_manager.workbuddy, app.state.store
    try:
        # This checks saved state only, not authentication or model probes.
        admission = manager.snapshot()
        required = set(OPERATIONS) - {"free_query", "sample_discovery", "acceptance_baseline", "acceptance_free_query"}
        modes = lambda op: {"bounded", "trusted_local"} if op == "review_agent_feedback" else (
            {"trusted_local"} if op == "workflow_authoring.v2" else {"bounded"})
        missing = sorted(op for op in required if admission["operation_capabilities"].get(op, {}).get("status") != "validated"
                         or not modes(op).issubset(admission["operation_capabilities"].get(op, {}).get("permission_modes", [])))
        report["missing_operations"] = missing
        from sap_business_agents_platform.workbuddy_validation import legacy_fixture_gate
        if (missing and not legacy_fixture_gate(manager, {'free_query'})) or admission.get("enabled") or manager.supervisor.pending():
            raise ValueError("verification_prerequisites_not_ready")
        release = manager.environment.release()
        if file_hash(Path(release["directory"]) / release["worker"]) != file_hash(
                ROOT / "src/sap_business_agents_platform/workbuddy_worker.py"):
            raise ValueError("verification_worker_upgrade_required")
        if batch:
            snapshot = check_batch(manager, batch)
            configure(batch, {"free_query"}, {"bounded"})
            attach_validation_monitor(manager, snapshot, directory / "safe-events.jsonl")
        else:
            snapshot = frozen_runtime_binding(manager.runtime_snapshot(require_enabled=False))
        original = manager.supervisor.run
        owned, schemas = [], []

        async def guarded_run(**kwargs):
            if kwargs["snapshot"] != snapshot or kwargs["operation"] != "free_query" or kwargs.get("mode", "bounded") != "bounded":
                raise ValueError("verification_binding_changed")
            owned.append(kwargs["task_id"])
            handler = kwargs["tool_handler"]

            async def guard(name, arguments):
                if batch:
                    check_batch(manager, batch)
                if name == "sap_skill_execute":
                    if batch:
                        raise ValueError("verification_read_scope_rejected")
                    return {"ok": False, "code": "verification_read_scope_rejected"}
                if name == "sap_query_execute":
                    try:
                        checked_plan(arguments.get("plan"), case=arguments_case, purchase_order=arguments_po,
                                     schema_results=schemas)
                    except ValueError as error:
                        diagnostic = rejected_plan_diagnostic(arguments.get("plan"), case=arguments_case,
                                                              purchase_order=arguments_po)
                        report.setdefault("scope_rejections", []).append(diagnostic)
                        atomic_json(destination, report)
                        store.append_event(kwargs["task_id"], "verification_scope_rejected", diagnostic)
                        if batch:
                            raise
                        return {"ok": False, "code": getattr(error, "code", "verification_read_scope_rejected")}
                    report["sap_calls"] += 1
                result = await handler(name, arguments)
                if name == "sap_schema_get":
                    schemas.append(result)
                return result

            arguments_case, arguments_po = arguments.case, arguments.purchase_order
            kwargs["tool_handler"] = guard

            def progress(kind, data):
                from sap_business_agents_platform.workbuddy_diagnostics import safe_progress
                safe = safe_progress(kind, data)
                if safe:
                    report.setdefault("native_diagnostics", []).append({"event": kind, **safe})
                    atomic_json(destination, report)
                store.append_event(kwargs["task_id"], kind, data)

            kwargs["emit"] = progress
            result = await original(**kwargs)
            if (result.get("actual_model") != snapshot.get("actual_model")
                    or result.get("model_identity_source") != "assistant_message"):
                raise ValueError("verification_model_identity_mismatch")
            return result

        manager.supervisor.run = guarded_run
        await app.state.plugin_manager.start()
        run_id = "wb_validation_" + uuid.uuid4().hex[:16]
        question = query(arguments.case, arguments.purchase_order)
        if batch and not scoped:
            frozen = json.loads((batch / "freeze.json").read_text(encoding="utf-8"))
            parameters = frozen["case_parameters"]["free_query"][arguments.case]
            if parameters["purchase_order"] != arguments.purchase_order:
                raise ValueError("verification_case_parameters_changed")
            question = parameters["query"]
        store.create_run(run_id, RunCreate(mode=RunMode.free_query, query=question), runtime=snapshot)
        report.update(run_id=run_id, phase="running", runtime=snapshot,
                      environment_digest=snapshot["environment_digest"])
        atomic_json(destination, report)
        try:
            with manager.supervisor.verification(snapshot, {"free_query"}):
                await app.state.coordinator._execute_workbuddy_harness(run_id)
            record = store.get_run(run_id)
            proof = checked_result(record, store.list_harness_tool_calls(run_id), case=arguments.case)
            if not owned:
                raise ValueError("verification_native_job_missing")
            for task_id in owned:
                job = json.loads((manager.environment.root / "jobs" / (digest(task_id) + ".json")).read_text(encoding="utf-8"))
                if job.get("status") != "completed" or not job.get("cleanup_complete") or not job.get("validation_job"):
                    raise ValueError("verification_native_cleanup_or_terminal_missing")
            if proof["qualifies_operation"]:
                manager.record_operation_validation(snapshot, "free_query", job_id=owned[-1],
                    permission_modes=["bounded"], evidence={"kind": "live_validation", "platform_contract_passed": True,
                    "checks": proof, "live_readonly_broker": True})
            report.update(phase="passed", qualified=proof["qualifies_operation"], checks=proof)
        except Exception as error:
            report.update(failed_attempt(error, store.get_run(run_id)))
    except Exception as error:
        report.update(phase="not_run", failure_code=str(error) if str(error).startswith("verification_")
                      and str(error).replace("_", "").isalnum() else "verification_preflight_failed")
    finally:
        await manager.supervisor.close()
        await app.state.plugin_manager.stop()
        report["cleanup_pending"] = bool(manager.supervisor.pending())
        if batch:
            try:
                check_batch(manager, batch)
            except Exception as error:
                report.update(phase="failed", binding_failure=str(error) if isinstance(error, AssertionError) else type(error).__name__)
        atomic_json(destination, report)
    print(json.dumps({"report": str(destination), "phase": report["phase"], "qualified": report["qualified"]}))
    return 0 if report["phase"] == "passed" and not report["cleanup_pending"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--purchase-order", default="4500001466")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--batch", help="Private continuation batch; reuse its verified frozen Runtime")
    parser.add_argument("--execute", action="store_true", help="Explicitly authorize this one Broker-only live case")
    arguments = parser.parse_args()
    if not re.fullmatch(r"[0-9]{10}", arguments.purchase_order):
        parser.error("purchase-order must contain exactly 10 digits")
    if not arguments.execute:
        print(json.dumps({"execute": False, "case": arguments.case, "query": query(arguments.case, arguments.purchase_order)}, ensure_ascii=False))
        return 0
    return asyncio.run(verify(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
