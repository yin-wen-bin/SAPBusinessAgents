from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from scripts.promote_three_stage_acceptance import baseline_report, promote
except ModuleNotFoundError:  # Direct script execution places scripts/ on sys.path.
    from promote_three_stage_acceptance import baseline_report, promote


SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def record(root: Path, module: str, artifact_path: Path) -> Path:
    artifact = _load(artifact_path)
    if artifact.get("verdict") == "PASS":
        return promote(root, module, artifact_path)
    case = artifact.get("case") if isinstance(artifact.get("case"), dict) else {}
    slug = str(case.get("agent_id") or "")
    verdict = str(artifact.get("verdict") or "")
    if verdict not in {"BLOCKED", "FAIL"}:
        raise ValueError("acceptance verdict must be PASS, BLOCKED, or FAIL")
    free = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    fixed = artifact.get("fixed_agent") if isinstance(artifact.get("fixed_agent"), dict) else {}
    free_comparison = str((free.get("comparison") or {}).get("verdict") or "BLOCKED")
    fixed_comparison = str((fixed.get("comparison") or {}).get("verdict") or "BLOCKED")
    blocking = [str(item) for item in artifact.get("blocking_limitations") or [] if str(item)]
    if verdict == "BLOCKED" and not (
        free_comparison == fixed_comparison == "MATCH" and blocking
    ):
        raise ValueError("BLOCKED requires MATCH/MATCH and an evidenced blocking limitation")
    hashes = artifact.get("hashes") if isinstance(artifact.get("hashes"), dict) else {}
    for name in (
        "codex_direct_baseline_hash",
        "free_query_hash",
        "adjudicated_result_hash",
        "fixed_agent_hash",
    ):
        if not SHA256.fullmatch(str(hashes.get(name) or "")):
            raise ValueError(f"acceptance artifact is missing {name}")
    manifest_path = root / "agents" / module / slug / "agent.json"
    manifest = _load(manifest_path)
    if manifest.get("slug") != slug:
        raise ValueError("artifact Agent does not match manifest")
    manifest["status"] = f"Three-stage live acceptance {verdict.lower()}"
    report_rel = "docs/three-stage-live-acceptance.md"
    manifest["validation"] = {
        "verdict": verdict,
        "testedAt": artifact.get("tested_at"),
        "evidenceScope": "bounded" if verdict == "BLOCKED" else "partial",
        "providers": ["codex-app-direct-sap", "embedded-sap-odata"],
        "summary": {
            "zh": (
                "三级结果语义一致，但真实能力或证据缺口阻止执行。"
                if verdict == "BLOCKED"
                else "三级验收存在可复现的不一致，执行入口保持禁用。"
            ),
            "en": (
                "The three stages are semantically consistent, but a live capability or evidence gap blocks execution."
                if verdict == "BLOCKED"
                else "The three-stage acceptance has a reproducible mismatch, so execution remains disabled."
            ),
        },
        "reportPath": report_rel,
        "executable": False,
        "freeQueryComparison": free_comparison,
        "fixedAgentComparison": fixed_comparison,
        "baselineRuntime": "codex_app_direct_sap",
        "usedSapBusinessAgentsForBaseline": False,
        "blockingLimitations": blocking,
        "codexDirectBaselineHash": hashes["codex_direct_baseline_hash"],
        "freeQueryHash": hashes["free_query_hash"],
        "adjudicatedResultHash": hashes["adjudicated_result_hash"],
        "fixedAgentHash": hashes["fixed_agent_hash"],
        "comparisonHash": str((fixed.get("comparison") or {}).get("comparison_hash") or ""),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = manifest_path.parent / report_rel
    report_path.parent.mkdir(parents=True, exist_ok=True)
    free_differences = (free.get("comparison") or {}).get("differences") or []
    fixed_differences = (fixed.get("comparison") or {}).get("differences") or []
    report_path.write_text(
        f"""# Three-stage live SAP acceptance: {slug}

## Verdict

`{verdict}` / `executable=false`

- Case: `{case.get('case_id')}`
- Tested at: `{artifact.get('tested_at')}`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `{free_comparison}`
- Fixed-Agent comparison: `{fixed_comparison}`
- Blocking limitations: `{', '.join(blocking) or 'none'}`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `{hashes['codex_direct_baseline_hash']}`
- SAPBusinessAgents free query: `{hashes['free_query_hash']}`
- Adjudicated result: `{hashes['adjudicated_result_hash']}`
- Fixed Agent: `{hashes['fixed_agent_hash']}`

## Comparison diagnostics

- Free-query differences: `{json.dumps(free_differences, ensure_ascii=False)}`
- Fixed-Agent differences: `{json.dumps(fixed_differences, ensure_ascii=False)}`

{baseline_report(root, artifact)}

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
""",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a PASS, BLOCKED, or FAIL three-stage artifact.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--module", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    path = record(Path(args.repository).resolve(), args.module, Path(args.artifact).resolve())
    print(json.dumps({"updated": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
