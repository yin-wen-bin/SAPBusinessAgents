from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase, canonical_hash, agent_execution_digest,
    validate_direct_baseline, compare_semantic_results,
)
from scripts.run_three_stage_campaign import (
    _load,
    _validate_campaign,
    _validate_frontend_result,
    _validate_skill_gates,
    _validate_case_expectations,
)


JsonObject = dict[str, Any]


def _verify_case_artifact(entry: JsonObject, artifact: JsonObject, root: Path) -> list[JsonObject]:
    """Recompute comparisons from frozen artifacts, never trust a PASS label."""
    if not entry.get("agent_snapshot"):
        return [{"code": "candidate_snapshot_required"}]
    try:
        manifest = _load((root / entry["agent_snapshot"]).resolve())
        rules_source = ((root / entry["rules_source"]).read_text(encoding="utf-8")
                        if entry.get("rules_source") else None)
        if (manifest.get("id") != entry["agent_id"] or manifest.get("version") != entry["agent_version"]
                or agent_execution_digest(manifest, rules_source) != entry["agent_execution_digest"]):
            return [{"code": "candidate_snapshot_drift"}]
        raw_contract = manifest["execution"]["acceptance"]
        if canonical_hash(raw_contract) != entry["acceptance_contract_digest"]:
            return [{"code": "acceptance_contract_digest_drift"}]
        contract = {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): value
                    for key, value in raw_contract.items()}
        contract["decimal_metrics"] = raw_contract.get("decimalMetricIds", [])
        case = CanonicalTestCase.from_dict(_load((root / entry["case"]).resolve()))
        baseline_payload = _load((root / entry["baseline"]).resolve())
        baseline = validate_direct_baseline(baseline_payload, case)
        _validate_case_expectations(entry, baseline)
        hashes = artifact.get("hashes") or {}
        if hashes.get("case_input_hash") != canonical_hash(case.input):
            return [{"code": "case_input_drift"}]
        if hashes.get("codex_direct_baseline_hash") != canonical_hash(baseline):
            return [{"code": "baseline_digest_drift"}]
        for stage in ("fixed_agent", "free_query"):
            if stage not in entry["required_stages"]:
                continue
            observed = artifact.get(stage) or {}
            normalized = observed.get("normalized_result")
            if not observed.get("run_id") or observed.get("status") not in {"completed", "inconclusive"}:
                return [{"code": "required_stage_not_executed", "stage": stage}]
            if not isinstance(normalized, dict) or hashes.get(stage + "_hash") != canonical_hash(normalized):
                return [{"code": "stage_result_digest_drift", "stage": stage}]
            comparison = compare_semantic_results(baseline, normalized, contract)
            if comparison.verdict != "MATCH":
                return [{"code": "stage_comparison_not_match", "stage": stage}]
        anchors = artifact.get("source_anchors") or {}
        if (anchors.get("verdict") != "PASS" or not anchors.get("before")
                or anchors.get("before") != anchors.get("after")
                or anchors.get("baseline_hash") != canonical_hash(baseline_payload)):
            return [{"code": "source_anchor_verification_missing"}]
        from scripts.acceptance_source_anchors import summarize
        frozen_anchors = []
        for phase in ("before", "after"):
            if not anchors.get(phase + "_artifact"):
                return [{"code": "source_anchor_artifact_missing"}]
            observed_anchor = _load(Path(anchors[phase + "_artifact"]))
            if canonical_hash(observed_anchor) != anchors.get(phase + "_artifact_hash"):
                return [{"code": "source_anchor_artifact_drift"}]
            if canonical_hash(observed_anchor.get("sources")) != observed_anchor.get("observed"):
                return [{"code": "source_anchor_artifact_drift"}]
            expected_sources = [{name: source.get("source_snapshot_hash") if name == "rows_hash" else source.get(name)
                                 for name in ("source_id", "schema_hash", "query_hash", "rows_hash", "row_count", "stable_order_by")}
                                for source in baseline_payload.get("sources", [])]
            if (not expected_sources or baseline_payload.get("supplemental_sources")
                    or canonical_hash(expected_sources) != observed_anchor.get("expected")):
                return [{"code": "source_anchor_coverage_mismatch"}]
            frozen_anchors.append(observed_anchor)
        if summarize(baseline_payload, *frozen_anchors)["verdict"] != "PASS":
            return [{"code": "sap_source_changed_during_acceptance"}]
    except (KeyError, OSError, ValueError, TypeError):
        return [{"code": "campaign_frozen_artifact_invalid"}]
    return []


def _write(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def verify_campaign(campaign_path: Path, state_path: Path) -> JsonObject:
    campaign = _load(campaign_path)
    if campaign.get("schema_version") != "2.0":
        raise ValueError("Agent campaign certification requires Campaign v2")
    entries = _validate_campaign(campaign)
    state = _load(state_path)
    expected_campaign_hash = canonical_hash(campaign)
    if state.get("campaign_hash") != expected_campaign_hash:
        raise ValueError("campaign state does not match the immutable Campaign definition")

    case_state = state.get("cases") if isinstance(state.get("cases"), dict) else {}
    aggregate_state = state.get("agents") if isinstance(state.get("agents"), dict) else {}
    certified_agents: list[JsonObject] = []
    blockers: list[JsonObject] = []
    campaign_root = campaign_path.parent

    for agent in campaign["agents"]:
        agent_id = str(agent["agent_id"])
        version = str(agent["agent_version"])
        agent_key = f"{agent_id}@{version}"
        aggregate = aggregate_state.get(agent_key) if isinstance(aggregate_state.get(agent_key), dict) else {}
        agent_blockers: list[JsonObject] = []
        if aggregate.get("verdict") != "PASS":
            agent_blockers.append(
                {
                    "code": "campaign_agent_aggregate_not_pass",
                    "missing_coverage_tags": aggregate.get("missing_coverage_tags") or [],
                    "failed_mandatory_cases": aggregate.get("failed_mandatory_cases") or [],
                }
            )

        gate_snapshot = _validate_skill_gates(agent, campaign_root)
        case_certificates: list[JsonObject] = []
        covered: set[str] = set()
        for entry in entries:
            if entry["agent_id"] != agent_id or entry["agent_version"] != version:
                continue
            case_id = str(entry["case_id"])
            key = f"{agent_key}:{case_id}"
            observed = case_state.get(key) if isinstance(case_state.get(key), dict) else {}
            acceptance_path = Path(str(observed.get("acceptance_path") or ""))
            if observed.get("verdict") != "PASS" or not acceptance_path.is_file():
                if entry["kind"] == "mandatory_live":
                    agent_blockers.append(
                        {"code": "mandatory_case_not_pass", "case_id": case_id}
                    )
                continue
            artifact = _load(acceptance_path)
            hashes = artifact.get("hashes") if isinstance(artifact.get("hashes"), dict) else {}
            if artifact.get("verdict") != "PASS":
                agent_blockers.append(
                    {"code": "acceptance_artifact_not_pass", "case_id": case_id}
                )
                continue
            if hashes.get("agent_execution_digest") != agent["agent_execution_digest"]:
                agent_blockers.append(
                    {"code": "candidate_execution_digest_drift", "case_id": case_id}
                )
                continue
            if hashes.get("acceptance_contract_digest") != agent["acceptance_contract_digest"]:
                agent_blockers.append(
                    {"code": "acceptance_contract_digest_drift", "case_id": case_id}
                )
                continue
            verification_issues = _verify_case_artifact(entry, artifact, campaign_root)
            if verification_issues:
                agent_blockers.extend({**issue, "case_id": case_id} for issue in verification_issues)
                continue
            fixed = artifact.get("fixed_agent") if isinstance(artifact.get("fixed_agent"), dict) else {}
            if "frontend" in entry["required_stages"]:
                _validate_frontend_result(
                    (campaign_root / str(entry["frontend_result"])).resolve(),
                    entry=entry,
                    fixed_run_id=str(fixed.get("run_id") or ""),
                )
            case_certificates.append(
                {
                    "case_id": case_id,
                    "kind": entry["kind"],
                    "coverage_tags": sorted(str(item) for item in entry["coverage_tags"]),
                    "required_stages": list(entry["required_stages"]),
                    "acceptance_digest": canonical_hash(artifact),
                    "reuse_fingerprint": hashes.get("reuse_fingerprint"),
                    "fixed_agent_run_id": fixed.get("run_id"),
                }
            )
            if entry["kind"] == "mandatory_live":
                covered.update(entry["coverage_tags"])
            elif entry["kind"] == "deterministic_fixture":
                covered.update(set(entry["coverage_tags"]) & set(agent.get("fixture_allowed_coverage_tags", [])))

        missing = sorted(set(agent["required_coverage_tags"]) - covered)
        if missing:
            agent_blockers.append({"code": "test_data_gap", "missing_coverage_tags": missing})

        status = "PASS" if not agent_blockers else "BLOCKED"
        certified_agents.append(
            {
                "agent_id": agent_id,
                "agent_version": version,
                "agent_execution_digest": agent["agent_execution_digest"],
                "acceptance_contract_digest": agent["acceptance_contract_digest"],
                "skill_gate_snapshot_digest": canonical_hash(gate_snapshot),
                "cases": case_certificates,
                "verdict": status,
                "blockers": agent_blockers,
            }
        )
        blockers.extend(
            {"agent_id": agent_id, "agent_version": version, **item}
            for item in agent_blockers
        )

    payload: JsonObject = {
        "schema_version": "1.0",
        "campaign_hash": expected_campaign_hash,
        "campaign_state_hash": canonical_hash(state),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "agents": certified_agents,
        "verdict": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
    }
    payload["certification_digest"] = canonical_hash(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a completed Campaign v2 before Agent lifecycle publication."
    )
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify_campaign(Path(args.campaign).resolve(), Path(args.state).resolve())
    _write(Path(args.output).resolve(), result)
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "certification_digest": result["certification_digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
