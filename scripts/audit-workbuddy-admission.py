"""Read-only three-layer inventory. Never activates policy or grants PASS."""
import argparse
import json
from pathlib import Path
from sap_business_agents_platform.workbuddy_environment import WorkBuddyEnvironment, file_hash
from sap_business_agents_platform.workbuddy_integration import binding, CORE
from sap_business_agents_platform import runtime_validation_qualification as quality


def inspect(repository, historical_index):
    environment = WorkBuddyEnvironment(repository)
    original = environment.state_path.read_bytes()
    state, release = environment.state(), environment.release()
    index = json.loads(Path(historical_index).read_text(encoding="utf-8"))
    rows, seen, operations = [], {}, set()
    for case in index["cases"]:
        identifier = case["original_record"]
        record = state.get("validation_ledger", {}).get(identifier)
        try:
            if not record or not quality.valid(record) or record.get("kind") != "case_pass":
                raise ValueError("historical_record_invalid")
            if identifier not in seen:
                seen[identifier] = quality.verify_pass(environment, record)
            if not seen[identifier]:
                raise ValueError("historical_evidence_invalid")
            row = {"case_id": case["case_id"], "original_record": identifier,
                "status": "historical_business_pass_verified", "operations": record["binding"]["operations"],
                "owned_jobs": record["owned_jobs"], "source_snapshot": record["source_snapshot"]["digest"]}
            operations.update(row["operations"])
        except Exception as error:
            row = {"case_id": case["case_id"], "original_record": identifier,
                   "status": "evidence_missing", "code": getattr(error, "code", type(error).__name__)}
        rows.append(row)
    check = state.get("models", {}).get(state.get("default_model_id"), {})
    return {"state_unchanged": original == environment.state_path.read_bytes(),
        "state_sha256": file_hash(environment.state_path), "target_sdk_binding": binding(release),
        "historical_cases": rows, "historical_operations_verified": len(operations),
        "sdk_integration": {"status": "audit_pending", "required_core_evidence": list(CORE),
            "note": "Historical SDK jobs may support transport certification, never cross-model business PASS."},
        "model_check": {"model": state.get("default_model_id"), "compatible": check.get("compatible"),
            "contract": check.get("probe_contract", "legacy"), "check_digest": check.get("check_digest"),
            "upgrade_to_new_probe": False},
        "production_policy_activated": False, "qualification_writes": 0, "model_calls": 0, "sap_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--historical-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.repository, args.historical_index)
    # Generated audit artifact only. No state or historical mutations.
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("historical_operations_verified", "state_unchanged",
        "qualification_writes", "model_calls", "sap_calls")}))
