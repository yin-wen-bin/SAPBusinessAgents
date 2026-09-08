from datetime import date
from delivery_delay_prediction import run_demo

def test_delivery_delay_prediction_demo_contract_and_rule():
    report = run_demo(as_of=date(2026, 8, 10))
    assert report["schema_version"] == "1.0"
    assert report["agent"] == "delivery-delay-prediction"
    assert report["status"] == "attention"
    assert report["read_only"] is True
    assert report["pagination"]["complete"] is True
