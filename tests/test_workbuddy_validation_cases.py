"""Synthetic source metadata/records only; no model or SAP qualification."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.workbuddy_validation_cases import checked_plan, checked_result, failed_attempt, frozen_runtime_binding, query, VerificationFailure
from tests.test_workbuddy_remaining import PLAN


def metadata():
    return {"ok": True, "data": {"schema_authority": True, "fields_truncated": False,
        "compatibility_status": "compatible", "metadata_timestamp": "synthetic-test-only",
        "entities": [{"service_name": PLAN["service_name"], "odata_version": "2.0", "entity_set": "A_PurchaseOrder",
                      "key_fields": ["PurchaseOrder"], "runtime_available": True}]}}


def record(case):
    return SimpleNamespace(status="inconclusive" if case == "limited_sample" else "completed", result=SimpleNamespace(
        presentation=SimpleNamespace(validation_ref="synthetic-ref"), artifacts=["synthetic-artifact"],
        completeness=SimpleNamespace(source_complete=case != "limited_sample", business_complete=case != "limited_sample",
                                     missing_evidence=[])))


CALLS = [{"tool_name": "sap_query_execute", "status": "completed"},
         {"tool_name": "sap_final_report_validate", "status": "completed", "output": {"ok": True}}]


def test_runtime_binding_matches_saved_run_projection_without_losing_freeze():
    from sap_business_agents_platform.models import RuntimeSnapshot
    raw = {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "configuration_digest": "frozen",
           "model": "hy4-preview", "environment_digest": "a" * 64, "model_check_digest": "checked",
           "operation_capabilities": {"free_query": {"status": "unverified"}}}
    expected = RuntimeSnapshot.model_validate(raw).model_dump(mode="json")
    assert raw != expected and expected["model_catalog_digest"] is None
    frozen = frozen_runtime_binding(raw)
    assert frozen == expected and frozen["environment_digest"] == raw["environment_digest"]
    assert frozen["model_check_digest"] == "checked" and frozen["configuration_digest"] == "frozen"
    assert frozen != frozen_runtime_binding({**raw, "model": "another-model"})
    frozen["operation_capabilities"]["free_query"]["status"] = "changed"
    assert raw["operation_capabilities"]["free_query"]["status"] == "unverified"
    with pytest.raises(ValueError):
        frozen_runtime_binding({"provider_id": "workbuddy"})


def test_limited_sample_remains_inconclusive_and_never_qualifies():
    checked_plan(PLAN, case="limited_sample", purchase_order="4500001466")
    value = record("limited_sample")
    assert checked_result(value, CALLS, case="limited_sample")["qualifies_operation"] is False
    value.result.completeness.source_complete = True
    with pytest.raises(VerificationFailure, match="claimed_complete"):
        checked_result(value, CALLS, case="limited_sample")
    with pytest.raises(VerificationFailure, match="scope_rejected"):
        checked_plan({**PLAN, "top": None}, case="limited_sample", purchase_order="4500001466")


def test_complete_case_requires_current_live_key_and_no_explicit_truncation():
    plan = {**PLAN, "top": None}
    with pytest.raises(VerificationFailure, match="live_unique_key_required"):
        checked_plan(plan, case="complete_unique_key", purchase_order="4500001466")
    checked_plan(plan, case="complete_unique_key", purchase_order="4500001466", schema_results=[metadata()])
    with pytest.raises(VerificationFailure, match="scope_rejected"):
        checked_plan(PLAN, case="complete_unique_key", purchase_order="4500001466", schema_results=[metadata()])
    assert checked_result(record("complete_unique_key"), CALLS, case="complete_unique_key")["qualifies_operation"] is True


@pytest.mark.parametrize("patch", [
    {"schema_authority": False}, {"fields_truncated": True}, {"compatibility_status": "incompatible"}, {"metadata_timestamp": None},
    {"entities": []}, {"entities": [{"key_fields": ["PurchaseOrder", "Item"]}]},
])
def test_seed_partial_failed_or_composite_metadata_cannot_prove_unique_key(patch):
    schema = metadata()
    schema["data"].update(patch)
    with pytest.raises(VerificationFailure, match="live_unique_key_required"):
        checked_plan({**PLAN, "top": None}, case="complete_unique_key", purchase_order="4500001466", schema_results=[schema])


@pytest.mark.parametrize("patch", [{"filters": []}, {"filters": [{"field": "PurchaseOrder", "value": "4500001467"}]},
    {"entity_set": "A_PurchaseOrderItem"}, {"select_fields": ["PurchaseOrder"]}, {"top": 2},
    {"plan_kind": "function_import"}, {"partition": {"field": "Date", "strategy": "adaptive_date", "from": "2020", "to": "2022"}}])
def test_neither_case_can_expand_the_original_read_scope(patch):
    for case in ("limited_sample", "complete_unique_key"):
        with pytest.raises(ValueError):
            checked_plan({**PLAN, **patch}, case=case, purchase_order="4500001466", schema_results=[metadata()])


@pytest.mark.parametrize("case", ["limited_sample", "complete_unique_key"])
def test_actual_runtime_failure_and_evidence_gaps_are_not_accepted(case):
    value = record(case)
    value.result.completeness.missing_evidence = ["harness_runtime_unavailable"]
    with pytest.raises(VerificationFailure, match="report_or_evidence_invalid"):
        checked_result(value, CALLS, case=case)
    value.status, value.result = "cancelled", None
    with pytest.raises(VerificationFailure, match="platform_not_completed"):
        checked_result(value, CALLS, case=case)


def test_failed_qualification_does_not_overwrite_platform_result():
    value = record("limited_sample")
    before = copy.deepcopy(value)
    result = failed_attempt(VerificationFailure("verification_failed"), value)
    assert value == before and value.status == "inconclusive"
    assert result == {"phase": "failed", "failure_code": "verification_failed", "platform_status": "inconclusive",
                      "platform_status_overridden": False}


def test_live_runner_is_opt_in_and_import_has_no_runtime_or_sap_effect(monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/verify-workbuddy-free-query.py"
    import sys
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("workbuddy_free_verification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "verify", lambda *_: pytest.fail("dry-run must not execute"))
    monkeypatch.setattr(sys, "argv", [str(path), "--case", "limited_sample", "--data-root", "unused"])
    assert module.main() == 0 and '"execute": false' in capsys.readouterr().out
    assert "top=1" in query("limited_sample", "4500001466")
    assert "实时 Schema" in query("complete_unique_key", "4500001466")
