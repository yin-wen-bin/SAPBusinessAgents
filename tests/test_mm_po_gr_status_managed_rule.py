from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.engine import _business_markdown_report
from sap_business_agents_platform.managed_rules import execute_managed_rule, source_digest
from sap_business_agents_platform.models import RunMode


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "tests" / "fixtures" / "managed_rules" / "mm_po_gr_status.py").read_text(encoding="utf-8")
DIGEST = source_digest(SOURCE)


def _step(results):
    return {"ok": True, "source_complete": True, "source_truncated": False, "results": results}


def _input(receipts=None, schedules=None):
    schedules = schedules or [
        {"PurchasingDocument": "PO-TEST-0001", "PurchasingDocumentItem": "10", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "2026-07-17T00:00:00Z", "ScheduleLineOrderQuantity": "1", "PurchaseOrderQuantityUnit": "PC"},
        {"PurchasingDocument": "PO-TEST-0002", "PurchasingDocumentItem": "10", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "/Date(1784246400000)/", "ScheduleLineOrderQuantity": "1", "PurchaseOrderQuantityUnit": "PC"},
    ]
    items = [
        {"PurchaseOrder": row["PurchasingDocument"], "PurchaseOrderItem": row["PurchasingDocumentItem"], "Material": "M-TEST", "Plant": "T100", "PurchaseOrderQuantityUnit": "PC", "IsCompletelyDelivered": row["PurchasingDocument"] == "PO-TEST-0002"}
        for row in schedules
    ]
    headers = [
        {"PurchaseOrder": row["PurchasingDocument"], "Supplier": "SUPPLIER-TEST", "PurchasingOrganization": "T100", "PurchasingGroup": "T01"}
        for row in schedules
    ]
    receipt_rows = receipts if receipts is not None else [
        {"MaterialDocumentYear": "2026", "MaterialDocument": "MD-TEST-0001", "MaterialDocumentItem": "1", "PurchaseOrder": "PO-TEST-0002", "PurchaseOrderItem": "10", "GoodsMovementType": "101", "DebitCreditCode": "S", "QuantityInEntryUnit": "1", "EntryUnit": "PC"}
    ]
    receipt_headers = [
        {"MaterialDocumentYear": row["MaterialDocumentYear"], "MaterialDocument": row["MaterialDocument"], "PostingDate": "2026-06-18T00:00:00Z"}
        for row in receipt_rows
    ]
    return {"run_input": {"schedule_line_delivery_date": "2026-07-17", "purchasing_organization": "T100"}, "evidence": {"data": {"step_results": {
        "target_schedule_lines": _step(schedules),
        "purchase_order_items": _step(items),
        "purchase_order_headers": _step(headers),
        "all_schedule_lines": _step(schedules),
        "material_document_items": _step(receipt_rows),
        "material_document_headers": _step(receipt_headers),
    }}}}


def _evaluate(value):
    return execute_managed_rule(SOURCE, value, expected_digest=DIGEST)["workflow_output"]


def test_zero_and_full_receipt_produce_business_rows_with_normal_dates():
    result = _evaluate(_input())
    assert result["schedule_line_count"] == 2
    assert [(row["purchase_order"], row["receipt_status"]) for row in result["records"]] == [
        ("PO-TEST-0001", "not_received"), ("PO-TEST-0002", "fully_received")
    ]
    assert {row["schedule_line_delivery_date"] for row in result["records"]} == {"2026-07-17"}
    assert result["records"][1]["latest_goods_receipt_posting_date"] == "2026-06-18"
    assert result["business_report"]["records"][0]["purchase_order"] == result["records"][0]["purchase_order"]
    assert result["business_report"]["records"][0]["receipt_status"] == {"zh": "未入库", "en": "Not received"}
    assert result["business_report"]["findings"][0]["text"]["zh"] == "SAP交货完成标识与实际入库数量分别判断。"


def test_business_markdown_contains_the_same_schedule_line_rows():
    output = _evaluate(_input())
    run = SimpleNamespace(
        summary={"zh": "", "en": ""},
        completeness=SimpleNamespace(source_complete=True, reason="GET-only evidence complete."),
        run_id="trial-mm-po-gr",
        mode=RunMode.agent,
        agent_id="mm-po-gr-status",
        completed_at="2026-09-13T00:00:00Z",
    )
    markdown = _business_markdown_report(run, output["business_report"])
    assert "## 业务记录" in markdown
    assert "PO-TEST-0001" in markdown and "未入库" in markdown
    assert "PO-TEST-0002" in markdown and "全部入库" in markdown


def test_partial_receipt_and_reversal_are_allocated_across_all_schedule_lines():
    schedules = [
        {"PurchasingDocument": "4500002000", "PurchasingDocumentItem": "10", "ScheduleLine": "1", "ScheduleLineDeliveryDate": "2026-07-16", "ScheduleLineOrderQuantity": "2", "PurchaseOrderQuantityUnit": "PC"},
        {"PurchasingDocument": "4500002000", "PurchasingDocumentItem": "10", "ScheduleLine": "2", "ScheduleLineDeliveryDate": "2026-07-17", "ScheduleLineOrderQuantity": "3", "PurchaseOrderQuantityUnit": "PC"},
    ]
    receipts = [
        {"MaterialDocumentYear": "2026", "MaterialDocument": "1", "MaterialDocumentItem": "1", "PurchaseOrder": "4500002000", "PurchaseOrderItem": "10", "GoodsMovementType": "101", "DebitCreditCode": "S", "QuantityInEntryUnit": "4", "EntryUnit": "PC"},
        {"MaterialDocumentYear": "2026", "MaterialDocument": "2", "MaterialDocumentItem": "1", "PurchaseOrder": "4500002000", "PurchaseOrderItem": "10", "GoodsMovementType": "102", "DebitCreditCode": "H", "QuantityInEntryUnit": "1", "EntryUnit": "PC"},
    ]
    value = _input(receipts, schedules)
    value["evidence"]["data"]["step_results"]["target_schedule_lines"]["results"] = [schedules[1]]
    result = _evaluate(value)
    assert result["records"][0]["received_quantity"] == "1"
    assert result["records"][0]["open_quantity"] == "2"
    assert result["records"][0]["receipt_status"] == "partially_received"


def test_unit_conflict_and_same_key_conflict_are_inconclusive():
    unit = _input([{ "MaterialDocumentYear": "2026", "MaterialDocument": "1", "MaterialDocumentItem": "1", "PurchaseOrder": "PO-TEST-0001", "PurchaseOrderItem": "10", "GoodsMovementType": "101", "DebitCreditCode": "S", "QuantityInEntryUnit": "1", "EntryUnit": "KG" }])
    result = _evaluate(unit)
    assert result["business_status"] == "inconclusive"
    assert "receipt_unit_conflict" in result["business_report"]["missing_evidence"] or result["inconclusive_count"] > 0

    conflict = _input()
    duplicate = deepcopy(conflict["evidence"]["data"]["step_results"]["target_schedule_lines"]["results"][0])
    duplicate["ScheduleLineOrderQuantity"] = "2"
    conflict["evidence"]["data"]["step_results"]["target_schedule_lines"]["results"].append(duplicate)
    result = _evaluate(conflict)
    assert result["business_status"] == "inconclusive"
    assert result["evidence_complete"] is False


def test_exact_duplicates_do_not_multiply_rows_and_incomplete_source_fails_closed():
    value = _input()
    first = value["evidence"]["data"]["step_results"]["target_schedule_lines"]["results"][0]
    value["evidence"]["data"]["step_results"]["target_schedule_lines"]["results"].append(deepcopy(first))
    result = _evaluate(value)
    assert result["schedule_line_count"] == 2
    value["evidence"]["data"]["step_results"]["material_document_headers"]["source_complete"] = False
    result = _evaluate(value)
    assert result["business_status"] == "inconclusive"
    assert result["source_complete"] is False
