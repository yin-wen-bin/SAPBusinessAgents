from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def generate(root: Path, output: Path) -> Path:
    rows = []
    for path in sorted((root / "agents").glob("*/*/agent.json")):
        manifest = _load(path)
        validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
        verdict = str(validation.get("verdict") or "NOT_TESTED")
        executable = validation.get("executable") is True
        if verdict not in {"PASS", "FAIL", "BLOCKED"}:
            raise ValueError(f"{manifest.get('slug')} has non-final verdict {verdict}")
        if executable != (verdict == "PASS"):
            raise ValueError(f"{manifest.get('slug')} violates the PASS/executable gate")
        report = path.parent / str(validation.get("reportPath") or "")
        if not report.is_file():
            raise ValueError(f"{manifest.get('slug')} acceptance report is missing")
        rows.append(
            {
                "module": path.parent.parent.name,
                "agent": str(manifest.get("slug") or ""),
                "verdict": verdict,
                "executable": executable,
                "free": str(validation.get("freeQueryComparison") or "-") ,
                "fixed": str(validation.get("fixedAgentComparison") or "-"),
                "scope": str(validation.get("evidenceScope") or "-"),
                "limitations": ", ".join(str(item) for item in validation.get("blockingLimitations") or []) or "none",
            }
        )
    counts = Counter(row["verdict"] for row in rows)
    lines = [
        f"# {len(rows)}-Agent three-stage live SAP acceptance campaign",
        "",
        "All direct baselines use `codex_app_direct_sap` and do not call SAPBusinessAgents. All SAP execution is read-only GET; raw rows, URLs, and credentials remain in ignored local artifacts.",
        "",
        "## Summary",
        "",
        f"- PASS / executable: {counts['PASS']}",
        f"- BLOCKED / disabled: {counts['BLOCKED']}",
        f"- FAIL / disabled: {counts['FAIL']}",
        "",
        "## Agent results",
        "",
        "| Module | Agent | Verdict | Executable | Free query | Fixed Agent | Evidence scope | Blocking limitation |",
        "|---|---|---:|:---:|:---:|:---:|:---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['module']} | `{row['agent']}` | {row['verdict']} | {str(row['executable']).lower()} | {row['free']} | {row['fixed']} | {row['scope']} | {row['limitations']} |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the current Agent live acceptance summary.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="docs/live-sap-agent-campaign.md")
    args = parser.parse_args()
    root = Path(args.repository).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    path = generate(root, output)
    print(json.dumps({"generated": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
