import json

from internal_order_project_control import analyze
from internal_order_project_control.cli import main
from internal_order_project_control.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "internal_order_project_control_deterministic_v4"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["evidence_complete"] is True
    assert result["actual_amount"] == "0.00"
    assert result["estimate_at_completion"] == "200.00"
    assert result["remaining_budget"] == "800.00"
    assert result["budget_ledger"] == "0001"
    assert result["comparison_currency"] == "USD"
    assert result["mode_acceptance_status"] == "pass"
    assert set(result["commitment_status_by_type"]) == {"21", "22", "24", "26"}
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "internal_order_project_control_deterministic_v4" in capsys.readouterr().out


def test_missing_plan_budget_and_commitment_are_not_reported_as_zero() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["order_plan"]["data"]["results"] = []
    payload["fallbacks"] = {}

    result = analyze(payload)
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert result["business_status"] == "inconclusive"
    assert result["evidence_complete"] is False
    assert metrics["actual_amount"] == "0.00"
    for metric in (
        "plan_amount",
        "budget_amount",
        "commitment_amount",
        "estimate_at_completion",
        "remaining_budget",
    ):
        assert metrics[metric] is None


def test_ambiguous_plan_categories_are_not_combined() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["run_input"].pop("planning_category")
    payload["evidence"]["order_plan_discovery"]["data"]["results"].append(
        {
            "OrderID": "600468",
            "PlanningCategory": "FCST",
            "AmountInCompanyCodeCurrency": "100.00",
            "CompanyCodeCurrency": "USD",
        }
    )

    result = analyze(payload)

    assert result["plan_status"] == "ambiguous"
    assert result["plan_amount"] is None
    assert result["business_status"] == "inconclusive"
    assert result["available_planning_categories"] == ["FCST", "PLAN"]


def test_unconfigured_value_type_and_mixed_currency_fail_closed() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["fallbacks"]["budget"]["rows"].append(
        {
            "OBJNR": "OR000000600468",
            "GJAHR": "2026",
            "WRTTP": "42",
            "VERSN": "000",
            "TWAER": "EUR",
            "WTJHR": "50.00",
        }
    )
    payload["fallbacks"]["commitment"]["commitment_details"][0]["currency"] = "EUR"
    payload["fallbacks"]["commitment"]["commitment_totals"]["groups"][0]["currency"] = "EUR"

    result = analyze(payload)

    assert result["budget_amount"] == "1000.00"
    assert result["evidence_complete"] is False
    assert {"unsupported_value_type", "currency_not_comparable"} <= set(result["evidence_gaps"])


def test_budget_rows_from_multiple_ledgers_are_not_double_counted() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    first = payload["fallbacks"]["budget"]["rows"][0]
    first["LEDNR"] = "0001"
    payload["fallbacks"]["budget"]["rows"].append({**first, "LEDNR": "0003"})
    payload["control_value_types"]["budget_ledger"] = None

    result = analyze(payload)

    assert result["budget_amount"] is None
    assert result["budget_status"] == "unknown"
    assert "budget_ledger_ambiguous" in result["evidence_gaps"]


def test_missing_commitment_type_and_unaccepted_mode_suppress_eac() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["fallbacks"]["commitment"]["commitment_totals"]["groups"] = [
        row
        for row in payload["fallbacks"]["commitment"]["commitment_totals"]["groups"]
        if row["commitment_type"] != "26"
    ]
    payload["control_value_types"]["mode_acceptance"]["INTERNAL_ORDER"] = "BLOCKED"

    result = analyze(payload)

    assert result["commitment_status_by_type"]["26"] == "unknown"
    assert result["commitment_amount"] is None
    assert result["estimate_at_completion"] is None
    assert result["business_status"] == "inconclusive"
    assert {"commitment_evidence", "internal_order_mode_acceptance"} <= set(result["evidence_gaps"])
