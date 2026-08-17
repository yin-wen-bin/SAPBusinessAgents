import json

from inventory_health_balancing import analyze
from inventory_health_balancing.cli import main
from inventory_health_balancing.fixture import demo_path


def test_demo_reports_expiry_and_transfer_quantity() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["status"] == "complete"
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["expiry_candidates"] == 1
    assert metrics["confirmed_transfer_quantity"] == "80"


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "inventory_health_balancing_deterministic_v1" in capsys.readouterr().out
