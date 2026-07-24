from __future__ import annotations

import unittest

from procure_to_pay_status.extractor import ParameterExtractionError, extract_query_parameters


class ParameterExtractorTests(unittest.TestCase):
    def test_extracts_chinese_po_and_item(self) -> None:
        result = extract_query_parameters("请检查采购订单 4500001234 项目 20 的付款状态")
        self.assertEqual(result.po_number, "4500001234")
        self.assertEqual(result.item_number, "00020")

    def test_extracts_english_form(self) -> None:
        result = extract_query_parameters("Is PO #4500001234 item 00050 paid?")
        self.assertEqual(result.po_number, "4500001234")
        self.assertEqual(result.item_number, "00050")

    def test_rejects_missing_po(self) -> None:
        with self.assertRaisesRegex(ParameterExtractionError, "未识别"):
            extract_query_parameters("这个采购订单付款了吗？")

    def test_rejects_ambiguous_po(self) -> None:
        with self.assertRaisesRegex(ParameterExtractionError, "多个"):
            extract_query_parameters("比较 PO 4500001234 和 PO 4500005678")


if __name__ == "__main__":
    unittest.main()

