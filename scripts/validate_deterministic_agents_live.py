from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.sap_read.base import SapReadError
from sap_business_agents_platform.sap_read.embedded_odata import EmbeddedODataProvider


TERMINAL = {"completed", "inconclusive", "failed", "cancelled"}
EXCLUDED_REFERENCE_AGENTS = {"procure-to-pay-status", "order-to-cash-status"}
SAP_DATE = re.compile(r"^/Date\(([-0-9]+)(?:[+-][0-9]{4})?\)/$")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _date_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    match = SAP_DATE.match(text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _window(value: Any, days: int = 0) -> tuple[str, str]:
    parsed = _date_value(value)
    center = date.fromisoformat(parsed) if parsed else date.today()
    return (center - timedelta(days=days)).isoformat(), (center + timedelta(days=days)).isoformat()


def _first(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    return next((row for row in rows if predicate(row)), {})


def _present(row: dict[str, Any], *fields: str) -> bool:
    return all(row.get(field) not in (None, "") for field in fields)


def _safe_input(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if "date" in key or key in {"as_of", "fiscal_year", "period"}:
            safe[key] = value
        elif value in (None, ""):
            safe[key] = value
        else:
            safe[key] = _hash(value)
    return safe


def _issue_codes(value: Any) -> list[str]:
    codes: set[str] = set()
    if isinstance(value, dict):
        code = value.get("code")
        if code:
            codes.add(str(code))
        for nested in value.values():
            codes.update(_issue_codes(nested))
    elif isinstance(value, list):
        for nested in value:
            codes.update(_issue_codes(nested))
    return sorted(codes)


def _count_key(value: Any, key: str, predicate: Callable[[Any], bool] | None = None) -> int:
    total = 0
    if isinstance(value, dict):
        if key in value and (predicate is None or predicate(value[key])):
            total += 1
        for nested in value.values():
            total += _count_key(nested, key, predicate)
    elif isinstance(value, list):
        for nested in value:
            total += _count_key(nested, key, predicate)
    return total


def _sum_result_counts(value: Any) -> int:
    total = 0
    if isinstance(value, dict):
        if isinstance(value.get("result_count"), int):
            total += int(value["result_count"])
        for nested in value.values():
            total += _sum_result_counts(nested)
    elif isinstance(value, list):
        for nested in value:
            total += _sum_result_counts(nested)
    return total


def _events(database_path: Path, run_id: str) -> list[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT event_type FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
    return [str(row[0]) for row in rows]


class LiveValidator:
    def __init__(self, root: Path, api_url: str) -> None:
        self.root = root
        self.api_url = api_url.rstrip("/")
        self.settings = Settings.from_env(root)
        self.provider = EmbeddedODataProvider(
            base_url=self.settings.sap_base_url,
            username=self.settings.sap_username,
            password=self.settings.sap_password,
            client=self.settings.sap_client,
            verify_ssl=self.settings.sap_verify_ssl,
            auth_type=self.settings.sap_auth_type,
            timeout_seconds=self.settings.sap_odata_timeout_seconds,
            max_results=self.settings.sap_max_results,
            page_size=self.settings.sap_page_size,
            relationship_catalog_path=root / "config" / "business-relationships.json",
            service_registry_path=root / "config" / "odata-services.json",
            catalog_seed_path=root / "data" / "catalog-seed" / "catalog.json",
        )
        self.discovery_observations: list[dict[str, Any]] = []

    async def discover(
        self,
        name: str,
        service: str,
        entity: str,
        fields: list[str],
        *,
        top: int = 100,
    ) -> list[dict[str, Any]]:
        plan = {
            "service_name": service,
            "odata_version": "2.0",
            "entity_set": entity,
            "http_method": "GET",
            "plan_kind": "direct",
            "select_fields": fields,
            "top": top,
            "rationale": "Bounded live-test sample discovery only.",
        }
        started = time.perf_counter()
        try:
            result = await self.provider.execute_plan(plan, query=f"discover {name}")
            rows = list((result.get("data") or {}).get("results") or [])
            self.discovery_observations.append(
                {
                    "name": name,
                    "ok": True,
                    "rows": len(rows),
                    "source_complete": result.get("source_complete") is True,
                    "bounded_discovery": True,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            return [row for row in rows if isinstance(row, dict)]
        except (SapReadError, httpx.HTTPError, ValueError) as exc:
            self.discovery_observations.append(
                {
                    "name": name,
                    "ok": False,
                    "bounded_discovery": True,
                    "error_codes": _issue_codes(getattr(exc, "detail", {})) or [getattr(exc, "code", type(exc).__name__)],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            return []

    async def samples(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        fi = await self.discover(
            "fi_items",
            "API_OPLACCTGDOCITEMCUBE_SRV",
            "A_OperationalAcctgDocItemCube",
            ["CompanyCode", "FiscalYear", "FiscalPeriod", "Supplier", "Customer", "PostingDate"],
            top=200,
        )
        gl = await self.discover(
            "grir_gl_items",
            "API_GLACCOUNTLINEITEM",
            "GLAccountLineItem",
            ["CompanyCode", "GLAccount", "PostingDate", "PurchasingDocument"],
            top=200,
        )
        sales = await self.discover(
            "sales_orders",
            "API_SALES_ORDER_SRV",
            "A_SalesOrder",
            ["SalesOrder", "SalesOrganization", "SalesOrderDate", "RequestedDeliveryDate"],
            top=200,
        )
        sales_items = await self.discover(
            "sales_order_items",
            "API_SALES_ORDER_SRV",
            "A_SalesOrderItem",
            ["SalesOrder", "SalesOrderItem", "ProductionPlant", "Material"],
            top=200,
        )
        billing = await self.discover(
            "billing_documents",
            "API_BILLING_DOCUMENT_SRV",
            "A_BillingDocument",
            ["BillingDocument", "SalesOrganization", "BillingDocumentDate"],
            top=100,
        )
        delivery = await self.discover(
            "outbound_deliveries",
            "API_OUTBOUND_DELIVERY_SRV",
            "A_OutbDeliveryHeader",
            ["DeliveryDocument", "SalesOrganization", "ActualGoodsMovementDate", "PlannedGoodsIssueDate"],
            top=200,
        )
        returns = await self.discover(
            "customer_returns",
            "API_CUSTOMER_RETURN_SRV",
            "A_CustomerReturn",
            ["CustomerReturn", "SalesOrganization", "CustomerReturnDate"],
            top=100,
        )
        planned = await self.discover(
            "planned_orders",
            "API_PLANNED_ORDERS",
            "A_PlannedOrder",
            ["PlannedOrder", "Material", "ProductionPlant", "PlndOrderPlannedStartDate"],
            top=200,
        )
        mrp = await self.discover(
            "mrp_materials",
            "API_MRP_MATERIALS_SRV_01",
            "A_MRPMaterial",
            ["Material", "MRPArea", "MRPPlant"],
            top=100,
        )
        coverage = await self.discover(
            "mrp_coverages",
            "API_MRP_MATERIALS_SRV_01",
            "MaterialCoverages",
            ["Material", "MRPArea", "MRPPlant", "MaterialShortageProfile", "MaterialShortageProfileCount"],
            top=100,
        )
        production = await self.discover(
            "production_orders",
            "API_PRODUCTION_ORDER_2_SRV",
            "A_ProductionOrder_2",
            ["ManufacturingOrder", "ProductionPlant", "Material", "MfgOrderPlannedStartDate"],
            top=100,
        )
        operations = await self.discover(
            "production_operations",
            "API_PRODUCTION_ORDER_2_SRV",
            "A_ProductionOrderOperation_2",
            ["ManufacturingOrder", "ProductionPlant", "WorkCenter", "OpActualExecutionStartDate"],
            top=200,
        )

        missing: list[str] = []
        samples: dict[str, dict[str, Any]] = {}
        ap = _first(fi, lambda row: _present(row, "CompanyCode", "Supplier"))
        ar = _first(fi, lambda row: _present(row, "CompanyCode", "Customer"))
        period = _first(fi, lambda row: _present(row, "CompanyCode", "FiscalYear", "FiscalPeriod"))
        grir = _first(gl, lambda row: _present(row, "CompanyCode", "GLAccount", "PostingDate", "PurchasingDocument"))
        exact_sales = _first(sales, lambda row: _present(row, "SalesOrder"))
        exact_billing = _first(billing, lambda row: _present(row, "BillingDocument"))
        range_sales = _first(sales, lambda row: _present(row, "SalesOrganization", "SalesOrderDate"))
        requested_sales = _first(sales, lambda row: _present(row, "SalesOrganization", "RequestedDeliveryDate")) or range_sales
        ranged_delivery = _first(delivery, lambda row: _present(row, "SalesOrganization", "ActualGoodsMovementDate"))
        ranged_return = _first(returns, lambda row: _present(row, "SalesOrganization", "CustomerReturnDate"))
        item = _first(sales_items, lambda row: _present(row, "ProductionPlant", "Material"))
        planned_row = _first(planned, lambda row: _present(row, "ProductionPlant", "Material", "PlndOrderPlannedStartDate"))
        mrp_row = _first(coverage, lambda row: _present(row, "MRPPlant", "MRPArea", "Material", "MaterialShortageProfile", "MaterialShortageProfileCount"))
        mrp_master = _first(mrp, lambda row: _present(row, "MRPPlant", "MRPArea", "Material"))
        prod = _first(production, lambda row: _present(row, "ManufacturingOrder"))
        operation = _first(operations, lambda row: _present(row, "ProductionPlant", "WorkCenter"))

        def require(agent: str, row: dict[str, Any]) -> dict[str, Any]:
            if not row:
                missing.append(agent)
            return row

        ap = require("ap-payment", ap)
        ar = require("ar-collection", ar)
        period = require("month-end-closing", period)
        grir = require("gr-ir-clearing", grir)
        exact_sales = require("billing-block-diagnosis", exact_sales)
        exact_billing = require("billing-document-agents", exact_billing)
        range_sales = require("sales-range-agents", range_sales)
        ranged_delivery = require("delivered-not-billed", ranged_delivery)
        ranged_return = require("returns-credit-anomaly", ranged_return)
        item = require("shortage-allocation-advisor", item)
        planned_row = require("demand-forecast-planning", planned_row)
        prod = require("production-order-agents", prod)
        operation = require("production-scheduling-capacity", operation)

        today = date.today().isoformat()
        from_gl, to_gl = _window(grir.get("PostingDate"), 0)
        from_sales, to_sales = _window(range_sales.get("SalesOrderDate"), 0)
        from_requested, to_requested = _window(requested_sales.get("RequestedDeliveryDate") or range_sales.get("SalesOrderDate"), 0)
        from_delivery, to_delivery = _window(ranged_delivery.get("ActualGoodsMovementDate"), 0)
        from_return, to_return = _window(ranged_return.get("CustomerReturnDate"), 0)
        from_planned, to_planned = _window(planned_row.get("PlndOrderPlannedStartDate"), 0)
        from_operation, to_operation = _window(operation.get("OpActualExecutionStartDate") or planned_row.get("PlndOrderPlannedStartDate"), 0)

        samples.update(
            {
                "ap-payment": {"company_code": str(ap.get("CompanyCode") or "1010"), "supplier": str(ap.get("Supplier") or "1000000"), "as_of": today},
                "ar-collection": {"company_code": str(ar.get("CompanyCode") or "1010"), "customer": str(ar.get("Customer") or "1000000"), "as_of": today},
                "gr-ir-clearing": {"company_code": str(grir.get("CompanyCode") or "1010"), "gl_account": str(grir.get("GLAccount") or "0021100000"), "date_from": from_gl, "date_to": to_gl},
                "month-end-closing": {"company_code": str(period.get("CompanyCode") or "1010"), "fiscal_year": str(period.get("FiscalYear") or date.today().year), "period": str(period.get("FiscalPeriod") or date.today().month), "sap_client": str(self.settings.sap_client or "100")},
                "billing-block-diagnosis": {"sales_order": str(exact_sales.get("SalesOrder") or "5814")},
                "billing-completeness-check": {"billing_document": str(exact_billing.get("BillingDocument") or "90000025")},
                "billing-dispute-classification": {"billing_document": str(exact_billing.get("BillingDocument") or "90000025")},
                "billing-output-monitor": {"billing_document": str(exact_billing.get("BillingDocument") or "90000025")},
                "delivered-not-billed": {"sales_organization": str(ranged_delivery.get("SalesOrganization") or "1710"), "date_from": from_delivery, "date_to": to_delivery},
                "delivery-delay-prediction": {"sales_organization": str(requested_sales.get("SalesOrganization") or "1710"), "date_from": from_requested, "date_to": to_requested},
                "due-delivery-prioritization": {"sales_organization": str(requested_sales.get("SalesOrganization") or "1710"), "plant": str(item.get("ProductionPlant") or "1710"), "date_from": from_requested, "date_to": to_requested},
                "returns-credit-anomaly": {"sales_organization": str(ranged_return.get("SalesOrganization") or range_sales.get("SalesOrganization") or "1710"), "date_from": from_return, "date_to": to_return},
                "shortage-allocation-advisor": {"plant": str(item.get("ProductionPlant") or "1710"), "material": str(item.get("Material") or "SG21"), "date_from": from_requested, "date_to": to_requested},
                "new-sales-demand-coverage": {
                    "plant": str(item.get("ProductionPlant") or "1710"),
                    "mrp_area": str(item.get("ProductionPlant") or "1710"),
                    "horizon_days": 90,
                    "demand_items": [
                        {
                            "material": str(item.get("Material") or "SG21"),
                            "quantity": 1,
                            "demand_date": today,
                        }
                    ],
                },
                "order-to-cash-status": {"sales_order": str(exact_sales.get("SalesOrder") or "5814")},
                "demand-forecast-planning": {"plant": str(planned_row.get("ProductionPlant") or item.get("ProductionPlant") or "1710"), "materials": [str(planned_row.get("Material") or item.get("Material") or "SG21")], "date_from": from_planned, "date_to": to_planned},
                "mrp-exception-analysis": {"plant": str(mrp_row.get("MRPPlant") or mrp_master.get("MRPPlant") or "1710"), "mrp_area": str(mrp_row.get("MRPArea") or mrp_master.get("MRPArea") or "1710"), "material": str(mrp_row.get("Material") or mrp_master.get("Material") or "SG21"), "shortage_profile": str(mrp_row.get("MaterialShortageProfile") or "SAP000000001"), "shortage_counter": str(mrp_row.get("MaterialShortageProfileCount") or "001")},
                "production-order-monitoring": {"manufacturing_order": str(prod.get("ManufacturingOrder") or "1000000")},
                "production-scheduling-capacity": {"plant": str(operation.get("ProductionPlant") or planned_row.get("ProductionPlant") or "1710"), "work_center": str(operation.get("WorkCenter") or "ASSEMBLY"), "date_from": from_operation, "date_to": to_operation},
                "production-variance-analysis": {"manufacturing_order": str(prod.get("ManufacturingOrder") or "1000000")},
            }
        )
        return samples, sorted(set(missing))

    async def run_agent(self, client: httpx.AsyncClient, agent: str, values: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        response = await client.post(
            f"{self.api_url}/api/runs",
            json={"mode": "agent", "agentId": agent, "input": values},
        )
        response.raise_for_status()
        run_id = str(response.json()["run_id"])
        deadline = time.monotonic() + max(420, self.settings.max_run_seconds + 120)
        record: dict[str, Any] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            current = await client.get(f"{self.api_url}/api/runs/{run_id}")
            current.raise_for_status()
            record = current.json()
            if record.get("status") in TERMINAL:
                break
        else:
            await client.post(f"{self.api_url}/api/runs/{run_id}/cancel")
            raise TimeoutError(f"Agent {agent} exceeded the live-test timeout.")

        result = record.get("result") or {}
        evidence = result.get("evidence") or []
        rules = result.get("rule_results") or []
        completeness = result.get("completeness") or {}
        errors = result.get("errors") or ([] if not record.get("error") else [record["error"]])
        business = next((item for item in reversed(rules) if isinstance(item, dict) and item.get("business_report")), {})
        report = business.get("business_report") or {}
        get_count = _count_key(evidence, "http_method", lambda value: str(value).upper() == "GET")
        evidence_rows = _sum_result_counts(evidence)
        error_codes = _issue_codes(errors)
        terminal_status = str(record.get("status"))
        technical_chain = (
            "failed"
            if terminal_status not in {"completed", "inconclusive"} or get_count == 0
            else "partial"
            if completeness.get("source_complete") is not True and error_codes
            else "passed"
        )
        return {
            "agent": agent,
            "run_id": run_id,
            "status": terminal_status,
            "technical_chain": technical_chain,
            "source_complete": completeness.get("source_complete") is True,
            "business_complete": completeness.get("business_complete") is True,
            "missing_evidence": list(completeness.get("missing_evidence") or []),
            "sap_get_count": get_count,
            "evidence_row_count": evidence_rows,
            "headline": (report.get("headline") or {}).get("zh") if isinstance(report.get("headline"), dict) else report.get("headline"),
            "business_status": business.get("business_status"),
            "error_codes": error_codes,
            "events": _events(self.settings.database_path, run_id),
            "sample": _safe_input(values),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def _gap_for(case: dict[str, Any], missing: str, index: int) -> dict[str, Any]:
    skillhub_markers = {
        "payment_run_and_bank_master_evidence",
        "bank_receipt_matching_evidence",
        "period_control_asset_depreciation_and_specialized_closing_checks",
        "billing_output_status_evidence",
        "billing_dispute_text_evidence",
        "billing_output_and_dispute_evidence",
        "atp_availability_evidence",
        "pir_evidence",
    }
    observation_markers = {
        "sap_read_timeout": ("transient_environment", "none"),
        "sap_read_http_error": ("sap_system_gap", "none"),
        "complete_capacity_bucket_evidence": ("test_data_gap", "none"),
        "mrp_coverage_or_supply_demand_evidence": ("test_data_gap", "none"),
    }
    if missing in observation_markers:
        classification, repository = observation_markers[missing]
    else:
        repository = "SAPSkillhub" if missing in skillhub_markers else "SAPBusinessAgents"
        classification = "skillhub_missing_capability" if repository == "SAPSkillhub" else "sapbusinessagents_gap"
    gap_id = f"DET-{index:03d}"
    return {
        "gap_id": gap_id,
        "process": case["agent"],
        "mode": "fixed_agent",
        "failed_layer": "business_evidence",
        "classification": classification,
        "target_repository": repository,
        "expected_behavior": f"Provide auditable read-only evidence for {missing}.",
        "actual_behavior": "The deterministic Agent completed the available GET-only steps and explicitly marked this evidence as unavailable.",
        "business_impact": "The Agent cannot assert business-process completion from the currently available evidence.",
        "reproduction_steps": [f"Run fixed Agent {case['agent']} with a valid live sample.", "Review completeness.missing_evidence."],
        "affected_api/entity/skill": missing,
        "sanitized_request_shape": case["sample"],
        "error_code": None,
        "validation_issues": [],
        "run_id/case_id": case["run_id"],
        "source_complete": case["source_complete"],
        "reproducible": True,
        "workaround": "Use an approved manual read-only SAP check and retain the exported evidence.",
        "proposed_capability": f"Add a registered, structured, validated read-only evidence provider for {missing}.",
        "suggested_issue_title": f"Add read-only evidence contract for {missing}",
        "issue_candidate": repository != "none",
    }


def _runtime_gap(case: dict[str, Any], index: int) -> dict[str, Any]:
    gap_id = f"DET-{index:03d}"
    return {
        "gap_id": gap_id,
        "process": case["agent"],
        "mode": "fixed_agent",
        "failed_layer": "deterministic_runtime",
        "classification": "sapbusinessagents_gap",
        "target_repository": "SAPBusinessAgents",
        "expected_behavior": "The live-schema-validated GET-only workflow executes to a terminal evidence result.",
        "actual_behavior": f"Run ended as {case['status']} with codes: {', '.join(case['error_codes']) or 'none'}.",
        "business_impact": "No usable deterministic result was produced for this Agent.",
        "reproduction_steps": [f"Run fixed Agent {case['agent']} with the sanitized input shape.", "Review the run events and error codes."],
        "affected_api/entity/skill": "See the Agent Schema v2 execution plan.",
        "sanitized_request_shape": case["sample"],
        "error_code": case["error_codes"],
        "validation_issues": [],
        "run_id/case_id": case["run_id"],
        "source_complete": case["source_complete"],
        "reproducible": True,
        "workaround": "Run the corresponding bounded GET manually until the deterministic plan is corrected.",
        "proposed_capability": "Correct the deterministic plan or embedded-provider execution contract.",
        "suggested_issue_title": f"Fixed Agent {case['agent']} cannot complete its GET-only live run",
        "issue_candidate": True,
    }


def _write_report(
    output: Path,
    cases: list[dict[str, Any]],
    discoveries: list[dict[str, Any]],
    test_data_gaps: list[str],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    gaps: list[dict[str, Any]] = []
    for case in cases:
        if case["technical_chain"] == "failed":
            gaps.append(_runtime_gap(case, len(gaps) + 1))
        roots_seen: set[str] = set()
        for missing_value in case["missing_evidence"]:
            missing = str(missing_value)
            root = (
                "production_cost_relationship"
                if missing in {"production_cost_evidence", "production_cost_relationship"}
                else missing
            )
            if root in roots_seen:
                continue
            roots_seen.add(root)
            gaps.append(_gap_for(case, root, len(gaps) + 1))
    comparison = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "all deterministic Agents except the separately established P2P and O2C reference Agents",
        "safety": {"read_only": True, "http_methods": ["GET"], "provider": "embedded-odata"},
        "discovery": discoveries,
        "test_data_gaps": test_data_gaps,
        "cases": cases,
    }
    (output / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "capability-gaps.json").write_text(json.dumps(gaps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    issue_root = output / "issue-candidates"
    issue_root.mkdir(exist_ok=True)
    for gap in gaps:
        if gap.get("issue_candidate") is not True:
            continue
        body = [
            f"# {gap['suggested_issue_title']}",
            "",
            f"- Gap: `{gap['gap_id']}`",
            f"- Classification: `{gap['classification']}`",
            f"- Agent: `{gap['process']}`",
            f"- Run: `{gap['run_id/case_id']}`",
            f"- Source complete: `{str(gap['source_complete']).lower()}`",
            "",
            "## Expected",
            "",
            str(gap["expected_behavior"]),
            "",
            "## Actual",
            "",
            str(gap["actual_behavior"]),
            "",
            "## Business impact",
            "",
            str(gap["business_impact"]),
            "",
            "## Sanitized input shape",
            "",
            "```json",
            json.dumps(gap["sanitized_request_shape"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        (issue_root / f"{gap['target_repository']}-{gap['gap_id']}.md").write_text("\n".join(body), encoding="utf-8")

    passed = sum(case["technical_chain"] == "passed" for case in cases)
    partial = sum(case["technical_chain"] == "partial" for case in cases)
    complete = sum(case["status"] == "completed" for case in cases)
    inconclusive = sum(case["status"] == "inconclusive" for case in cases)
    failed = sum(case["technical_chain"] == "failed" for case in cases)
    lines = [
        "# 非参考固定 Agent 真机只读验收",
        "",
        f"- 已发出真实 SAP GET：**{sum(case['sap_get_count'] > 0 for case in cases)}/{len(cases)}**",
        f"- 技术链路完整通过：**{passed}/{len(cases)}**",
        f"- 技术链路部分通过：**{partial}**",
        f"- 业务结果 completed：**{complete}**",
        f"- 因证据或能力边界 inconclusive：**{inconclusive}**",
        f"- 技术失败：**{failed}**",
        f"- 能力缺口与观察项：**{len(gaps)}**",
        "- 安全边界：embedded SAP Provider，严格 GET-only；候选发现为有界查询，不作为完整性证据。",
        "",
        "| Agent | 技术链路 | 运行状态 | SAP GET | 证据行 | 查询源完整 | 业务完整 | 结论 |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for case in cases:
        headline = str(case.get("headline") or "").replace("|", "\\|")
        lines.append(
            f"| `{case['agent']}` | {case['technical_chain']} | {case['status']} | "
            f"{case['sap_get_count']} | {case['evidence_row_count']} | "
            f"{str(case['source_complete']).lower()} | {str(case['business_complete']).lower()} | {headline} |"
        )
    if test_data_gaps:
        lines.extend(["", "## 测试数据观察", ""])
        lines.extend(f"- `{item}`：自动发现未找到完整样本，已使用保守回退输入；结果不得视为完整业务验收。" for item in test_data_gaps)
    if gaps:
        lines.extend(["", "## 能力缺口", ""])
        for gap in gaps:
            suffix = "可形成 issue 候选" if gap.get("issue_candidate") else "仅记录观察，不形成 issue 候选"
            lines.append(f"- `{gap['gap_id']}` · `{gap['classification']}` · `{gap['target_repository']}` · `{gap['process']}`：{gap['affected_api/entity/skill']}（{suffix}）")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "`source_complete=true` 只表示该 Agent 配置的查询范围完整返回；不等于业务流程完成。",
            "固定 Agent 的业务结论仅来自确定性规则；缺少银行、输出、争议、PIR、ATP、期间控制或成本关联证据时，必须保持 `inconclusive`。",
            "",
        ]
    )
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")


async def _main(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    validator = LiveValidator(root, args.api_url)
    health = await validator.provider.health()
    if health.get("ok") is not True or (health.get("data") or {}).get("read_only") is not True:
        raise RuntimeError("Embedded SAP Provider is not configured as read-only.")
    samples, test_data_gaps = await validator.samples()
    manifests = []
    for path in root.glob("agents/*/*/agent.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest.get("schemaVersion") == 2
            and manifest.get("slug") not in EXCLUDED_REFERENCE_AGENTS
            and manifest.get("slug") in samples
        ):
            manifests.append((str(manifest.get("module") or ""), str(manifest["slug"])))
    manifests.sort()
    cases: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
        platform = await client.get(f"{validator.api_url}/api/health")
        platform.raise_for_status()
        platform_health = platform.json()
        if (
            platform_health.get("ok") is not True
            or (platform_health.get("sap_read") or {}).get("selected_provider") != "embedded"
            or ((platform_health.get("sap_read") or {}).get("data") or {}).get("read_only") is not True
            or int(platform_health.get("executable_agents") or 0) != 30
        ):
            raise RuntimeError("SAPBusinessAgents is not ready with every embedded GET-only Agent.")
        for _module, agent in manifests:
            values = samples[agent]
            try:
                case = await validator.run_agent(client, agent, values)
            except (httpx.HTTPError, TimeoutError) as exc:
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
                    "sample": _safe_input(values),
                    "elapsed_ms": 0,
                }
            cases.append(case)
            print(
                f"{len(cases):02d}/{len(manifests)} {agent}: {case['status']} "
                f"GET={case['sap_get_count']} rows={case['evidence_row_count']}",
                flush=True,
            )
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output = Path(args.output).resolve() if args.output else root / ".local-data" / "live-tests" / f"{stamp}-deterministic-agents"
    _write_report(output, cases, validator.discovery_observations, test_data_gaps)
    print(f"REPORT={output}", flush=True)
    return 0 if all(case["technical_chain"] != "failed" for case in cases) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate all non-reference deterministic Agents against live SAP using GET only.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
