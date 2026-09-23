import json

from cost_center_expense_anomaly import analyze
from cost_center_expense_anomaly.cli import main
from cost_center_expense_anomaly.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "cost_center_expense_anomaly_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "cost_center_expense_anomaly_deterministic_v1" in capsys.readouterr().out


def test_complete_empty_plan_is_missing_evidence_not_zero_plan() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["plan_items"]["step_results"]["plan_items"]["results"] = []

    result = analyze(payload)
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert result["business_status"] == "capability_blocked"
    assert metrics["plan_amount"] is None
    assert metrics["variance_amount"] is None
    assert "plan_evidence_missing" in result["missing_evidence"]
