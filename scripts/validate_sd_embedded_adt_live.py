from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.skills import SkillError, SkillRegistry
from validate_deterministic_agents_live import LiveValidator


SD_AGENTS = (
    "delivered-not-billed",
    "billing-block-diagnosis",
    "billing-completeness-check",
    "billing-output-monitor",
    "delivery-delay-prediction",
    "due-delivery-prioritization",
    "shortage-allocation-advisor",
    "billing-dispute-classification",
    "returns-credit-anomaly",
    "order-to-cash-anomaly-monitor",
    "order-to-cash-status",
)
ISSUES = {
    "billing-output-monitor": "https://github.com/yin-wen-bin/SAPSkillhub/issues/12",
    "billing-dispute-classification": "https://github.com/yin-wen-bin/SAPSkillhub/issues/13",
}
TERMINAL = {"completed", "inconclusive", "failed", "cancelled"}


def _version(root: Path, *, include_dirty: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip() if include_dirty else ""
    except (FileNotFoundError, NotADirectoryError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() + ("+working-tree" if dirty else "")


def _manifest_scope(root: Path, agent: str) -> tuple[list[str], list[str]]:
    manifest = json.loads((root / "agents" / "SD" / agent / "agent.json").read_text(encoding="utf-8"))
    services: set[str] = set()
    adt_objects: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("executor") == "skill" and value.get("skillId") == "sap-adt-table-export":
                mapping = value.get("inputMapping") or {}
                if mapping.get("object"):
                    adt_objects.add(str(mapping["object"]))
            if value.get("service_name") and value.get("entity_set"):
                services.add(f"{value['service_name']}/{value['entity_set']}")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(manifest.get("execution"))
    return sorted(services), sorted(adt_objects)


async def _adt_preflight(root: Path) -> dict[str, Any]:
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
        "timeout": skill.get("timeout"),
        "profile_alias": "sapba-live-readonly",
    }
    if not skill.get("available"):
        result["reason"] = "skill_entrypoint_unavailable"
        return result
    task = {
        "schema_version": 1,
        "source_type": "table",
        "object": "TSTC",
        "fields": ["TCODE", "PGMNA"],
        "filters": [{"field": "TCODE", "operator": "eq", "value": "SE16N"}],
        "order_by": ["TCODE"],
        "max_rows": 2,
    }
    try:
        output = await registry.execute("sap-adt-table-export", task)
    except SkillError as exc:
        result.update({"status": "blocked", "reason": type(exc).__name__, "message": str(exc)})
        return result
    result.update(
        {
            "status": str(output.get("status")),
            "row_count": int(output.get("row_count") or 0),
            "source_complete": bool((output.get("completeness") or {}).get("source_complete")),
            "hash_verified": any(
                artifact.get("type") == "output_manifest" and artifact.get("verified") is True
                for artifact in output.get("artifacts") or []
                if isinstance(artifact, dict)
            ),
        }
    )
    return result


def _business_verdict(case: dict[str, Any]) -> str:
    missing = set(case.get("missing_evidence") or [])
    if case.get("technical_chain") == "failed":
        return "阻塞"
    if case["agent"] in {"billing-output-monitor", "billing-dispute-classification"} and missing:
        return "阻塞"
    if case.get("status") == "completed" and not missing:
        return "通过"
    return "部分通过"


def _write_reports(
    root: Path,
    output: Path,
    cases: list[dict[str, Any]],
    discoveries: list[dict[str, Any]],
    preflight: dict[str, Any],
    code_version: str,
    embedded_version: str,
    adt_skill_version: str,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_version": code_version,
        "provider": "embedded",
        "embedded_provider_version": embedded_version,
        "adt_skill_version": adt_skill_version,
        "provider_fallback_calls": 0,
        "adt_preflight": preflight,
        "discoveries": discoveries,
        "cases": cases,
    }
    (output / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = [
        "# SD 11 Agent Embedded + ADT 真机校验总览",
        "",
        f"- 测试时间：{payload['generated_at']}",
        f"- 代码版本：`{code_version}`",
        f"- 主数据通道：Embedded SAP Read Provider `{embedded_version}`（GET-only）",
        f"- ADT Skill版本：`{adt_skill_version}`",
        "- 自动 Provider 回退调用数：**0**",
        f"- ADT平台预检：`{preflight.get('status', 'unknown')}`",
        "- 原始证据：仅保存在被忽略的 `.local-data/live-tests/embedded-adt/`。",
        "",
        "| Agent | 技术状态 | 业务结论 | SAP GET | 查询源完整 | 关键缺口 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for case in cases:
        missing = ", ".join(case.get("missing_evidence") or []) or "无"
        summary.append(
            f"| `{case['agent']}` | {case['status']} | {_business_verdict(case)} | {case['sap_get_count']} | "
            f"{str(case['source_complete']).lower()} | {missing} |"
        )
    summary.extend(
        [
            "",
            "## 验收边界",
            "",
            "本轮仅在外部连接可用时为每个Agent执行一个自动发现的真实样本；当前若显示GET=0，则表示未执行SAP请求。尚未达到每流程5个样本及全部状态覆盖，不能宣称生产验收完成。",
            "ADT `partial`、`failed`、超限、Hash不一致或Profile不可用均保持 `inconclusive`；MDKP证据不替代ATP。",
            "候选发现查询仅用于选样，不作为源数据完整性证据。",
            "",
        ]
    )
    (root / "agents" / "SD" / "EMBEDDED_ADT_LIVE_VALIDATION_SUMMARY.md").write_text("\n".join(summary), encoding="utf-8")

    for case in cases:
        services, adt_objects = _manifest_scope(root, case["agent"])
        verdict = _business_verdict(case)
        lines = [
            f"# {case['agent']} 真机测试报告",
            "",
            f"- 测试日期：{payload['generated_at']}",
            f"- 代码版本：`{code_version}`",
            "- 系统与客户端：已脱敏；连接配置和凭据不落库",
            f"- Embedded Provider：`embedded` `{embedded_version}`，严格GET-only",
            f"- ADT Skill版本：`{adt_skill_version}`",
            "- 自动 Provider 回退调用数：`0`",
            f"- 技术状态：`{case['status']}`",
            f"- 业务结论：`{verdict}`",
            "",
            "## 真机证据",
            "",
            f"- 自然语言/结构化用例输入（脱敏）：`{json.dumps(case['sample'], ensure_ascii=False)}`",
            f"- Embedded服务与实体：{', '.join(f'`{item}`' for item in services) or '无'}",
            f"- SAP GET次数：{case['sap_get_count']}；证据行计数：{case['evidence_row_count']}；耗时：{case['elapsed_ms']} ms",
            f"- 查询源完整：`{str(case['source_complete']).lower()}`；业务完整：`{str(case['business_complete']).lower()}`",
            f"- 分页/错误代码：{', '.join(case.get('error_codes') or []) or '无'}",
            "",
            "## ADT缺口证据",
            "",
            f"- Skill：`sap-adt-table-export`；平台预检：`{preflight.get('status', 'unknown')}`",
            f"- 允许对象：{', '.join(f'`{item}`' for item in adt_objects) or '本流程默认不使用ADT'}",
            "- Profile别名：`sapba-live-readonly`；URL、客户端、凭据和CA路径均位于仓库外。",
            f"- Hash验证：`{str(preflight.get('hash_verified', False)).lower()}`；完整性：`{str(preflight.get('source_complete', False)).lower()}`",
            "",
            "## Fixture与推断边界",
            "",
            "Fixture仅覆盖规则分支，不替代真机通过。真实业务原始行、客户、金额和完整凭证号未写入本报告。",
            f"当前缺口：{', '.join(case.get('missing_evidence') or []) or '无已声明缺口'}。",
            "本轮样本执行状态以SAP GET次数为准；GET=0表示未执行真机样本。正常、异常、取消、部分处理和空结果覆盖尚未完成时，结论保持部分通过或阻塞。",
            "",
            "## Issue与复测",
            "",
            (
                f"复评已有Issue：{ISSUES[case['agent']]}；因当前未完成业务对象真机预检而保持Open，未创建重复Issue。"
                if case["agent"] in ISSUES
                else "未因Profile缺失、权限或无业务样本自动创建项目Issue。只有可重复的平台或Skill通用缺陷才进入去重Issue流程。"
            ),
            "",
        ]
        report = root / "agents" / "SD" / case["agent"] / "docs" / "live-sap-test-report.md"
        report.write_text("\n".join(lines), encoding="utf-8")


async def _main(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    validator = LiveValidator(root, args.api_url)
    health = await validator.provider.health()
    preflight = await _adt_preflight(root)
    embedded_version = str((health.get("data") or {}).get("provider_version") or "unknown")
    adt_skill_version = _version(validator.settings.skillhub_root, include_dirty=False)
    cases: list[dict[str, Any]] = []
    if health.get("ok") is not True or (health.get("data") or {}).get("read_only") is not True:
        for agent in SD_AGENTS:
            cases.append(
                {
                    "agent": agent,
                    "run_id": "not-created",
                    "status": "failed",
                    "technical_chain": "failed",
                    "source_complete": False,
                    "business_complete": False,
                    "missing_evidence": ["embedded_provider_configuration"],
                    "sap_get_count": 0,
                    "evidence_row_count": 0,
                    "headline": "Embedded Provider未配置外部连接，未执行SAP请求",
                    "business_status": "failed",
                    "error_codes": [
                        str(issue.get("code"))
                        for issue in health.get("validation_issues") or []
                        if isinstance(issue, dict) and issue.get("code")
                    ],
                    "events": [],
                    "sample": {},
                    "elapsed_ms": 0,
                }
            )
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        output = root / ".local-data" / "live-tests" / "embedded-adt" / stamp
        _write_reports(root, output, cases, validator.discovery_observations, preflight, _version(root), embedded_version, adt_skill_version)
        print(f"REPORT={output}", flush=True)
        return 2

    samples, _missing = await validator.samples()
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
        for index, agent in enumerate(SD_AGENTS, 1):
            try:
                case = await validator.run_agent(client, agent, samples[agent])
            except (httpx.HTTPError, TimeoutError, KeyError) as exc:
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
                    "headline": "真机运行未完成",
                    "business_status": "failed",
                    "error_codes": [type(exc).__name__],
                    "events": [],
                    "sample": {},
                    "elapsed_ms": 0,
                }
            cases.append(case)
            print(f"{index:02d}/11 {agent}: {case['status']} GET={case['sap_get_count']}", flush=True)

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = root / ".local-data" / "live-tests" / "embedded-adt" / stamp
    _write_reports(root, output, cases, validator.discovery_observations, preflight, _version(root), embedded_version, adt_skill_version)
    print(f"REPORT={output}", flush=True)
    return 0 if all(case["sap_get_count"] > 0 for case in cases) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all 11 SD Agents against Embedded SAP and controlled ADT fallbacks.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
