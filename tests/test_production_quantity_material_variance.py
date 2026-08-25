from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.agent_rules import evaluate_business_agent


ROOT = Path(__file__).resolve().parents[1]


def _evidence(step_id: str, *rows: dict[str, object], complete: bool = True) -> dict[str, object]:
    return {
        "ok": True,
        "status": "completed" if complete else "inconclusive",
        "source_complete": complete,
        "source_truncated": not complete,
        "validation_issues": [],
        "step_results": {
            step_id: {
                "results": list(rows),
                "source_complete": complete,
            }
        },
    }


def _order_1001233(*, teco: bool = True, complete: bool = True) -> dict[str, object]:
    order = "1001233"
    return {
        "agent_id": "production-variance-analysis",
        "run_input": {"manufacturing_order": order},
        "evidence": {
            "production_order_header": _evidence(
                "production_order_header",
                {
                    "ManufacturingOrder": order,
                    "Material": "EWMS4-50",
                    "ProductionPlant": "1710",
                    "ProductionUnit": "PC",
                    "OrderIsTechnicallyCompleted": "X" if teco else "",
                    "OrderIsConfirmed": "X",
                },
                complete=complete,
            ),
            "production_order_items": _evidence(
                "production_order_items",
                {
                    "ManufacturingOrder": order,
                    "ManufacturingOrderItem": "0001",
                    "Material": "EWMS4-50",
                    "ProductionPlant": "1710",
                    "ProductionUnit": "PC",
                    "MfgOrderItemPlannedTotalQty": "7",
                    "MfgOrderItemGoodsReceiptQty": "6",
                },
            ),
            "production_operations": _evidence(
                "production_operations",
                {
                    "ManufacturingOrder": order,
                    "ManufacturingOrderOperation": "0010",
                    "WorkCenter": "ASSEMBLY",
                    "OpPlannedTotalQuantity": "7",
                    "OpTotalConfirmedYieldQty": "7",
                    "OperationUnit": "PC",
                    "OperationIsConfirmed": "X",
                    "OperationIsPartiallyConfirmed": "",
                },
            ),
            "production_components": _evidence(
                "production_components",
                *[
                    {
                        "ManufacturingOrder": order,
                        "Reservation": "1",
                        "ReservationItem": f"{index:04d}",
                        "Material": material,
                        "RequiredQuantity": required,
                        "WithdrawnQuantity": required,
                        "BaseUnit": "PC",
                    }
                    for index, (material, required) in enumerate(
                        (("COMP-1", "28"), ("COMP-2", "56"), ("COMP-3", "224")),
                        1,
                    )
                ],
            ),
            "material_documents": _evidence(
                "material_documents",
                *[
                    {
                        "ManufacturingOrder": order,
                        "MaterialDocumentYear": "2026",
                        "MaterialDocument": f"50000000{index}",
                        "MaterialDocumentItem": "0001",
                        "Material": material,
                        "GoodsMovementType": "261",
                        "QuantityInBaseUnit": quantity,
                        "MaterialBaseUnit": "PC",
                        "DebitCreditCode": "H",
                    }
                    for index, (material, quantity) in enumerate(
                        (
                            ("COMP-1", "14"),
                            ("COMP-1", "14"),
                            ("COMP-2", "56"),
                            ("COMP-3", "100"),
                            ("COMP-3", "124"),
                        ),
                        1,
                    )
                ],
                {
                    "ManufacturingOrder": order,
                    "MaterialDocumentYear": "2026",
                    "MaterialDocument": "5000000099",
                    "MaterialDocumentItem": "0001",
                    "Material": "EWMS4-50",
                    "GoodsMovementType": "101",
                    "QuantityInBaseUnit": "6",
                    "MaterialBaseUnit": "PC",
                    "DebitCreditCode": "S",
                },
            ),
        },
    }


def test_teco_receipt_shortfall_is_explained_without_claiming_production_shortfall() -> None:
    result = evaluate_business_agent(_order_1001233())
    output = result["workflow_output"]

    assert output["teco_status"] == "confirmed"
    assert output["planned_quantity"] == "7"
    assert output["confirmed_yield_quantity"] == "7"
    assert output["goods_receipt_quantity"] == "6"
    assert output["receipt_variance_quantity"] == "-1"
    assert output["quantity_status"] == "short_receipt"
    assert output["component_status"] == "matched"
    assert output["movement_status"] == "documented"
    assert output["component_variance_count"] == 0
    assert output["reversal_count"] == 0
    assert output["cost_status"] == "not_assessed"
    assert output["business_status"] == "attention"
    assert output["source_complete"] is True
    assert output["evidence_complete"] is True
    assert [item["code"] for item in output["root_cause_candidates"]] == [
        "receipt_shortfall_after_confirmation"
    ]
    assert "生产已确认 7 PC，but" not in result["business_report"]["headline"]["zh"]
    assert result["business_report"]["headline"]["zh"] == "生产已确认 7 PC，但库存只收到 6 PC"


def test_non_teco_variance_remains_in_progress() -> None:
    result = evaluate_business_agent(_order_1001233(teco=False))
    assert result["workflow_output"]["business_status"] == "in_progress"
    assert result["workflow_output"]["quantity_status"] == "short_receipt"


def test_only_final_operation_yield_is_used() -> None:
    payload = _order_1001233()
    payload["evidence"]["production_operations"] = _evidence(
        "production_operations",
        {
            "ManufacturingOrder": "1001233",
            "ManufacturingOrderOperation": "0010",
            "OpTotalConfirmedYieldQty": "7",
            "OperationUnit": "PC",
            "OperationIsConfirmed": "X",
        },
        {
            "ManufacturingOrder": "1001233",
            "ManufacturingOrderOperation": "0020",
            "OpTotalConfirmedYieldQty": "7",
            "OperationUnit": "PC",
            "OperationIsConfirmed": "X",
        },
    )
    result = evaluate_business_agent(payload)
    assert result["workflow_output"]["confirmed_yield_quantity"] == "7"


def test_reversals_are_net_reconciled_and_reported() -> None:
    payload = _order_1001233()
    rows = payload["evidence"]["material_documents"]["step_results"]["material_documents"]["results"]
    rows.append(
        {
            "ManufacturingOrder": "1001233",
            "MaterialDocumentYear": "2026",
            "MaterialDocument": "5000000100",
            "MaterialDocumentItem": "0001",
            "Material": "EWMS4-50",
            "GoodsMovementType": "101",
            "QuantityInBaseUnit": "1",
            "MaterialBaseUnit": "PC",
            "DebitCreditCode": "S",
        }
    )
    rows.append(
        {
            "ManufacturingOrder": "1001233",
            "MaterialDocumentYear": "2026",
            "MaterialDocument": "5000000101",
            "MaterialDocumentItem": "0001",
            "Material": "EWMS4-50",
            "GoodsMovementType": "102",
            "QuantityInBaseUnit": "1",
            "MaterialBaseUnit": "PC",
            "DebitCreditCode": "H",
            "ReversedMaterialDocument": "5000000100",
            "ReversedMaterialDocumentYear": "2026",
            "ReversedMaterialDocumentItem": "0001",
        }
    )

    result = evaluate_business_agent(payload)
    assert result["workflow_output"]["reversal_count"] == 1
    assert result["workflow_output"]["movement_status"] == "reversal_present"
    assert "material_movement_reversal_effect" in {
        item["code"] for item in result["workflow_output"]["root_cause_candidates"]
    }


def test_incomplete_source_or_conflicting_units_is_inconclusive() -> None:
    incomplete = evaluate_business_agent(_order_1001233(complete=False))
    assert incomplete["workflow_output"]["business_status"] == "inconclusive"
    assert incomplete["workflow_output"]["source_complete"] is False
    assert incomplete["workflow_output"]["evidence_complete"] is False

    conflicting = _order_1001233()
    operation = conflicting["evidence"]["production_operations"]["step_results"]["production_operations"]["results"][0]
    operation["OperationUnit"] = "KG"
    result = evaluate_business_agent(conflicting)
    assert result["workflow_output"]["business_status"] == "inconclusive"
    assert "production_operation_unit_conflict" in result["missing_evidence"]


def test_manifest_is_quantity_material_only_and_passed_live_acceptance() -> None:
    manifest = json.loads(
        (ROOT / "agents/PP/production-variance-analysis/agent.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.2.0"
    assert manifest["validation"]["verdict"] == "PASS"
    assert manifest["validation"]["executable"] is True
    assert manifest["validation"]["blockingLimitations"] == []
    assert manifest["execution"]["steps"][-1]["operation"] == "evaluate_business_agent"
    assert evaluate_business_agent(_order_1001233())["rule_id"] == "production_quantity_material_variance_v2"
    assert all("cost_rows" not in json.dumps(step) for step in manifest["execution"]["steps"])
    assert "production_cost_evidence" not in json.dumps(manifest)
    assert "production_cost_relationship" not in json.dumps(manifest)
