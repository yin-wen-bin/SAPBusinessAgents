from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.skills import SkillError, SkillRegistry
from validate_deterministic_agents_live import LiveValidator, _safe_input


CO_AGENTS = (
    "cost-center-expense-anomaly",
    "co-month-end-allocation-settlement",
    "product-cost-variance",
    "budget-rolling-forecast",
    "internal-order-project-control",
)


def _version(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() + ("+working-tree" if dirty else "")


def _first(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    return next((row for row in rows if predicate(row)), {})


def _present(row: dict[str, Any], *fields: str) -> bool:
    return all(row.get(field) not in {None, ""} for field in fields)


def _period(value: Any, default: int = 1) -> int:
    try:
        return max(1, min(12, int(str(value))))
    except ValueError:
        return default


def _manifest_scope(root: Path, agent: str) -> tuple[list[str], list[str]]:
    manifest = json.loads((root / "agents" / "CO" / agent / "agent.json").read_text(encoding="utf-8"))
    services: set[str] = set()
    objects: set[str] = set()
    for step in manifest["execution"]["steps"]:
        if step.get("executor") == "sap_read":
            plan = (step.get("request") or {}).get("plan") or {}
            services.add(f"{plan.get('service_name')}/{plan.get('entity_set')}")
        if step.get("executor") == "skill":
            objects.add(str((step.get("inputMapping") or {}).get("object") or ""))
    return sorted(item for item in services if "/" in item), sorted(item for item in objects if item)


def _manifest_steps(root: Path, agent: str) -> list[dict[str, str]]:
    manifest = json.loads((root / "agents" / "CO" / agent / "agent.json").read_text(encoding="utf-8"))
    return [
        {"step_id": str(step.get("id") or ""), "executor": str(step.get("executor") or "")}
        for step in manifest["execution"]["steps"]
    ]


def _run_audit(database_path: Path, run_id: str) -> dict[str, Any]:
    statuses: dict[str, dict[str, str]] = {}
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT event_type, data_json FROM events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        result_row = connection.execute(
            "SELECT result_json FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    for event_type, data_json in rows:
        data = json.loads(data_json)
        step_id = str(data.get("step_id") or "")
        if not step_id:
            continue
        state = statuses.setdefault(step_id, {"status": "started"})
        if event_type == "step_skipped":
            state.update({"status": "skipped", "reason": str(data.get("reason") or "unspecified")})
        elif event_type == "evidence_gap_recorded":
            error = data.get("error") or {}
            state.update(
                {
                    "status": "failed",
                    "reason": str(error.get("code") or "evidence_gap_recorded")
                    if isinstance(error, dict)
                    else "evidence_gap_recorded",
                }
            )
        elif event_type in {"evidence_received", "rule_completed", "tool_completed"} and state.get("status") != "failed":
            state["status"] = "complete"
    result_json = str(result_row[0] or "") if result_row else ""
    return {
        "step_statuses": statuses,
        "evidence_sha256": hashlib.sha256(result_json.encode("utf-8")).hexdigest() if result_json else "unavailable",
    }


async def _adt_preflight(root: Path, sample: dict[str, Any]) -> dict[str, Any]:
    settings = Settings.from_env(root)
    registry = SkillRegistry(settings.skillhub_root, root / "config" / "skills.json")
    try:
        skill = registry.get("sap-adt-table-export")
    except KeyError:
        return {"status": "failed", "reason": "skill_not_registered"}
    result: dict[str, Any] = {
        "status": "registered" if skill.get("available") else "failed",
        "read_only": skill.get("read_only") is True,
        "validated": skill.get("validated") is True,
        "profile_alias": "sapba-live-readonly",
    }
    if not skill.get("available"):
        result["reason"] = "skill_entrypoint_unavailable"
        return result
    try:
        output = await registry.execute(
            "sap-adt-table-export",
            {
                "schema_version": 1,
                "connection_profile": "sapba-live-readonly",
                "source_type": "table",
                "object": "TSTC",
                "fields": ["TCODE", "PGMNA"],
                "filters": [{"field": "TCODE", "operator": "eq", "value": "SE16N"}],
                "order_by": ["TCODE"],
                "max_rows": 2,
            },
        )
    except SkillError as exc:
        result.update({"status": "blocked", "reason": type(exc).__name__, "message": str(exc)})
        return result
    result.update(
        {
            "status": str(output.get("status")),
            "row_count": int(output.get("row_count") or 0),
            "source_complete": (output.get("completeness") or {}).get("source_complete") is True,
            "paging_complete": (output.get("completeness") or {}).get("paging_complete") is True,
            "hash_verified": any(
                item.get("type") == "output_manifest" and item.get("verified") is True
                for item in output.get("artifacts") or []
                if isinstance(item, dict)
            ),
            "validation_issue_codes": [
                str(item.get("code"))
                for item in output.get("validation_issues") or []
                if isinstance(item, dict) and item.get("code")
            ],
        }
    )
    try:
        co_output = await registry.execute(
            "sap-adt-table-export",
            {
                "schema_version": 1,
                "connection_profile": "sapba-live-readonly",
                "source_type": "table",
                "object": "CSKS",
                "fields": ["KOKRS", "KOSTL", "DATAB", "DATBI", "BUKRS", "WAERS", "BKZKP"],
                "filters": [
                    {"field": "KOKRS", "operator": "eq", "value": sample["controlling_area"]},
                    {"field": "KOSTL", "operator": "eq", "value": sample["cost_center"]},
                ],
                "order_by": ["KOKRS", "KOSTL", "DATBI"],
                "max_rows": 2,
            },
        )
        result["co_probe"] = {
            "object": "CSKS",
            "status": str(co_output.get("status")),
            "row_count": int(co_output.get("row_count") or 0),
            "read_only": co_output.get("read_only") is True,
            "validated": co_output.get("validated") is True,
            "source_complete": (co_output.get("completeness") or {}).get("source_complete") is True,
            "paging_complete": (co_output.get("completeness") or {}).get("paging_complete") is True,
            "validation_issue_codes": [
                str(item.get("code"))
                for item in co_output.get("validation_issues") or []
                if isinstance(item, dict) and item.get("code")
            ],
        }
    except SkillError as exc:
        result["co_probe"] = {"object": "CSKS", "status": "blocked", "reason": type(exc).__name__}
    return result


async def _samples(validator: LiveValidator) -> dict[str, dict[str, Any]]:
    cost_centers = await validator.discover(
        "co_cost_centers",
        "API_COSTCENTER_SRV",
        "A_CostCenter",
        ["ControllingArea", "CostCenter", "CompanyCode"],
        top=100,
    )
    actual = await validator.discover(
        "co_actual_items",
        "API_OPLACCTGDOCITEMCUBE_SRV",
        "A_OperationalAcctgDocItemCube",
        ["CompanyCode", "FiscalYear", "FiscalPeriod", "ControllingArea", "CostCenter", "OrderID", "WBSElementInternalID", "Material", "Plant"],
        top=300,
    )
    plan = await validator.discover(
        "co_plan_items",
        "API_FINPLANNINGENTRYITEM_SRV",
        "A_FinPlanningEntryItem",
        ["CompanyCode", "LedgerFiscalYear", "FiscalPeriod", "PlanningCategory", "CostCenter", "OrderID", "WBSElementExternalID", "Plant"],
        top=300,
    )
    production = await validator.discover(
        "co_production_orders",
        "API_PRODUCTION_ORDER_2_SRV",
        "A_ProductionOrder_2",
        ["ManufacturingOrder", "Material", "ProductionPlant", "CompanyCode"],
        top=100,
    )

    planned_cc = _first(plan, lambda row: _present(row, "CompanyCode", "LedgerFiscalYear", "CostCenter", "PlanningCategory"))
    actual_cc = _first(actual, lambda row: _present(row, "CompanyCode", "FiscalYear", "FiscalPeriod", "CostCenter"))
    cc_id = str(planned_cc.get("CostCenter") or actual_cc.get("CostCenter") or "DEMO")
    master = _first(cost_centers, lambda row: str(row.get("CostCenter") or "") == cc_id) or _first(cost_centers, lambda row: _present(row, "ControllingArea", "CostCenter", "CompanyCode"))
    company = str(planned_cc.get("CompanyCode") or actual_cc.get("CompanyCode") or master.get("CompanyCode") or "1010")
    year = str(planned_cc.get("LedgerFiscalYear") or actual_cc.get("FiscalYear") or date.today().year)
    period = _period(planned_cc.get("FiscalPeriod") or actual_cc.get("FiscalPeriod"), date.today().month)
    controlling = str(master.get("ControllingArea") or actual_cc.get("ControllingArea") or "A000")
    category = str(planned_cc.get("PlanningCategory") or "PLAN")
    cc_id = str(planned_cc.get("CostCenter") or actual_cc.get("CostCenter") or master.get("CostCenter") or "DEMO")

    prod = _first(production, lambda row: _present(row, "ManufacturingOrder", "Material", "ProductionPlant"))
    order_row = _first(actual, lambda row: _present(row, "OrderID"))
    manufacturing_order = str(prod.get("ManufacturingOrder") or order_row.get("OrderID") or "DEMO")
    material = str(prod.get("Material") or order_row.get("Material") or "DEMO")
    plant = str(prod.get("ProductionPlant") or order_row.get("Plant") or "0001")
    order_company = str(prod.get("CompanyCode") or order_row.get("CompanyCode") or company)
    order_year = str(order_row.get("FiscalYear") or year)
    order_period = _period(order_row.get("FiscalPeriod"), period)

    wbs_row = _first(actual, lambda row: _present(row, "WBSElementInternalID"))
    control_row = order_row or wbs_row
    object_type = "INTERNAL_ORDER" if order_row else "WBS"
    object_id = str(control_row.get("OrderID") or control_row.get("WBSElementInternalID") or manufacturing_order)

    return {
        "cost-center-expense-anomaly": {
            "controlling_area": controlling,
            "company_code": company,
            "cost_center": cc_id,
            "fiscal_year": year,
            "period_from": str(period),
            "period_to": str(period),
            "planning_category": category,
            "variance_threshold_pct": 20,
        },
        "co-month-end-allocation-settlement": {
            "controlling_area": controlling,
            "company_code": order_company,
            "fiscal_year": order_year,
            "period": str(order_period),
            "internal_order": manufacturing_order,
            "allocation_cycle": "LIVE-CHECK",
        },
        "product-cost-variance": {
            "company_code": order_company,
            "fiscal_year": order_year,
            "period": str(order_period),
            "manufacturing_order": manufacturing_order,
            "material": material,
            "valuation_area": plant,
        },
        "budget-rolling-forecast": {
            "company_code": company,
            "cost_center": cc_id,
            "fiscal_year": year,
            "current_period": f"{period:03d}",
            "planning_category": category,
            "risk_threshold_pct": 10,
        },
        "internal-order-project-control": {
            "object_type": object_type,
            "object_id": object_id,
            "company_code": str(control_row.get("CompanyCode") or order_company),
            "fiscal_year": str(control_row.get("FiscalYear") or order_year),
            "planning_category": category,
        },
    }


def _verdict(case: dict[str, Any]) -> str:
    if int(case.get("sap_get_count") or 0) == 0 or case.get("technical_chain") == "failed":
        return "BLOCKED"
    if case.get("source_complete") is True and not case.get("missing_evidence"):
        return "PASS"
    return "PARTIAL"


def _write_reports(
    root: Path,
    output: Path,
    cases: list[dict[str, Any]],
    discoveries: list[dict[str, Any]],
    preflight: dict[str, Any],
) -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at,
        "code_version": _version(root),
        "provider": "embedded",
        "sapclaw_calls": 0,
        "se16n_calls": 0,
        "adt_preflight": preflight,
        "discoveries": discoveries,
        "cases": cases,
    }
    (output / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = [
        "# CO 五类 Agent Embedded + ADT 真机校验总览",
        "",
        f"- 测试时间：{generated_at}",
        "- 主通道：Embedded GET-only OData",
        f"- ADT 技术预检：`{preflight.get('status', 'unknown')}`",
        "- SAPClaw 调用：`0`；SE16N 调用：`0`",
        "- 原始证据仅保存在被忽略的 `.local-data/live-tests/co/`。",
        "",
        "| Agent | Verdict | SAP GET | Source complete | Missing evidence |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for case in cases:
        summary.append(
            f"| `{case['agent']}` | {_verdict(case)} | {case.get('sap_get_count', 0)} | "
            f"{str(case.get('source_complete') is True).lower()} | {', '.join(case.get('missing_evidence') or []) or 'none'} |"
        )
    summary.extend(["", "候选发现使用显式 top，仅用于选样，绝不作为源完整性证据。", ""])
    (root / "agents" / "CO" / "LIVE_SAP_VALIDATION_SUMMARY.md").write_text("\n".join(summary), encoding="utf-8")

    for case in cases:
        agent = str(case["agent"])
        verdict = _verdict(case)
        services, objects = _manifest_scope(root, agent)
        manifest_steps = _manifest_steps(root, agent)
        step_statuses = case.get("step_statuses") or {}
        api_statuses: list[str] = []
        adt_statuses: list[str] = []
        rule_statuses: list[str] = []
        for step in manifest_steps:
            step_id = step["step_id"]
            audit = step_statuses.get(step_id) or {"status": "not_observed"}
            rendered = f"`{step_id}`=`{audit.get('status', 'not_observed')}"
            if audit.get("reason"):
                rendered += f"/{audit['reason']}"
            rendered += "`"
            if step["executor"] == "sap_read":
                api_statuses.append(rendered)
            elif step["executor"] == "skill":
                adt_statuses.append(rendered)
            else:
                rule_statuses.append(rendered)
        lines = [
            f"# {agent} 真机测试报告",
            "",
            f"- 测试时间：{generated_at}",
            f"- 代码版本：`{payload['code_version']}`",
            "- 系统、客户端、凭据和业务标识均已脱敏",
            "- 主 Provider：`embedded-sap-odata`，严格 GET-only",
            "- 条件补证：`sap-adt-table-export`",
            "- SAPClaw 调用：`0`；SE16N 调用：`0`",
            f"- Verdict：**{verdict}**",
            "",
            "## Embedded evidence",
            "",
            f"- 自动发现输入（脱敏）：`{json.dumps(case.get('sample') or {}, ensure_ascii=False)}`",
            f"- 服务/实体：{', '.join(f'`{item}`' for item in services)}",
            f"- SAP GET：{case.get('sap_get_count', 0)}；证据行计数：{case.get('evidence_row_count', 0)}；耗时：{case.get('elapsed_ms', 0)} ms",
            f"- 查询源完整：`{str(case.get('source_complete') is True).lower()}`；业务完整：`{str(case.get('business_complete') is True).lower()}`",
            f"- 错误代码：{', '.join(case.get('error_codes') or []) or 'none'}",
            f"- 证据 SHA-256：`{case.get('evidence_sha256', 'unavailable')}`",
            "",
            "## Step status",
            "",
            f"- Embedded API：{', '.join(api_statuses) or 'none'}",
            f"- ADT：{', '.join(adt_statuses) or 'none'}",
            f"- Deterministic rules：{', '.join(rule_statuses) or 'none'}",
            "",
            "## ADT evidence",
            "",
            f"- 技术预检：`{preflight.get('status', 'unknown')}`；read_only=`{str(preflight.get('read_only') is True).lower()}`；validated=`{str(preflight.get('validated') is True).lower()}`",
            f"- source_complete=`{str(preflight.get('source_complete') is True).lower()}`；paging_complete=`{str(preflight.get('paging_complete') is True).lower()}`；manifest_hash_verified=`{str(preflight.get('hash_verified') is True).lower()}`",
            f"- 本流程白名单候选：{', '.join(f'`{item}`' for item in objects) or 'none'}",
            "",
            "## Boundary and conclusion",
            "",
            f"- Missing evidence：{', '.join(case.get('missing_evidence') or []) or 'none'}",
            f"- 结果摘要：{case.get('headline') or 'none'}；业务状态：`{case.get('business_status') or 'unknown'}`",
            "- `source_complete=true` 仅描述实际执行的查询源，不等于业务流程完成或风险已消除。",
            "- API 操作故障、权限、超时、截断和完整空结果均不会触发 ADT。",
            "- 未发布原始行、金额、真实标识、URL、账号或凭据。",
            "",
            "## Issue decision",
            "",
            "Profile/白名单/权限或业务样本缺口不是代码缺陷，不自动建 Issue。只有最小有界复现确认平台或 Skill 通用缺陷后才去重提交。",
            "",
        ]
        report = root / "agents" / "CO" / agent / "docs" / "live-sap-test-report.md"
        report.write_text("\n".join(lines), encoding="utf-8")
        manifest_path = root / "agents" / "CO" / agent / "agent.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["validation"] = {
            "verdict": verdict,
            "testedAt": generated_at,
            "evidenceScope": (
                "complete"
                if verdict == "PASS"
                else "partial"
                if int(case.get("sap_get_count") or 0) > 0
                else "bounded"
            ),
            "providers": ["embedded-sap-odata", "sap-adt-table-export"],
            "summary": {
                "zh": "真机查询源与必需证据完整。" if verdict == "PASS" else "已执行 Embedded 真机读取，但仍有明确证据缺口。" if verdict == "PARTIAL" else "未形成可用的真机证据链。",
                "en": "Live query sources and required evidence are complete." if verdict == "PASS" else "Embedded live reads ran, but explicit evidence gaps remain." if verdict == "PARTIAL" else "No usable live evidence chain was completed.",
            },
            "reportPath": "docs/live-sap-test-report.md",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _main(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    validator = LiveValidator(root, args.api_url)
    health = await validator.provider.health()
    if health.get("ok") is not True:
        raise RuntimeError("Embedded provider is not configured as a read-only SAP connection.")
    samples = await _samples(validator)
    preflight = await _adt_preflight(root, samples["cost-center-expense-anomaly"])
    cases: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        platform = (await client.get(f"{validator.api_url}/api/health"))
        platform.raise_for_status()
        platform_health = platform.json()
        if (
            platform_health.get("ok") is not True
            or (platform_health.get("sap_read") or {}).get("selected_provider") != "embedded"
            or ((platform_health.get("sap_read") or {}).get("data") or {}).get("read_only") is not True
        ):
            raise RuntimeError("Platform is not using the read-only Embedded provider.")
        for index, agent in enumerate(CO_AGENTS, 1):
            try:
                case = await validator.run_agent(client, agent, samples[agent])
                case.update(_run_audit(validator.settings.database_path, str(case["run_id"])))
            except (httpx.HTTPError, TimeoutError, KeyError, ValueError) as exc:
                case = {
                    "agent": agent,
                    "run_id": "not-created",
                    "status": "failed",
                    "technical_chain": "failed",
                    "source_complete": False,
                    "business_complete": False,
                    "missing_evidence": [],
                    "sap_get_count": 0,
                    "evidence_row_count": 0,
                    "error_codes": [type(exc).__name__],
                    "events": [],
                    "sample": _safe_input(samples[agent]),
                    "elapsed_ms": 0,
                }
            cases.append(case)
            print(f"{index}/5 {agent}: {case['status']} GET={case['sap_get_count']}", flush=True)
    output = root / ".local-data" / "live-tests" / "co" / datetime.now().strftime("%Y%m%dT%H%M%S")
    _write_reports(root, output, cases, validator.discovery_observations, preflight)
    print(f"REPORT={output}")
    return 0 if all(int(case.get("sap_get_count") or 0) > 0 for case in cases) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate five CO Agents against Embedded SAP and conditional ADT.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
