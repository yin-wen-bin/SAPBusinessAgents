from __future__ import annotations

from copy import deepcopy
import csv
from pathlib import Path
from types import SimpleNamespace

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import RunCoordinator, _business_markdown_report, _default_presentation
from sap_business_agents_platform.models import Completeness, RunMode, RunResult


def _embedded(*rows: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "status": "completed",
        "source_complete": True,
        "source_truncated": False,
        "data": {
            "results": list(rows),
            "source_complete": True,
            "source_truncated": False,
        },
    }


def _step(*rows: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "results": list(rows),
        "source_complete": complete,
        "source_truncated": not complete,
    }


def _base_payload() -> dict[str, object]:
    return {
        "agent_id": "material-shortage-procurement-response",
        "run_input": {
            "material": "MAT-001",
            "plant": "1710",
            "purchasing_organization": "1710",
            "as_of": "2026-08-25",
        },
        "evidence": {
            "mrp_master": _embedded(
                {
                    "Material": "MAT-001",
                    "MRPPlant": "1710",
                    "MaterialProcurementCategory": "F",
                    "BaseUnit": "EA",
                }
            ),
            "mrp": _embedded(
                {
                    "Material": "MAT-001",
                    "MRPPlant": "1710",
                    "MRPArea": "1710",
                    "MaterialShortageProfile": "SAP000000001",
                    "MaterialShortageProfileCount": "001",
                    "MRPPlanningSegmentType": "02",
                    "MRPPlanningSegmentNumber": "",
                    "MaterialShortageQuantity": "5",
                    "MaterialBaseUnit": "EA",
                }
            ),
            "pr": _embedded(),
            "po_schedule": {
                "ok": True,
                "source_complete": True,
                "source_truncated": False,
                "step_results": {
                    "schedule_po_items": _step(),
                    "po_headers": _step(),
                    "po_schedules": _step(),
                    "po_receipts": _step(),
                    "receipt_headers": _step(),
                },
            },
            "source": _embedded(),
        },
        "fallbacks": {},
        "known_gaps": [],
    }


def _pr(
    number: str,
    status: str,
    *,
    requested: str = "10",
    ordered: str = "0",
    assigned: bool = False,
    fixed_supplier: str = "",
    **extra: object,
) -> dict[str, object]:
    return {
        "PurchaseRequisition": number,
        "PurchaseRequisitionItem": "10",
        "Material": "MAT-001",
        "Plant": "1710",
        "PurchaseRequisitionItemText": f"Item {number}",
        "RequestedQuantity": requested,
        "OrderedQuantity": ordered,
        "BaseUnit": "EA",
        "DeliveryDate": "2026-08-20",
        "ProcessingStatus": "N",
        "PurReqnReleaseStatus": status,
        "SourceOfSupplyIsAssigned": assigned,
        "FixedSupplier": fixed_supplier,
        "IsClosed": False,
        "IsDeleted": False,
        **extra,
    }


def _metrics(result: dict[str, object]) -> dict[str, object]:
    return {item["id"]: item["value"] for item in result["metrics"]}


def _tables(result: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        table["id"]: table
        for table in result["business_report"]["action_tables"]
    }


def test_pr_actions_follow_release_source_and_remaining_quantity_semantics() -> None:
    payload = _base_payload()
    payload["evidence"]["pr"] = _embedded(
        _pr("10000001", "01"),
        _pr("10000002", "02", ReleaseIsNotCompleted=False),
        _pr("10000003", "03"),
        _pr("10000004", "04"),
        _pr("10000005", "05", assigned=True, fixed_supplier="17300001"),
        _pr("10000006", "08"),
        _pr("10000007", "05", ReleaseIsNotCompleted=True),
        _pr("10000008", "05", requested="10", ordered="4", assigned=True),
        _pr("10000009", "05", requested="10", ordered="10", assigned=True),
        _pr("10000010", "05", IsDeleted=True),
        _pr("10000011", "05", IsClosed=True),
        _pr("10000012", "05", ProcessingStatus="B"),
    )

    result = evaluate_business_agent(payload)
    rows = _tables(result)["pr_actions"]["rows"]
    actions = {
        row["purchase_requisition"]: row["action"]["zh"]
        for row in rows
    }

    assert actions == {
        "10000001": "完善采购申请",
        "10000002": "分配货源并处理 PR",
        "10000003": "完成审批",
        "10000004": "完成审批",
        "10000005": "转换为采购订单",
        "10000006": "处理拒绝并重新提交",
        "10000007": "人工复核状态",
        "10000008": "转换为采购订单",
    }
    assert next(
        row for row in rows if row["purchase_requisition"] == "10000008"
    )["remaining_quantity"] == "6"
    assert all("action_id" not in row for row in rows)
    metrics = _metrics(result)
    assert metrics["pr_action_total"] == 8
    assert metrics["pr_awaiting_release"] == 2
    assert metrics["pr_ready_to_convert"] == 2
    assert metrics["pr_source_or_processing_required"] == 2
    assert metrics["pending_pr"] == metrics["pr_action_total"]


def test_po_expediting_uses_net_receipts_at_cutoff_and_allocates_by_schedule() -> None:
    payload = _base_payload()
    payload["evidence"]["po_schedule"]["step_results"] = {
        "schedule_po_items": _step(
            {
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "Material": "MAT-001",
                "Plant": "1710",
                "PurchaseOrderQuantityUnit": "EA",
                "PurchaseOrderItemText": "External component",
                "PurchaseRequisition": "10000001",
                "PurchaseRequisitionItem": "10",
            }
        ),
        "po_headers": _step(
            {
                "PurchaseOrder": "4500000001",
                "Supplier": "17300001",
                "PurchasingOrganization": "1710",
            }
        ),
        "po_schedules": _step(
            {
                "PurchasingDocument": "4500000001",
                "PurchasingDocumentItem": "10",
                "ScheduleLine": "1",
                "ScheduleLineDeliveryDate": "2026-08-01",
                "ScheduleLineOrderQuantity": "6",
                "ScheduleLineCommittedQuantity": "99",
                "PurchaseOrderQuantityUnit": "EA",
            },
            {
                "PurchasingDocument": "4500000001",
                "PurchasingDocumentItem": "10",
                "ScheduleLine": "2",
                "ScheduleLineDeliveryDate": "2026-08-02",
                "ScheduleLineOrderQuantity": "4",
                "ScheduleLineCommittedQuantity": "99",
                "PurchaseOrderQuantityUnit": "EA",
            },
        ),
        "po_receipts": _step(
            {
                "MaterialDocumentYear": "2026",
                "MaterialDocument": "5000000001",
                "MaterialDocumentItem": "1",
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "QuantityInEntryUnit": "7",
                "EntryUnit": "EA",
                "DebitCreditCode": "S",
            },
            {
                "MaterialDocumentYear": "2026",
                "MaterialDocument": "5000000002",
                "MaterialDocumentItem": "1",
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "QuantityInEntryUnit": "2",
                "EntryUnit": "EA",
                "DebitCreditCode": "H",
            },
            {
                "MaterialDocumentYear": "2026",
                "MaterialDocument": "5000000003",
                "MaterialDocumentItem": "1",
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "QuantityInEntryUnit": "20",
                "EntryUnit": "EA",
                "DebitCreditCode": "S",
            },
        ),
        "receipt_headers": _step(
            {"MaterialDocumentYear": "2026", "MaterialDocument": "5000000001", "PostingDate": "2026-08-10"},
            {"MaterialDocumentYear": "2026", "MaterialDocument": "5000000002", "PostingDate": "2026-08-11"},
            {"MaterialDocumentYear": "2026", "MaterialDocument": "5000000003", "PostingDate": "2026-09-01"},
        ),
    }

    result = evaluate_business_agent(payload)
    rows = _tables(result)["po_expedite_actions"]["rows"]

    assert [(row["schedule_line"], row["received_quantity"], row["open_quantity"]) for row in rows] == [
        ("1", "5", "1"),
        ("2", "0", "4"),
    ]
    assert [row["committed_quantity"] for row in rows] == ["99", "99"]
    assert _metrics(result)["po_schedule_lines_to_expedite"] == 2
    assert _metrics(result)["expedite_po"] == 2


def test_po_unit_conflict_or_incomplete_receipt_source_suppresses_count() -> None:
    unit_conflict = _base_payload()
    unit_conflict["evidence"]["po_schedule"]["step_results"] = {
        "schedule_po_items": _step(
            {
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "Material": "MAT-001",
                "Plant": "1710",
                "PurchaseOrderQuantityUnit": "EA",
            }
        ),
        "po_headers": _step({"PurchaseOrder": "4500000001", "Supplier": "17300001"}),
        "po_schedules": _step(
            {
                "PurchasingDocument": "4500000001",
                "PurchasingDocumentItem": "10",
                "ScheduleLine": "1",
                "ScheduleLineDeliveryDate": "2026-08-01",
                "ScheduleLineOrderQuantity": "10",
                "PurchaseOrderQuantityUnit": "EA",
            }
        ),
        "po_receipts": _step(
            {
                "MaterialDocumentYear": "2026",
                "MaterialDocument": "5000000001",
                "MaterialDocumentItem": "1",
                "PurchaseOrder": "4500000001",
                "PurchaseOrderItem": "10",
                "QuantityInEntryUnit": "2",
                "EntryUnit": "KG",
                "DebitCreditCode": "S",
            }
        ),
        "receipt_headers": _step(
            {"MaterialDocumentYear": "2026", "MaterialDocument": "5000000001", "PostingDate": "2026-08-10"}
        ),
    }

    result = evaluate_business_agent(unit_conflict)
    assert _metrics(result)["po_schedule_lines_to_expedite"] is None
    assert "po_receipt_unit_conflict" in result["missing_evidence"]
    assert _tables(result)["po_expedite_actions"]["source_complete"] is False

    incomplete = deepcopy(unit_conflict)
    incomplete["evidence"]["po_schedule"]["step_results"]["po_receipts"] = _step(
        complete=False
    )
    result = evaluate_business_agent(incomplete)
    assert _metrics(result)["po_schedule_lines_to_expedite"] is None
    assert "po_receipts_evidence" in result["missing_evidence"]
    assert "po_schedule_evidence" in result["missing_evidence"]


def test_action_tables_are_localized_ordered_and_exported_in_full(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["evidence"]["pr"] = _embedded(
        *(
            _pr(str(10000100 + index), "03")
            for index in range(205)
        )
    )
    rule_result = evaluate_business_agent(payload)
    run = RunResult(
        run_id="run-shortage-actions",
        mode=RunMode.agent,
        agent_id="material-shortage-procurement-response",
        rule_results=[rule_result],
        completeness=Completeness(
            source_complete=True,
            business_complete=True,
            reason="fixture",
        ),
        summary=rule_result["summary"],
    )

    presentation = _default_presentation(run)
    metric_index = next(
        index for index, block in enumerate(presentation.blocks) if block.type == "metrics"
    )
    pr_table_index = next(
        index
        for index, block in enumerate(presentation.blocks)
        if block.title and block.title.zh == "采购申请待办"
    )
    coverage_index = next(
        index
        for index, block in enumerate(presentation.blocks)
        if block.title and block.title.zh == "业务记录"
    )
    pr_table = presentation.blocks[pr_table_index]

    assert metric_index < pr_table_index < coverage_index
    assert pr_table.columns[0].label.zh == "建议动作"
    assert pr_table.columns[0].label.en == "Recommended action"
    assert pr_table.rows[0].values[0].zh == "完成审批"
    assert len(pr_table.rows) == 200
    assert pr_table.total_rows == 205
    assert pr_table.source_complete is True

    markdown = _business_markdown_report(run, rule_result["business_report"])
    assert "## 采购申请待办" in markdown
    assert "页面报告展示前 200 条" in markdown
    assert "`pr-actions.csv`" in markdown

    coordinator = object.__new__(RunCoordinator)
    coordinator.settings = SimpleNamespace(data_root=tmp_path)
    artifacts = coordinator._write_artifacts(run)
    artifact_names = {item["name"] for item in artifacts}
    assert {
        "pr-actions.csv",
        "po-expedite-actions.csv",
        "source-candidates.csv",
    } <= artifact_names
    with (tmp_path / "artifacts" / run.run_id / "pr-actions.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        csv_rows = list(csv.reader(handle))
    assert csv_rows[0][0] == "建议动作"
    assert csv_rows[1][0] == "完成审批"
    assert len(csv_rows) == 206
