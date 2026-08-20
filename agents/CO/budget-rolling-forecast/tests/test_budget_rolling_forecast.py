import json

from budget_rolling_forecast import analyze
from budget_rolling_forecast.cli import main
from budget_rolling_forecast.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "budget_rolling_forecast_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "budget_rolling_forecast_deterministic_v1" in capsys.readouterr().out


def test_complete_empty_plan_is_unavailable_not_zero() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["plan_items"]["step_results"]["plan_items"]["results"] = []

    result = analyze(payload)
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert metrics["annual_plan"] is None
    assert metrics["full_year_forecast"] == "1200"
    assert "budget_evidence_missing" in result["business_report"]["limitations"]
