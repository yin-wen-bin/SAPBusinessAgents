"""Shortage Allocation Advisor agent."""
from datetime import date
from pathlib import Path
from sd_o2c_shared import analyze, load_evidence

SLUG = "shortage-allocation-advisor"
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "demo.json"

def run_demo(*, as_of: date = date(2026, 8, 10)):
    return analyze(SLUG, "库存不足时应该优先分配给哪些订单？", load_evidence(DEFAULT_FIXTURE), as_of=as_of)

__all__ = ["DEFAULT_FIXTURE", "SLUG", "run_demo"]
