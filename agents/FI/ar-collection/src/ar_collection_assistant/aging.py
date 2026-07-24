from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import OpenItem


AGING_BUCKETS = ("current", "days_1_30", "days_31_60", "days_61_90", "days_over_90")


def days_overdue(item: OpenItem, as_of: date) -> int:
    return max((as_of - item.due_date).days, 0)


def aging_bucket(item: OpenItem, as_of: date) -> str:
    overdue = days_overdue(item, as_of)
    if overdue == 0:
        return "current"
    if overdue <= 30:
        return "days_1_30"
    if overdue <= 60:
        return "days_31_60"
    if overdue <= 90:
        return "days_61_90"
    return "days_over_90"


def build_aging(items: list[OpenItem], as_of: date) -> dict[str, object]:
    bucket_amounts = {bucket: Decimal("0") for bucket in AGING_BUCKETS}
    aged_items: list[dict[str, object]] = []

    for item in sorted(items, key=lambda candidate: (candidate.due_date, candidate.document_id)):
        bucket = aging_bucket(item, as_of)
        overdue = days_overdue(item, as_of)
        bucket_amounts[bucket] += item.amount
        aged_items.append(
            {
                "document_id": item.document_id,
                "invoice_id": item.invoice_id,
                "posting_date": item.posting_date,
                "due_date": item.due_date,
                "days_overdue": overdue,
                "aging_bucket": bucket,
                "amount": item.amount,
                "currency": item.currency,
                "reference": item.reference,
                "dispute_status": item.dispute_status,
            }
        )

    overdue_total = sum(
        (item.amount for item in items if days_overdue(item, as_of) > 0),
        Decimal("0"),
    )
    return {
        "as_of": as_of,
        "open_total": sum((item.amount for item in items), Decimal("0")),
        "overdue_total": overdue_total,
        "bucket_amounts": bucket_amounts,
        "items": aged_items,
    }

