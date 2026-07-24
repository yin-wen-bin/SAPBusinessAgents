from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from ar_collection_assistant import ARCollectionAssistant, FixtureARGateway
from ar_collection_assistant.models import QueryIntent, to_primitive


class ServiceEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assistant = ARCollectionAssistant(FixtureARGateway())
        self.as_of = date(2026, 7, 22)

    def test_weekly_collection_query_runs_full_slice(self) -> None:
        result = self.assistant.query("列出本周需要催收的客户", self.as_of)
        self.assertEqual(result["intent"], QueryIntent.LIST_WEEKLY_COLLECTIONS)
        self.assertEqual(result["summary"]["customer_count"], 3)
        self.assertEqual(
            [entry["customer"].customer_id for entry in result["customers"]],
            ["C2000", "C1000", "C4000"],
        )

        by_customer = {entry["customer"].customer_id: entry for entry in result["customers"]}
        c1000 = by_customer["C1000"]
        self.assertEqual(c1000["risk"].pending_receipt_amount, Decimal("22000.00"))
        self.assertEqual(c1000["risk"].net_collection_amount, Decimal("45000.00"))
        self.assertEqual(c1000["payment_match_suggestions"][0].status.value, "exact")
        self.assertNotIn("INV-2026-1188，", c1000["communication_draft"]["body"])
        self.assertEqual(c1000["communication_draft"]["status"], "review_required")

        c4000 = by_customer["C4000"]
        self.assertEqual(c4000["risk"].priority, "HOLD_REVIEW")
        self.assertEqual(c4000["communication_draft"]["status"], "withheld")
        self.assertIsNone(c4000["communication_draft"]["body"])

        self.assertEqual(result["summary"]["unmatched_receipt_count"], 1)
        self.assertFalse(result["controls"]["payment_clearing_posted"])
        self.assertFalse(result["controls"]["communication_auto_send"])

    def test_unmatched_receipt_query_returns_manual_worklist(self) -> None:
        result = self.assistant.query("列出未匹配银行到账", self.as_of)
        self.assertEqual(result["intent"], QueryIntent.LIST_UNMATCHED_RECEIPTS)
        self.assertEqual(result["customers"], [])
        self.assertEqual(len(result["unmatched_bank_receipts"]), 1)
        receipt = result["unmatched_bank_receipts"][0]
        self.assertEqual(receipt["receipt"].receipt_id, "FEB-20260721-002")
        self.assertEqual(receipt["recommended_action"], "manual_research_in_feban")

    def test_customer_aging_query_includes_current_items(self) -> None:
        result = self.assistant.query("查看 C3000 的未清项目与账龄", self.as_of)
        self.assertEqual(result["intent"], QueryIntent.GET_AGING)
        self.assertEqual(len(result["customers"]), 1)
        customer = result["customers"][0]
        self.assertEqual(customer["aging"]["bucket_amounts"]["current"], Decimal("8000.00"))
        self.assertEqual(customer["communication_draft"]["status"], "not_required")

    def test_output_contract_is_json_safe_and_money_is_string(self) -> None:
        primitive = to_primitive(self.assistant.query("列出本周需要催收的客户", self.as_of))
        self.assertEqual(primitive["schema_version"], "1.0")
        self.assertEqual(primitive["summary"]["totals_by_currency"]["CNY"], "200000.00")
        self.assertEqual(primitive["summary"]["actionable_totals_by_currency"]["CNY"], "80000.00")
        self.assertEqual(primitive["customers"][0]["risk"]["score"], "46.7")


if __name__ == "__main__":
    unittest.main()
