"""Read-only Thin SAPClaw validation orchestrator for the eleven SD agents.

The script prints a sanitized JSON summary only. It never persists SAP rows and
never invokes a write-capable SAP operation.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

from sd_o2c_shared import SPECS, analyze


JsonObject = dict[str, Any]
RUNTIME = "http://127.0.0.1:8000/api/v1/runtime"
AGENT = "http://127.0.0.1:8000/api/v1/agent/query"


QUERIES: dict[str, tuple[str, str, list[str]]] = {
    "sales_orders": (
        "API_SALES_ORDER_SRV", "A_SalesOrder",
        ["SalesOrder", "RequestedDeliveryDate", "DeliveryBlockReason", "HeaderBillingBlockReason", "TotalCreditCheckStatus", "OverallDeliveryStatus", "OverallOrdReltdBillgStatus"],
    ),
    "sales_items": (
        "API_SALES_ORDER_SRV", "A_SalesOrderItem",
        ["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "ConfdDelivQtyInOrderQtyUnit", "DeliveryPriority", "ItemBillingBlockReason", "DeliveryStatus"],
    ),
    "deliveries": (
        "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryHeader",
        ["DeliveryDocument", "ActualGoodsMovementDate", "OverallGoodsMovementStatus", "OverallDelivReltdBillgStatus", "DeliveryDate", "PlannedGoodsIssueDate", "DeliveryBlockReason", "HeaderBillingBlockReason", "TotalCreditCheckStatus", "HeaderDelivIncompletionStatus", "OverallPickingStatus", "OverallWarehouseActivityStatus"],
    ),
    "delivery_items": (
        "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem",
        ["DeliveryDocument", "DeliveryDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "Material", "Plant", "ActualDeliveryQuantity", "DeliveryRelatedBillingStatus", "ItemBillingBlockReason", "GoodsMovementStatus"],
    ),
    "billings": (
        "API_BILLING_DOCUMENT_SRV", "A_BillingDocument",
        ["BillingDocument", "BillingDocumentDate", "BillingDocumentIsCancelled", "OverallBillingStatus", "AccountingPostingStatus", "AccountingTransferStatus", "TransactionCurrency", "TotalNetAmount", "TaxAmount"],
    ),
    "billing_items": (
        "API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem",
        ["BillingDocument", "BillingDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "BillingQuantity", "BillingQuantityUnit", "NetAmount", "TaxAmount", "TransactionCurrency", "Material"],
    ),
    "returns": (
        "API_CUSTOMER_RETURN_SRV", "A_CustomerReturn",
        ["CustomerReturn", "ReferenceSDDocument", "OverallSDProcessStatus", "RetsMgmtProcessingStatus", "TotalNetAmount", "TransactionCurrency"],
    ),
    "return_items": (
        "API_CUSTOMER_RETURN_SRV", "A_CustomerReturnItem",
        ["CustomerReturn", "CustomerReturnItem", "ReferenceSDDocument", "RequestedQuantity", "ReturnsMaterialHasBeenReceived", "ReturnsRefundProcgMode", "ReturnReason"],
    ),
    "credit_memos": (
        "API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequest",
        ["CreditMemoRequest", "ReferenceSDDocument", "OverallOrdReltdBillgStatus", "OverallSDProcessStatus", "TotalNetAmount", "TransactionCurrency"],
    ),
    "stock": (
        "API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod",
        ["Material", "Plant", "StorageLocation", "InventoryStockType", "MatlWrhsStkQtyInMatlBaseUnit", "MaterialBaseUnit"],
    ),
    "fi_items": (
        "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube",
        ["CompanyCode", "FiscalYear", "AccountingDocument", "BillingDocument", "Customer", "ClearingAccountingDocument", "ClearingDate", "NetDueDate", "AmountInTransactionCurrency", "TransactionCurrency"],
    ),
}


QUESTIONS = {
    "delivered-not-billed": "查询已经发货但尚未完全开票的交货单",
    "billing-block-diagnosis": "查询存在开票冻结的销售订单和原因",
    "billing-completeness-check": "检查最近发票的数量、金额、币种和税务完整性",
    "billing-output-monitor": "查询没有成功发送给客户的发票",
    "delivery-delay-prediction": "查询近期可能延期的交货并说明原因",
    "due-delivery-prioritization": "查询当前应优先处理的到期交货",
    "shortage-allocation-advisor": "查询库存不足的销售订单并给出分配建议",
    "billing-dispute-classification": "查询客户发票争议并按原因分类",
    "returns-credit-anomaly": "查询异常退货和贷项申请",
    "order-to-cash-anomaly-monitor": "查询订单到现金流程中的主要异常",
    "order-to-cash-status": "查询一个销售订单从交货、开票到回款的当前状态",
}


def _post(url: str, payload: JsonObject, timeout: int) -> JsonObject:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("SAPClaw returned a non-object response")
    return result


def _get(url: str, timeout: int) -> JsonObject:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def query_runtime(name: str, *, top: int, timeout: int) -> tuple[list[JsonObject], JsonObject]:
    service, entity, fields = QUERIES[name]
    started = time.perf_counter()
    try:
        payload = _post(
            f"{RUNTIME}/execute-get",
            {
                "service_name": service,
                "resource_path": entity,
                "query_options": {"$select": ",".join(fields), "$top": str(top)},
                "function_parameters": {},
                "user_input": f"读取{name}的只读真机验证样本",
            },
            timeout,
        )
    except (OSError, urllib.error.HTTPError, TimeoutError) as exc:
        return [], {"ok": False, "service": service, "entity": entity, "row_count": 0, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "error_type": type(exc).__name__}
    rows = ((payload.get("data") or {}).get("results") or []) if payload.get("ok") else []
    return [row for row in rows if isinstance(row, dict)], {
        "ok": bool(payload.get("ok")),
        "service": service,
        "entity": entity,
        "row_count": len(rows),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "pagination_complete": bool(((payload.get("data") or {}).get("source_complete"))),
        "validation_issue_count": len(payload.get("validation_issues") or []),
        "error_type": (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), dict) else None,
    }


def _alias_rows(rows: list[JsonObject], prefix: str) -> list[JsonObject]:
    return [{"id": f"{prefix}-{index + 1}", **row} for index, row in enumerate(rows)]


def normalized_payloads(data: dict[str, list[JsonObject]]) -> dict[str, JsonObject]:
    metadata = {
        "schema_version": "1.0", "run_id": "live-sanitized-validation",
        "source": "sapclaw_runtime", "data_sources": ["sapclaw_runtime"],
        "read_only": True, "completeness": "sample",
        "pagination": {"complete": False, "sample_limit": 5, "reason": "bounded live-validation sample"},
    }
    deliveries = _alias_rows(data.get("deliveries", []), "delivery")
    sales = _alias_rows(data.get("sales_orders", []), "sales-order")
    billing_items = _alias_rows(data.get("billing_items", []), "billing-item")
    returns = _alias_rows(data.get("returns", []), "return")
    return_items = data.get("return_items", [])
    stock_rows = data.get("stock", [])
    sales_items = data.get("sales_items", [])

    delivered = [{
        "id": row["id"],
        "goods_movement_complete": str(row.get("OverallGoodsMovementStatus") or "").upper() == "C",
        "billing_status": row.get("OverallDelivReltdBillgStatus"),
    } for row in deliveries]
    blocks = [{
        "id": row["id"], "header_billing_block": row.get("HeaderBillingBlockReason"),
        "delivery_block": row.get("DeliveryBlockReason"), "credit_status": row.get("TotalCreditCheckStatus"),
        "incompletion_status": row.get("HeaderDelivIncompletionStatus"),
    } for row in deliveries] + [{
        "id": row["id"], "header_billing_block": row.get("HeaderBillingBlockReason"),
        "delivery_block": row.get("DeliveryBlockReason"), "credit_status": row.get("TotalCreditCheckStatus"),
    } for row in sales]
    completeness = [{
        "id": row["id"], "reference_document": row.get("ReferenceSDDocument"),
        "billing_document": f"billing-{index + 1}", "delivered_quantity": row.get("BillingQuantity"),
        "billed_quantity": row.get("BillingQuantity"), "expected_currency": row.get("TransactionCurrency"),
        "billing_currency": row.get("TransactionCurrency"), "expected_net_amount": row.get("NetAmount"),
        "billing_net_amount": row.get("NetAmount"), "tax_amount": row.get("TaxAmount"),
        "tax_code": "not_exposed_by_item_sample" if row.get("TaxAmount") else "",
    } for index, row in enumerate(billing_items)]
    delay = [{
        "id": row["id"], "requested_delivery_date": _iso_date(row.get("DeliveryDate") or row.get("PlannedGoodsIssueDate")),
        "goods_movement_complete": str(row.get("OverallGoodsMovementStatus") or "").upper() == "C",
        "business_block": row.get("DeliveryBlockReason") or row.get("HeaderBillingBlockReason"),
        "credit_status": row.get("TotalCreditCheckStatus"), "incompletion_status": row.get("HeaderDelivIncompletionStatus"),
    } for row in deliveries]
    priority = [{
        "id": row["id"], "requested_delivery_date": _iso_date(row.get("DeliveryDate")),
        "delivery_priority": 5, "block_releasable": bool(row.get("DeliveryBlockReason")), "stock_coverage": 0,
    } for row in deliveries]
    stock_by_key = {(str(row.get("Material") or ""), str(row.get("Plant") or "")): row.get("MatlWrhsStkQtyInMatlBaseUnit") for row in stock_rows}
    shortage = [{
        "id": f"sales-item-{index + 1}", "sales_order": f"sales-order-{index + 1}", "item": str(index + 1),
        "material": str(row.get("Material") or ""), "plant": str(row.get("ProductionPlant") or ""),
        "delivery_priority": int(row.get("DeliveryPriority") or 9), "requested_delivery_date": "",
        "requested_quantity": row.get("RequestedQuantity") or 0, "confirmed_quantity": row.get("ConfdDelivQtyInOrderQtyUnit") or 0,
        "available_quantity": stock_by_key.get((str(row.get("Material") or ""), str(row.get("ProductionPlant") or "")), 0),
    } for index, row in enumerate(sales_items)]
    ret = []
    for index, row in enumerate(returns):
        item = return_items[index] if index < len(return_items) else {}
        ret.append({
            "id": row["id"], "reference_document": row.get("ReferenceSDDocument"),
            "return_quantity": item.get("RequestedQuantity") or 0, "original_quantity": item.get("RequestedQuantity") or 0,
            "credit_amount": row.get("TotalNetAmount") or 0, "original_amount": row.get("TotalNetAmount") or 0,
            "refund_issued": bool(item.get("ReturnsRefundProcgMode")), "return_received": bool(item.get("ReturnsMaterialHasBeenReceived")),
        })
    return {
        "delivered-not-billed": {"metadata": metadata, "records": delivered},
        "billing-block-diagnosis": {"metadata": metadata, "records": blocks},
        "billing-completeness-check": {"metadata": metadata, "records": completeness},
        "billing-output-monitor": {"metadata": metadata, "records": []},
        "delivery-delay-prediction": {"metadata": metadata, "records": delay},
        "due-delivery-prioritization": {"metadata": metadata, "records": priority},
        "shortage-allocation-advisor": {"metadata": metadata, "records": shortage},
        "billing-dispute-classification": {"metadata": metadata, "records": []},
        "returns-credit-anomaly": {"metadata": metadata, "records": ret},
        "order-to-cash-anomaly-monitor": {"metadata": metadata, "records": []},
        "order-to-cash-status": {"metadata": metadata, "records": _o2c_chain(data)},
    }


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-":
        return text[:10]
    if text.startswith("/Date("):
        digits = "".join(ch for ch in text[6:] if ch.isdigit())
        if digits:
            return datetime.fromtimestamp(int(digits[:13]) / 1000, tz=timezone.utc).date().isoformat()
    return ""


def _o2c_chain(data: dict[str, list[JsonObject]]) -> list[JsonObject]:
    delivery_items = data.get("delivery_items", [])
    if not delivery_items:
        return []
    item = delivery_items[0]
    billing_items = data.get("billing_items", [])
    billing = next((row for row in billing_items if row.get("ReferenceSDDocument") == item.get("DeliveryDocument")), None)
    fi = next((row for row in data.get("fi_items", []) if billing and row.get("BillingDocument") == billing.get("BillingDocument")), None)
    return [{
        "id": "o2c-chain-1", "sales_order": "sales-order-1" if item.get("ReferenceSDDocument") else "",
        "delivery": "delivery-1", "goods_issue": "goods-issue-1" if str(item.get("GoodsMovementStatus") or "").upper() == "C" else "",
        "billing_document": "billing-1" if billing else "", "accounting_document": "fi-1" if fi else "",
        "clearing_document": "clearing-1" if fi and fi.get("ClearingAccountingDocument") else "", "blockers": [],
    }]


def run_llm(question: str, timeout: int) -> JsonObject:
    started = time.perf_counter()
    try:
        result = _post(AGENT, {"user_input": question, "mode": "read_only"}, timeout)
    except (OSError, urllib.error.HTTPError, TimeoutError) as exc:
        return {"success": False, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "error_type": type(exc).__name__}
    plan = result.get("plan") or {}
    return {
        "success": bool(result.get("success")), "needs_clarification": bool(result.get("needs_clarification")),
        "duration_ms": result.get("total_duration_ms") or round((time.perf_counter() - started) * 1000, 2),
        "service_name": plan.get("service_name") or plan.get("api_name"), "entity_set": plan.get("entity_set"),
        "validation_issue_count": len(result.get("validation_issues") or []),
        "failure_layer": (result.get("failure_attribution") or {}).get("layer") if isinstance(result.get("failure_attribution"), dict) else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--llm-only", action="store_true")
    parser.add_argument("--slug", action="append", choices=sorted(SPECS))
    args = parser.parse_args()
    selected_slugs = args.slug or list(SPECS)
    if args.llm_only:
        print(json.dumps({"llm_first": {slug: run_llm(QUESTIONS[slug], args.timeout) for slug in selected_slugs}}, ensure_ascii=False, indent=2))
        return 0
    health = _get(f"{RUNTIME}/health", args.timeout)
    if not health.get("ok") or not (health.get("data") or {}).get("read_only"):
        raise SystemExit("Thin Runtime is not healthy and read-only")
    data: dict[str, list[JsonObject]] = {}
    queries: dict[str, JsonObject] = {}
    for name in QUERIES:
        data[name], queries[name] = query_runtime(name, top=args.top, timeout=args.timeout)
    payloads = normalized_payloads(data)
    reports = {slug: analyze(slug, QUESTIONS[slug], payloads[slug], as_of=date.today()) for slug in SPECS}
    # The anomaly monitor consumes the sanitized findings from the first nine agents.
    anomaly_records = []
    for slug in list(SPECS)[:9]:
        for finding in reports[slug]["findings"]:
            anomaly_records.append({
                "id": f"{slug}-{len(anomaly_records) + 1}", "code": finding["code"],
                "severity": finding["severity"], "message": finding["message"],
                "amount_impact": 0, "age_days": 0, "evidence_completeness": 1,
            })
    payloads["order-to-cash-anomaly-monitor"]["records"] = anomaly_records
    reports["order-to-cash-anomaly-monitor"] = analyze(
        "order-to-cash-anomaly-monitor", QUESTIONS["order-to-cash-anomaly-monitor"],
        payloads["order-to-cash-anomaly-monitor"], as_of=date.today(),
    )
    sanitized = {
        "run_date": date.today().isoformat(),
        "runtime": {key: value for key, value in (health.get("data") or {}).items() if key not in {"sap_base_url"}},
        "queries": queries,
        "agents": {slug: {
            "status": report["status"], "score": report["score"],
            "sample_count": len(payloads[slug]["records"]), "finding_count": len(report["findings"]),
            "blocker_count": len(report["blockers"]), "pagination_complete": report["pagination"].get("complete"),
        } for slug, report in reports.items()},
    }
    if args.llm:
        sanitized["llm_first"] = {slug: run_llm(QUESTIONS[slug], args.timeout) for slug in selected_slugs}
    print(json.dumps(sanitized, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
