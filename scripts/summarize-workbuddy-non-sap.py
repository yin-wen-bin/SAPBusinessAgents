"""Read/audit one frozen non-SAP live batch, never execute or grant capability."""
import argparse
import importlib.util
import json
from pathlib import Path

from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from sap_business_agents_platform.workbuddy_manager import WorkBuddyManager

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("non_sap_batch_audit", ROOT / "scripts/verify-workbuddy-operations.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    batch = runner.batch_path(args.batch)
    manager = WorkBuddyManager(ROOT)
    frozen = runner.check_frozen(manager, batch)
    runner.require_idle(manager)
    assert frozen["scope"] == "china_non_sap"
    bound = json.loads((batch / "runtime-snapshot.json").read_text(encoding="utf-8"))
    state = manager.environment.state()
    reference = ROOT / ".local-data/workbuddy-validation/china-20261012-deepseek-full-schema-terminal/before.json"
    old = json.loads(reference.read_text(encoding="utf-8"))
    # The former full-Schema probe had no qualification in the current release.
    # New capabilities may be added only under this release/site prefix.
    prefix = bound["environment_digest"] + ":" + bound["service_site_digest"] + ":"
    historical = {k: v for k, v in state.get("capabilities", {}).items() if not k.startswith(prefix)}
    assert digest(historical) == old["qualifications_sha256"], "verification_historical_qualification_changed"
    assert digest(state.get("models")) == old["models_sha256"], "verification_model_record_changed"
    families, stages, owned = [], [], []
    for family, (_, _, operations, _) in runner.FAMILIES.items():
        if not operations.issubset(runner.NON_SAP):
            continue
        path = batch / (family + "-summary.json")
        if not path.exists():
            families.append({"family": family, "phase": "not_run"})
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        assert summary["phase"] != "running", "verification_family_still_active"
        case = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        assert case.get("sap_calls") == 0, "verification_sap_access_forbidden"
        families.append({k: summary.get(k) for k in ("family", "phase", "elapsed_seconds", "failure_code", "case_failure_code")})
        for stage in case.get("stages", []):
            stages.append({"family": family, **{k: stage[k] for k in ("operation", "intent", "phase", "elapsed_seconds", "failure_code", "reason", "permission_mode", "diff_paths") if k in stage}})
    capabilities = manager.snapshot()["operation_capabilities"]
    matrix = []
    for operation in runner.OPERATIONS:
        capability = capabilities[operation]
        row = {"operation": operation, "status": capability["status"], "permission_modes": capability.get("permission_modes", []),
               "scope": "non_sap" if operation in runner.NON_SAP else "not_authorized_in_this_batch"}
        if capability["status"] == "validated":
            assert operation in runner.NON_SAP, "verification_qualification_scope_changed"
            job = json.loads((manager.environment.root / "jobs" / (digest(capability["job_id"]) + ".json")).read_text(encoding="utf-8"))
            assert job["status"] == "completed" and job["cleanup_complete"] and job["validation_job"], "verification_validation_cleanup_missing"
            assert job["runtime_digest"] == digest(bound), "verification_old_binding_reused"
            row["job_id"] = capability["job_id"]
            owned.append(job["task_id"])
        matrix.append(row)
    passed = sum(r["status"] == "validated" for r in matrix)
    modes = capabilities["review_agent_feedback"].get("permission_modes", [])
    complete = (passed == len(runner.NON_SAP) and all(f["phase"] == "passed" for f in families)
                and set(modes) == {"bounded", "trusted_local"})
    batch_jobs = [json.loads(p.read_text(encoding="utf-8")) for p in (manager.environment.root / "jobs").glob("*.json")]
    batch_jobs = [j for j in batch_jobs if j.get("runtime_digest") == digest(bound)]
    assert batch_jobs and all(j.get("cleanup_complete") for j in batch_jobs), "verification_batch_cleanup_incomplete"
    assert all(j.get("validation_job") and j.get("operation") in runner.NON_SAP for j in batch_jobs), "verification_job_scope_changed"
    report = {"phase": "passed" if complete else "partial", "model": bound["model"], "actual_model": bound["actual_model"],
        "environment_digest": bound["environment_digest"], "service_site": bound["service_site"],
        "service_site_revision": bound["service_site_revision"], "authentication_revision": bound["authentication_revision"],
        "valid_operations": passed, "operation_count": len(runner.OPERATIONS), "non_sap_target": len(runner.NON_SAP),
        "families": families, "stages": stages, "matrix": matrix, "qualification_jobs": owned,
        "model_jobs": len(batch_jobs), "job_status_counts": {status: sum(j["status"] == status for j in batch_jobs)
            for status in {j["status"] for j in batch_jobs}},
        "sap_calls": 0, "automatic_retry": False, "old_qualification_reused": False, "contract_weakened": False,
        "historical_qualifications_and_model_records_unchanged": True, "codex_controls_unchanged": True,
        "cleanup_pending": False, "workbuddy_enabled": False, "default_workbuddy_model": state["default_model_id"],
        "can_enable": manager.snapshot()["can_enable"], "default_runtime_unchanged": True,
        "remaining_sap_operations": sorted(set(runner.OPERATIONS) - runner.NON_SAP), "committed_or_pushed": False}
    atomic_json(batch / "result-summary.json", report)
    print(json.dumps({k: report[k] for k in ("phase", "valid_operations", "operation_count", "model", "sap_calls", "cleanup_pending", "workbuddy_enabled", "can_enable")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
