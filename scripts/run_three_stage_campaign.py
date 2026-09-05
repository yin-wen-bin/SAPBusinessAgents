from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    canonical_hash,
    runtime_acceptance_identity,
    validate_direct_baseline,
)


JsonObject = dict[str, Any]
_CASE_KINDS = {"mandatory_live", "optional_live", "deterministic_fixture"}
_STAGES = {"baseline", "free_query", "fixed_agent", "frontend"}


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_sensitive_case_inputs(enabled: bool) -> dict[str, dict[str, str]]:
    if not enabled:
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("--sensitive-input-stdin requires a JSON object on stdin")
    value = json.loads(raw)
    cases = value.get("cases") if isinstance(value, dict) else None
    if not isinstance(cases, dict):
        raise ValueError("campaign sensitive input must contain a cases object")
    result: dict[str, dict[str, str]] = {}
    for case_key, fields in cases.items():
        if not isinstance(case_key, str) or not case_key or not isinstance(fields, dict):
            raise ValueError("campaign sensitive case entries must be named objects")
        normalized: dict[str, str] = {}
        for field, item in fields.items():
            if not isinstance(field, str) or not isinstance(item, str) or not item.strip():
                raise ValueError("campaign sensitive inputs must be non-blank strings")
            normalized[field] = item.strip()
        if not normalized:
            raise ValueError("campaign sensitive case entry must not be empty")
        result[case_key] = normalized
    return result


def _write(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_campaign(value: JsonObject) -> list[JsonObject]:
    version = str(value.get("schema_version") or "")
    if version == "1.0":
        return _validate_campaign_v1(value)
    if version != "2.0":
        raise ValueError("campaign schema_version must be 1.0 or 2.0")
    agents = value.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("campaign agents must be a non-empty array")
    flattened: list[JsonObject] = []
    seen: set[tuple[str, str, str]] = set()
    for agent_index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"campaign agents[{agent_index}] must be an object")
        required = {
            "module", "agent_id", "agent_version", "agent_execution_digest",
            "acceptance_contract_digest", "prerequisite_skill_gates",
            "required_coverage_tags", "runtime_snapshot", "agent_catalog_digest",
            "cases",
        }
        allowed = required | {
            "agent_snapshot", "rules_source", "fixture_allowed_coverage_tags",
        }
        if not required.issubset(agent) or not set(agent).issubset(allowed):
            raise ValueError(f"campaign agents[{agent_index}] has unexpected or missing fields")
        agent_id = str(agent.get("agent_id") or "")
        agent_version = str(agent.get("agent_version") or "")
        if not agent_id or not re.fullmatch(r"\d+\.\d+\.\d+", agent_version):
            raise ValueError(f"campaign agents[{agent_index}] has an invalid Agent identity")
        for field in ("agent_execution_digest", "acceptance_contract_digest"):
            _require_sha256(agent.get(field), f"campaign agents[{agent_index}].{field}")
        _require_sha256(
            agent.get("agent_catalog_digest"),
            f"campaign agents[{agent_index}].agent_catalog_digest",
        )
        if not isinstance(agent.get("runtime_snapshot"), dict):
            raise ValueError(f"campaign agents[{agent_index}].runtime_snapshot must be an object")
        for field in (
            "prerequisite_skill_gates", "required_coverage_tags",
            "fixture_allowed_coverage_tags",
        ):
            if not isinstance(agent.get(field, []), list):
                raise ValueError(f"campaign agents[{agent_index}].{field} must be an array")
        cases = agent.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"campaign agents[{agent_index}].cases must be a non-empty array")
        for case_index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise ValueError(
                    f"campaign agents[{agent_index}].cases[{case_index}] must be an object"
                )
            case_required = {
                "case_id", "kind", "coverage_tags", "required_stages",
                "expected_business_status", "expected_source_complete",
                "expected_evidence_complete", "expected_business_complete",
                "expected_gap_codes", "case", "baseline", "output",
            }
            case_allowed = case_required | {"fixed_result", "frontend_result", "capacity_only"}
            if not case_required.issubset(case) or not set(case).issubset(case_allowed):
                raise ValueError(
                    f"campaign agents[{agent_index}].cases[{case_index}] has unexpected or missing fields"
                )
            case_id = str(case.get("case_id") or "")
            key = (agent_id, agent_version, case_id)
            if not case_id or key in seen:
                raise ValueError("campaign case identity is missing or duplicated")
            seen.add(key)
            if case.get("kind") not in _CASE_KINDS:
                raise ValueError(f"campaign case {case_id} has an invalid kind")
            stages = case.get("required_stages")
            if not isinstance(stages, list) or not stages or not set(stages).issubset(_STAGES):
                raise ValueError(f"campaign case {case_id} has invalid required_stages")
            if not {"baseline", "fixed_agent"}.issubset(stages):
                raise ValueError(f"campaign case {case_id} must require baseline and fixed_agent")
            if "capacity_only" in case and type(case["capacity_only"]) is not bool:
                raise ValueError("capacity_only must be boolean")
            if case.get("capacity_only") and (
                agent_id != "ar-collection"
                or case.get("coverage_tags") != ["batch_fifty_customers"]
                or set(stages) != {"baseline", "fixed_agent"}
                or case.get("kind") != "mandatory_live"
            ):
                raise ValueError("capacity_only is reserved for the AR 50-customer capacity case")
            if case.get("kind") in {"mandatory_live", "optional_live"} and "free_query" not in stages and not case.get("capacity_only"):
                raise ValueError(f"live campaign case {case_id} must require free_query")
            if "frontend" in stages and not str(case.get("frontend_result") or ""):
                raise ValueError(f"campaign case {case_id} must declare frontend_result")
            for field in ("coverage_tags", "expected_business_status", "expected_gap_codes"):
                raw = case.get(field)
                if not isinstance(raw, list) or any(not str(item).strip() for item in raw):
                    raise ValueError(f"campaign case {case_id}.{field} must be an identifier array")
            if not case["expected_business_status"]:
                raise ValueError(f"campaign case {case_id}.expected_business_status is required")
            for field in (
                "expected_source_complete", "expected_evidence_complete",
                "expected_business_complete",
            ):
                if not isinstance(case.get(field), bool):
                    raise ValueError(f"campaign case {case_id}.{field} must be boolean")
            flattened.append({**case, **{k: v for k, v in agent.items() if k != "cases"}})
        if not any(case.get("kind") == "mandatory_live" and not case.get("capacity_only")
                   and "free_query" in case["required_stages"] for case in cases):
            raise ValueError("each Agent requires a representative three-stage mandatory live case")
    return flattened


def _validate_campaign_v1(value: JsonObject) -> list[JsonObject]:
    entries = value.get("agents")
    if not isinstance(entries, list) or not entries:
        raise ValueError("campaign agents must be a non-empty array")
    seen: set[str] = set()
    result: list[JsonObject] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"campaign agents[{index}] must be an object")
        required = {"module", "agent_id", "case", "baseline", "output"}
        allowed = {*required, "fixed_result"}
        if not required.issubset(entry) or not set(entry).issubset(allowed):
            raise ValueError(f"campaign agents[{index}] has unexpected or missing fields")
        agent_id = str(entry.get("agent_id") or "")
        if not agent_id or agent_id in seen:
            raise ValueError(f"campaign agents[{index}].agent_id is missing or duplicated")
        seen.add(agent_id)
        result.append(dict(entry))
    return result


def _require_sha256(value: Any, label: str) -> None:
    text = str(value or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        raise ValueError(f"{label} must be a full SHA-256")


def _state_entry(state: JsonObject, key: str) -> JsonObject:
    cases = state.setdefault("cases", {})
    return cases.setdefault(
        key,
        {
            "phase": "pending", "attempts": 0, "baseline_hash": None,
            "free_run_id": None, "acceptance_path": None, "verdict": None,
            "last_error": None,
        },
    )


def _matched_free_run_id(artifact: JsonObject) -> str | None:
    free_result = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    comparison = free_result.get("comparison") if isinstance(free_result.get("comparison"), dict) else {}
    run_id = str(free_result.get("run_id") or "")
    return run_id if run_id and comparison.get("verdict") == "MATCH" else None


def _terminal_acceptance(artifact: JsonObject, expected_hash: str, *, require_free: bool = True) -> tuple[str, str] | None:
    verdict = str(artifact.get("verdict") or "")
    free = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    fixed = artifact.get("fixed_agent") if isinstance(artifact.get("fixed_agent"), dict) else {}
    free_comparison = free.get("comparison") if isinstance(free.get("comparison"), dict) else {}
    fixed_comparison = fixed.get("comparison") if isinstance(fixed.get("comparison"), dict) else {}
    run_id = str(free.get("run_id") or "")
    if (
        verdict not in {"PASS", "BLOCKED"}
        or (require_free and free_comparison.get("verdict") != "MATCH")
        or fixed_comparison.get("verdict") != "MATCH"
        or (require_free and free_comparison.get("expected_hash") != expected_hash)
        or fixed_comparison.get("expected_hash") != expected_hash
        or (require_free and not run_id)
        or (verdict == "BLOCKED" and not artifact.get("blocking_limitations"))
    ):
        return None
    return verdict, run_id


def _sap_metadata_fingerprint(baseline_payload: JsonObject) -> str:
    return canonical_hash(
        [
            {
                "source_id": source.get("source_id"),
                "schema_hash": source.get("schema_hash"),
                "query_hash": source.get("query_hash"),
            }
            for source in baseline_payload.get("sources") or []
            if isinstance(source, dict)
        ]
    )


def _reuse_fingerprint(
    entry: JsonObject,
    case: CanonicalTestCase,
    baseline: JsonObject,
    skill_snapshot: list[JsonObject],
    sap_metadata_fingerprint: str,
) -> tuple[str, JsonObject]:
    fields = {
        "case_input_hash": canonical_hash(case.input),
        "business_date": str(
            case.input.get("business_date") or case.input.get("as_of") or ""
        ),
        "codex_direct_baseline_hash": canonical_hash(baseline),
        "agent_execution_digest": entry.get("agent_execution_digest"),
        "acceptance_contract_digest": entry.get("acceptance_contract_digest"),
        "runtime_snapshot_hash": canonical_hash(
            runtime_acceptance_identity(entry.get("runtime_snapshot"))
        ),
        "agent_catalog_digest": entry.get("agent_catalog_digest"),
        "skill_gate_snapshot_hash": canonical_hash(skill_snapshot),
        "sap_metadata_fingerprint": sap_metadata_fingerprint,
    }
    return canonical_hash(fields), fields


def _validate_frontend_result(
    path: Path,
    *,
    entry: JsonObject,
    fixed_run_id: str,
) -> JsonObject:
    value = _load(path)
    required = {
        "verdict", "agent_id", "agent_version", "agent_execution_digest",
        "fixed_agent_run_id", "locales", "checks", "tested_at", "report_digest",
    }
    if set(value) != required:
        raise ValueError("frontend result has unexpected or missing fields")
    if value.get("verdict") != "PASS":
        raise ValueError("frontend acceptance is not PASS")
    for field in ("agent_id", "agent_version", "agent_execution_digest"):
        expected = entry.get(field)
        if value.get(field) != expected:
            raise ValueError(f"frontend acceptance {field} drifted")
    if value.get("fixed_agent_run_id") != fixed_run_id:
        raise ValueError("frontend acceptance does not refer to the fixed-Agent run")
    locales = value.get("locales")
    if not isinstance(locales, dict) or set(locales) != {"zh", "en"} or set(locales.values()) != {"PASS"}:
        raise ValueError("frontend acceptance must pass in zh and en")
    checks = value.get("checks")
    if not isinstance(checks, dict) or not checks or any(item is not True for item in checks.values()):
        raise ValueError("frontend acceptance checks must all pass")
    _require_sha256(value.get("report_digest"), "frontend acceptance report_digest")
    return value


def _validate_skill_gates(entry: JsonObject, campaign_root: Path) -> list[JsonObject]:
    snapshots: list[JsonObject] = []
    seen: set[str] = set()
    for raw in entry.get("prerequisite_skill_gates") or []:
        if not isinstance(raw, dict) or not str(raw.get("path") or ""):
            raise ValueError("prerequisite Skill gate must declare an artifact path")
        skill_id = str(raw.get("skill_id") or "")
        if not skill_id or skill_id in seen:
            raise ValueError("prerequisite Skill gate IDs must be present and unique")
        seen.add(skill_id)
        gate_path = (campaign_root / str(raw["path"])).resolve()
        if campaign_root != gate_path and campaign_root not in gate_path.parents:
            raise ValueError("prerequisite Skill gate path escapes the campaign root")
        gate = _load(gate_path)
        if gate.get("skill_id") != skill_id:
            raise ValueError(f"Skill gate {skill_id} artifact identity does not match")
        if gate.get("schema_version") != "2.0":
            raise ValueError(f"Skill gate {skill_id} must use independent gate schema 2.0")
        if gate.get("verdict") != "PASS":
            raise ValueError(f"Skill gate {gate.get('skill_id') or gate_path.name} is not PASS")
        for field in (
            "git_commit", "package_sha256", "profile_sha256", "metadata_sha256",
            "output_schema_sha256",
        ):
            expected = raw.get(field)
            if expected is not None and gate.get(field) != expected:
                raise ValueError(f"Skill gate {gate.get('skill_id')} {field} drifted")
        for field in ("baseline_hash", "comparison_hash"):
            _require_sha256(gate.get(field), f"Skill gate {skill_id}.{field}")
        for field in ("readonly_audit", "privacy_audit"):
            audit = gate.get(field)
            if not isinstance(audit, dict) or audit.get("verdict") != "PASS":
                raise ValueError(f"Skill gate {skill_id}.{field} is not PASS")
        independent = gate.get("independent_validation")
        if not isinstance(independent, dict):
            raise ValueError(f"Skill gate {skill_id}.independent_validation is missing")
        if independent.get("tested_skill_imported") is not False:
            raise ValueError(f"Skill gate {skill_id} imported the tested Skill")
        if independent.get("tested_skill_runtime_called") is not False:
            raise ValueError(f"Skill gate {skill_id} used the tested Skill as its baseline")
        if not str(independent.get("reader_id") or "").strip():
            raise ValueError(f"Skill gate {skill_id} independent reader identity is missing")
        _require_sha256(
            independent.get("reader_sha256"),
            f"Skill gate {skill_id}.independent_validation.reader_sha256",
        )
        for values, label in (
            (independent.get("metadata_sha256"), "metadata_sha256"),
            (independent.get("source_snapshot_hashes"), "source_snapshot_hashes"),
        ):
            if not isinstance(values, list) or not values:
                raise ValueError(
                    f"Skill gate {skill_id}.independent_validation.{label} is missing"
                )
            for index, value in enumerate(values):
                _require_sha256(
                    value,
                    f"Skill gate {skill_id}.independent_validation.{label}[{index}]",
                )
        if independent.get("complete_zero_sample_passed") is not True:
            raise ValueError(f"Skill gate {skill_id} has no complete zero proof")
        if independent.get("nonzero_sample_passed") is not True:
            raise ValueError(f"Skill gate {skill_id} has no nonzero proof")
        partitions = independent.get("partition_count")
        if type(partitions) is not int or partitions < 2:
            raise ValueError(f"Skill gate {skill_id} has no forced partition proof")
        snapshots.append({
            key: gate.get(key)
            for key in (
                "skill_id", "git_commit", "package_sha256", "profile_version",
                "profile_sha256", "metadata_sha256", "output_schema_sha256",
                "baseline_hash", "comparison_hash", "readonly_audit", "privacy_audit",
                "independent_validation", "verdict",
            )
        })
    return snapshots


def _validate_case_expectations(entry: JsonObject, baseline: JsonObject) -> None:
    if not entry.get("case_id"):
        return
    if baseline.get("business_status") not in set(entry["expected_business_status"]):
        raise ValueError(
            f"baseline business status does not satisfy case {entry['case_id']}"
        )
    for field in ("source_complete", "evidence_complete", "business_complete"):
        expected = entry[f"expected_{field}"]
        if baseline.get(field) is not expected:
            raise ValueError(f"baseline {field} does not satisfy case {entry['case_id']}")
    if {str(item) for item in baseline.get("evidence_gap_codes") or []} != {
        str(item) for item in entry["expected_gap_codes"]
    }:
        raise ValueError(
            f"baseline evidence gaps do not satisfy case {entry['case_id']}"
        )


def _sanitize_log(value: str) -> str:
    text = re.sub(
        r"(?i)(reference|secretref|payer|account|customer)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        value,
    )
    return re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", "[REDACTED-ACCOUNT]", text)[-20_000:]


def _record_failure_log(
    state_path: Path, key: str, completed: subprocess.CompletedProcess[str]
) -> JsonObject:
    # Arbitrary child output can contain unlabelled business values. Regex
    # redaction cannot prove it safe; retain diagnostics only as ciphertext.
    try:
        from scripts.direct_sap_read import write_encrypted_rows
    except ModuleNotFoundError:
        # Running this file directly places ``scripts`` (rather than the
        # repository root) on sys.path. Keep the installed/module and direct
        # CLI entry points equivalent.
        from direct_sap_read import write_encrypted_rows
    log = (completed.stdout or "") + "\n" + (completed.stderr or "")
    digest = "sha256:" + hashlib.sha256(log.encode("utf-8")).hexdigest()
    log_root = state_path.parent / ".private-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', key)}-{digest[7:19]}"
    if not log_path.exists():
        write_encrypted_rows(log_path, [{"diagnostic": log}])
    return {
        "error_code": "acceptance_subprocess_failed",
        "failed_stage": "case_acceptance",
        "sanitized_message": f"Acceptance subprocess exited with code {completed.returncode}.",
        "log_digest": digest,
        "private_log_path": str(log_path / "rows.ndjson.aesgcm"),
    }


def _aggregate_agents(campaign: JsonObject, entries: list[JsonObject], state: JsonObject) -> None:
    if campaign.get("schema_version") != "2.0":
        return
    aggregates: JsonObject = {}
    for agent in campaign["agents"]:
        agent_id = str(agent["agent_id"])
        version = str(agent["agent_version"])
        required_tags = {str(item) for item in agent["required_coverage_tags"]}
        fixture_allowed = {str(item) for item in agent.get("fixture_allowed_coverage_tags") or []}
        covered: set[str] = set()
        failures: list[str] = []
        for entry in entries:
            if entry["agent_id"] != agent_id or entry["agent_version"] != version:
                continue
            key = f"{agent_id}@{version}:{entry['case_id']}"
            case_state = (state.get("cases") or {}).get(key) or {}
            if entry["kind"] == "mandatory_live" and case_state.get("verdict") != "PASS":
                failures.append(key)
            if case_state.get("verdict") == "PASS" and (
                entry["kind"] == "mandatory_live"
                or (
                    entry["kind"] == "deterministic_fixture"
                    and set(entry["coverage_tags"]).issubset(fixture_allowed)
                )
            ):
                covered.update(str(item) for item in entry["coverage_tags"])
        missing_tags = sorted(required_tags - covered)
        aggregates[f"{agent_id}@{version}"] = {
            "verdict": "PASS" if not failures and not missing_tags else "BLOCKED",
            "required_coverage_tags": sorted(required_tags),
            "covered_tags": sorted(covered),
            "missing_coverage_tags": missing_tags,
            "failed_mandatory_cases": failures,
            "reason_code": "test_data_gap" if missing_tags else None,
        }
    state["agents"] = aggregates


def run(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    campaign_path = Path(args.campaign).resolve()
    campaign = _load(campaign_path)
    sensitive_case_inputs = _read_sensitive_case_inputs(args.sensitive_input_stdin)
    entries = _validate_campaign(campaign)
    state_path = Path(args.state).resolve()
    state = _load(state_path) if state_path.exists() else {
        "schema_version": "2.0" if campaign.get("schema_version") == "2.0" else "1.0",
        "campaign_hash": canonical_hash(campaign),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None, "cases": {}, "agents": {},
    }
    if state.get("campaign_hash") != canonical_hash(campaign):
        raise ValueError("campaign changed after state was created; use a new state file")

    failures = 0
    for entry in entries:
        if args.module and entry["module"] != args.module:
            continue
        agent_id = str(entry["agent_id"])
        if args.agent and agent_id != args.agent:
            continue
        if args.case and str(entry.get("case_id") or "") != args.case:
            continue
        version = str(entry.get("agent_version") or "legacy")
        case_id = str(entry.get("case_id") or agent_id)
        key = f"{agent_id}@{version}:{case_id}"
        sensitive_inputs = sensitive_case_inputs.get(key) or sensitive_case_inputs.get(case_id) or {}
        item = _state_entry(state, key)
        if args.reuse_only and not item.get("free_run_id"):
            continue
        case_path = (campaign_path.parent / str(entry["case"])).resolve()
        baseline_path = (campaign_path.parent / str(entry["baseline"])).resolve()
        output_path = (campaign_path.parent / str(entry["output"])).resolve()
        case = CanonicalTestCase.from_dict(_load(case_path))
        if case.agent_id != agent_id or (entry.get("case_id") and case.case_id != case_id):
            raise ValueError(f"campaign entry {key} does not match its CanonicalTestCase")
        baseline_payload = _load(baseline_path)
        baseline = validate_direct_baseline(baseline_payload, case)
        _validate_case_expectations(entry, baseline)
        baseline_hash = canonical_hash(baseline_payload)
        if item.get("baseline_hash") not in {None, baseline_hash}:
            raise ValueError(f"frozen baseline changed for {key}")
        item["baseline_hash"] = baseline_hash
        item["skill_gate_snapshot"] = _validate_skill_gates(entry, campaign_path.parent)
        sap_metadata_fingerprint = _sap_metadata_fingerprint(baseline_payload)
        expected_reuse_fingerprint, fingerprint_fields = _reuse_fingerprint(
            entry,
            case,
            baseline,
            item["skill_gate_snapshot"],
            sap_metadata_fingerprint,
        )
        item["reuse_fingerprint"] = expected_reuse_fingerprint
        item["reuse_fingerprint_fields"] = fingerprint_fields
        existing_acceptance = Path(item.get("acceptance_path") or (output_path / "acceptance.json"))
        if existing_acceptance.exists():
            existing_artifact = _load(existing_acceptance)
            terminal = _terminal_acceptance(existing_artifact, canonical_hash(baseline),
                                             require_free="free_query" in entry.get("required_stages", ["free_query"]))
            if terminal and (
                not entry.get("agent_execution_digest")
                or (existing_artifact.get("hashes") or {}).get("agent_execution_digest")
                == entry.get("agent_execution_digest")
            ) and (
                (existing_artifact.get("hashes") or {}).get("reuse_fingerprint")
                == expected_reuse_fingerprint
            ):
                if "frontend" in (entry.get("required_stages") or []):
                    frontend_path = (
                        campaign_path.parent / str(entry.get("frontend_result") or "")
                    ).resolve()
                    if not frontend_path.is_file():
                        item["three_stage_verdict"] = terminal[0]
                        item["verdict"] = None
                        item["free_run_id"] = terminal[1]
                        item["acceptance_path"] = str(existing_acceptance)
                        item["phase"] = "awaiting_frontend"
                        item["last_error"] = None
                        continue
                    try:
                        _validate_frontend_result(
                            frontend_path,
                            entry=entry,
                            fixed_run_id=str((existing_artifact.get("fixed_agent") or {}).get("run_id") or ""),
                        )
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        item["three_stage_verdict"] = terminal[0]
                        item["verdict"] = None
                        item["free_run_id"] = terminal[1]
                        item["acceptance_path"] = str(existing_acceptance)
                        item["phase"] = "awaiting_frontend"
                        item["last_error"] = {
                            "error_code": "frontend_acceptance_stale_or_invalid",
                            "failed_stage": "frontend",
                            "sanitized_message": str(exc),
                        }
                        continue
                item["verdict"], item["free_run_id"] = terminal
                item["three_stage_verdict"] = terminal[0]
                item["acceptance_path"] = str(existing_acceptance)
                item["phase"] = "accepted" if item["verdict"] == "PASS" else "blocked"
                item["last_error"] = None
                continue
        if args.dry_run:
            continue

        item["phase"] = "running"
        item["attempts"] = int(item.get("attempts") or 0) + 1
        output_path = output_path / f"attempt-{item['attempts']:03d}"
        if output_path.exists():
            raise ValueError("campaign_attempt_artifact_immutable")
        item["last_error"] = None
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write(state_path, state)
        command = [
            sys.executable, str(root / "scripts" / "run_three_stage_acceptance.py"),
            "--repository", str(root), "--module", str(entry["module"]),
            "--case", str(case_path), "--baseline", str(baseline_path),
            "--output", str(output_path), "--api-url", args.api_url,
            "--baseline-timeout", str(args.baseline_timeout),
            "--free-timeout", str(args.free_timeout),
            "--fixed-timeout", str(args.fixed_timeout),
            "--acceptance-contract-digest", str(entry.get("acceptance_contract_digest") or ""),
            "--case-input-hash", fingerprint_fields["case_input_hash"],
            "--business-date", fingerprint_fields["business_date"],
            "--skill-gate-snapshot-hash", fingerprint_fields["skill_gate_snapshot_hash"],
            "--sap-metadata-fingerprint", fingerprint_fields["sap_metadata_fingerprint"],
            "--agent-catalog-digest", fingerprint_fields["agent_catalog_digest"],
            "--runtime-snapshot-hash", fingerprint_fields["runtime_snapshot_hash"],
        ]
        if campaign.get("schema_version") == "2.0" and entry["kind"] != "deterministic_fixture":
            command.extend(["--anchor-profile", str(getattr(args, "anchor_profile", None)
                            or Path.home() / ".codex/secure/sap-direct-readonly.json")])
        if item.get("free_run_id"):
            command.extend(["--free-run-id", str(item["free_run_id"])])
        if "free_query" not in (entry.get("required_stages") or ["free_query"]):
            command.append("--skip-free-query")
        for option, field in (
            ("--fixed-result", "fixed_result"), ("--agent-snapshot", "agent_snapshot"),
            ("--rules-source", "rules_source"), ("--agent-version", "agent_version"),
            ("--agent-execution-digest", "agent_execution_digest"),
        ):
            if entry.get(field):
                value = str(entry[field])
                if field in {"fixed_result", "agent_snapshot", "rules_source"}:
                    value = str((campaign_path.parent / value).resolve())
                command.extend([option, value])
        if sensitive_inputs:
            command.append("--sensitive-input-stdin")
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            input=(json.dumps(sensitive_inputs, ensure_ascii=False) if sensitive_inputs else None),
            check=False,
        )
        acceptance_path = output_path / "acceptance.json"
        item["acceptance_path"] = str(acceptance_path)
        terminal_blocked = False
        if acceptance_path.exists():
            artifact = _load(acceptance_path)
            if (artifact.get("hashes") or {}).get("reuse_fingerprint") != expected_reuse_fingerprint:
                artifact["verdict"] = "FAIL"
                artifact.setdefault("validation_issues", []).append(
                    {"code": "acceptance_reuse_fingerprint_mismatch"}
                )
                item.setdefault("verification_issues", []).append("acceptance_reuse_fingerprint_mismatch")
            if artifact.get("verdict") == "PASS" and "frontend" in (
                entry.get("required_stages") or []
            ):
                frontend_path = (
                    campaign_path.parent / str(entry.get("frontend_result") or "")
                ).resolve()
                if not frontend_path.is_file():
                    item["three_stage_verdict"] = "PASS"
                    item["verdict"] = None
                    item["free_run_id"] = _matched_free_run_id(artifact)
                    item["phase"] = "awaiting_frontend"
                    item["last_error"] = None
                    state["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _write(state_path, state)
                    continue
                try:
                    _validate_frontend_result(
                        frontend_path,
                        entry=entry,
                        fixed_run_id=str((artifact.get("fixed_agent") or {}).get("run_id") or ""),
                    )
                    item["frontend_result"] = str(frontend_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    item["three_stage_verdict"] = "PASS"
                    item["verdict"] = None
                    item["free_run_id"] = _matched_free_run_id(artifact)
                    item["phase"] = "awaiting_frontend"
                    item["last_error"] = {
                        "error_code": "frontend_acceptance_stale_or_invalid",
                        "failed_stage": "frontend",
                        "sanitized_message": str(exc),
                    }
                    state["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _write(state_path, state)
                    continue
            item["verdict"] = artifact.get("verdict")
            item["three_stage_verdict"] = artifact.get("verdict")
            item["free_run_id"] = _matched_free_run_id(artifact)
            terminal_blocked = bool(
                artifact.get("verdict") == "BLOCKED"
                and ((artifact.get("free_query") or {}).get("comparison") or {}).get("verdict") == "MATCH"
                and ((artifact.get("fixed_agent") or {}).get("comparison") or {}).get("verdict") == "MATCH"
                and artifact.get("blocking_limitations")
            )
            item["phase"] = (
                "accepted" if artifact.get("verdict") == "PASS"
                else "blocked" if terminal_blocked else "needs_adjudication"
            )
        else:
            item["verdict"] = "BLOCKED"
            item["phase"] = "blocked"
        effective_failure = completed.returncode != 0 and not terminal_blocked
        if effective_failure:
            failures += 1
            item["last_error"] = _record_failure_log(state_path, key, completed)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write(state_path, state)
        if effective_failure and not args.continue_on_failure:
            break

    _aggregate_agents(campaign, entries, state)
    if campaign.get("schema_version") == "2.0":
        failures += sum(
            item.get("verdict") != "PASS" for item in (state.get("agents") or {}).values()
        )
    if not args.dry_run:
        _write(state_path, state)
    print(json.dumps({
        "campaign": str(campaign_path), "state": str(state_path),
        "cases": len(entries), "failures": failures, "dry_run": args.dry_run,
    }, ensure_ascii=False))
    return 0 if failures == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a resumable multi-case live acceptance campaign.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--module")
    parser.add_argument("--agent")
    parser.add_argument("--case")
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--baseline-timeout", type=int, default=600)
    parser.add_argument("--free-timeout", type=int, default=1800)
    parser.add_argument("--fixed-timeout", type=int, default=600)
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reuse-only", action="store_true")
    parser.add_argument("--anchor-profile")
    parser.add_argument(
        "--sensitive-input-stdin",
        action="store_true",
        help="Read per-case protected inputs as JSON from stdin.",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
