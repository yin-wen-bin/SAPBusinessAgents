from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import (
    ARSnapshot,
    BankReceipt,
    CustomerAccount,
    DisputeStatus,
    OpenItem,
    PaymentHistory,
)


class FixtureARGateway:
    """Local, replaceable implementation of the read-only SAP boundary."""

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        self.fixture_path = Path(fixture_path) if fixture_path else Path(__file__).parent / "fixtures" / "demo_ar.json"

    def load_snapshot(self, as_of: date) -> ARSnapshot:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        accounts = tuple(
            CustomerAccount(
                customer_id=row["customer_id"],
                name=row["name"],
                company_code=row["company_code"],
                currency=row["currency"],
                credit_limit=Decimal(row["credit_limit"]),
                current_exposure=Decimal(row["current_exposure"]),
                dunning_block=bool(row.get("dunning_block", False)),
                preferred_language=row.get("preferred_language", "zh-CN"),
            )
            for row in payload["accounts"]
        )
        open_items = tuple(
            OpenItem(
                document_id=row["document_id"],
                customer_id=row["customer_id"],
                company_code=row["company_code"],
                invoice_id=row["invoice_id"],
                posting_date=date.fromisoformat(row["posting_date"]),
                due_date=date.fromisoformat(row["due_date"]),
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                reference=row.get("reference", ""),
                document_type=row.get("document_type", "DR"),
                dispute_status=DisputeStatus(row.get("dispute_status", "none")),
            )
            for row in payload["open_items"]
            if date.fromisoformat(row["posting_date"]) <= as_of
        )
        payment_history = tuple(
            PaymentHistory(
                customer_id=row["customer_id"],
                average_days_late=Decimal(row["average_days_late"]),
                on_time_rate=Decimal(row["on_time_rate"]),
                broken_promises_12m=int(row.get("broken_promises_12m", 0)),
            )
            for row in payload["payment_history"]
        )
        receipts = tuple(
            BankReceipt(
                receipt_id=row["receipt_id"],
                value_date=date.fromisoformat(row["value_date"]),
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                payer_name=row["payer_name"],
                reference=row["reference"],
                bank_account=row["bank_account"],
            )
            for row in payload["unmatched_receipts"]
            if date.fromisoformat(row["value_date"]) <= as_of
        )
        extracted_at = datetime.fromisoformat(payload["extracted_at"]).astimezone(timezone.utc)
        return ARSnapshot(
            accounts=accounts,
            open_items=open_items,
            payment_history=payment_history,
            unmatched_receipts=receipts,
            source_system=payload.get("source_system", "fixture"),
            extracted_at=extracted_at,
        )

