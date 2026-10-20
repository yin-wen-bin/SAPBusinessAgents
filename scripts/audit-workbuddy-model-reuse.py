"""Explicit read-only production audit; all rehearsal writes are isolated.

Run with this candidate's src on PYTHONPATH. No SDK/manager instantiation.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.workbuddy_auth_recovery import _document, applicable_receipt
from sap_business_agents_platform.workbuddy_auth_reuse_audit import propose
from sap_business_agents_platform.workbuddy_environment import digest, file_hash
from sap_business_agents_platform.workbuddy_integration import _artifact_bytes, INFRASTRUCTURE_FILES, PROTOCOL_FILES, manifest


def audit(workspace, output):
    workspace, output = workspace.resolve(), output.resolve()
    local = workspace / ".local-data"
    # Never let a report replace runtime/auth/config state or its ancestors.
    protected_roots = [local / "runtimes", local / "sdk-runtimes", workspace / "config",
                       workspace / "src", workspace / "site"]
    if (output.exists() or not output.is_relative_to(local)
            or output == local or any(output.is_relative_to(p) or p.is_relative_to(output) for p in protected_roots)):
        raise ValueError("A new isolated report directory under .local-data is required")
    root = local / "runtimes/workbuddy"
    paths = [root / "state.json", local / "sdk-runtimes/authentication.json",
             local / "sdk-runtimes/default.json", root / "unfinished-jobs.json", root / "job-index-protocol.json"]
    paths += sorted((workspace / "config").rglob("*"))
    paths = [p for p in paths if p.is_file()]
    baseline = {str(p.relative_to(workspace)): file_hash(p) for p in paths}
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    authentication = json.loads((local / "sdk-runtimes/authentication.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True)
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    write(output / "protected-before.json", baseline)
    report = {"contract": "sapba.workbuddy.auth-reuse-audit/1", "proposal_only": True,
              "current_revision": state.get("authentication_revision"), "models": [],
              "model_calls": 0, "sap_calls": 0, "production_writes": 0}
    live = SimpleNamespace(root=root)
    for model in state.get("models", {}):
        candidates = [r for r in state.get("authentication_recoveries", {}).values()
                      if model in r.get("model_checks", {})]
        if not candidates:
            report["models"].append({"model": model, "decision": "blocked", "reason": "original_identity_reference_missing"})
            continue
        old = candidates[-1]
        if old.get("id") != digest({k: v for k, v in old.items() if k != "id"}):
            report["models"].append({"model": model, "decision": "blocked", "reason": "reference_receipt_invalid"})
            continue
        # Copy ONLY verified safe metadata, never a virtualenv or auth cache.
        isolated_root = output / ("evidence-" + digest(model)[:12])
        isolated_root.mkdir()
        env = SimpleNamespace(root=isolated_root)
        try:
            refs = [old["original_identity"], old["model_checks"][model]]
            for reference in refs:
                content = _artifact_bytes(live, reference)
                target = (isolated_root / reference["path"]).resolve()
                if not target.is_relative_to(isolated_root): raise ValueError("invalid artifact path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            check = _document(env, old["model_checks"][model])
            job_path = root / "jobs" / (digest(check["source_job_id"]) + ".json")
            job = json.loads(job_path.read_text(encoding="utf-8"))
            if digest(job) != check["source_job_digest"]: raise ValueError("source job changed")
            write(isolated_root / "jobs" / job_path.name, job)
            result = propose(env, state, authentication, original_identity=old["original_identity"],
                             original_check=old["model_checks"][model])
            row = {"model": model, "decision": result["decision"], "reason": result["reason"]}
            if result["decision"] == "proposal_only":
                current = isolated_root / "current-identity.json"
                write(current, result["current_identity_artifact"])
                receipt = result["receipt_fields"]
                receipt["current_identity"] = {"path": current.name, "sha256": file_hash(current)}
                receipt["id"] = digest(receipt)
                simulated = copy.deepcopy(state)
                simulated.setdefault("authentication_recoveries", {})[receipt["id"]] = receipt
                accepted = applicable_receipt(env, simulated, model=model,
                    source_revision=check["authentication_revision"], check_digest=check["check_digest"])
                if accepted != receipt: raise ValueError("isolated rehearsal rejected")
                write(isolated_root / "proposal.json", {"receipt": receipt, "requirements": result["requirements"]})
                write(isolated_root / "state-copy.json", simulated)
                row.update(source_revision=check["authentication_revision"], target_revision=state["authentication_revision"],
                           isolated_rehearsal=True, original_history_unchanged=simulated["model_check_history"] == state["model_check_history"],
                           receipt_id=receipt["id"])
            report["models"].append(row)
        except (KeyError, ValueError, OSError) as exc:
            report["models"].append({"model": model, "decision": "blocked", "reason": "source_evidence_unavailable"})
    candidate = Path(__file__).resolve().parents[1] / "src/sap_business_agents_platform"
    original = workspace / "src/sap_business_agents_platform"
    report["dependency_changes"] = {
        "protocol": [p for p in PROTOCOL_FILES if file_hash(candidate/p) != file_hash(original/p)],
        "infrastructure": [p for p in INFRASTRUCTURE_FILES if file_hash(candidate/p) != file_hash(original/p)]}
    after = {str(p.relative_to(workspace)): file_hash(p) for p in paths}
    report["protected_unchanged"] = baseline == after
    report["binding_must_be_rechecked_before_application"] = True
    write(output / "protected-after.json", after)
    write(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if baseline != after:
        raise RuntimeError("Protected state changed concurrently; audit is not applicable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.workspace, args.output)
