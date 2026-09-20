"""Review/dry-run or save one lifecycle revision; never publish or certify.

Usage: python -m scripts.repair_po_sales_candidate_contract --expected-revision N [--apply]
The existing local draft supplies the queries; no SAP credentials/data are fixtures.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from urllib.request import urlopen

from sap_business_agents_platform.acceptance_contract import contract_issues, readiness
from sap_business_agents_platform.managed_rules import source_digest, validate_managed_rule, execute_managed_rule
from sap_business_agents_platform.manifests import validate_execution

DRAFT_ID = "agent_draft_d74116c4d594ee85a6def92b"
RULE = Path(__file__).parent / "fixtures" / "po_sales_candidate_rules.py"


def build_package(original: dict) -> dict:
    package = copy.deepcopy(original)
    manifest = package["manifest"]
    package["rules"] = RULE.read_text(encoding="utf-8")
    manifest["managedRule"]["sha256"] = source_digest(package["rules"])
    execution = manifest["execution"]
    report_step = next(step for step in execution["steps"] if step["id"] == "build_business_report")
    report_step["inputMapping"].update({
        "purchase_order_items": "{{steps.sap_query_3.output}}",
        "mrp_candidates": "{{steps.sap_query_5.output}}",
    })
    props = execution["outputSchema"]["properties"]
    def prop(kind: str, zh: str, en: str, **kw):
        value = {"type": kind, "title": {"zh": zh, "en": en}, **kw}
        if "enum" in kw:
            labels = {"normal": ("正常", "Normal"), "attention": ("需关注", "Needs attention"), "inconclusive": ("证据不足", "Inconclusive"), "candidate_for_review": ("候选待复核", "Candidate for review")}
            value["x-sapba-display"] = {"format": "enum", "labels": {name: dict(zip(("zh", "en"), labels[name])) for name in kw["enum"]}}
        return value
    row = {
        "sales_order": prop("string", "销售订单", "Sales order", minLength=1),
        "verified_item_count": prop("integer", "已核实项目数", "Verified items", minimum=0),
        "schedule_line_count": prop("integer", "已核实计划行数", "Verified schedule lines", minimum=0),
        "assessment": prop("string", "业务评估", "Business assessment", enum=["candidate_for_review", "inconclusive"]),
    }
    additions = {
        "records": prop("array", "候选销售订单明细", "Candidate sales-order records", items={"type": "object", "properties": row, "required": list(row), "additionalProperties": False}),
        "verified_item_count": prop("integer", "已核实项目数", "Verified items", minimum=0),
        "schedule_line_count": prop("integer", "已核实计划行数", "Verified schedule lines", minimum=0),
        "business_status": prop("string", "业务状态", "Business status", enum=["normal", "attention", "inconclusive"]),
        "evidence_complete": prop("boolean", "业务证据完整性", "Evidence completeness"),
        "business_complete": prop("boolean", "业务检查完整性", "Business completeness"),
        "evidence_gap_codes": prop("array", "证据缺口", "Evidence gaps", items={"type": "string"}),
    }
    props.update(additions)
    manifest["outputs"] = {locale: [schema["title"][locale] for schema in props.values()] for locale in ("zh", "en")}
    execution["outputSchema"]["required"] = list(props)
    for name in [*additions, "source_complete", "candidate_sales_orders", "candidate_sales_order_count", "business_report"]:
        execution["outputMapping"][name] = "{{steps.build_business_report.output.workflow_output." + name + "}}"
    scope = (
        "Screen potential-association candidates only, not causal delay impact, shortage quantity or delivery changes. "
        "Start from the confirmed purchase_order's items, narrowed by optional material. Preserve each actual Material/Plant pair; never form a Cartesian product. "
        "For those exact pairs use sales-order demand (MRPElementCategory VC); optional sales_order restricts MRPElement. "
        "Include a sales order only when at least one sales-order item has that same order, material and production plant in the corresponding MRP demand. "
        "Deduplicate verified items by SalesOrder/SalesOrderItem and schedule lines by SalesOrder/SalesOrderItem/ScheduleLine; count only schedule lines of verified items. "
        "Read complete evidence before claiming exhaustive results. Complete empty scope is zero; missing, bounded or conflicting required evidence is inconclusive. "
        "No stock allocation, FIFO, date cutoff, unit conversion, ATP or pegging is performed. Counts in incomplete output describe observed verified records only."
    )
    execution["acceptance"] = {
        "contractVersion": "1.0", "requiredAssessments": [], "comparisonMode": "business_semantic", "recordScope": "records",
        "recordDefinition": "One unique sales order with verified material/plant association to the confirmed purchase order scope.",
        "scopeDefinition": scope,
        "businessKeys": ["sales_order"], "facts": ["assessment", "verified_item_count", "schedule_line_count"],
        "metrics": ["candidate_sales_order_count", "verified_item_count", "schedule_line_count"],
        "factDefinitions": {
            "sales_order": "Canonical SAP SalesOrder ID, verified against sales-order item evidence for the matching MRP order/material/plant triple.",
            "assessment": "candidate_for_review if required source and association evidence is complete; inconclusive if required evidence is incomplete. Neither code proves causal delay impact.",
            "verified_item_count": "Count unique SalesOrderItem values for this order whose material/plant triple is verified by PO scope and MRP demand. Integer, no double-counting repeated rows.",
            "schedule_line_count": "Count unique SalesOrderItem/ScheduleLine pairs belonging only to this order's verified items. A complete query with no rows is zero.",
        },
        "metricDefinitions": {
            "candidate_sales_order_count": "Count unique sales_order records at the defined grain. Complete empty scope is zero. In incomplete output this is the observed count, never a claim of completeness.",
            "verified_item_count": "Sum the record verified_item_count values: unique verified SalesOrder/SalesOrderItem keys in this scope.",
            "schedule_line_count": "Sum the record schedule_line_count values: unique verified SalesOrder/SalesOrderItem/ScheduleLine keys in this scope.",
        },
        "metricInvariants": {
            "candidate_sales_order_count": {"operation": "count_records"},
            "verified_item_count": {"operation": "sum", "field": "verified_item_count"},
            "schedule_line_count": {"operation": "sum", "field": "schedule_line_count"},
        },
        "businessStatusDefinition": "attention when complete evidence yields candidates; normal for complete zero candidates; inconclusive when required evidence is missing, incomplete, truncated or conflicting. Source/evidence/business completeness are separate explicit booleans.",
        "currencyAndUnitPolicy": "compare_only_when_same_or_conversion_validated",
        "requiredLimitations": ["POTENTIAL_EXPOSURE_ONLY"],
    }
    manifest["purpose"] = {"zh": "按采购订单实际物料与工厂配对、销售订单类MRP需求及订单项目筛查潜在关联候选，不计算实际短缺或交期推迟。", "en": scope}
    workflow = next(item for item in manifest["workflow"] if "build_business_report" in item.get("executionStepIds", []))
    workflow["operations"] = {
        "zh": ["核对采购订单物料与工厂的实际配对；按订单、物料、工厂对应MRP需求核实销售订单项目，不交叉拼接独立集合。", "按销售订单去重形成规范记录；项目和计划行按组合键去重计数，报告由同一份记录生成。", "完整零结果、候选待复核和证据不足分别显示；仅输出潜在关联，不证明实际延期影响。"],
        "en": [
            "Verify actual material/plant pairs from the purchase order, then verify sales-order items against matching order/material/plant MRP demand without crossing independent sets.",
            "Build one canonical record per sales order; deduplicate items and schedule lines by their stable composite keys, and derive the report from those same records.",
            "Separate complete zero, candidates for review, and incomplete evidence. Report potential association only and do not claim actual delay impact.",
        ],
    }
    marker = "## 候选筛查与验收 / Candidate screening and acceptance"
    readme = str(package.get("readme") or "")
    readme = readme.split(marker, 1)[0].rstrip()
    package["readme"] = readme + "\n\n" + marker + "\n\n" + manifest["purpose"]["zh"] + "\n\n" + scope + "\n\n规范记录和业务状态见 execution.acceptance；三级比较仅检验本次案例，不证明实际延期因果关系。\nCanonical records and business states are defined by execution.acceptance; case acceptance does not establish causal delay impact.\n"
    validate_managed_rule(package["rules"], expected_digest=manifest["managedRule"]["sha256"])
    validate_execution(manifest, "candidate-contract-repair")
    assert not contract_issues(manifest), contract_issues(manifest)
    return package


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replay-run", help="Offline regression against an existing run, never a new certificate.")
    args = parser.parse_args()
    with urlopen("http://127.0.0.1:8765/api/authoring/agents/" + DRAFT_ID, timeout=30) as response:
        draft = json.load(response)
    if draft["revision"] != args.expected_revision or draft.get("active_operation") or draft["status"] == "published":
        raise RuntimeError("Draft changed, published or busy; inspect the latest state.")
    package = build_package(draft["package"])
    print(json.dumps({"draft_id": DRAFT_ID, "revision": draft["revision"], "contract_issues": contract_issues(package["manifest"]), "changed": package != draft["package"]}))
    if args.replay_run:
        with urlopen("http://127.0.0.1:8765/api/runs/" + args.replay_run, timeout=30) as response:
            run = json.load(response)
        result = run["result"]
        evidence = {item["step_id"]: item["payload"] for item in result["evidence"] if item.get("step_id") and "payload" in item}
        output = execute_managed_rule(package["rules"], {
            "mode": "business_report", "candidate_source_complete": result["workflow_output"].get("source_complete") is True,
            **{name: evidence[step] for name, step in {"purchase_order_items": "sap_query_3", "mrp_candidates": "sap_query_5", "sales_order_items": "sap_query_6", "schedule_lines": "sap_query_7"}.items()},
        }, expected_digest=package["manifest"]["managedRule"]["sha256"])["workflow_output"]
        check = readiness(package["manifest"], draft["revision"] + 1, result={"workflow_output": output})
        print(json.dumps({"offline_only": True, "source_run": args.replay_run, "readiness": check, "metrics": output["business_report"]["metrics"], "business_status": output["business_status"]}, ensure_ascii=False))
        if check["status"] != "ready":
            raise RuntimeError("Offline replay did not satisfy the contract")
    if args.apply:
        from sap_business_agents_platform.app import create_app
        from sap_business_agents_platform.models import AgentDraftUpdate
        lifecycle = create_app().state.agent_lifecycle
        latest = lifecycle.get_draft(DRAFT_ID)
        if latest["revision"] != args.expected_revision or latest["package"] != draft["package"]:
            raise RuntimeError("Draft changed during preparation.")
        result = lifecycle.update(DRAFT_ID, AgentDraftUpdate(expectedRevision=args.expected_revision, manifest=package["manifest"], rules=package["rules"], readme=package["readme"]))
        print(json.dumps({"saved_revision": result["revision"], "status": result["status"]}))


if __name__ == "__main__":
    main()
