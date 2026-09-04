from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .fixture_gateway import FixtureARGateway
from .models import to_primitive
from .service import ARCollectionAssistant


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ar-collection",
        description="Run the review-first AR Collection Assistant against a fixture.",
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="Natural-language request, for example: 列出本周需要催收的客户",
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--fixture", help="Path to a replacement fixture JSON file")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    query = " ".join(args.query).strip() or "列出本周需要催收的客户"
    assistant = ARCollectionAssistant(FixtureARGateway(args.fixture))
    result = to_primitive(assistant.query(query, args.as_of))
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
