from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform import rules


def _response(step_results: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    return {
        "ok": True,
        "case_id": "case-fixture",
        "data": {
            "source_complete": True,
            "step_results": {
                step_id: {"source_complete": True, "results": rows}
                for step_id, rows in step_results.items()
            },
        },
    }


def test_p2p_rule_never_treats_clearing_reference_as_payment() -> None:
    result = rules.evaluate_p2p_status(
        {
            "sapclaw": _response(
                {
                    "purchase_order": [{"PurchaseOrder": "fixture"}],
                    "purchase_order_items": [{"PurchaseOrderItem": "10"}],
                    "material_documents": [{"GoodsMovementType": "101"}],
                    "supplier_invoice_items": [{"SupplierInvoice": "fixture"}],
                    "accounting_items": [
                        {
                            "IsCleared": True,
                            "ClearingAccountingDocument": "fixture-clearing",
                            "AccountingDocumentType": "RE",
                            "PaymentMethod": "",
                            "HouseBank": "",
                        }
                    ],
                }
            )
        }
    )
    assert result["stages"]["fi_clearing"]["state"] == "confirmed"
    assert result["stages"]["payment"]["state"] == "not_confirmed"
    assert result["business_status"] == "partial"
    assert result["status"] == "inconclusive"
    assert result["business_report"]["headline"]["zh"] == "已确认财务清账，尚未确认付款"
    assert result["business_report"]["tone"] == "warning"
    payment_stage = next(
        stage for stage in result["business_report"]["stages"] if stage["id"] == "payment"
    )
    assert payment_stage["state_label"]["zh"] == "未确认"
    assert "清账编号本身不能证明已经付款" in payment_stage["detail"]["zh"]


def test_p2p_business_report_explains_receipt_without_invoice_in_business_language() -> None:
    result = rules.evaluate_p2p_status(
        {
            "sapclaw": _response(
                {
                    "purchase_order": [{"PurchaseOrder": "fixture"}],
                    "purchase_order_items": [{"PurchaseOrderItem": "10"}],
                    "material_documents": [{"GoodsMovementType": "101"}],
                    "supplier_invoice_items": [],
                    "accounting_items": [
                        {"AccountingDocumentType": "WE", "IsCleared": False},
                        {"AccountingDocumentType": "WE", "IsCleared": False},
                    ],
                    "clearing_documents": [],
                }
            )
        }
    )

    report = result["business_report"]
    assert report["headline"]["zh"] == "已找到采购订单和收货记录，尚未找到供应商发票"
    assert "没有找到引用该订单的供应商发票" in report["overview"]["zh"]
    assert "partial" not in report["summary"]["zh"]
    invoice_stage = next(stage for stage in report["stages"] if stage["id"] == "supplier_invoice")
    assert invoice_stage["state_label"]["zh"] == "未找到"
    assert any("MIRO" in action for action in report["next_actions"]["zh"])


def test_o2c_rule_separates_ar_clearing_from_bank_receipt() -> None:
    result = rules.evaluate_o2c_status(
        {
            "sapclaw": _response(
                {
                    "sales_order": [{"SalesOrder": "fixture"}],
                    "sales_order_items": [{"SalesOrderItem": "10"}],
                    "delivery_items": [
                        {"DeliveryDocument": "fixture", "GoodsMovementStatus": "C"}
                    ],
                    "delivery_headers": [{"OverallGoodsMovementStatus": "C"}],
                    "billing_items_by_delivery": [{"BillingDocument": "fixture"}],
                    "billing_headers_by_delivery": [{"OverallBillingStatus": "C"}],
                    "accounting_by_delivery_billing": [
                        {
                            "IsCleared": True,
                            "ClearingAccountingDocument": "fixture-clearing",
                            "AccountingDocumentType": "RV",
                        }
                    ],
                    "clearing_documents_by_delivery_billing": [
                        {"AccountingDocumentType": "DZ", "HouseBank": ""}
                    ],
                }
            )
        }
    )
    assert result["stages"]["ar_clearing"]["state"] == "confirmed"
    assert result["stages"]["bank_receipt"]["state"] == "unknown"
    assert result["business_status"] == "complete_to_ar_clearing"
    assert result["status"] == "complete"
    assert result["business_report"]["tone"] == "info"
    assert result["business_report"]["headline"]["zh"] == "订单已完成至应收清账，银行到账仍需单独确认"
    assert "财务清账本身不能证明款项已经到达银行" in result["business_report"]["overview"]["zh"]


def test_fixed_manifests_use_process_rules_and_delivery_to_billing_binding() -> None:
    root = Path(__file__).resolve().parents[1]
    p2p = json.loads(
        (root / "agents" / "MM" / "procure-to-pay-status" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    o2c = json.loads(
        (root / "agents" / "SD" / "order-to-cash-status" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    assert p2p["execution"]["steps"][-1]["operation"] == "evaluate_p2p_status"
    assert o2c["execution"]["steps"][-1]["operation"] == "evaluate_o2c_status"
    plan_steps = o2c["execution"]["steps"][0]["request"]["plan"]["steps"]
    billing = next(step for step in plan_steps if step["step_id"] == "billing_items_by_delivery")
    assert billing["filter_from_previous"] == [
        {
            "field": "ReferenceSDDocument",
            "source_step_id": "delivery_items",
            "source_field": "DeliveryDocument",
            "fanout": True,
            "fetch_all_for_binding": True,
        }
    ]
