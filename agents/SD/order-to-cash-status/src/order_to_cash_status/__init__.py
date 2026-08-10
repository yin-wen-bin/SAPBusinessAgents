"""Order-to-Cash Status agent."""
from datetime import date
from pathlib import Path
from sd_o2c_shared import analyze, load_evidence

SLUG = "order-to-cash-status"
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "demo.json"

def run_demo(*, as_of: date = date(2026, 8, 10)):
    return analyze(SLUG, "客户订单当前进行到哪一步，为什么还没有回款？", load_evidence(DEFAULT_FIXTURE), as_of=as_of)

__all__ = ["DEFAULT_FIXTURE", "SLUG", "run_demo"]
