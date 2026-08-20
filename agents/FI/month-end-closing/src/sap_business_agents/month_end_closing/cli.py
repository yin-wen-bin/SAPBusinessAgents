"""Command-line entry point for a fixture-backed closing assessment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Sequence

from .config import load_checklist
from .engine import MonthEndClosingAssistant
from .gateway import FixtureSapGateway, SapGateway
from .models import ClosingContext
from .se16n_fallback import Se16nObservationGateway


def parse_question(question: str) -> ClosingContext:
    year = re.search(r"(?P<year>\d{4})\s*年", question)
    period = re.search(r"(?P<period>\d{1,2})\s*(?:月|期)", question)
    company = re.search(r"公司代码\s*[:：]?\s*(?P<company>[A-Za-z0-9]{1,8})", question)
    if not (year and period and company):
        raise ValueError("问题中必须包含年份、月份/期间和公司代码")
    return ClosingContext(company.group("company"), int(year.group("year")), int(period.group("period")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SAP Month-end Closing Assistant")
    parser.add_argument("--question", help="例如：检查 2026 年 7 月公司代码 1010 的月结状态。")
    parser.add_argument("--company-code")
    parser.add_argument("--year", type=int)
    parser.add_argument("--period", type=int)
    parser.add_argument("--config", type=Path, default=Path("config/month_end_checklist.toml"))
    parser.add_argument(
        "--gateway",
        choices=("fixture", "se16n"),
        default="fixture",
    )
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/1010_2026_07.json"))
    parser.add_argument(
        "--sap-client",
        default="100",
        help="必须与 SE16N 证据一致的三位 SAP 客户端。",
    )
    parser.add_argument(
        "--se16n-manifest",
        type=Path,
        help=(
            "经复核且绑定导出文件 SHA-256 的 SE16N 清单；"
            "使用 --gateway se16n 时必须提供。"
        ),
    )
    parser.add_argument("--output", type=Path, help="可选 JSON 报告输出路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if args.question:
        context = parse_question(args.question)
    elif args.company_code and args.year is not None and args.period is not None:
        context = ClosingContext(args.company_code, args.year, args.period)
    else:
        raise SystemExit("请提供 --question，或同时提供 --company-code、--year、--period")

    gateway: SapGateway
    if args.gateway == "fixture":
        gateway = FixtureSapGateway.from_file(args.fixture)
    else:
        if args.se16n_manifest is None:
            raise SystemExit("--gateway se16n 必须提供 --se16n-manifest")
        gateway = Se16nObservationGateway.from_file(
            args.se16n_manifest, expected_client=args.sap_client
        )
    assistant = MonthEndClosingAssistant(load_checklist(args.config), gateway)
    payload = json.dumps(assistant.assess(context).to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _configure_utf8_stdout() -> None:
    """Make Chinese JSON reliable in Windows shells with a non-UTF-8 code page."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
