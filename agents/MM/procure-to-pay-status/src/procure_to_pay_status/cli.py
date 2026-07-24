"""Command-line entry point."""

from __future__ import annotations

import argparse
from datetime import date
import json
import sys
from pathlib import Path
from typing import Sequence

from .analyzer import P2PAnalysisError
from .assistant import P2PStatusAssistant
from .extractor import ParameterExtractionError
from .fixture import DEFAULT_FIXTURE, FixtureP2PDataSource
from .formatting import render_markdown


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须是 YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="p2p-status",
        description="查询 SAP 采购订单从收货、发票校验到付款的逐项目状态。",
    )
    parser.add_argument("question", nargs="+", help="自然语言问题，例如：PO 4500001234 是否已付款？")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="SAP-like JSON fixture 路径")
    parser.add_argument("--as-of", type=_parse_date, default=date.today(), help="判断付款到期状态的日期")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Windows may inherit a legacy code page (for example cp932) even when the
    # question and response are Chinese. Explicit UTF-8 keeps CLI output stable.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        assistant = P2PStatusAssistant(FixtureP2PDataSource(args.fixture))
        report = assistant.ask(" ".join(args.question), as_of=args.as_of)
    except (OSError, ValueError, ParameterExtractionError, P2PAnalysisError) as exc:
        print(f"查询失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
