from __future__ import annotations

import json
import re
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent
from sap_business_agents_platform.engine import _default_presentation
from sap_business_agents_platform.models import Completeness, RunMode, RunResult


STEP_IDS = (
    "production_order",
    "production_order_items",
    "production_statuses",
    "production_operations",
    "production_components",
    "material_documents",
)


def _step(rows: list[dict[str, object]], *, complete: bool = True) -> dict[str, object]:
    return {
        "ok": complete,
        "source_complete": complete,
        "source_truncated": False,
        "results": rows,
    }


def _inputs(*, incomplete_step: str | None = None, include_header: bool = True) -> dict[str, object]:
    rows: dict[str, list[dict[str, object]]] = {
        "production_order": ([{
            "ManufacturingOrder": "1001820",
            "ManufacturingOrderType": "YBM1",
            "Material": "SG21",
            "ProductionPlant": "1710",
            "MfgOrderPlannedStartDate": "2022-11-24",
            "MfgOrderPlannedEndDate": "2022-11-30",
            "TotalQuantity": "10",
            "MfgOrderConfirmedYieldQty": "0",
        }] if include_header else []),
        "production_order_items": ([{
            "ManufacturingOrder": "1001820",
            "ManufacturingOrderItem": "1",
            "Material": "SG21",
            "ProductionPlant": "1710",
            "MfgOrderItemPlannedTotalQty": "10",
            "MfgOrderItemGoodsReceiptQty": "0",
            "MfgOrderItemActualDeviationQty": "0",
        }] if include_header else []),
        "production_statuses": ([
            {"ManufacturingOrder": "1001820", "StatusCode": "I0001", "StatusShortName": "CRTD", "StatusName": "已创建"},
            {"ManufacturingOrder": "1001820", "StatusCode": "I0016", "StatusShortName": "PRC", "StatusName": "预成本核算"},
            {"ManufacturingOrder": "1001820", "StatusCode": "I0028", "StatusShortName": "SETC", "StatusName": "结算规则已创建"},
            {"ManufacturingOrder": "1001820", "StatusCode": "I0340", "StatusShortName": "MACM", "StatusName": "已承诺物料"},
        ] if include_header else []),
        "production_operations": ([{
            "ManufacturingOrder": "1001820",
            "ManufacturingOrderOperation": "0010",
            "WorkCenter": "WINDING",
            "OperationIsConfirmed": "",
            "OperationIsPartiallyConfirmed": "",
            "OpPlannedTotalQuantity": "10",
            "OpTotalConfirmedYieldQty": "0",
            "OpErlstSchedldExecStrtDte": "2022-11-28",
            "OpErlstSchedldExecEndDte": "2022-11-28",
        }] if include_header else []),
        "production_components": ([{
            "ManufacturingOrder": "1001820",
            "Reservation": "10866",
            "ReservationItem": "1",
            "Material": "RM12",
            "Plant": "1710",
            "RequiredQuantity": "10",
            "WithdrawnQuantity": "0",
            "ConfirmedAvailableQuantity": "10",
        }] if include_header else []),
        "material_documents": [],
    }
    steps = {
        step_id: _step(rows[step_id], complete=step_id != incomplete_step)
        for step_id in STEP_IDS
    }
    complete = incomplete_step is None
    return {
        "agent_id": "production-order-monitoring",
        "run_input": {"manufacturing_order": "1001820"},
        "known_gaps": [],
        "evidence": {
            "collect_production_execution": {
                "ok": complete,
                "source_complete": complete,
                "step_results": steps,
            }
        },
    }


def _evaluate(**kwargs: object) -> dict[str, object]:
    return evaluate_business_agent(_inputs(**kwargs))


def test_monitor_projects_complete_run_into_reusable_business_output() -> None:
    result = _evaluate()
    output = result["workflow_output"]

    assert result["rule_id"] == "production_order_monitoring_deterministic_v2"
    assert result["business_status"] == "attention"
    assert result["source_complete"] is True
    assert result["evidence_complete"] is True
    assert output["order_header"] == {
        "manufacturing_order": "1001820",
        "order_type": "YBM1",
        "material": "SG21",
        "plant": "1710",
        "planned_start": "2022-11-24",
        "planned_end": "2022-11-30",
        "planned_quantity": "10",
        "confirmed_yield_quantity": "0",
    }
    assert [item["status_short_name"] for item in output["status_details"]] == [
        "CRTD", "PRC", "SETC", "MACM"
    ]
    assert output["release_observation"] == "rel_not_observed"
    assert output["operation_count"] == 1
    assert output["unconfirmed_operation_count"] == 1
    assert output["operation_details"][0]["confirmation_status"] == "not_confirmed"
    assert output["component_count"] == 1
    assert output["pending_withdrawal_component_count"] == 1
    assert output["component_details"][0]["withdrawal_status"] == "pending"
    assert output["material_movement_item_count"] == 0
    assert output["movement_details"] == []

    report = result["business_report"]
    assert report["display_records"] is False
    assert report["stages_after_summary"] is True
    assert all(
        re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}\.csv", table["artifact_name"])
        for table in report["action_tables"]
    )
    movement_stage = next(item for item in report["stages"] if item["id"] == "movements")
    assert movement_stage["state"] == "complete_empty"
    assert movement_stage["state_label"]["zh"] == "查询完整，无记录"
    assert "material_documents_source_incomplete" not in result["missing_evidence"]
    component_finding = next(
        item for item in report["findings"] if item["code"] == "component_withdrawal_pending"
    )
    assert "不等同于缺料判断" in component_finding["detail"]["zh"]


def test_monitor_renders_business_summary_before_detailed_tables() -> None:
    result = _evaluate()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "agents" / "PP" / "production-order-monitoring" / "agent.json").read_text(
            encoding="utf-8"
        )
    )
    presentation = _default_presentation(
        RunResult(
            run_id="run-production-monitor-fixture",
            mode=RunMode.agent,
            agent_id="production-order-monitoring",
            rule_results=[result],
            completeness=Completeness(
                source_complete=True,
                business_complete=True,
                reason="fixture",
            ),
            summary=result["summary"],
        ),
        output_schema=manifest["execution"]["outputSchema"],
    )

    block_titles = [block.title.zh if block.title else "" for block in presentation.blocks]
    assert block_titles[:4] == ["", "关键业务指标", "订单概览", "各阶段结果"]
    assert "工序执行明细" in block_titles
    assert "组件领料与可用量" in block_titles
    movement = next(block for block in presentation.blocks if block.title and block.title.zh == "物料移动")
    assert movement.type == "notice"
    assert movement.tone == "success"
    assert movement.text.zh == "查询完整，未发现该订单的物料移动行项目。"
    assert not any(block.title and block.title.zh == "业务记录" for block in presentation.blocks)

    metrics = next(block for block in presentation.blocks if block.type == "metrics")
    labels = {metric.id: metric.label.zh for metric in metrics.metrics}
    assert labels["unconfirmed_operation_count"] == "未完全确认工序"
    assert labels["material_movement_item_count"] == "物料移动行项目"
    overview = next(block for block in presentation.blocks if block.title and block.title.zh == "订单概览")
    values = {entry.label.zh: entry.value.zh for entry in overview.entries}
    assert values["计划数量"] == "10"
    assert values["确认产量"] == "0"


def test_monitor_complete_empty_order_is_not_found_without_missing_evidence() -> None:
    result = _evaluate(include_header=False)

    assert result["business_status"] == "not_found"
    assert result["source_complete"] is True
    assert result["evidence_complete"] is True
    assert result["missing_evidence"] == []
    assert result["workflow_output"]["order_header"] is None
    assert result["workflow_output"]["operation_details"] == []


def test_monitor_incomplete_material_document_source_fails_closed() -> None:
    result = _evaluate(incomplete_step="material_documents")

    assert result["business_status"] == "capability_blocked"
    assert result["status"] == "inconclusive"
    assert result["source_complete"] is False
    assert result["evidence_complete"] is False
    assert "material_documents_source_incomplete" in result["missing_evidence"]
    movement_stage = next(
        item for item in result["business_report"]["stages"] if item["id"] == "movements"
    )
    assert movement_stage["state"] == "unknown"
    assert "未发现该订单" not in movement_stage["detail"]["zh"]


def test_monitor_missing_quantities_remain_unknown_not_zero_or_shortage() -> None:
    inputs = _inputs()
    component = inputs["evidence"]["collect_production_execution"]["step_results"][
        "production_components"
    ]["results"][0]
    component["RequiredQuantity"] = None
    component["WithdrawnQuantity"] = None
    component["ConfirmedAvailableQuantity"] = None

    result = evaluate_business_agent(inputs)
    detail = result["workflow_output"]["component_details"][0]
    assert detail["required_quantity"] is None
    assert detail["withdrawn_quantity"] is None
    assert detail["confirmed_available_quantity"] is None
    assert detail["withdrawal_status"] == "unknown"
    assert result["workflow_output"]["pending_withdrawal_component_count"] == 0


def test_monitor_manifest_exposes_live_accepted_v2_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "agents" / "PP" / "production-order-monitoring" / "agent.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["version"] == "0.2.0"
    assert manifest["validation"]["verdict"] == "PASS"
    assert manifest["validation"]["executable"] is True
    assert manifest["validation"]["freeQueryComparison"] == "MATCH"
    assert manifest["validation"]["fixedAgentComparison"] == "MATCH"
    required = set(manifest["execution"]["outputSchema"]["required"])
    assert {
        "order_header",
        "order_items",
        "status_details",
        "release_observation",
        "operation_details",
        "component_details",
        "movement_details",
        "evidence_complete",
    } <= required
