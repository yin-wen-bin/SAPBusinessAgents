import json

from inventory_health_balancing import analyze
from inventory_health_balancing.cli import main
from inventory_health_balancing.fixture import demo_path


def test_demo_returns_current_snapshot_without_health_checks() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    metrics = {item["id"]: item["value"] for item in result["metrics"]}

    assert result["status"] == "complete"
    assert result["business_status"] == "snapshot_only"
    assert result["workflow_output"]["slow_moving_status"] == "not_requested"
    assert result["workflow_output"]["obsolete_status"] == "not_requested"
    assert result["workflow_output"]["expiry_status"] == "not_requested"
    assert metrics["current_unrestricted_stock"] == "100"
    assert "confirmed_transfer_quantity" not in metrics


def test_demo_contains_no_historical_balance_or_transfer_output() -> None:
    result = analyze(json.loads(demo_path().read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False).lower()

    assert "historical_stock_balance_evidence" not in rendered
    assert "confirmed_transfer_quantity" not in rendered
    assert "transfer" not in rendered
    assert "调拨" not in rendered


def test_cli(capsys) -> None:
    assert main([]) == 0
    assert "inventory_health_check_deterministic_v2" in capsys.readouterr().out
