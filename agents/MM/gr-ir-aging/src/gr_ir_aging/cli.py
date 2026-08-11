"""CLI for evidence-backed GR/IR ageing."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Sequence

from .analyzer import GrIrAnalysisError, GrIrAnalyzer
from .evidence import EvidenceSnapshot, EvidenceValidationError
from .formatting import render_markdown
from .model import QueryParameters


DEFAULT_FIXTURE = Path(__file__).with_name("fixtures") / "gr_ir_demo.json"


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _buckets(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item) for item in _csv(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("buckets must be comma-separated integers") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze GR/IR ageing from complete read-only SAPClaw evidence")
    parser.add_argument("--company-code", required=True)
    parser.add_argument("--key-date", type=_date, required=True)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--purchase-orders", type=_csv, default=())
    parser.add_argument("--plants", type=_csv, default=())
    parser.add_argument("--gl-accounts", type=_csv, default=())
    parser.add_argument("--ageing-threshold", type=int, default=0)
    parser.add_argument("--ageing-buckets", type=_buckets, default=(30, 60, 90))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        query = QueryParameters(
            company_code=args.company_code,
            key_date=args.key_date,
            purchasing_documents=args.purchase_orders,
            plants=args.plants,
            gl_accounts=args.gl_accounts,
            ageing_threshold=args.ageing_threshold,
            ageing_buckets=args.ageing_buckets,
        )
        result = GrIrAnalyzer().analyze(EvidenceSnapshot.load(args.evidence), query)
    except (OSError, ValueError, GrIrAnalysisError, EvidenceValidationError) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) if args.json else render_markdown(result))
    return 3 if result.status.value == "inconclusive" else 0


if __name__ == "__main__":
    raise SystemExit(main())
