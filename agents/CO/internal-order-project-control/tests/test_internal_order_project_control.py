import json

from internal_order_project_control import analyze
from internal_order_project_control.cli import main
from internal_order_project_control.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "internal_order_project_control_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "internal_order_project_control_deterministic_v1" in capsys.readouterr().out


def test_missing_plan_budget_and_commitment_are_not_reported_as_zero() -> None:
    payload = json.loads(demo_path().read_text(encoding="utf-8"))
    payload["evidence"]["order_plan"]["step_results"]["order_plan"]["results"] = []
    payload["fallbacks"] = {}

    result = analyze(payload)
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert result["business_status"] == "capability_blocked"
    assert metrics["actual_amount"] == "400"
    for metric in (
        "plan_amount",
        "budget_amount",
        "commitment_amount",
        "estimate_at_completion",
        "remaining_budget",
    ):
        assert metrics[metric] is None
