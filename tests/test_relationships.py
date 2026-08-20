from __future__ import annotations

import json
from pathlib import Path

from sap_business_agents_platform.relationships import RelationshipCatalog


ROOT = Path(__file__).resolve().parents[1]


def _catalog() -> RelationshipCatalog:
    return RelationshipCatalog.load(ROOT / "config" / "business-relationships.json")


def _plan(steps: list[dict[str, object]]) -> list[tuple[str, dict[str, object]]]:
    return [
        (
            "o2c",
            {
                "service_name": "API_SALES_ORDER_SRV",
                "odata_version": "2.0",
                "entity_set": "A_SalesOrder",
                "plan_kind": "multi_step",
                "steps": steps,
            },
        )
    ]


def _literal_step(
    step_id: str,
    service: str,
    entity: str,
    field: str,
    value: str = "BUSINESS_KEY_FIXTURE",
) -> dict[str, object]:
    return {
        "step_id": step_id,
        "service_name": service,
        "odata_version": "2.0",
        "entity_set": entity,
        "filters": [
            {"field": field, "operator": "eq", "value": value, "value_type": "string"}
        ],
    }


def test_relationship_catalog_rejects_sales_order_reused_as_internal_order() -> None:
    issues = _catalog().validate_plans(
        _plan(
            [
                _literal_step(
                    "sales_order", "API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder"
                ),
                _literal_step(
                    "fi",
                    "API_OPLACCTGDOCITEMCUBE_SRV",
                    "A_OperationalAcctgDocItemCube",
                    "OrderID",
                ),
            ]
        )
    )
    assert len(issues) == 1
    issue = issues[0]["validation_issues"][0]
    assert issue["code"] == "relationship_literal_semantic_mismatch"
    assert issue["source_semantic"] == "sales_order_id"
    assert issue["target_semantic"] == "internal_order_id"
    assert "BUSINESS_KEY_FIXTURE" not in json.dumps(issues)


def test_relationship_catalog_accepts_o2c_delivery_billing_fi_chain() -> None:
    steps: list[dict[str, object]] = [
        _literal_step(
            "sales_order", "API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder"
        ),
        _literal_step(
            "delivery",
            "API_OUTBOUND_DELIVERY_SRV",
            "A_OutbDeliveryItem",
            "ReferenceSDDocument",
        ),
        {
            "step_id": "billing",
            "service_name": "API_BILLING_DOCUMENT_SRV",
            "odata_version": "2.0",
            "entity_set": "A_BillingDocumentItem",
            "filter_from_previous": [
                {
                    "field": "ReferenceSDDocument",
                    "source_step_id": "delivery",
                    "source_field": "DeliveryDocument",
                }
            ],
        },
        {
            "step_id": "fi",
            "service_name": "API_OPLACCTGDOCITEMCUBE_SRV",
            "odata_version": "2.0",
            "entity_set": "A_OperationalAcctgDocItemCube",
            "filter_from_previous": [
                {
                    "field": "BillingDocument",
                    "source_step_id": "billing",
                    "source_field": "BillingDocument",
                }
            ],
        },
    ]
    assert _catalog().validate_plans(_plan(steps)) == []


def test_relationship_catalog_rejects_unregistered_cross_entity_binding() -> None:
    steps: list[dict[str, object]] = [
        _literal_step(
            "sales_order", "API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder"
        ),
        {
            "step_id": "billing",
            "service_name": "API_BILLING_DOCUMENT_SRV",
            "odata_version": "2.0",
            "entity_set": "A_BillingDocumentItem",
            "filter_from_previous": [
                {
                    "field": "OrderID",
                    "source_step_id": "sales_order",
                    "source_field": "SalesOrder",
                }
            ],
        },
    ]
    issues = _catalog().validate_plans(_plan(steps))
    assert issues[0]["validation_issues"][0]["code"] == "relationship_binding_unapproved"


def test_relationship_catalog_accepts_p2p_business_key_semantics() -> None:
    plans = [
        (
            "p2p",
            {
                "service_name": "API_PURCHASEORDER_PROCESS_SRV",
                "odata_version": "2.0",
                "entity_set": "A_PurchaseOrder",
                "plan_kind": "multi_step",
                "steps": [
                    _literal_step(
                        "purchase_order",
                        "API_PURCHASEORDER_PROCESS_SRV",
                        "A_PurchaseOrder",
                        "PurchaseOrder",
                    ),
                    _literal_step(
                        "goods_receipt",
                        "API_MATERIAL_DOCUMENT_SRV",
                        "A_MaterialDocumentItem",
                        "PurchaseOrder",
                    ),
                    _literal_step(
                        "fi",
                        "API_OPLACCTGDOCITEMCUBE_SRV",
                        "A_OperationalAcctgDocItemCube",
                        "PurchasingDocument",
                    ),
                ],
            },
        )
    ]
    assert _catalog().validate_plans(plans) == []
