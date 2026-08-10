"""Order-to-Cash Anomaly Monitor agent."""
from datetime import date
from pathlib import Path
from sd_o2c_shared import analyze, load_evidence

SLUG = "order-to-cash-anomaly-monitor"
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "demo.json"

def run_demo(*, as_of: date = date(2026, 8, 10)):
    return analyze(SLUG, "汇总最近订单到现金流程中最严重的异常。", load_evidence(DEFAULT_FIXTURE), as_of=as_of)

__all__ = ["DEFAULT_FIXTURE", "SLUG", "run_demo"]
