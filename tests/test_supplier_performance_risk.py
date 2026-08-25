from __future__ import annotations

from sap_business_agents_platform.agent_rules import evaluate_business_agent


def _payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "ok": True,
        "source_complete": True,
        "data": {"results": rows},
    }


def test_supplier_performance_uses_localized_otif_name() -> None:
    schedules = [
        {
            "PurchaseOrder": "4500000001",
            "PurchaseOrderItem": "10",
            "ScheduleLine": str(index),
            "ScheduleLineDeliveryDate": f"2026-08-{index:02d}",
            "ScheduleLineOrderQuantity": "10",
            "PurchaseOrderQuantityUnit": "PC",
        }
        for index in range(1, 6)
    ]
    receipts = [
        {
            "PurchaseOrder": "4500000001",
            "PurchaseOrderItem": "10",
            "MaterialDocumentYear": "2026",
            "MaterialDocument": f"500000000{index}",
            "MaterialDocumentItem": "1",
            "PostingDate": f"2026-08-{index:02d}",
            "QuantityInEntryUnit": "10",
            "DebitCreditCode": "S",
        }
        for index in range(1, 6)
    ]
    result = evaluate_business_agent(
        {
            "agent_id": "supplier-performance-risk",
            "run_input": {"date_to": "2026-08-31"},
            "evidence": {
                "po_schedule": _payload(schedules),
                "receipt": _payload(receipts),
                "supplier": _payload([{"Supplier": "SUPPLIER-1"}]),
            },
        }
    )

    report = result["business_report"]
    assert "准时足量交付率(OTIF)" in report["headline"]["zh"]
    assert "On Time In Full (OTIF)" in report["headline"]["en"]
    otif_metric = next(
        metric for metric in report["metrics"] if metric["id"] == "otif_percent"
    )
    assert otif_metric["label"] == {
        "zh": "准时足量交付率(OTIF)",
        "en": "On Time In Full (OTIF)",
    }
