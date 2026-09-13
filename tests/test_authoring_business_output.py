from types import SimpleNamespace

from sap_business_agents_platform.authoring_checks import (
    candidate_business_output_issues,
    trial_business_output_summary,
)


def _manifest(operation="evidence_summary", business_field=False, business_report=False):
    properties = {
        "status": {"type": "string"},
        "source_complete": {"type": "boolean"},
        "successful_source_count": {"type": "integer"},
    }
    if business_field:
        properties["records"] = {"type": "array", "items": {"type": "object"}}
    if business_report:
        properties["business_report"] = {"type": "object"}
    return {
        "execution": {
            "steps": [
                {"id": "read", "executor": "sap_read", "operation": "execute_plan"},
                {"id": "assess", "executor": "rule", "operation": operation},
            ],
            "outputSchema": {"type": "object", "properties": properties},
        }
    }


def test_sap_reading_draft_requires_rule_and_nontechnical_business_output():
    issue = candidate_business_output_issues(_manifest())
    assert issue[0]["code"] == "agent_business_output_contract_missing"
    assert candidate_business_output_issues(_manifest("managed_agent_rule", business_field=True)) == []
    assert candidate_business_output_issues(_manifest("managed_agent_rule", business_report=True)) == []


def test_non_sap_draft_does_not_receive_the_live_business_output_gate():
    manifest = _manifest()
    manifest["execution"]["steps"] = [manifest["execution"]["steps"][1]]
    assert candidate_business_output_issues(manifest) == []


def test_trial_summary_requires_both_report_and_substantive_presentation():
    missing = SimpleNamespace(workflow_output={"business_report": {"headline": {"zh": "完成", "en": "Done"}}}, presentation={"blocks": []})
    assert trial_business_output_summary(missing)["business_output_available"] is False
    result = SimpleNamespace(
        workflow_output={"business_report": {"metrics": [{"id": "record_count", "value": 2}], "records": [{"id": 1}, {"id": 2}]}},
        presentation={"blocks": [{"type": "table", "rows": [{"values": ["1"]}, {"values": ["2"]}]}]},
    )
    summary = trial_business_output_summary(result)
    assert summary == {"business_output_available": True, "business_record_count": 2, "business_output_issues": []}


def test_trial_summary_does_not_count_an_unmapped_internal_rule_report():
    result = SimpleNamespace(
        workflow_output={},
        rule_results=[{"business_report": {"records": [{"id": 1}, {"id": 2}]}}],
        presentation={"blocks": [{"type": "table", "rows": [{"values": ["1"]}]}]},
    )
    summary = trial_business_output_summary(result)
    assert summary["business_output_available"] is False
    assert summary["business_record_count"] == 0
