import json

from co_month_end_allocation_settlement import analyze
from co_month_end_allocation_settlement.cli import main
from co_month_end_allocation_settlement.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "co_month_end_allocation_settlement_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "co_month_end_allocation_settlement_deterministic_v1" in capsys.readouterr().out
