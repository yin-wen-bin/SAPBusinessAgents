import json

from supplier_performance_risk import analyze
from supplier_performance_risk.cli import main
from supplier_performance_risk.fixture import demo_path


def test_demo_computes_formal_otif_with_five_samples() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["status"] == "complete"
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["due_schedule_lines"] == 5
    assert metrics["otif_percent"] == 80.0


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "supplier_performance_risk_deterministic_v1" in capsys.readouterr().out
