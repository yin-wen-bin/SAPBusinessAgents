from __future__ import annotations

import copy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sap_business_agents_platform.acceptance_contract import (
    BusinessContractError, compile_contract, contract_issues, normalize_fixed, readiness,
)
from sap_business_agents_platform.acceptance_projection import (
    AcceptanceProjectionSpec,
    projection_schema,
    validate_projection,
)
from sap_business_agents_platform.managed_rules import execute_managed_rule, source_digest


def sample_manifest():
    records = {"company_code": {"type": "string"}, "business_status": {"type": "string", "enum": ["normal", "attention", "inconclusive"]}, "item_count": {"type": "integer", "minimum": 0}}
    records["business_status"]["x-sapba-display"] = {"format": "status", "labels": {name: {"zh": name, "en": name} for name in records["business_status"]["enum"]}}
    root = {"records": {"type": "array", "items": {"type": "object", "properties": records, "required": list(records), "additionalProperties": False}},
            "count": {"type": "integer"}, "business_status": records["business_status"],
            **{name: {"type": "boolean"} for name in ("source_complete", "evidence_complete", "business_complete")}}
    for name, schema in [*root.items(), *records.items()]:
        schema["title"] = {"zh": "业务结果 " + name, "en": "Business result " + name}
    return {"execution": {"outputSchema": {"type": "object", "properties": root, "required": list(root)}, "acceptance": {
        "contractVersion": "1.0", "businessKeys": ["company_code"], "facts": ["business_status", "item_count"], "metrics": ["count"],
        "recordScope": "records", "recordDefinition": "One company", "scopeDefinition": "Confirmed company only, with complete source evidence.",
        "businessStatusDefinition": "normal for complete evidence, inconclusive otherwise",
        "factDefinitions": {name: "Evidence-backed " + name for name in records},
        "metricDefinitions": {"count": "Unique company records, complete zero is zero."},
        "metricInvariants": {"count": {"operation": "count_records"}}, "requiredLimitations": [],
        "requiredAssessments": [],
    }}}


def sample_result():
    rows = [{"company_code": "1710", "business_status": "normal", "item_count": 2}]
    return {"workflow_output": {"records": rows, "count": 1, "business_status": "normal",
        "source_complete": True, "evidence_complete": True, "business_complete": True,
        "business_report": {"records": copy.deepcopy(rows), "metrics": [{"id": "count", "value": 1}]}}}


def test_preflight_compares_real_output_without_runtime_or_sap():
    manifest = sample_manifest()
    assert not contract_issues(manifest)
    assert readiness(manifest, 4)["status"] == "trial_required"
    report = readiness(manifest, 4, result=sample_result())
    assert report["status"] == "ready" and report["revision"] == 4
    changed = copy.deepcopy(manifest)
    changed["execution"]["acceptance"]["scopeDefinition"] += " Changed."
    assert readiness(changed, 5)["contract_digest"] != report["contract_digest"]


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r["workflow_output"]["records"][0].pop("company_code"), "contract_business_key_invalid"),
    (lambda r: r["workflow_output"]["records"][0].update(business_status="complete"), "contract_field_invalid"),
    (lambda r: r["workflow_output"]["records"][0].update(item_count="2"), "contract_field_invalid"),
    (lambda r: r["workflow_output"]["records"].append(dict(r["workflow_output"]["records"][0])), "contract_business_key_invalid"),
    (lambda r: r["workflow_output"].update(count=7), "contract_metric_invariant_failed"),
    (lambda r: r["workflow_output"]["business_report"].update(records=[]), "contract_report_records_mismatch"),
])
def test_invalid_output_is_contract_failure_not_business_mismatch(mutate, code):
    result = sample_result()
    mutate(result)
    with pytest.raises(BusinessContractError) as error:
        normalize_fixed(result, compile_contract(sample_manifest()))
    assert code in {item["code"] for item in error.value.issues}


def test_empty_output_and_model_projection_keep_integer_enum_and_nonnullable_key():
    manifest = sample_manifest()
    result = sample_result()
    result["workflow_output"].update(records=[], count=0, business_report={"records": [], "metrics": [{"id": "count", "value": 0}]})
    assert readiness(manifest, 1, result=result)["status"] == "ready"
    from scripts.run_three_stage_acceptance import _projection_spec
    schema = projection_schema(AcceptanceProjectionSpec.model_validate(_projection_spec(compile_contract(manifest))))
    fields = schema["properties"]["records"]["items"]["properties"]
    assert fields["item_count"]["type"] == ["integer"]
    assert fields["business_status"]["enum"] == ["normal", "attention", "inconclusive"]
    assert not Draft202012Validator(fields["company_code"]).is_valid(None)


def test_projection_schema_errors_are_safe_and_field_specific():
    spec = AcceptanceProjectionSpec.model_validate({
        **__import__("scripts.run_three_stage_acceptance", fromlist=["_projection_spec"])
        ._projection_spec(compile_contract(sample_manifest())),
    })
    issues = validate_projection(
        spec,
        {
            "records": [{
                "company_code": "1710",
                "business_status": "normal",
                "item_count": "secret-value-must-not-leak",
                "unexpected": "secret-value-must-not-leak",
                "evidence_refs": ["e1"],
            }],
            "metrics": {"count": 1},
            "business_status": "normal",
            "source_complete": True,
            "evidence_complete": True,
            "business_complete": True,
            "evidence_gap_codes": [],
            "evidence_refs": ["e1"],
        },
        {"e1": {"source_type": "sap_live", "source_complete": True}},
    )
    assert {item["code"] for item in issues} >= {
        "acceptance_projection_type_invalid",
        "acceptance_projection_field_unexpected",
    }
    assert all("secret-value-must-not-leak" not in str(item) for item in issues)


def test_acceptance_prompt_only_requires_record_fields_declared_by_schema():
    from scripts.run_three_stage_acceptance import _acceptance_prompt
    from sap_business_agents_platform.acceptance import CanonicalTestCase

    manifest = sample_manifest()
    manifest["execution"]["acceptance"]["facts"] = ["item_count"]
    contract = compile_contract(manifest)
    case = CanonicalTestCase(
        schema_version="1.0",
        case_id="one",
        agent_id="sample",
        question={"zh": "测试", "en": "Test"},
        input={},
        business_conditions={},
        expected_grain=("company_code",),
    )
    prompt = _acceptance_prompt(case, contract)
    assert "include business_status on each record" not in prompt
    assert "only when it appears in the canonical record fields" in prompt


def test_legacy_definitions_are_diagnostic_not_implicitly_upgraded():
    manifest = sample_manifest()
    del manifest["execution"]["acceptance"]["contractVersion"]
    before = copy.deepcopy(manifest)
    assert "contract_version" not in compile_contract(manifest)
    assert contract_issues(manifest)[0]["code"] == "contract_version_required"
    assert manifest == before


def test_required_assessments_distinguish_missing_empty_supported_and_unknown():
    manifest = sample_manifest()
    compiled = compile_contract(manifest)
    assert compiled["required_assessments_declared"] is True
    assert compiled["required_assessments"] == []
    del manifest["execution"]["acceptance"]["requiredAssessments"]
    assert "contract_required_assessments_missing" in {
        item["code"] for item in contract_issues(manifest)
    }
    manifest["execution"]["acceptance"]["requiredAssessments"] = ["inventory_fifo"]
    assert not contract_issues(manifest)
    manifest["execution"]["acceptance"]["requiredAssessments"] = ["future_assessment"]
    assert "contract_required_assessment_unknown" in {
        item["code"] for item in contract_issues(manifest)
    }


def test_invalid_definition_shape_is_diagnostic_and_invalid_runtime_state_is_not_tested():
    manifest = sample_manifest()
    manifest["execution"]["acceptance"]["metricInvariants"] = ["not-a-rule"]
    assert readiness(manifest, 1)["status"] == "needs_input"
    from scripts.run_three_stage_acceptance import _normalize_acceptance_projection
    with pytest.raises(BusinessContractError):
        _normalize_acceptance_projection({"records": []}, None, compile_contract(sample_manifest()))


def test_same_schema_and_semantics_drive_cli_and_campaign_contracts():
    from sap_business_agents_platform.agent_acceptance_campaigns import _contract
    assert _contract(sample_manifest()) == compile_contract(sample_manifest())


def test_technical_metrics_and_missing_semantics_are_not_accepted():
    manifest = sample_manifest()
    manifest["execution"]["acceptance"]["metrics"].append("successful_source_count")
    manifest["execution"]["acceptance"]["factDefinitions"] = {}
    codes = {item["code"] for item in contract_issues(manifest)}
    assert {"contract_technical_metric", "contract_fact_definition_missing"} <= codes


def evidence(rows, complete=True):
    return {"source_complete": complete, "data": {"results": rows}}


def run_candidates(**overrides):
    source = (Path(__file__).resolve().parents[1] / "scripts/fixtures/po_sales_candidate_rules.py").read_text(encoding="utf-8")
    values = {
        "mode": "business_report", "candidate_source_complete": True,
        "purchase_order_items": evidence([{"Material": "M1", "Plant": "P1"}, {"Material": "M2", "Plant": "P2"}]),
        "mrp_candidates": evidence([{"Material": "M1", "MRPPlant": "P1", "MRPElement": "SO1", "MRPElementCategory": "VC"}, {"Material": "M1", "MRPPlant": "P2", "MRPElement": "SO2", "MRPElementCategory": "VC"}]),
        "sales_order_items": evidence([{"SalesOrder": "SO1", "SalesOrderItem": "10", "Material": "M1", "ProductionPlant": "P1"}, {"SalesOrder": "SO2", "SalesOrderItem": "10", "Material": "M1", "ProductionPlant": "P2"}]),
        "schedule_lines": evidence([{"SalesOrder": "SO1", "SalesOrderItem": "10", "ScheduleLine": "1"}, {"SalesOrder": "SO1", "SalesOrderItem": "10", "ScheduleLine": "1"}]),
    }
    values.update(overrides)
    return execute_managed_rule(source, values, expected_digest=source_digest(source))["workflow_output"]


def test_candidate_scope_is_paired_not_cartesian_and_counts_are_deduplicated():
    output = run_candidates()
    assert output["candidate_sales_orders"] == ["SO1"]
    assert output["verified_item_count"] == output["schedule_line_count"] == 1
    assert output["records"][0]["assessment"] == "candidate_for_review"
    assert output["business_report"]["records"] == output["records"]


def test_complete_zero_and_failed_read_are_distinct():
    empty = run_candidates(purchase_order_items=evidence([]))
    assert empty["records"] == [] and empty["business_status"] == "normal"
    failed = run_candidates(purchase_order_items=evidence([], False))
    assert failed["business_status"] == "inconclusive" and not failed["source_complete"]
    partial = run_candidates(candidate_source_complete=False)
    assert partial["records"][0]["assessment"] == "inconclusive"
