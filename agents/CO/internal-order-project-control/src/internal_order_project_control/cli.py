from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import analyze
from .fixture import demo_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only internal-order-project-control")
    parser.add_argument("--input", type=Path, default=demo_path())
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    result = analyze(json.loads(args.input.read_text(encoding="utf-8")))
    if args.format == "markdown":
        report = result["business_report"]
        print(f"# {report['headline']['en']}\n\n{report['overview']['en']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
