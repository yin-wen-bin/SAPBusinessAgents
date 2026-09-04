from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from ar_collection_assistant.aging import aging_bucket, build_aging
from ar_collection_assistant.matching import match_receipts
from ar_collection_assistant.models import (
    BankReceipt,
    CustomerAccount,
    MatchStatus,
    OpenItem,
)


class AgingTests(unittest.TestCase):
    def _item(self, due_date: date, amount: str = "100") -> OpenItem:
        return OpenItem(
            document_id=due_date.isoformat(),
            customer_id="C1",
            company_code="1000",
            invoice_id=f"INV-{due_date.isoformat()}",
            posting_date=date(2026, 1, 1),
            due_date=due_date,
            amount=Decimal(amount),
            currency="CNY",
        )

    def test_aging_boundaries_and_totals(self) -> None:
        as_of = date(2026, 7, 22)
        items = [
            self._item(date(2026, 7, 22)),
            self._item(date(2026, 7, 21)),
            self._item(date(2026, 6, 22)),
            self._item(date(2026, 6, 21)),
            self._item(date(2026, 5, 23)),
            self._item(date(2026, 4, 23)),
            self._item(date(2026, 4, 22)),
        ]

        self.assertEqual(aging_bucket(items[0], as_of), "current")
        self.assertEqual(aging_bucket(items[1], as_of), "days_1_30")
        self.assertEqual(aging_bucket(items[2], as_of), "days_1_30")
        self.assertEqual(aging_bucket(items[3], as_of), "days_31_60")
        self.assertEqual(aging_bucket(items[4], as_of), "days_31_60")
        self.assertEqual(aging_bucket(items[5], as_of), "days_61_90")
        self.assertEqual(aging_bucket(items[6], as_of), "days_over_90")
        result = build_aging(items, as_of)
        self.assertEqual(result["open_total"], Decimal("700"))
        self.assertEqual(result["overdue_total"], Decimal("600"))


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = CustomerAccount(
            customer_id="C1",
            name="示例客户",
            company_code="1000",
            currency="CNY",
            credit_limit=Decimal("10000"),
            current_exposure=Decimal("4000"),
        )
        self.item = OpenItem(
            document_id="1901",
            customer_id="C1",
            company_code="1000",
            invoice_id="INV-1901",
            posting_date=date(2026, 6, 1),
            due_date=date(2026, 7, 1),
            amount=Decimal("1000"),
            currency="CNY",
        )

    def test_exact_reference_and_amount_match(self) -> None:
        receipt = BankReceipt(
            receipt_id="R1",
            value_date=date(2026, 7, 20),
            amount=Decimal("1000"),
            currency="CNY",
            payer_name="示例客户",
            reference="INV 1901",
            bank_account="B1",
        )
        match = match_receipts((receipt,), (self.item,), (self.account,), date(2026, 7, 22))[0]
        self.assertEqual(match.status, MatchStatus.EXACT)
        self.assertEqual(match.candidate_document_id, "1901")
        self.assertGreaterEqual(match.confidence, Decimal("0.95"))

    def test_unknown_receipt_stays_unmatched(self) -> None:
        receipt = BankReceipt(
            receipt_id="R2",
            value_date=date(2026, 7, 20),
            amount=Decimal("777"),
            currency="CNY",
            payer_name="未知付款人",
            reference="UNKNOWN",
            bank_account="B1",
        )
        match = match_receipts((receipt,), (self.item,), (self.account,), date(2026, 7, 22))[0]
        self.assertEqual(match.status, MatchStatus.UNMATCHED)
        self.assertIsNone(match.candidate_document_id)


if __name__ == "__main__":
    unittest.main()
