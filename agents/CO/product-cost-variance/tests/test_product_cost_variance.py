import json

from product_cost_variance import analyze
from product_cost_variance.cli import main
from product_cost_variance.fixture import demo_path


def test_demo_is_deterministic_and_complete() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["rule_id"] == "product_cost_variance_deterministic_v1"
    assert result["status"] == "complete"
    assert result["source_complete"] is True
    assert result["business_report"]["headline"]["zh"]


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "product_cost_variance_deterministic_v1" in capsys.readouterr().out
