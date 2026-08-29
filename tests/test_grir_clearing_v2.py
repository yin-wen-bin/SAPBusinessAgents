from __future__ import annotations

from copy import deepcopy
import csv
from types import SimpleNamespace

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import (
    RunCoordinator,
    _business_markdown_report,
    _default_presentation,
)
from sap_business_agents_platform.models import Completeness, RunMode, RunResult


def _step(*rows: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "results": list(rows),
        "source_complete": complete,
        "source_truncated": not complete,
    }


def _payload() -> dict[str, object]:
    candidate_rows = [
        {
            "CompanyCode": "1710",
            "FiscalYear": "2020",
            "AccountingDocument": "5100000001",
            "AccountingDocumentItem": "1",
            "PurchasingDocument": "4500000001",
            "PurchasingDocumentItem": "10",
            "PostingDate": "2020-01-02",
            "AmountInCompanyCodeCurrency": "100",
            "CompanyCodeCurrency": "CNY",
            "DebitCreditCode": "S",
        },
        {
            "CompanyCode": "1710",
            "FiscalYear": "2020",
            "AccountingDocument": "5100000002",
            "AccountingDocumentItem": "1",
            "PurchasingDocument": "4500000002",
            "PurchasingDocumentItem": "10",
            "PostingDate": "2020-01-02",
            "AmountInCompanyCodeCurrency": "110",
            "CompanyCodeCurrency": "CNY",
            "DebitCreditCode": "S",
        },
    ]
    history_rows = [
        *candidate_rows,
        {
            "CompanyCode": "1710",
            "FiscalYear": "2020",
            "AccountingDocument": "5000000001",
            "AccountingDocumentItem": "1",
            "PurchasingDocument": "4500000001",
            "PurchasingDocumentItem": "10",
            "PostingDate": "2020-01-03",
            "AmountInCompanyCodeCurrency": "100",
            "CompanyCodeCurrency": "CNY",
            "DebitCreditCode": "H",
        },
        {
            "CompanyCode": "1710",
            "FiscalYear": "2020",
            "AccountingDocument": "5000000002",
            "AccountingDocumentItem": "1",
            "PurchasingDocument": "4500000002",
            "PurchasingDocumentItem": "10",
            "PostingDate": "2020-01-03",
            "AmountInCompanyCodeCurrency": "100",
            "CompanyCodeCurrency": "CNY",
            "DebitCreditCode": "H",
        },
    ]
    return {
        "agent_id": "gr-ir-clearing",
        "run_input": {
            "company_code": "1710",
            "gl_account": "21120000",
            "date_from": "2020-01-01",
            "date_to": "2020-12-31",
        },
        "evidence": {
            "collect_grir_evidence": {
                "source_complete": True,
                "step_results": {
                    "gl_items": _step(*candidate_rows),
                    "grir_gl_history": _step(*history_rows),
                    "purchase_order_items": _step(
                        {
                            "PurchaseOrder": "4500000001",
                            "PurchaseOrderItem": "10",
                            "Material": "MAT1",
                            "Plant": "1710",
                            "PurchaseOrderQuantityUnit": "PC",
                        },
                        {
                            "PurchaseOrder": "4500000002",
                            "PurchaseOrderItem": "10",
                            "Material": "MAT2",
                            "Plant": "1710",
                            "PurchaseOrderQuantityUnit": "PC",
                        },
                    ),
                    "material_documents": _step(
                        {
                            "MaterialDocumentYear": "2020",
                            "MaterialDocument": "5000000001",
                            "MaterialDocumentItem": "1",
                            "PurchaseOrder": "4500000001",
                            "PurchaseOrderItem": "10",
                            "QuantityInEntryUnit": "10",
                            "EntryUnit": "PC",
                            "DebitCreditCode": "S",
                            "GoodsMovementType": "101",
                        },
                        {
                            "MaterialDocumentYear": "2020",
                            "MaterialDocument": "5000000002",
                            "MaterialDocumentItem": "1",
                            "PurchaseOrder": "4500000002",
                            "PurchaseOrderItem": "10",
                            "QuantityInEntryUnit": "10",
                            "EntryUnit": "PC",
                            "DebitCreditCode": "S",
                            "GoodsMovementType": "101",
                        },
                    ),
                    "material_document_headers": _step(
                        {
                            "MaterialDocumentYear": "2020",
                            "MaterialDocument": "5000000001",
                            "PostingDate": "2020-01-02",
                        },
                        {
                            "MaterialDocumentYear": "2020",
                            "MaterialDocument": "5000000002",
                            "PostingDate": "2020-01-02",
                        },
                    ),
                    "supplier_invoice_items": _step(
                        {
                            "SupplierInvoice": "5100000001",
                            "FiscalYear": "2020",
                            "SupplierInvoiceItem": "1",
                            "PurchaseOrder": "4500000001",
                            "PurchaseOrderItem": "10",
                            "QuantityInPurchaseOrderUnit": "10",
                            "PurchaseOrderQuantityUnit": "PC",
                            "SupplierInvoiceItemAmount": "100",
                        },
                        {
                            "SupplierInvoice": "5100000002",
                            "FiscalYear": "2020",
                            "SupplierInvoiceItem": "1",
                            "PurchaseOrder": "4500000002",
                            "PurchaseOrderItem": "10",
                            "QuantityInPurchaseOrderUnit": "10",
                            "PurchaseOrderQuantityUnit": "PC",
                            "SupplierInvoiceItemAmount": "100",
                        },
                    ),
                    "supplier_invoice_headers": _step(
                        {
                            "SupplierInvoice": "5100000001",
                            "FiscalYear": "2020",
                            "PostingDate": "2020-01-03",
                            "SupplierInvoiceIsCreditMemo": False,
                        },
                        {
                            "SupplierInvoice": "5100000002",
                            "FiscalYear": "2020",
                            "PostingDate": "2020-01-03",
                            "SupplierInvoiceIsCreditMemo": False,
                        },
                    ),
                },
            }
        },
        "known_gaps": [],
    }


def _tables(result: dict[str, object]) -> dict[str, dict[str, object]]:
    report = result["business_report"]
    assert isinstance(report, dict)
    return {table["id"]: table for table in report["action_tables"]}


def test_equal_quantities_do_not_force_attention_but_open_value_does() -> None:
    result = evaluate_business_agent(_payload())

    assert result["rule_id"] == "gr_ir_clearing_deterministic_v2"
    assert result["business_status"] == "attention"
    assert result["source_complete"] is True
    assert result["business_complete"] is True
    assert result["workflow_output"]["examined_item_count"] == 2
    assert result["workflow_output"]["matched_item_count"] == 1
    assert result["workflow_output"]["follow_up_item_count"] == 1
    assert result["workflow_output"]["unknown_item_count"] == 0

    follow_up = _tables(result)["confirmed_follow_up"]
    assert [row["purchase_order"] for row in follow_up["rows"]] == ["4500000002"]
    assert follow_up["rows"][0]["receipt_quantity"] == "10"
    assert follow_up["rows"][0]["invoice_quantity"] == "10"
    assert follow_up["rows"][0]["gr_ir_open_amount"] == "10"
    assert follow_up["rows"][0]["primary_reason"] == "price_difference"

    all_rows = _tables(result)["all_reconciliation_records"]
    assert all_rows["display"] is False
    matched = next(row for row in all_rows["rows"] if row["purchase_order"] == "4500000001")
    assert matched["reconciliation_status"] == "matched"
    assert matched["business_status"] == "normal"


def test_incomplete_source_preserves_confirmed_follow_up_and_marks_other_items_unknown() -> None:
    payload = _payload()
    evidence = payload["evidence"]["collect_grir_evidence"]
    evidence["source_complete"] = False
    evidence["step_results"]["grir_gl_history"]["source_complete"] = False
    evidence["step_results"]["grir_gl_history"]["source_truncated"] = True

    result = evaluate_business_agent(payload)

    assert result["business_status"] == "inconclusive"
    assert result["source_complete"] is False
    assert result["workflow_output"]["follow_up_item_count"] == 1
    assert result["workflow_output"]["unknown_item_count"] == 1
    assert _tables(result)["confirmed_follow_up"]["rows"][0]["purchase_order"] == "4500000002"
    assert _tables(result)["needs_confirmation"]["rows"][0]["purchase_order"] == "4500000001"


def test_missing_material_header_is_an_evidence_gap_not_a_false_match() -> None:
    payload = deepcopy(_payload())
    evidence = payload["evidence"]["collect_grir_evidence"]
    evidence["step_results"]["material_document_headers"] = _step()

    result = evaluate_business_agent(payload)

    assert result["business_status"] == "inconclusive"
    first = next(
        row
        for row in _tables(result)["needs_confirmation"]["rows"]
        if row["purchase_order"] == "4500000001"
    )
    assert "material_document_posting_date_missing" in first["evidence_gaps"]


def test_frontend_shows_only_action_lists_but_exports_the_full_reconciliation(
    tmp_path,
) -> None:
    rule_result = evaluate_business_agent(_payload())
    run = RunResult(
        run_id="run-grir-v2",
        mode=RunMode.agent,
        agent_id="gr-ir-clearing",
        rule_results=[rule_result],
        completeness=Completeness(
            source_complete=True,
            business_complete=True,
            reason="fixture",
        ),
        summary=rule_result["summary"],
    )

    presentation = _default_presentation(run)
    table_titles = {
        block.title.en
        for block in presentation.blocks
        if block.title is not None and block.type == "table"
    }
    assert "Confirmed follow-up" in table_titles
    assert "All reconciliation records" not in table_titles
    follow_up_block = next(
        block
        for block in presentation.blocks
        if block.title is not None and block.title.en == "Confirmed follow-up"
    )
    reason_index = next(
        index for index, column in enumerate(follow_up_block.columns) if column.key == "primary_reason"
    )
    severity_index = next(
        index for index, column in enumerate(follow_up_block.columns) if column.key == "severity"
    )
    assert follow_up_block.rows[0].values[reason_index].zh == "数量一致但金额未平"
    assert follow_up_block.rows[0].values[severity_index].zh in {"高", "中", "低"}

    markdown = _business_markdown_report(run, rule_result["business_report"])
    assert "gr-ir-all-records.csv" not in markdown

    coordinator = object.__new__(RunCoordinator)
    coordinator.settings = SimpleNamespace(data_root=tmp_path)
    artifacts = coordinator._write_artifacts(run)
    artifact_names = {item["name"] for item in artifacts}
    assert {
        "gr-ir-follow-up.csv",
        "gr-ir-needs-confirmation.csv",
        "gr-ir-all-records.csv",
    } <= artifact_names
    with (tmp_path / "artifacts" / run.run_id / "gr-ir-all-records.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 3
