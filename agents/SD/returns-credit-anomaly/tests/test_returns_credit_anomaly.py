from datetime import date
from returns_credit_anomaly import run_demo

def test_returns_credit_anomaly_demo_contract_and_rule():
    report = run_demo(as_of=date(2026, 8, 10))
    assert report["schema_version"] == "1.0"
    assert report["agent"] == "returns-credit-anomaly"
    assert report["status"] == "attention"
    assert report["read_only"] is True
    assert report["pagination"]["complete"] is True
