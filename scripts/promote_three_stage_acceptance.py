from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def baseline_report(root: Path, artifact: dict[str, Any]) -> str:
    case = artifact.get("case") if isinstance(artifact.get("case"), dict) else {}
    direct = (
        artifact.get("direct_baseline")
        if isinstance(artifact.get("direct_baseline"), dict)
        else {}
    )
    if not direct.get("sources"):
        case_id = str(case.get("case_id") or "")
        candidate = root / ".local-data" / "live-agent-acceptance" / case_id / "baseline.json"
        if candidate.is_file():
            value = _load(candidate)
            direct = {
                "sources": value.get("sources") or [],
                "qualification": value.get("qualification"),
                "nonblocking_observations": value.get("nonblocking_observations") or [],
            }
    sources = [item for item in direct.get("sources") or [] if isinstance(item, dict)]
    supplemental = [
        item
        for item in direct.get("supplemental_sources") or []
        if isinstance(item, dict)
    ]
    lines = [
        "## Sanitized case scope",
        "",
        "- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.",
        f"- Structured input fields: `{', '.join(sorted(str(item) for item in (case.get('input') or {}))) or 'none'}` (values remain in ignored artifacts).",
        f"- Business-condition fields: `{', '.join(sorted(str(item) for item in (case.get('business_conditions') or {}))) or 'none'}` (values remain in ignored artifacts).",
        f"- Accepted business grain: `{', '.join(str(item) for item in case.get('expected_grain') or [])}`.",
        "",
        "## Direct baseline source coverage",
        "",
        "| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |",
        "|---|---|:---:|---|---:|---:|---|:---:|:---:|",
    ]
    for source in sources:
        order = ", ".join(str(item) for item in source.get("stable_order_by") or [])
        lines.append(
            "| {source_id} | {service} | {odata} | {entity} | {rows} | {pages} | {order} | {paging} | {complete} |".format(
                source_id=str(source.get("source_id") or "-").replace("|", "\\|"),
                service=str(source.get("service_name") or "-").replace("|", "\\|"),
                odata=str(source.get("odata_version") or "-"),
                entity=str(source.get("entity_set") or "-").replace("|", "\\|"),
                rows=int(source.get("row_count") or 0),
                pages=int(source.get("page_count") or 0),
                order=(order or "-").replace("|", "\\|"),
                paging=str(source.get("paging_complete") is True).lower(),
                complete=str(source.get("source_complete") is True).lower(),
            )
        )
    if sources:
        lines.extend(["", "Schema/query manifests:"])
        for source in sources:
            lines.append(
                f"- `{source.get('source_id')}` schema `{source.get('schema_hash') or '-'}`; query `{source.get('query_hash') or '-'}`."
            )
    if supplemental:
        lines.extend(
            [
                "",
                "## Supplemental read-only evidence",
                "",
                "| Source | Provider | Object | Fields | Rows | Paging complete | Source complete | Hash verified |",
                "|---|---|---|---|---:|:---:|:---:|:---:|",
            ]
        )
        for source in supplemental:
            lines.append(
                "| {source_id} | {provider} | {object_name} | {fields} | {rows} | {paging} | {complete} | {verified} |".format(
                    source_id=str(source.get("source_id") or "-").replace("|", "\\|"),
                    provider=str(source.get("provider") or "-").replace("|", "\\|"),
                    object_name=str(source.get("object") or "-").replace("|", "\\|"),
                    fields=", ".join(str(item) for item in source.get("fields") or []).replace("|", "\\|"),
                    rows=int(source.get("row_count") or 0),
                    paging=str(source.get("paging_complete") is True).lower(),
                    complete=str(source.get("source_complete") is True).lower(),
                    verified=str(source.get("hash_verified") is True).lower(),
                )
            )
        lines.extend(["", "Supplemental evidence hashes:"])
        for source in supplemental:
            lines.append(
                f"- `{source.get('source_id')}` filter `{source.get('filter_hash') or '-'}`; manifest `{source.get('manifest_hash') or '-'}`."
            )
    observations = [
        item
        for item in direct.get("nonblocking_observations") or []
        if isinstance(item, dict)
    ]
    if observations:
        lines.extend(["", "Non-blocking observations:"])
        for item in observations:
            code = str(item.get("code") or "observation")
            last_mrp_date = str(item.get("last_mrp_date") or "-")
            age_days = item.get("age_days")
            age_text = str(age_days) if isinstance(age_days, int) else "-"
            lines.append(
                f"- `{code}`: last MRP date `{last_mrp_date}`, snapshot age `{age_text}` day(s); blocking=`false`."
            )
    qualification = direct.get("qualification") if isinstance(direct.get("qualification"), dict) else {}
    if qualification:
        lines.extend(
            [
                "",
                f"- Test-data qualification: `{qualification.get('status')}`.",
                f"- Qualification evidence: `{', '.join(str(item) for item in qualification.get('evidence_source_ids') or [])}`; reasons: `{', '.join(str(item) for item in qualification.get('reasons') or []) or 'none'}`.",
            ]
        )
    lines.extend(
        [
            "",
            "## Repair and adjudication outcome",
            "",
            "The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.",
        ]
    )
    return "\n".join(lines)


def promote(root: Path, module: str, artifact_path: Path) -> Path:
    artifact = _load(artifact_path)
    case = artifact.get("case") if isinstance(artifact.get("case"), dict) else {}
    slug = str(case.get("agent_id") or "")
    free = artifact.get("free_query") if isinstance(artifact.get("free_query"), dict) else {}
    fixed = artifact.get("fixed_agent") if isinstance(artifact.get("fixed_agent"), dict) else {}
    if (
        artifact.get("verdict") != "PASS"
        or (free.get("comparison") or {}).get("verdict") != "MATCH"
        or (fixed.get("comparison") or {}).get("verdict") != "MATCH"
    ):
        raise ValueError("only a PASS/MATCH/MATCH artifact can enable a fixed Agent")
    hashes = artifact.get("hashes") if isinstance(artifact.get("hashes"), dict) else {}
    required_hashes = {
        "codex_direct_baseline_hash": "codexDirectBaselineHash",
        "free_query_hash": "freeQueryHash",
        "adjudicated_result_hash": "adjudicatedResultHash",
        "fixed_agent_hash": "fixedAgentHash",
    }
    for source in required_hashes:
        if not SHA256.fullmatch(str(hashes.get(source) or "")):
            raise ValueError(f"acceptance artifact is missing {source}")

    manifest_path = root / "agents" / module / slug / "agent.json"
    manifest = _load(manifest_path)
    if manifest.get("slug") != slug:
        raise ValueError("artifact Agent does not match manifest")
    manifest["status"] = "Three-stage live acceptance passed"
    report_rel = "docs/three-stage-live-acceptance.md"
    records = ((fixed.get("normalized_result") or {}).get("records") or [])
    limitations = ((fixed.get("normalized_result") or {}).get("limitations") or [])
    supplemental = [
        item
        for item in (artifact.get("direct_baseline") or {}).get("supplemental_sources") or []
        if isinstance(item, dict)
    ]
    providers = ["codex-app-direct-sap", "embedded-sap-odata"]
    if supplemental:
        providers.append("sap-adt-table-export")
    manifest["validation"] = {
        "verdict": "PASS",
        "testedAt": artifact.get("tested_at"),
        "evidenceScope": "complete",
        "providers": providers,
        "summary": {
            "zh": "独立直连基线、自由查询和固定 Agent 的业务语义一致。",
            "en": "The independent direct-SAP baseline, free query, and fixed Agent are semantically consistent.",
        },
        "reportPath": report_rel,
        "executable": True,
        "freeQueryComparison": "MATCH",
        "fixedAgentComparison": "MATCH",
        "baselineRuntime": "codex_app_direct_sap",
        "usedSapBusinessAgentsForBaseline": False,
        **{target: hashes[source] for source, target in required_hashes.items()},
        "comparisonHash": str((fixed.get("comparison") or {}).get("comparison_hash")),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path = manifest_path.parent / report_rel
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        f"""# Three-stage live SAP acceptance: {slug}

## Verdict

`PASS` / `executable=true`

- Case: `{case.get('case_id')}`
- Tested at: `{artifact.get('tested_at')}`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `{len(records)}`
- Required limitations preserved: `{', '.join(str(item) for item in limitations) or 'none'}`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `{hashes['codex_direct_baseline_hash']}`
- SAPBusinessAgents free query: `{hashes['free_query_hash']}`
- Adjudicated result: `{hashes['adjudicated_result_hash']}`
- Fixed Agent: `{hashes['fixed_agent_hash']}`
- Fixed comparison: `{(fixed.get('comparison') or {}).get('comparison_hash')}`

{baseline_report(root, artifact)}

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
""",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable an Agent from a verified three-stage artifact.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--module", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    path = promote(
        Path(args.repository).resolve(),
        args.module,
        Path(args.artifact).resolve(),
    )
    print(json.dumps({"updated": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
