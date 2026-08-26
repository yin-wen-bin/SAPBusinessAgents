from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import InputValidationError, _validate_input
from sap_business_agents_platform.rules import resolve_production_cost_scope


ROOT = Path(__file__).resolve().parents[1]


def _embedded(step_id: str, *rows: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "status": "completed" if complete else "inconclusive",
        "source_complete": complete,
        "source_truncated": not complete,
        "step_results": {step_id: {"results": list(rows), "source_complete": complete}},
    }


def _cost_skill(*details: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "skill_id": "sap-production-order-cost-analysis",
        "status": "complete" if complete else "partial",
        "read_only": True,
        "validated": complete,
        "order_context": {
            "manufacturing_order": "1001233",
            "company_code": "1710",
            "controlling_area": "A000",
            "object_number": "OR000001001233",
        },
        "analysis_scope": {
            "analysis_period_from": "2020011",
            "analysis_period_to": "2020011",
            "ledger": "0L",
            "currency_role": "10",
            "target_cost_variant": 1,
        },
        "cost_element_details": list(details),
        "totals": {},
        "relationship_evidence": {
            "source": "AUFK",
            "source_complete": True,
            "relationship_fields": ["AUFNR", "OBJNR", "KOKRS", "BUKRS"],
        },
        "completeness": {
            "source_complete": complete,
            "evidence_complete": complete,
            "paging_complete": complete,
        },
        "validation_issues": [] if complete else [{"code": "parameterized_production_cost_cds_unavailable"}],
    }


def _detail(cost_element: str, plan: str, target: str, actual: str) -> dict[str, object]:
    return {
        "company_code": "1710",
        "controlling_area": "A000",
        "ledger": "0L",
        "currency_role": "10",
        "cost_element": cost_element,
        "plan_cost": plan,
        "target_cost": target,
        "actual_cost": actual,
        "actual_target_variance": str(Decimal(actual) - Decimal(target)),
        "currency": "USD",
        "analysis_period_from": "2020011",
        "analysis_period_to": "2020011",
        "evidence_source": "released_production_order_cost_cds",
    }


def _payload(skill: dict[str, object]) -> dict[str, object]:
    return {
        "agent_id": "product-cost-variance",
        "run_input": {"manufacturing_order": "1001233"},
        "scope": {
            "scope_resolved": True,
            "analysis_period_from": "2020011",
            "analysis_period_to": "2020011",
        },
        "evidence": {
            "production_order": _embedded(
                "production_order",
                {
                    "ManufacturingOrder": "1001233",
                    "CompanyCode": "1710",
                    "Material": "EWMS4-50",
                    "ProductionPlant": "1710",
                },
            )
        },
        "fallbacks": {"production_cost": skill},
        "known_gaps": [],
    }


def test_scope_is_derived_from_complete_actual_posting_periods() -> None:
    result = resolve_production_cost_scope(
        {
            "run_input": {"manufacturing_order": "1001233"},
            "actual_cost": _embedded(
                "actual",
                {"FiscalYear": "2020", "FiscalPeriod": "11", "CompanyCode": "1710", "ControllingArea": "A000", "CompanyCodeCurrency": "USD"},
                {"FiscalYear": "2020", "FiscalPeriod": "12", "CompanyCode": "1710", "ControllingArea": "A000", "CompanyCodeCurrency": "USD"},
            ),
        }
    )
    assert result["scope_resolved"] is True
    assert result["analysis_period_from"] == "2020011"
    assert result["analysis_period_to"] == "2020012"


def test_scope_year_and_period_contracts() -> None:
    year = resolve_production_cost_scope(
        {"run_input": {"manufacturing_order": "1001233", "fiscal_year": "2020"}, "actual_cost": {}}
    )
    assert (year["analysis_period_from"], year["analysis_period_to"]) == ("2020001", "2020016")
    exact = resolve_production_cost_scope(
        {"run_input": {"manufacturing_order": "1001233", "fiscal_year": "2020", "period": 11}, "actual_cost": {}}
    )
    assert (exact["analysis_period_from"], exact["analysis_period_to"]) == ("2020011", "2020011")
    with pytest.raises(ValueError, match="fiscal_year"):
        resolve_production_cost_scope(
            {"run_input": {"manufacturing_order": "1001233", "period": 11}, "actual_cost": {}}
        )


def test_complete_cost_elements_produce_deterministic_variance() -> None:
    result = evaluate_business_agent(
        _payload(
            _cost_skill(
                _detail("400000", "100", "90", "100"),
                _detail("500000", "20", "20", "20"),
            )
        )
    )
    output = result["workflow_output"]
    assert result["rule_id"] == "production_order_cost_variance_v2"
    assert output["plan_cost_total"] == "120"
    assert output["target_cost_total"] == "110"
    assert output["actual_cost_total"] == "120"
    assert output["actual_target_variance"] == "10"
    assert output["cost_status"] == "unfavorable_variance"
    assert output["business_status"] == "attention"
    assert output["source_complete"] is True
    assert output["evidence_complete"] is True


def test_zero_target_with_actual_cost_is_unplanned_cost() -> None:
    result = evaluate_business_agent(_payload(_cost_skill(_detail("400000", "0", "0", "5"))))
    assert result["workflow_output"]["cost_status"] == "unplanned_cost"
    assert result["workflow_output"]["business_status"] == "attention"


def test_missing_released_cost_evidence_stays_inconclusive() -> None:
    result = evaluate_business_agent(_payload(_cost_skill(complete=False)))
    output = result["workflow_output"]
    assert output["business_status"] == "inconclusive"
    assert output["plan_cost_total"] is None
    assert output["target_cost_total"] is None
    assert output["actual_cost_total"] is None
    assert output["source_complete"] is False
    assert "production_cost_evidence" in result["missing_evidence"]
    assert "parameterized_production_cost_cds_unavailable" in result["missing_evidence"]


def test_manifest_uses_new_skill_and_remains_blocked_until_free_query_acceptance() -> None:
    manifest = json.loads(
        (ROOT / "agents/CO/product-cost-variance/agent.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.2.0"
    assert manifest["execution"]["inputSchema"]["required"] == ["manufacturing_order"]
    skill_steps = [step for step in manifest["execution"]["steps"] if step["executor"] == "skill"]
    assert [step["skillId"] for step in skill_steps] == ["sap-production-order-cost-analysis"]
    assert manifest["validation"]["verdict"] == "BLOCKED"
    assert manifest["validation"]["executable"] is False
    assert manifest["validation"]["blockingLimitations"] == ["free_query_skill_execution"]
    assert manifest["validation"]["fixedAgentComparison"] == "MATCH"
    assert manifest["validation"]["freeQueryComparison"] == "BLOCKED"
    assert "standard_cost_evidence" not in json.dumps(manifest)
    with pytest.raises(InputValidationError) as exc_info:
        _validate_input(
            {"manufacturing_order": "1001233", "period": 11},
            manifest["execution"]["inputSchema"],
        )
    assert exc_info.value.detail["constraint"] == "dependent_required"
