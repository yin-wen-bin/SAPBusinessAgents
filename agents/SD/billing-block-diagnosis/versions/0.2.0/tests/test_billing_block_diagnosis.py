from datetime import date
from billing_block_diagnosis import run_demo

def test_billing_block_diagnosis_demo_contract_and_rule():
    report = run_demo(as_of=date(2026, 8, 10))
    assert report["schema_version"] == "1.0"
    assert report["agent"] == "billing-block-diagnosis"
    assert report["status"] == "blocked"
    assert report["read_only"] is True
    assert report["pagination"]["complete"] is True
