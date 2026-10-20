"""Offline safe diagnostics; these tests never grant Runtime qualification."""
import copy
import json
from pathlib import Path

import pytest


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from workbuddy_validation_diagnostics import rejected_plan_diagnostic
    return rejected_plan_diagnostic


PLAN = {"schema_version": "1.0", "plan_kind": "direct", "service_name": "API_PURCHASEORDER_PROCESS_SRV",
        "odata_version": "2.0", "entity_set": "A_PurchaseOrder", "http_method": "GET",
        "select_fields": ["PurchaseOrder", "CompanyCode", "Supplier"],
        "filters": [{"field": "PurchaseOrder", "operator": "eq", "value": "4500001466"}], "top": None}


def test_missing_limit_is_specific_and_source_is_not_mutated(diagnostic):
    before = copy.deepcopy(PLAN)
    result = diagnostic(PLAN, case="limited_sample", purchase_order="4500001466")
    assert result["issues"] == [{"path": "/top", "constraint": "one_row_read_limit"}]
    assert all(result["authorized_scope_matches"].values())
    assert result["read_limit"] == {"present": True, "is_null": True, "bounded_integer": False}
    assert result["request_plan_digest"].startswith("sha256:") and PLAN == before


def test_diagnostic_never_echoes_foreign_values_or_unknown_payload(diagnostic):
    plan = copy.deepcopy(PLAN)
    plan["filters"][0]["value"] = "SECRET-business-value"
    result = diagnostic(plan, case="limited_sample", purchase_order="4500001466")
    assert result["authorized_scope_matches"]["filter"] is False
    assert "/filters" in {item["path"] for item in result["issues"]}
    assert "SECRET" not in json.dumps(result) and "4500001466" not in json.dumps(result)
    result = diagnostic({**plan, "credentials": "SECRET-token"}, case="limited_sample", purchase_order="4500001466")
    assert result["issues"] == [{"path": "/plan", "constraint": "supported_plan_structure"}]
    assert "SECRET" not in json.dumps(result) and "credentials" not in json.dumps(result)


@pytest.mark.parametrize("plan", [None, [], "SECRET", {"steps": []}])
def test_malformed_input_diagnostic_is_bounded_and_value_free(diagnostic, plan):
    result = diagnostic(plan, case="limited_sample", purchase_order="4500001466")
    assert result["issues"] and "SECRET" not in json.dumps(result)


def test_diagnostic_cannot_authorize_execution_or_rewrite_limit(diagnostic):
    from workbuddy_validation_cases import checked_plan, VerificationFailure
    with pytest.raises(VerificationFailure):
        checked_plan(PLAN, case="limited_sample", purchase_order="4500001466")
    diagnostic(PLAN, case="limited_sample", purchase_order="4500001466")
    with pytest.raises(VerificationFailure):
        checked_plan(PLAN, case="limited_sample", purchase_order="4500001466")
    allowed = {**PLAN, "top": 1}
    assert checked_plan(allowed, case="limited_sample", purchase_order="4500001466")["top"] == 1
    assert diagnostic(allowed, case="limited_sample", purchase_order="4500001466")["read_limit"]["value"] == 1
