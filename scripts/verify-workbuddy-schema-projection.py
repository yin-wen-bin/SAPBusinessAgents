"""Generate/check public synthetic Schema fixtures without model or SAP calls."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from sap_business_agents_platform.acceptance_projection import output_schema
from sap_business_agents_platform.runtime_harness_contract import _HARNESS_OUTPUT_SCHEMA
from sap_business_agents_platform.workbuddy_harness import native_terminal_instructions
from sap_business_agents_platform.workbuddy_environment import atomic_json, digest
from workbuddy_schema_contrast import equivalent_union_schema


def main():
    root = Path(__file__).resolve().parents[1]
    destination = root / ".local-data/workbuddy-schema-projection-offline-20261012"
    assert not destination.exists(), "verification_fresh_offline_directory_required"
    schema = output_schema(_HARNESS_OUTPUT_SCHEMA, None)
    contrast = equivalent_union_schema(schema)
    example = {"status": "inconclusive", "intent": "offline formatter protocol probe", "clarification_question": "",
        "input_kind": None, "input_field": None, "summary": {"zh": "仅核对格式，不查询SAP。", "en": "Formatting only; no SAP query."},
        "source_complete": False, "business_complete": False, "missing_evidence": ["offline_formatter_probe"],
        "evidence_refs": [], "executed_plans": [], "presentation": None}
    original_check, contrast_check = Draft202012Validator(schema), Draft202012Validator(contrast)
    original_check.validate(example)
    contrast_check.validate(example)
    partial = {name: example[name] for name in ("status", "intent", "clarification_question")}
    assert not original_check.is_valid(partial) and not contrast_check.is_valid(partial)
    invalid = {**example, "input_kind": "null", "input_field": "null"}
    assert not original_check.is_valid(invalid) and not contrast_check.is_valid(invalid)
    atomic_json(destination / "input.json", {"schema": schema, "contrast_schema": contrast, "example": example,
        "instructions": native_terminal_instructions(schema)})
    report = {"phase": "offline_fixture_checked", "model_calls": 0, "sap_calls": 0,
        "original_schema_sha256": digest(schema), "contrast_schema_sha256": digest(contrast),
        "property_count": len(schema["properties"]), "required_fields": schema["required"],
        "complete_null_example_passed": True, "three_field_failure_rejected": True,
        "invalid_null_strings_rejected": True, "production_driver_changed": False,
        "contrast_live_validated": False, "operation_qualification_granted": False}
    atomic_json(destination / "fixture-report.json", report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
