from datetime import date
from delivered_not_billed import run_demo

def test_delivered_not_billed_demo_contract_and_rule():
    report = run_demo(as_of=date(2026, 8, 10))
    assert report["schema_version"] == "1.0"
    assert report["agent"] == "delivered-not-billed"
    assert report["status"] == "attention"
    assert report["read_only"] is True
    assert report["pagination"]["complete"] is True
