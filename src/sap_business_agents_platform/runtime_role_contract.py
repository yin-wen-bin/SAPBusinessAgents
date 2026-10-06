"""Provider-owned role evidence contract; the service remains authoritative."""
import json
import copy

from .runtime_diagnostics import checked_output
from .runtime_contract import RuntimeContractError as WorkBuddyError

COLLECTIONS = ("roles", "processes", "operations", "agent_matches", "rejected_candidates", "workflow_suggestions", "agent_gaps")
REF = {"type": "object", "properties": {"document_id": {"type": "string"}, "chunk_id": {"type": "string"}},
       "required": ["document_id", "chunk_id"]}
SCHEMA = {"type": "object", "required": [*COLLECTIONS, "document_issues", "catalog_evaluation"],
          "properties": {**{name: {"type": "array", "items": {"type": "object", "required": ["evidence_refs"],
               "properties": {"evidence_refs": {"type": "array", "minItems": 1, "items": REF}}}} for name in COLLECTIONS},
              "document_issues": {"type": "array"}, "catalog_evaluation": {"type": "object"}}}


def native_output_schema(schema):
    """Project the legacy envelope into native JSON, not a second business model.

    Codex keeps its original wire schema. SDKs supporting native objects can
    avoid asking the model to manually escape the complete role analysis.
    """
    from .runtime_prompts import ROLE_MATCHING_OUTPUT_SCHEMA
    result = copy.deepcopy(schema)
    if schema == ROLE_MATCHING_OUTPUT_SCHEMA:
        result["properties"]["analysis_json"] = copy.deepcopy(SCHEMA)
        result["properties"]["analysis_json"]["properties"]["non_sap_operation_count"] = {
            "type": "integer", "minimum": 0}
    return result


def canonical_output(raw, schema, *, operation):
    """Lossless encoding only; never repair text, infer facts or add defaults."""
    from .runtime_prompts import ROLE_MATCHING_OUTPUT_SCHEMA
    if schema != ROLE_MATCHING_OUTPUT_SCHEMA:
        return raw
    wire = native_output_schema(schema)
    checked_output(json.dumps(raw, ensure_ascii=False), wire, operation=operation,
                   output_format="native_json_schema")
    result = copy.deepcopy(raw)
    result["analysis_json"] = json.dumps(raw["analysis_json"], ensure_ascii=False)
    return checked_output(json.dumps(result, ensure_ascii=False), schema,
                          operation=operation, output_format="canonical_json_envelope")


def references(data):
    return {(d["document_id"], c["chunk_id"]) for d in data.get("documents", []) for c in d.get("chunks", [])}


def prompt(data):
    return ("Every conclusion in roles, processes, operations, agent_matches, rejected_candidates, workflow_suggestions "
            "and agent_gaps requires nonempty evidence_refs:[{document_id,chunk_id}] from the supplied document registry. "
            "Do not use Agent IDs as document citations, fabricate IDs or cite an unrelated chunk. "
            "Return empty collections when unsupported; document evidence is not live SAP evidence. "
            "analysis_json must contain all seven collections, document_issues and catalog_evaluation. "
            "Preserve stable operation IDs on feedback. Evaluate the complete supplied catalog and explicitly record "
            "all scanned/matched/rejected IDs and coverage, matching and consolidation completeness per user_context.\n"
            + json.dumps({"available_document_refs": [{"document_id": d, "chunk_id": c} for d, c in sorted(references(data))],
                          "analysis_schema": SCHEMA}, ensure_ascii=False) + "\n")


def decode(text, data, operation):
    value = checked_output(text, SCHEMA, operation=operation, output_format="embedded_json")
    allowed = references(data)
    for name in COLLECTIONS:
        for index, item in enumerate(value[name]):
            if any((r["document_id"], r["chunk_id"]) not in allowed for r in item["evidence_refs"]):
                error = WorkBuddyError("role_matching_evidence_ref_invalid")
                error.detail = {"validation_issues": [{"code": error.code,
                    "path": f"/{name}/{index}/evidence_refs", "constraint": "known_document_reference"}]}
                raise error
    return value
