"""Command line entry point for the runnable AP assistant slice."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Sequence

from .mock_adapter import MockSapApDataAdapter
from .service import ApPaymentAssistant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SAP AP Payment Assistant")
    parser.add_argument("query", help="自然语言查询")
    parser.add_argument("--as-of", type=date.fromisoformat, help="查询基准日 YYYY-MM-DD")
    parser.add_argument("--fixture", type=Path, help="替换默认 mock SAP JSON fixture")
    parser.add_argument("--compact", action="store_true", help="输出紧凑 JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    assistant = ApPaymentAssistant(MockSapApDataAdapter(args.fixture))
    response = assistant.ask(args.query, as_of=args.as_of)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.compact:
        print(json.dumps(response.to_dict(), ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
    return 0 if response.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
