from datetime import date
from shortage_allocation_advisor import run_demo

def test_shortage_allocation_advisor_demo_contract_and_rule():
    report = run_demo(as_of=date(2026, 8, 10))
    assert report["schema_version"] == "1.0"
    assert report["agent"] == "shortage-allocation-advisor"
    assert report["status"] == "attention"
    assert report["read_only"] is True
    assert report["pagination"]["complete"] is True
