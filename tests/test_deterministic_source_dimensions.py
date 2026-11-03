"""Offline regressions for deterministic projection compilation; no SAP/SDK."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from sap_business_agents_platform.deterministic_projection import retain_account_item_dimensions
from sap_business_agents_platform.engine import RunCoordinator
from sap_business_agents_platform.normalization import SapValueNormalizer
from sap_business_agents_platform.source_semantics import catalog, plan_issues


def legacy_plan():
    source = next(p for p in catalog()["sources"] if p["view"] == "account_items")
    return {"service_name": source["service_name"], "odata_version": source["odata_version"],
            "entity_set": source["entity_set"], "http_method": "GET",
            "select_fields": [field for field in source["detail_fields"] if field != "SpecialGLCode"],
            "filters": [{"field": "CompanyCode", "operator": "eq", "value": "1710"}],
            "tuple_filters": [{"fields": ["FiscalYear", "AccountingDocument"], "values": [["2022", "0000000001"]]}]}


def test_compiler_retains_classification_and_exact_original_scope_without_mutation():
    plan = legacy_plan()
    before = copy.deepcopy(plan)
    assert plan_issues(plan, catalog())[0]["required_fields"] == ["SpecialGLCode"]
    compiled = retain_account_item_dimensions(plan)
    assert not plan_issues(compiled, catalog())
    assert compiled["select_fields"] == before["select_fields"] + ["SpecialGLCode"]
    assert {k: v for k, v in compiled.items() if k != "select_fields"} == {k: v for k, v in before.items() if k != "select_fields"}
    assert plan == before
    # The Provider/query repair contract remains strict for an uncompiled plan.
    assert plan_issues(plan, catalog())[0]["required_fields"] == ["SpecialGLCode"]


def test_multi_step_inheritance_and_idempotency():
    plan = legacy_plan()
    service, version = plan.pop("service_name"), plan.pop("odata_version")
    multi = {"service_name": service, "odata_version": version, "steps": [{"step_id": name, **copy.deepcopy(plan)} for name in ("linked", "full", "clearing")]}
    compiled = retain_account_item_dimensions(multi)
    assert all(step["select_fields"][-1] == "SpecialGLCode" for step in compiled["steps"])
    assert not plan_issues(compiled, catalog())
    assert retain_account_item_dimensions(compiled) == compiled


@pytest.mark.parametrize("change", [
    {"select_fields": []}, {"http_method": "POST"},
    {"select_fields": ["Supplier"]},
    {"output_contract": {"mode": "explicit", "display_grain": "ledger_account_summary"}},
    {"group_by": ["CompanyCode"]}, {"aggregates": [{"field": "Amount", "operation": "sum"}]},
    {"entity_set": "UnregisteredEntity"},
])
def test_non_detail_or_unregistered_read_is_not_rewritten(change):
    plan = {**legacy_plan(), **change}
    assert retain_account_item_dimensions(plan) == plan


@pytest.mark.parametrize('metadata_available', [True, False])
def test_engine_compiles_before_provider_validation_and_keeps_failure_gate(metadata_available):
    captured = []
    class Provider:
        async def validate_plan(self, plan, query):
            captured.append(copy.deepcopy(plan))
            return {"ok": metadata_available and not plan_issues(plan, catalog())}
        async def execute_plan(self, plan, query):
            assert plan == captured[-1]
            return {"ok": True, "data": {"results": []}}
    owner = SimpleNamespace(normalizer=SapValueNormalizer(), sap_read=Provider(),
        store=SimpleNamespace(append_event=lambda *a: None, get_harness_state=lambda *a: {}),
        relationships=SimpleNamespace(validate_plans=lambda *a: []))
    rendered = {"plan": legacy_plan()}
    request = RunCoordinator._execute_step(owner, "offline", {"id": "collect", "executor": "sap_read", "operation": "execute_plan"}, rendered, "unchanged business scope")
    if metadata_available:
        assert asyncio.run(request)["ok"]
    else:
        from sap_business_agents_platform.sap_read import SapReadError
        with pytest.raises(SapReadError, match='Provider rejected'):
            asyncio.run(request)
    assert captured[0]["select_fields"][-1] == "SpecialGLCode"
    assert rendered["plan"]["filters"] == legacy_plan()["filters"]
