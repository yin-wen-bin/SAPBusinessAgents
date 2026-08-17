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
