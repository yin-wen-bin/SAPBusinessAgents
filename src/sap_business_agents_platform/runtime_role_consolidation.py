"""SDK-free final-phase contract and operation reconciliation.

Catalog matching remains authoritative. A compiled proposal is a checked,
declared capability combination, not evidence that SAP execution succeeded.
"""
from __future__ import annotations

import copy
import json

from .runtime_diagnostics import checked_output
from .runtime_workflow_contract import TEXT, STRINGS, STAGE

VERSION = "sapba.role-consolidation/1"
SUPPORT_VERSION = "sapba.role-candidate-support/1"
_SUPPORTED_COVERAGE = frozenset({"full", "partial"})
_SUPPORTED_CONFIDENCE = frozenset({"medium", "high"})
REF = {"type": "object", "additionalProperties": False,
       "properties": {name: {"type": "string", "minLength": 1} for name in ("document_id", "chunk_id")},
       "required": ["document_id", "chunk_id"]}
REFS = {"type": "array", "minItems": 1, "items": REF}
OPERATION_IDS = {"type": "array", "minItems": 1, "uniqueItems": True,
                 "items": {"type": "string", "minLength": 1}}

def _object(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": copy.deepcopy(properties)}

_TEXT = copy.deepcopy(TEXT)
for _field in _TEXT["properties"].values():
    _field["minLength"] = 1
_STAGE = _object({key: STAGE["properties"][key] for key in (
    "id", "agent_id", "confidence", "capability", "reason", "bindings", "requested_outputs")})
_STAGE["properties"]["agent_id"] = {"type": "string", "minLength": 1}
_STAGE["properties"]["confidence"] = {"type": "string", "enum": ["high"]}
_SUGGESTION = _object({"suggestion_id": {"type": "string", "minLength": 1},
    "operation_ids": OPERATION_IDS, "title": _TEXT, "description": _TEXT, "intent": _TEXT,
    "stages": {"type": "array", "minItems": 1, "items": _STAGE}, "evidence_refs": REFS})
_GAP = _object({"gap_id": {"type": "string", "minLength": 1}, "operation_ids": OPERATION_IDS,
    "required_capability": _TEXT, "required_inputs": STRINGS, "required_outputs": STRINGS,
    "safety_boundary": _TEXT, "business_impact": _TEXT, "partial_agent_ids": STRINGS,
    "reason": _TEXT, "evidence_refs": REFS})
SCHEMA = _object({"summary_zh": {"type": "string", "minLength": 1},
    "summary_en": {"type": "string", "minLength": 1},
    "workflow_suggestions": {"type": "array", "items": _SUGGESTION},
    "agent_gaps": {"type": "array", "items": _GAP}})

def decode(result, *, operation):
    """No legacy-envelope defaults and no cross-phase decoding."""
    return checked_output(result.final_response, SCHEMA, operation=operation,
                          output_format="role_consolidation_json")

def refs(item):
    return {(r.get("document_id"), r.get("chunk_id"))
            for r in item.get("evidence_refs", []) if isinstance(r, dict)
            and all(isinstance(r.get(name), str) and r[name] for name in ("document_id", "chunk_id"))}

def _operation_capability_signals(operation):
    text = " ".join([str(operation.get("name") or ""), str(operation.get("description") or ""),
                     *[str(item) for item in operation.get("outputs") or []]]).lower()
    return {"pgi_status"} if any(token in text for token in (
        "pgi", "goods issue", "goods movement", "发货过账", "出库过账")) else set()

def _gap_matches_signals(value, signals):
    return "pgi_status" in signals and any(token in value.lower() for token in (
        "pgi", "goods issue", "goods movement", "发货过账", "出库过账"))

def has_full_coverage(match):
    return match.get("coverage") == "full" and not match.get("uncovered_capabilities")

def checked_candidate(match, *, operation, agent, capability_signals=()):
    """Existing catalogue normalization, shared before prompting and saving.

    Executability/acceptance are catalogue facts, never model declarations.
    Do not mutate page records or change the pre-existing coverage corrections.
    """
    result = copy.deepcopy(match)
    result.update(executable=agent.get("executable", False),
                  validation_verdict=agent.get("validation_verdict", "NOT_TESTED"))
    if result.get("coverage") not in {"full", "partial", "none"}:
        result["coverage"] = "partial"
    if result.get("confidence") not in {"high", "medium", "low"}:
        result["confidence"] = "low"
    required_signals = _operation_capability_signals(operation)
    uncovered = [str(item) for item in result.get("uncovered_capabilities") or []]
    if (result.get("coverage") == "partial" and required_signals
            and required_signals.issubset(set(capability_signals)) and uncovered
            and all(_gap_matches_signals(item, required_signals) for item in uncovered)):
        result["coverage"] = "full"
        result["uncovered_capabilities"] = []
    if (has_full_coverage(result)
            and result.get("executable") and result.get("validation_verdict") == "PASS"):
        # Preserve the existing catalogue correction, not a stage's self-rating.
        result["confidence"] = "high"
    return result

def candidate_support_issues(match, *, operation_id):
    """One support predicate for projected eligibility and final reconciliation."""
    issues = []
    if match.get("operation_id") != operation_id:
        issues.append("operation_mismatch")
    if not isinstance(match.get("agent_id"), str) or not match["agent_id"]:
        issues.append("candidate_id_invalid")
    if match.get("coverage") not in _SUPPORTED_COVERAGE:
        issues.append("coverage_not_supported")
    if match.get("confidence") not in _SUPPORTED_CONFIDENCE:
        issues.append("match_confidence_insufficient")
    if not match.get("executable"):
        issues.append("agent_not_executable")
    if match.get("validation_verdict") != "PASS":
        issues.append("acceptance_not_pass")
    return issues

def candidate_support_projection(operations, matches, catalog):
    """Bounded safe eligibility from frozen catalogue plus checked page matches.

    Low-confidence matches remain in the original evaluation/audit context.
    Missing catalogue entries fail closed even if a model supplies PASS flags.
    """
    agents = {item["agent_id"]: item for item in catalog.get("items", [])}
    projection = []
    for operation in operations:
        op_id = operation["operation_id"]
        supported, single, full, rejected = set(), set(), set(), []
        for match in matches:
            if match.get("operation_id") != op_id:
                continue
            agent_id = match.get("agent_id")
            agent = agents.get(agent_id)
            if agent is None:
                rejected.append({"agent_id": agent_id, "reasons": ["candidate_not_in_catalog"]})
                continue
            checked = checked_candidate(match, operation=operation, agent=agent,
                capability_signals=catalog.get("capability_signals", {}).get(agent_id, ()))
            is_full = has_full_coverage(checked)
            if is_full:
                full.add(agent_id)
            reasons = candidate_support_issues(checked, operation_id=op_id)
            if reasons:
                rejected.append({"agent_id": agent_id, "reasons": reasons})
            else:
                supported.add(agent_id)
                if is_full:
                    single.add(agent_id)
        projection.append({"operation_id": op_id, "eligible_stage_agent_ids": sorted(supported),
            "eligible_single_agent_ids": sorted(single), "full_coverage_agent_ids": sorted(full),
            "unsupported_candidates": rejected})
    return {"rule": {"contract_version": SUPPORT_VERSION,
                     "coverage": sorted(_SUPPORTED_COVERAGE),
                     "match_confidence": sorted(_SUPPORTED_CONFIDENCE),
                     "executable": True, "validation_verdict": "PASS"},
            "operations": projection}

def candidate_support_instructions():
    return """The platform's Candidate support projection is authoritative for suggestion eligibility.
Use only eligible_stage_agent_ids for EVERY claimed operation; a suggestion spanning operations
must satisfy all their lists. The rule's match_confidence refers to the checked preceding page
match, not the stage's confidence. Writing confidence=high in a stage cannot upgrade a prior match.
Unsupported candidates remain explanatory/audit context only; do not add them as optional context
stages or change their page evaluations. A one-Agent suggestion must use eligible_single_agent_ids;
a partial match alone cannot become full coverage. Multi-Agent suggestions still require valid
ports, bindings, evidence and compiler checks. Full coverage is distinct from executable eligibility:
full_coverage_agent_ids may explain covered operations even when they cannot be suggested."""

def match_conflicts(analysis):
    """Identical repeated records may deduplicate; contradictory records may not."""
    groups, issues = {}, []
    for item in [*analysis.get("agent_matches", []), *analysis.get("rejected_candidates", [])]:
        if not isinstance(item, dict):
            continue
        key = (item.get("operation_id"), item.get("agent_id"))
        if not all(isinstance(value, str) and value for value in key):
            issues.append({"code": "role_matching_record_invalid"})
            continue
        content = {name: item.get(name) for name in ("coverage", "confidence", "reason", "uncovered_capabilities")}
        content["evidence_refs"] = sorted(refs(item))
        signature = json.dumps(content, sort_keys=True, ensure_ascii=False)
        if key in groups and signature != groups[key]:
            issue = {"code": "role_matching_record_conflict", "operation_id": key[0], "agent_id": key[1]}
            if issue not in issues:
                issues.append(issue)
        groups[key] = signature
    return issues

def reconcile(analysis, compiled, *, conflicts=()):
    """Account for every operation without equating coverage with executability."""
    issues = list(conflicts)
    operations = {op["operation_id"]: op for op in analysis.get("operations", []) if isinstance(op, dict)
                  and isinstance(op.get("operation_id"), str) and op["operation_id"]}
    if len(operations) != len(analysis.get("operations", [])) or not all(operations):
        issues.append({"code": "role_matching_operation_id_invalid"})
    matches = analysis.get("agent_matches", [])
    dispositions = []
    for collection in ("agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps"):
        for item in analysis.get(collection, []):
            if not isinstance(item, dict):
                issues.append({"code": "role_matching_record_invalid", "collection": collection})
                continue
            ids = [item.get("operation_id")] if collection in {"agent_matches", "rejected_candidates"} else item.get("operation_ids", [])
            if not isinstance(ids, list) or not ids or any(not isinstance(op, str) or op not in operations for op in ids):
                issues.append({"code": "role_matching_operation_ref_invalid", "collection": collection})
            elif any(not refs(operations[op]) or not refs(operations[op]).issubset(refs(item)) for op in ids):
                issues.append({"code": "role_matching_operation_evidence_invalid", "collection": collection})
    for collection, identifier in (("workflow_suggestions", "suggestion_id"), ("agent_gaps", "gap_id")):
        seen = {}
        for item in analysis.get(collection, []):
            key = item.get(identifier)
            if not isinstance(key, str) or not key:
                issues.append({"code": "role_matching_record_invalid", "collection": collection})
                continue
            signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen and seen[key] != signature:
                issues.append({"code": "role_matching_record_conflict", "collection": collection})
            seen[key] = signature
    for op_id, operation in operations.items():
        candidates = [m for m in matches if m.get("operation_id") == op_id]
        full = [m for m in candidates if has_full_coverage(m)]
        combinations = []
        for suggestion in compiled:
            if op_id not in suggestion.get("operation_ids", []):
                continue
            stage_ids = {s.get("agent_id") for s in suggestion.get("stages", [])}
            supported = {m.get("agent_id") for m in candidates
                         if not candidate_support_issues(m, operation_id=op_id)}
            executable_suggestion = bool(suggestion.get("validated") and stage_ids and stage_ids.issubset(supported))
            if executable_suggestion and len(stage_ids) >= 2:
                combinations.append(suggestion)
            elif executable_suggestion and len(stage_ids) == 1 and stage_ids.issubset({m.get("agent_id") for m in full}):
                # The existing compiler permits a single-Agent workflow. It is a
                # saved suggestion for an already covered operation, not evidence
                # that a partial match has become a complete combination.
                pass
            else:
                issues.append({"code": "role_matching_combination_unsupported", "operation_id": op_id,
                               "suggestion_id": suggestion.get("suggestion_id")})
        gaps = [g for g in analysis.get("agent_gaps", []) if op_id in g.get("operation_ids", [])]
        if gaps and (full or combinations):
            issues.append({"code": "role_matching_resolution_conflict", "operation_id": op_id})
        if full:
            disposition = {"operation_id": op_id, "resolution": "single_agent", "covered": True,
                "executable": any(m.get("executable") and m.get("validation_verdict") == "PASS" for m in full),
                "agent_ids": sorted({m["agent_id"] for m in full})}
        elif combinations:
            disposition = {"operation_id": op_id, "resolution": "declared_validated_combination", "covered": True,
                           "executable": True, "suggestion_ids": [s["suggestion_id"] for s in combinations]}
        elif gaps:
            disposition = {"operation_id": op_id, "resolution": "capability_gap", "covered": False,
                           "executable": False, "gap_ids": [g["gap_id"] for g in gaps]}
        else:
            disposition = {"operation_id": op_id, "resolution": "unresolved", "covered": False, "executable": False}
            issues.append({"code": "role_matching_operation_unresolved", "operation_id": op_id})
        dispositions.append(disposition)
    return {"contract_version": VERSION, "complete": not issues,
            "operations": dispositions, "issues": issues}
