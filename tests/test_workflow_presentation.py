from __future__ import annotations

from copy import deepcopy

from sap_business_agents_platform.models import Completeness, RunMode, RunResult
from sap_business_agents_platform.workflow_presentation import (
    compose_workflow_presentation,
    workflow_ap_scopes_csv,
    workflow_markdown_report,
    workflow_orders_csv,
    workflow_presentation_table_page,
)


def _localized(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def _order(
    purchase_order: str = "4500000031",
    *,
    status: str = "complete",
    source_complete: bool = True,
    evidence_complete: bool = True,
) -> dict:
    return {
        "purchase_order": purchase_order,
        "company_code": "1710",
        "supplier": "17300002",
        "business_status": status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "gr_ir_match_status": "confirmed",
        "ap_clearing_status": "confirmed",
        "payment_document_status": "confirmed",
        "bank_settlement_status": "not_assessed",
        "business_report": {
            "headline": _localized("SAP付款流程已确认", "SAP payment flow confirmed"),
            "overview": _localized(
                "工序证据已分别核验。", "The process evidence was checked separately."
            ),
            "stages": [
                {
                    "id": "purchase_order",
                    "label": _localized("采购订单", "Purchase order"),
                    "state": "confirmed",
                    "state_label": _localized("已确认", "Confirmed"),
                    "detail": _localized("订单数量4 PC。", "Order quantity is 4 PC."),
                }
            ],
            "record_columns": [
                {"key": "purchase_order", "label": _localized("采购订单", "Purchase order")},
                {"key": "order_quantity", "label": _localized("订单数量", "Order quantity")},
                {"key": "net_received_quantity", "label": _localized("收货数量", "Received")},
                {"key": "net_invoiced_quantity", "label": _localized("发票数量", "Invoiced")},
                {"key": "unit", "label": _localized("单位", "Unit")},
            ],
            "records": [
                {
                    "purchase_order": purchase_order,
                    "order_quantity": "4",
                    "net_received_quantity": "4",
                    "net_invoiced_quantity": "4",
                    "unit": "PC",
                }
            ],
            "evidence_tables": [
                {
                    "id": "accounting_documents",
                    "title": _localized("完整财务凭证", "Full accounting documents"),
                    "columns": [
                        {
                            "key": "accounting_document",
                            "label": _localized("财务凭证", "Accounting document"),
                        },
                        {"key": "document_type", "label": _localized("类型", "Type")},
                    ],
                    "rows": [
                        {"accounting_document": "5000000025", "document_type": "WE"},
                        {"accounting_document": "5100000023", "document_type": "RE"},
                    ],
                },
                {
                    "id": "gr_ir_matching",
                    "title": _localized("GR/IR匹配", "GR/IR matching"),
                    "columns": [
                        {"key": "net_amount", "label": _localized("净额", "Net amount")},
                        {"key": "currency", "label": _localized("币种", "Currency")},
                        {"key": "status_label", "label": _localized("状态", "Status")},
                    ],
                    "rows": [
                        {
                            "net_amount": "0.00",
                            "currency": "USD",
                            "status_label": _localized("已匹配", "Matched"),
                        }
                    ],
                },
                {
                    "id": "clearing_and_payment",
                    "title": _localized("清账与付款", "Clearing and payment"),
                    "columns": [
                        {"key": "clearing_document", "label": _localized("清账凭证", "Clearing document")},
                        {"key": "payment_document_type", "label": _localized("付款类型", "Payment type")},
                    ],
                    "rows": [
                        {"clearing_document": "1500000009", "payment_document_type": "KZ"}
                    ],
                },
            ],
            "missing_evidence": [],
            "next_actions": [_localized("核对银行流水。", "Review the bank statement.")],
        },
    }


def _scope(*purchase_orders: str) -> dict:
    return {
        "scope_id": "1710:17300002",
        "company_code": "1710",
        "supplier": "17300002",
        "purchase_orders": list(purchase_orders),
        "business_status": "complete",
        "open_item_count": 0,
        "payment_blocked_count": 0,
        "source_complete": True,
        "evidence_complete": True,
        "payment_run_evidence_complete": False,
        "bank_master_evidence_complete": False,
        "bank_settlement_evidence_complete": False,
        "bank_settlement_status": "not_assessed",
    }


def _result(orders: list[dict], scopes: list[dict] | None = None) -> RunResult:
    scopes = scopes if scopes is not None else [_scope(*(item["purchase_order"] for item in orders))]
    ap_output = {
        "scope_results": scopes,
        "business_status": "complete" if scopes else "inconclusive",
        "source_complete": bool(scopes),
        "evidence_complete": bool(scopes),
        "business_report": {
            "overview": _localized("付款准备度已复核。", "Payment readiness was reviewed."),
            "missing_evidence": ["payment_run_and_bank_master_evidence"] if scopes else [],
            "next_actions": [_localized("补充付款运行证据。", "Obtain payment-run evidence.")],
        },
    }
    return RunResult(
        run_id="run_workflow_presentation",
        mode=RunMode.workflow,
        workflow_id="workflow-ec0e3072",
        node_results=[
            {
                "node_id": "trace_procure_to_pay_status",
                "status": "completed",
                "output": {
                    "po_results": orders,
                    "source_complete": all(item["source_complete"] for item in orders),
                    "evidence_complete": all(item["evidence_complete"] for item in orders),
                    "business_report": {"next_actions": []},
                },
            },
            {
                "node_id": "review_payment_readiness",
                "status": "completed" if scopes else "skipped",
                "output": ap_output,
            },
        ],
        completeness=Completeness(
            source_complete=False,
            business_complete=False,
            reason="Specialized evidence is incomplete.",
            missing_evidence=["payment_run_and_bank_master_evidence"] if scopes else [],
        ),
    )


def test_single_order_workflow_presentation_preserves_full_business_chain() -> None:
    view = compose_workflow_presentation(_result([_order()]))

    assert view is not None
    assert view["layout"] == "single"
    assert view["overall_status"] == "inconclusive"
    assert view["source_complete"] is True
    assert view["evidence_complete"] is False
    order = view["order_results"][0]
    assert order["purchase_order"] == "4500000031"
    assert order["records"][0]["order_quantity"] == "4"
    assert order["ap_scope_refs"] == ["1710:17300002"]
    assert [table["id"] for table in order["evidence_tables"]] == [
        "accounting_documents",
        "gr_ir_matching",
        "clearing_and_payment",
    ]
    assert set(view["missing_evidence"]) == {
        "payment_run_evidence_incomplete",
        "bank_master_evidence_incomplete",
        "bank_settlement_not_proven",
    }

    report = workflow_markdown_report(view)
    for expected in (
        "4500000031",
        "5000000025",
        "5100000023",
        "0.00",
        "USD",
        "1500000009",
        "KZ",
    ):
        assert expected in report
    assert "{'zh':" not in report
    assert "4500000031" in workflow_orders_csv(view)
    assert "1710:17300002" in workflow_ap_scopes_csv(view)


def test_multi_order_presentation_sorts_risk_first_and_pages_full_tables() -> None:
    statuses = ["complete", "not_found", "in_progress", "attention", "inconclusive", "blocked"]
    orders = []
    for index in range(50):
        item = deepcopy(_order(f"45{index:08d}", status=statuses[index % len(statuses)]))
        item["business_report"]["evidence_tables"][0]["rows"] = [
            {"accounting_document": f"DOC{row:03d}", "document_type": "WE"}
            for row in range(25)
        ]
        orders.append(item)
    view = compose_workflow_presentation(_result(orders))

    assert view is not None
    assert view["layout"] == "multi"
    assert len(view["order_results"]) == 50
    assert view["order_results"][0]["business_status"] == "blocked"
    table = view["order_results"][0]["evidence_tables"][0]
    assert len(table["rows"]) == 20
    assert table["display_truncated"] is True
    page = workflow_presentation_table_page(
        _result(orders), table["table_id"], offset=20, limit=20
    )
    assert len(page["rows"]) == 5
    assert page["has_next"] is False


def test_skipped_ap_keeps_p2p_facts_but_is_explicitly_inconclusive() -> None:
    view = compose_workflow_presentation(_result([_order()], scopes=[]))

    assert view is not None
    assert view["order_results"][0]["records"][0]["net_received_quantity"] == "4"
    assert view["ap_report"]["executed"] is False
    assert view["ap_scope_results"] == []
    assert view["overall_status"] == "inconclusive"
    assert view["source_complete"] is False
    assert view["evidence_complete"] is False
    assert "ap_payment_scope_unavailable" in view["missing_evidence"]


def test_top_level_p2p_incompleteness_cannot_be_overridden_by_complete_orders() -> None:
    result = _result([_order()])
    result.node_results[0]["output"]["source_complete"] = False
    view = compose_workflow_presentation(result)

    assert view is not None
    assert view["source_complete"] is False
    assert view["overall_status"] == "inconclusive"
