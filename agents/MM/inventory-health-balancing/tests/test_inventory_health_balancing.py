import json
from copy import deepcopy
from datetime import date

from inventory_health_balancing import analyze
from inventory_health_balancing.cli import main
from inventory_health_balancing.fixture import demo_path


def test_historical_demo_suppresses_transfer_quantity() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    assert result["status"] == "inconclusive"
    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["expiry_candidates"] == 1
    assert metrics["confirmed_transfer_quantity"] is None
    assert "historical_stock_balance_evidence" in result["missing_evidence"]


def test_current_date_demo_can_quantify_transfer_candidate() -> None:
    payload = deepcopy(json.loads(demo_path().read_text(encoding="utf-8")))
    payload["run_input"]["as_of"] = date.today().isoformat()

    result = analyze(payload)

    metrics = {item["id"]: item["value"] for item in result["metrics"]}
    assert metrics["confirmed_transfer_quantity"] == "80"


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "inventory_health_balancing_deterministic_v1" in capsys.readouterr().out
