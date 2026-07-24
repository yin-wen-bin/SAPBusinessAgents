from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from procure_to_pay_status.analyzer import P2PAnalysisError
from procure_to_pay_status.assistant import P2PStatusAssistant
from procure_to_pay_status.fixture import DEFAULT_FIXTURE, FixtureP2PDataSource
from procure_to_pay_status.formatting import render_markdown
from procure_to_pay_status.model import ItemStatus, QueryParameters


class VerticalSliceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = P2PStatusAssistant(FixtureP2PDataSource(DEFAULT_FIXTURE))

    def test_demo_po_covers_main_business_states(self) -> None:
        report = self.assistant.ask(
            "PO 4500001234 是否已经收货、发票校验和付款？",
            as_of=date(2026, 7, 22),
        )
        states = {item.item_number: item.status for item in report.items}
        self.assertEqual(states["00010"], ItemStatus.NOT_RECEIVED)
        self.assertEqual(states["00020"], ItemStatus.PARTIALLY_RECEIVED)
        self.assertEqual(states["00030"], ItemStatus.RECEIVED_NOT_INVOICED)
        self.assertEqual(states["00040"], ItemStatus.INVOICED_NOT_PAID)
        self.assertEqual(states["00050"], ItemStatus.PAID)
        self.assertEqual(states["00060"], ItemStatus.PARTIALLY_PAID)

    def test_blocked_overdue_invoice_explains_both_reasons(self) -> None:
        report = self.assistant.query(
            QueryParameters("4500001234", "00040"),
            as_of=date(2026, 7, 22),
        )
        item = report.items[0]
        codes = {finding.code for finding in item.findings}
        self.assertEqual(item.open_amount, Decimal("1000"))
        self.assertIn("INVOICE_BLOCKED", codes)
        self.assertIn("PAYMENT_BLOCK", codes)
        self.assertIn("PAYMENT_OVERDUE", codes)

    def test_payment_and_partial_payment_follow_fi_references(self) -> None:
        paid = self.assistant.query(
            QueryParameters("4500001234", "00050"), as_of=date(2026, 7, 22)
        ).items[0]
        partial = self.assistant.query(
            QueryParameters("4500001234", "00060"), as_of=date(2026, 7, 22)
        ).items[0]
        self.assertEqual(paid.documents.clearing_documents, ("2000000050/2026",))
        self.assertEqual(paid.paid_amount, Decimal("1000"))
        self.assertEqual(partial.paid_amount, Decimal("400.0"))
        self.assertEqual(partial.open_amount, Decimal("600.0"))

    def test_item_filter_and_markdown_line_output(self) -> None:
        report = self.assistant.ask("采购订单 4500001234 行项目 30", as_of=date(2026, 7, 22))
        rendered = render_markdown(report)
        self.assertEqual(len(report.items), 1)
        self.assertIn("| 00030 |", rendered)
        self.assertIn("已收货未发票", rendered)

    def test_unknown_item_has_clear_error(self) -> None:
        with self.assertRaisesRegex(P2PAnalysisError, "不存在项目 00999"):
            self.assistant.query(QueryParameters("4500001234", "00999"), as_of=date(2026, 7, 22))

    def test_json_contract_uses_strings_for_financial_values(self) -> None:
        report = self.assistant.query(
            QueryParameters("4500001234", "00050"), as_of=date(2026, 7, 22)
        )
        payload = report.to_dict()
        self.assertEqual(payload["as_of"], "2026-07-22")
        self.assertEqual(payload["items"][0]["status"], "paid")
        self.assertEqual(payload["items"][0]["invoiced_amount"], "1000")
        self.assertEqual(payload["items"][0]["status_label"], "已付款")


if __name__ == "__main__":
    unittest.main()

