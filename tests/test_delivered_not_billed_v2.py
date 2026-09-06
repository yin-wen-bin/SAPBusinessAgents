from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from sap_business_agents_platform.agent_rules import evaluate_business_agent


STEP_IDS = (
    "delivery_headers",
    "delivery_items",
    "billing_items",
    "billing_headers",
    "cancellation_documents_by_reference",
    "cancellation_documents_by_target",
    "source_sales_items",
)


def _delivery_item(item: str, quantity: object = "10", unit: str = "PC") -> dict[str, object]:
    return {
        "DeliveryDocument": "80000001",
        "DeliveryDocumentItem": item,
        "ReferenceSDDocument": "50000001",
        "ReferenceSDDocumentItem": item,
        "ActualDeliveryQuantity": quantity,
        "DeliveryQuantityUnit": unit,
        "GoodsMovementStatus": "C",
    }


def _sales_item(item: str, quantity: object = "10", unit: str = "PC", amount: object = "100", currency: str = "USD") -> dict[str, object]:
    return {
        "SalesOrder": "50000001",
        "SalesOrderItem": item,
        "RequestedQuantity": quantity,
        "RequestedQuantityUnit": unit,
        "NetAmount": amount,
        "TransactionCurrency": currency,
    }


def _billing_item(document: str, item: str, quantity: object, amount: object, *, unit: str = "PC", currency: str = "USD") -> dict[str, object]:
    return {
        "BillingDocument": document,
        "BillingDocumentItem": "10",
        "ReferenceSDDocument": "80000001",
        "ReferenceSDDocumentItem": item,
        "BillingQuantity": quantity,
        "BillingQuantityUnit": unit,
        "NetAmount": amount,
        "TransactionCurrency": currency,
    }


def _billing_header(document: str, billing_date: str = "2026-09-01", **extra: object) -> dict[str, object]:
    return {
        "BillingDocument": document,
        "BillingDocumentDate": billing_date,
        "BillingDocumentIsCancelled": False,
        "CancelledBillingDocument": "",
        **extra,
    }


def _input(
    *,
    delivery_items: list[dict[str, object]],
    billing_items: list[dict[str, object]] | None = None,
    billing_headers: list[dict[str, object]] | None = None,
    sales_items: list[dict[str, object]] | None = None,
    movement_date: str = "2026-09-01",
    date_to: str = "2026-09-06",
    complete: bool = True,
) -> dict[str, object]:
    rows = {
        "delivery_headers": [{
            "DeliveryDocument": "80000001",
            "ActualGoodsMovementDate": movement_date,
            "OverallGoodsMovementStatus": "C",
        }],
        "delivery_items": delivery_items,
        "billing_items": billing_items or [],
        "billing_headers": billing_headers or [],
        "cancellation_documents_by_reference": [],
        "cancellation_documents_by_target": [],
        "source_sales_items": sales_items if sales_items is not None else [
            _sales_item(str(row["DeliveryDocumentItem"])) for row in delivery_items
        ],
    }
    return {
        "agent_id": "delivered-not-billed",
        "run_input": {
            "sales_organization": "1710",
            "date_from": movement_date,
            "date_to": date_to,
        },
        "evidence": {
            "collect_delivered_not_billed": {
                "ok": True,
                "source_complete": complete,
                "data": {
                    "source_complete": complete,
                    "step_results": {
                        step_id: {
                            "source_complete": complete,
                            "source_truncated": not complete,
                            "results": rows[step_id],
                        }
                        for step_id in STEP_IDS
                    },
                },
            }
        },
        "known_gaps": [],
    }


def _records(result: dict[str, object]) -> dict[str, dict[str, object]]:
    report = result["business_report"]
    assert isinstance(report, dict)
    rows = report["records"]
    assert isinstance(rows, list)
    return {str(row["delivery_document_item"]): row for row in rows}


def _metrics(result: dict[str, object]) -> dict[str, object]:
    return {str(metric["id"]): metric["value"] for metric in result["metrics"]}


def test_item_grain_classifies_and_calculates_amounts_without_cross_unit_or_currency_totals() -> None:
    delivery_items = [_delivery_item(item) for item in ("10", "20", "30", "40")]
    billing_items = [
        _billing_item("90000020", "20", "2", "20"),
        _billing_item("90000021", "20", "2", "20"),
        _billing_item("90000030", "30", "10", "100"),
        _billing_item("90000040", "40", "12", "120"),
    ]
    result = evaluate_business_agent(
        _input(
            delivery_items=delivery_items,
            billing_items=billing_items,
            billing_headers=[_billing_header(str(row["BillingDocument"])) for row in billing_items],
        )
    )

    records = _records(result)
    assert records["000010"]["billing_state"] == "unbilled"
    assert records["000010"]["remaining_quantity"] == "10"
    assert records["000010"]["unbilled_net_amount"] == "100"
    assert records["000020"]["billing_state"] == "partially_billed"
    assert records["000020"]["active_billed_quantity"] == "4"
    assert records["000020"]["remaining_quantity"] == "6"
    assert records["000020"]["unbilled_net_amount"] == "60"
    assert records["000030"]["billing_state"] == "fully_billed"
    assert records["000040"]["billing_state"] == "overbilled"
    assert all(row["amount_basis"] == "sales_order_item_net_amount_proration" for row in records.values())
    assert all(row["amount_is_estimate"] is True for row in records.values())
    assert _metrics(result) == {
        "delivered_not_billed": 2,
        "unbilled_items": 1,
        "partially_billed_items": 1,
        "fully_billed_items": 1,
        "overbilled_items": 1,
        "inconclusive_items": 0,
    }
    assert result["business_status"] == "attention"
    assert result["status"] == "complete"


def test_billing_cutoff_and_cancellation_relationship_are_applied_as_of_date() -> None:
    base = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[_billing_item("90000010", "10", "10", "100")],
        billing_headers=[
            _billing_header(
                "90000010",
                BillingDocumentIsCancelled=True,
                CancelledBillingDocument="91000010",
            ),
        ],
    )
    before_cancellation = deepcopy(base)
    before_cancellation["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["cancellation_documents_by_reference"]["results"] = [
        _billing_header("91000010", "2026-09-07")
    ]
    result = evaluate_business_agent(before_cancellation)
    assert _records(result)["000010"]["billing_state"] == "fully_billed"

    after_cancellation = deepcopy(base)
    after_cancellation["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["cancellation_documents_by_reference"]["results"] = [
        _billing_header("91000010", "2026-09-05")
    ]
    result = evaluate_business_agent(after_cancellation)
    assert _records(result)["000010"]["billing_state"] == "unbilled"

    billed_after_cutoff = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[_billing_item("90000011", "10", "10", "100")],
        billing_headers=[_billing_header("90000011", "2026-09-07")],
    )
    result = evaluate_business_agent(billed_after_cutoff)
    assert _records(result)["000010"]["billing_state"] == "unbilled"


@pytest.mark.parametrize(
    ("days", "bucket"),
    [(7, "low"), (8, "medium"), (30, "medium"), (31, "high"), (60, "high"), (61, "critical")],
)
def test_aging_boundaries(days: int, bucket: str) -> None:
    cutoff = date(2026, 9, 6)
    result = evaluate_business_agent(
        _input(
            delivery_items=[_delivery_item("10")],
            movement_date=(cutoff - timedelta(days=days)).isoformat(),
        )
    )
    record = _records(result)["000010"]
    assert record["age_days"] == days
    assert record["aging_bucket"] == bucket


@pytest.mark.parametrize(
    "mutation, expected_gap",
    [
        (lambda payload: payload["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["delivery_items"]["results"][0].update({"ActualDeliveryQuantity": None}), "delivered_quantity_evidence"),
        (lambda payload: payload["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["delivery_items"]["results"][0].update({"ActualDeliveryQuantity": "-1"}), "delivered_quantity_evidence"),
        (lambda payload: payload["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["billing_items"]["results"][0].update({"BillingQuantityUnit": "KG"}), "quantity_unit_mismatch"),
        (lambda payload: payload["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["delivery_headers"]["results"][0].update({"ActualGoodsMovementDate": ""}), "actual_goods_movement_date"),
    ],
)
def test_invalid_quantity_unit_or_pgi_evidence_is_inconclusive(mutation, expected_gap: str) -> None:
    payload = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[_billing_item("90000010", "10", "4", "40")],
        billing_headers=[_billing_header("90000010")],
    )
    mutation(payload)
    result = evaluate_business_agent(payload)
    record = _records(result)["000010"]
    assert record["billing_state"] == "inconclusive"
    assert expected_gap in record["evidence_gaps"]
    assert result["status"] == "inconclusive"


def test_amount_is_null_not_zero_when_currency_or_order_evidence_is_not_comparable() -> None:
    payload = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[_billing_item("90000010", "10", "4", "40", currency="EUR")],
        billing_headers=[_billing_header("90000010")],
    )
    result = evaluate_business_agent(payload)
    record = _records(result)["000010"]
    assert record["billing_state"] == "partially_billed"
    assert record["unbilled_net_amount"] is None
    assert record["active_billed_net_amount"] is None
    assert "unbilled_amount_evidence" in result["missing_evidence"]
    assert result["status"] == "inconclusive"
    assert result["business_status"] == "attention"


def test_duplicate_billing_rows_are_deduplicated_but_conflicting_references_fail_closed() -> None:
    billing = _billing_item("90000010", "10", "4", "40")
    duplicate = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[billing, deepcopy(billing)],
        billing_headers=[_billing_header("90000010")],
    )
    assert _records(evaluate_business_agent(duplicate))["000010"]["active_billed_quantity"] == "4"

    conflict = deepcopy(duplicate)
    conflict_row = deepcopy(billing)
    conflict_row["ReferenceSDDocumentItem"] = "20"
    conflict["evidence"]["collect_delivered_not_billed"]["data"]["step_results"]["billing_items"]["results"] = [billing, conflict_row]
    result = evaluate_business_agent(conflict)
    assert _records(result)["000010"]["billing_state"] == "inconclusive"
    assert "billing_item_reference_conflict" in result["missing_evidence"]


def test_incomplete_paging_never_reports_normal_and_empty_complete_scope_can_be_normal() -> None:
    incomplete = _input(
        delivery_items=[_delivery_item("10")],
        billing_items=[_billing_item("90000010", "10", "10", "100")],
        billing_headers=[_billing_header("90000010")],
        complete=False,
    )
    result = evaluate_business_agent(incomplete)
    assert result["status"] == "inconclusive"
    assert result["business_status"] == "inconclusive"

    empty = _input(delivery_items=[], sales_items=[])
    result = evaluate_business_agent(empty)
    assert result["status"] == "complete"
    assert result["business_status"] == "normal"
    assert result["business_report"]["records"] == []
