from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def test_month_end_direct_evidence_alias_reaches_rule() -> None:
    result = evaluate_business_agent(
        {
            "agent_id": "month-end-closing",
            "run_input": {"company_code": "1710", "fiscal_year": "2018", "period": "3"},
            "known_gaps": [
                "period_control_asset_depreciation_and_specialized_closing_checks"
            ],
            "evidence": {
                "fi_period_items": {
                    "source_complete": True,
                    "step_results": {},
                    "data": {
                        "source_complete": True,
                        "results": [{"AccountingDocument": "1"}, {"AccountingDocument": "2"}],
                    },
                }
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["business_report"]["metrics"]}
    assert metrics["fi_rows"] == 2


def test_month_end_step_results_are_not_double_counted_with_data_summary() -> None:
    rows = [{"AccountingDocument": "1"}, {"AccountingDocument": "2"}]
    result = evaluate_business_agent(
        {
            "agent_id": "month-end-closing",
            "run_input": {"company_code": "1710", "fiscal_year": "2018", "period": "3"},
            "evidence": {
                "fi_period_items": {
                    "source_complete": True,
                    "step_results": {
                        "fi_period_items": {"source_complete": True, "results": rows}
                    },
                    "data": {"source_complete": True, "results": rows},
                }
            },
        }
    )

    metrics = {item["id"]: item["value"] for item in result["business_report"]["metrics"]}
    assert metrics["fi_rows"] == 2


def test_month_end_manifest_exposes_direct_query_alias() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "agents" / "FI" / "month-end-closing" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    collect, evaluate = manifest["execution"]["steps"]
    assert collect["request"]["plan"]["step_id"] == "fi_period_items"
    assert evaluate["inputMapping"]["evidence"] == {
        "fi_period_items": "{{steps.collect_month_end_evidence.output}}"
    }
