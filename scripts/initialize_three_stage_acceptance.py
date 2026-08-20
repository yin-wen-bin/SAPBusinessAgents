from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def initialize(root: Path, excluded: set[str]) -> int:
    tested_at = datetime.now(timezone.utc).isoformat()
    changed = 0
    for path in sorted((root / "agents").glob("*/*/agent.json")):
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schemaVersion") != 2:
            continue
        slug = str(manifest.get("slug") or path.parent.name)
        if slug in excluded:
            continue
        previous = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
        report_path = path.parent / "docs" / "three-stage-live-acceptance.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        prior_note = (
            f"- Prior fixed-chain validation: `{previous.get('verdict')}` at "
            f"`{previous.get('testedAt')}`. This is retained as preflight evidence only.\n"
            if previous
            else "- Prior fixed-chain validation: not recorded.\n"
        )
        title = manifest.get("name") if isinstance(manifest.get("name"), dict) else {}
        report = f"""# Three-stage live SAP acceptance: {title.get('en') or slug}

## Verdict

`BLOCKED` / `executable=false`

The Agent remains visible but execution is disabled. A Codex App direct-SAP baseline and the
matching SAPBusinessAgents free-query adjudication have not yet both completed for this Agent.
An earlier deterministic GET-only preflight, where present, does not satisfy the new three-stage
acceptance contract.

## Scope

- Agent: `{slug}`
- Module: `{manifest.get('module')}`
- Required chain: independent direct SAP baseline -> free query -> fixed Agent
- Free-query comparison: `NOT_TESTED`
- Fixed-Agent comparison: `NOT_TESTED`
{prior_note}- SAP write operations: none

## Required next evidence

1. Discover a bounded live business case without using SAPBusinessAgents capabilities.
2. Freeze a GET-only direct baseline with schema and result SHA-256 hashes.
3. Run the same business conditions through free query and adjudicate semantic differences.
4. Run the fixed Agent with the matching structured input and compare stable business keys.

No business conclusion is asserted by this report.
"""
        report_path.write_text(report, encoding="utf-8")
        manifest["status"] = "Three-stage live acceptance blocked"
        manifest["validation"] = {
            "verdict": "BLOCKED",
            "testedAt": tested_at,
            "evidenceScope": "bounded",
            "providers": ["embedded-sap-odata"],
            "summary": {
                "zh": "三级真机闭环尚未完成；Agent 保留展示但禁用执行。",
                "en": "Three-stage live acceptance is incomplete; the Agent remains visible but disabled.",
            },
            "reportPath": "docs/three-stage-live-acceptance.md",
            "executable": False,
            "freeQueryComparison": "NOT_TESTED",
            "fixedAgentComparison": "NOT_TESTED",
        }
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize fail-closed three-stage live acceptance for fixed Agents."
    )
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    count = initialize(Path(args.repository).resolve(), set(args.exclude))
    print(json.dumps({"updated_agents": count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
