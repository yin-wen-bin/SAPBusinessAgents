from __future__ import annotations

import argparse
import calendar
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from .analyzer import GrirAnalyzer, RuleConfig
from .fixture_adapter import FixtureGrirDataSource
from .models import AnalysisCriteria
from .reporting import write_report
from .service import GrirClearingService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze GR/IR exceptions from normalized SAP data")
    parser.add_argument("--fixture", type=Path, required=True, help="normalized fixture JSON file")
    parser.add_argument("--as-of", type=date.fromisoformat, help="analysis date (YYYY-MM-DD)")
    parser.add_argument("--month", help="analysis month (YYYY-MM); uses month end or today for current month")
    parser.add_argument("--company-code")
    parser.add_argument("--plant")
    parser.add_argument("--po-number")
    parser.add_argument("--activity-from", type=date.fromisoformat)
    parser.add_argument("--activity-to", type=date.fromisoformat)
    parser.add_argument("--quantity-tolerance", type=Decimal, default=Decimal("0.001"))
    parser.add_argument("--amount-tolerance", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--long-age-days", type=int, default=90)
    parser.add_argument("--high-severity-days", type=int, default=180)
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        as_of = _resolve_as_of(args.as_of, args.month)
        criteria = AnalysisCriteria(
            as_of_date=as_of,
            company_code=args.company_code,
            plant=args.plant,
            po_number=args.po_number,
            activity_from=args.activity_from,
            activity_to=args.activity_to,
        )
        config = RuleConfig(
            quantity_tolerance=args.quantity_tolerance,
            amount_tolerance=args.amount_tolerance,
            long_outstanding_days=args.long_age_days,
            high_severity_days=args.high_severity_days,
        )
    except ValueError as exc:
        parser.error(str(exc))
    source = FixtureGrirDataSource.from_file(args.fixture)
    report = GrirClearingService(source, GrirAnalyzer(config)).analyze(criteria)
    output = write_report(report, args.output, args.format)
    print(
        f"GR/IR analysis complete: examined={report.summary.examined_po_items}, "
        f"exceptions={report.summary.exception_count}, output={output.resolve()}"
    )
    return 0


def _resolve_as_of(explicit: date | None, month: str | None) -> date:
    if explicit and month:
        raise ValueError("use either --as-of or --month, not both")
    if explicit:
        return explicit
    today = date.today()
    if not month:
        return today
    try:
        year_text, month_text = month.split("-", 1)
        year, month_number = int(year_text), int(month_text)
        last_day = date(year, month_number, calendar.monthrange(year, month_number)[1])
    except (ValueError, IndexError) as exc:
        raise ValueError("--month must use YYYY-MM") from exc
    return min(today, last_day) if (year, month_number) >= (today.year, today.month) else last_day
