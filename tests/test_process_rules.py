from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform import rules
from sap_business_agents_platform.engine import _business_markdown_report, _default_presentation
from sap_business_agents_platform.models import Completeness, RunMode, RunResult


def _response(
    step_results: dict[str, list[dict[str, object]]], *, complete: bool = True
) -> dict[str, object]:
    return {
        "ok": True,
        "case_id": "case-fixture",
        "data": {
            "source_complete": complete,
            "step_results": {
                step_id: {"source_complete": complete, "results": rows}
                for step_id, rows in step_results.items()
            },
        },
    }


def _fi_row(
    document: str,
    item: str,
    document_type: str,
    *,
    account_type: str,
    gl_account: str,
    amount: str,
    currency: str = "USD",
    purchase_order: str = "4500000041",
    purchase_order_item: str = "10",
    cleared: bool = False,
    clearing_document: str = "",
    clearing_year: str = "0",
    clearing_date: str = "",
) -> dict[str, object]:
    return {
        "CompanyCode": "1710",
        "FiscalYear": "2017",
        "AccountingDocument": document,
        "AccountingDocumentItem": item,
        "AccountingDocumentType": document_type,
        "FinancialAccountType": account_type,
        "GLAccount": gl_account,
        "AmountInCompanyCodeCurrency": amount,
        "CompanyCodeCurrency": currency,
        "PurchasingDocument": purchase_order,
        "PurchasingDocumentItem": purchase_order_item,
        "IsCleared": cleared,
        "ClearingAccountingDocument": clearing_document,
        "ClearingDocFiscalYear": clearing_year,
        "ClearingDate": clearing_date,
    }


def _p2p_steps(
    *,
    invoice_amount: str = "35.00",
    invoice_currency: str = "USD",
    vendor_cleared: bool = True,
    include_vendor_row: bool = True,
    payment_type: str = "ZP",
    payment_method: str = "C",
    house_bank: str = "USBK1",
) -> dict[str, list[dict[str, object]]]:
    receipt_inventory = _fi_row(
        "5000000029", "1", "WE", account_type="S", gl_account="51100000", amount="35.00"
    )
    receipt_grir = _fi_row(
        "5000000029", "2", "WE", account_type="S", gl_account="21120000", amount="-35.00"
    )
    invoice_grir = _fi_row(
        "5100000025",
        "2",
        "RE",
        account_type="S",
        gl_account="21120000",
        amount=invoice_amount,
        currency=invoice_currency,
    )
    vendor = _fi_row(
        "5100000025",
        "1",
        "RE",
        account_type="K",
        gl_account="21100000",
        amount="-35.00",
        purchase_order="",
        purchase_order_item="",
        cleared=vendor_cleared,
        clearing_document="2000000006" if vendor_cleared else "",
        clearing_year="2018" if vendor_cleared else "0",
        clearing_date="/Date(1520899200000)/" if vendor_cleared else "",
    )
    clearing_rows = []
    if vendor_cleared:
        clearing_rows = [
            {
                "CompanyCode": "1710",
                "FiscalYear": "2018",
                "AccountingDocument": "2000000006",
                "AccountingDocumentItem": "1",
                "AccountingDocumentType": payment_type,
                "FinancialAccountType": "K",
                "PaymentMethod": payment_method,
                "HouseBank": house_bank,
                "HouseBankAccount": "USAC1" if house_bank else "",
            }
        ]
    full_rows = [receipt_inventory, receipt_grir, invoice_grir]
    if include_vendor_row:
        full_rows.insert(2, vendor)
    return {
        "purchase_order": [{"PurchaseOrder": "4500000041", "CompanyCode": "1710"}],
        "purchase_order_items": [
            {
                "PurchaseOrder": "4500000041",
                "PurchaseOrderItem": "10",
                "Material": "TG10",
                "Plant": "1710",
                "OrderQuantity": "1",
                "PurchaseOrderQuantityUnit": "PC",
            }
        ],
        "material_documents": [{"GoodsMovementType": "101"}],
        "supplier_invoice_items": [{"SupplierInvoice": "5100000025"}],
        "accounting_items": [receipt_inventory, receipt_grir, invoice_grir],
        "full_accounting_documents": full_rows,
        "clearing_documents": clearing_rows,
    }


def _evaluate_p2p(
    steps: dict[str, list[dict[str, object]]], *, complete: bool = True
) -> dict[str, object]:
    return rules.evaluate_p2p_status(
        {
            "run_input": {"purchase_order": "4500000041"},
            "sap_read": _response(steps, complete=complete),
            "rule_config": {
                "goods_receipt_document_types": ["WE"],
                "supplier_invoice_document_types": ["RE"],
                "payment_document_types": ["KZ", "ZP"],
                "gr_ir_amount_tolerance": "0.01",
            },
        }
    )


def test_p2p_expands_full_documents_and_separates_grir_clearing_and_payment() -> None:
    result = _evaluate_p2p(_p2p_steps())

    assert result["rule_id"] == "p2p_deterministic_status_v2"
    assert result["counts"]["po_linked_accounting_items"] == 3
    assert result["counts"]["accounting_documents"] == 2
    assert result["counts"]["full_accounting_items"] == 4
    assert result["stages"]["gr_ir_match"]["state"] == "confirmed"
    assert result["stages"]["ap_clearing"]["state"] == "confirmed"
    assert result["stages"]["payment_document"]["state"] == "confirmed"
    assert result["stages"]["bank_settlement"]["state"] == "not_assessed"
    assert result["business_status"] == "complete"
    assert result["status"] == "complete"
    assert result["evidence_complete"] is True
    assert "银行实际扣款未单独核验" in result["business_report"]["headline"]["zh"]

    tables = {table["id"]: table for table in result["business_report"]["evidence_tables"]}
    invoice_document = next(
        row
        for row in tables["accounting_documents"]["rows"]
        if row["accounting_document"] == "5100000025"
    )
    assert invoice_document["po_linked_items"] == 1
    assert invoice_document["full_items"] == 2
    assert tables["gr_ir_matching"]["rows"][0]["net_amount"] == "0.00"
    clearing = tables["clearing_and_payment"]["rows"][0]
    assert clearing["invoice_item"] == "1"
    assert clearing["clearing_document"] == "2000000006"
    assert clearing["clearing_date"] == "2018-03-13"
    assert clearing["payment_document_type"] == "ZP"


def test_p2p_never_treats_a_nonpayment_clearing_document_as_payment() -> None:
    result = _evaluate_p2p(_p2p_steps(payment_type="AB"))

    assert result["stages"]["ap_clearing"]["state"] == "confirmed"
    assert result["stages"]["payment_document"]["state"] == "not_confirmed"
    assert result["business_status"] == "partial"
    assert result["status"] == "inconclusive"
    payment_stage = next(
        stage
        for stage in result["business_report"]["stages"]
        if stage["id"] == "payment_document"
    )
    assert "清账编号本身不等同于付款" in payment_stage["detail"]["zh"]


def test_p2p_payment_requires_method_and_house_bank_information() -> None:
    missing_method = _evaluate_p2p(_p2p_steps(payment_method=""))
    missing_bank = _evaluate_p2p(_p2p_steps(house_bank=""))

    assert missing_method["stages"]["ap_clearing"]["state"] == "confirmed"
    assert missing_method["stages"]["payment_document"]["state"] == "not_confirmed"
    assert missing_bank["stages"]["payment_document"]["state"] == "not_confirmed"
    assert missing_method["counts"]["payment_documents"] == 0
    assert missing_bank["counts"]["payment_documents"] == 0


def test_p2p_grir_uses_signed_amounts_instead_of_iscleared() -> None:
    result = _evaluate_p2p(_p2p_steps(invoice_amount="30.00"))

    assert result["stages"]["gr_ir_match"]["state"] == "not_confirmed"
    assert result["counts"]["gr_ir_open_groups"] == 1
    table = next(
        table for table in result["business_report"]["evidence_tables"] if table["id"] == "gr_ir_matching"
    )
    assert table["rows"][0]["net_amount"] == "-5.00"


def test_p2p_grir_missing_amount_or_common_account_is_unknown() -> None:
    missing_amount_steps = _p2p_steps()
    missing_amount_steps["accounting_items"][2]["AmountInCompanyCodeCurrency"] = None
    missing_amount = _evaluate_p2p(missing_amount_steps)

    different_account_steps = _p2p_steps()
    different_account_steps["accounting_items"][2]["GLAccount"] = "21129999"
    different_account = _evaluate_p2p(different_account_steps)

    assert missing_amount["stages"]["gr_ir_match"]["state"] == "unknown"
    assert missing_amount["evidence_complete"] is False
    assert different_account["stages"]["gr_ir_match"]["state"] == "unknown"


def test_p2p_missing_full_vendor_row_is_unknown_not_uncleared() -> None:
    result = _evaluate_p2p(_p2p_steps(include_vendor_row=False))

    assert result["stages"]["ap_clearing"]["state"] == "unknown"
    assert result["evidence_complete"] is False
    assert result["status"] == "inconclusive"


def test_p2p_uncleared_supplier_item_does_not_confirm_payment() -> None:
    result = _evaluate_p2p(_p2p_steps(vendor_cleared=False))

    assert result["stages"]["ap_clearing"]["state"] == "not_confirmed"
    assert result["stages"]["payment_document"]["state"] == "not_confirmed"
    assert result["counts"]["cleared_supplier_items"] == 0
    assert result["counts"]["payment_documents"] == 0


def test_p2p_multiple_invoices_with_one_uncleared_is_partial() -> None:
    steps = _p2p_steps()
    second_receipt = _fi_row(
        "5000000030", "2", "WE", account_type="S", gl_account="21120000", amount="-35.00"
    )
    second_invoice = _fi_row(
        "5100000026", "2", "RE", account_type="S", gl_account="21120000", amount="35.00"
    )
    second_vendor = _fi_row(
        "5100000026",
        "1",
        "RE",
        account_type="K",
        gl_account="21100000",
        amount="-35.00",
        purchase_order="",
        purchase_order_item="",
    )
    steps["accounting_items"].extend([second_receipt, second_invoice])
    steps["full_accounting_documents"].extend(
        [
            _fi_row(
                "5000000030", "1", "WE", account_type="S", gl_account="51100000", amount="35.00"
            ),
            second_receipt,
            second_vendor,
            second_invoice,
        ]
    )
    steps["supplier_invoice_items"].append({"SupplierInvoice": "5100000026"})

    result = _evaluate_p2p(steps)

    assert result["stages"]["gr_ir_match"]["state"] == "confirmed"
    assert result["stages"]["ap_clearing"]["state"] == "partial"
    assert result["stages"]["payment_document"]["state"] == "partial"
    assert result["business_status"] == "partial"


def test_p2p_mixed_currency_grir_is_unknown() -> None:
    result = _evaluate_p2p(_p2p_steps(invoice_currency="EUR"))

    assert result["stages"]["gr_ir_match"]["state"] == "unknown"
    assert result["counts"]["gr_ir_matched_groups"] == 0


def test_p2p_incomplete_source_cannot_return_complete() -> None:
    result = _evaluate_p2p(_p2p_steps(), complete=False)

    assert result["source_complete"] is False
    assert result["evidence_complete"] is False
    assert result["stages"]["gr_ir_match"]["state"] == "unknown"
    assert result["status"] == "inconclusive"


def test_p2p_default_presentation_renders_business_evidence_tables_bilingually() -> None:
    rule_result = _evaluate_p2p(_p2p_steps())
    presentation = _default_presentation(
        RunResult(
            run_id="run-fixture",
            mode=RunMode.agent,
            agent_id="procure-to-pay-status",
            rule_results=[rule_result],
            completeness=Completeness(
                source_complete=True,
                business_complete=True,
                reason="fixture",
            ),
            summary=rule_result["summary"],
        )
    )

    table_titles = {
        block.title.zh: block.title.en
        for block in presentation.blocks
        if block.type == "table" and block.title is not None
    }
    assert table_titles["完整财务凭证"] == "Full accounting documents"
    assert table_titles["GR/IR收货与发票匹配"] == "GR/IR receipt and invoice matching"
    assert table_titles["供应商清账与SAP付款凭证"] == "Supplier clearing and SAP payment documents"
    grir_table = next(
        block for block in presentation.blocks if block.title and block.title.zh == "GR/IR收货与发票匹配"
    )
    assert grir_table.rows[0].values[-1].zh == "已匹配"
    assert grir_table.rows[0].values[-1].en == "Matched"
    metrics = next(
        block for block in presentation.blocks if block.type == "metrics"
    )
    open_groups = next(metric for metric in metrics.metrics if metric.id == "gr_ir_open_groups")
    assert open_groups.value.zh == "0"
    assert open_groups.value.en == "0"
    next_actions = next(
        block for block in presentation.blocks if block.title and block.title.zh == "建议下一步"
    )
    assert next_actions.items[0].zh == "如需审计，可下载业务报告和阶段明细留档。"
    assert next_actions.items[0].en == "For audit purposes, download the business report and stage details."

    report = _business_markdown_report(
        RunResult(
            run_id="run-fixture",
            mode=RunMode.agent,
            agent_id="procure-to-pay-status",
            rule_results=[rule_result],
            completeness=Completeness(
                source_complete=True,
                business_complete=True,
                reason="本次严格只读查询范围完整。",
            ),
            summary=rule_result["summary"],
        ),
        rule_result["business_report"],
    )
    assert "## 完整财务凭证" in report
    assert "| 5100000025 |" in report
    assert "| 10 | 21120000 | -35.00 | 35.00 | 0.00 | USD | 已匹配 |" in report


def test_p2p_business_report_explains_receipt_without_invoice_in_business_language() -> None:
    result = rules.evaluate_p2p_status(
        {
            "sap_read": _response(
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
            "sap_read": _response(
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
