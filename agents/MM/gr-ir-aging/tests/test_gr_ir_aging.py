from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from gr_ir_aging.analyzer import GrIrAnalyzer
from gr_ir_aging.evidence import EvidenceSnapshot, EvidenceValidationError
from gr_ir_aging.model import QueryParameters, ResultStatus


FIXTURE = Path(__file__).parents[1] / "src" / "gr_ir_aging" / "fixtures" / "gr_ir_demo.json"


class GrIrAgingTests(unittest.TestCase):
    def test_calculates_residual_age_and_bucket(self) -> None:
        result = GrIrAnalyzer().analyze(
            EvidenceSnapshot.load(FIXTURE),
            QueryParameters("1000", date(2026, 8, 1)),
        )
        self.assertEqual(result.status, ResultStatus.COMPLETE)
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(str(item.residual_amount), "200")
        self.assertEqual(item.ageing_days, 83)
        self.assertEqual(item.ageing_bucket, "61-90")
        self.assertEqual(str(result.totals[0].amount), "200")
        self.assertIn("FI:1000/2026/1900000001/1", item.source_documents)

    def test_filters_by_company_po_plant_account_and_key_date(self) -> None:
        result = GrIrAnalyzer().analyze(
            EvidenceSnapshot.load(FIXTURE),
            QueryParameters("1000", date(2026, 8, 1), ("9999999999",), ("1000",), (), 0),
        )
        self.assertEqual(result.items, ())

    def test_rejects_incomplete_pagination(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["completeness"]["source_complete"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(EvidenceValidationError, "pagination"):
                EvidenceSnapshot.load(path)

    def test_rejects_duplicate_business_key(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["entities"]["A_OperationalAcctgDocItemCube"].append(
            dict(payload["entities"]["A_OperationalAcctgDocItemCube"][0])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(EvidenceValidationError, "Duplicate"):
                EvidenceSnapshot.load(path)

    def test_mixed_company_currency_is_inconclusive(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["entities"]["A_OperationalAcctgDocItemCube"][1]["CompanyCodeCurrency"] = "USD"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = GrIrAnalyzer().analyze(
                EvidenceSnapshot.load(path), QueryParameters("1000", date(2026, 8, 1))
            )
        self.assertEqual(result.status, ResultStatus.INCONCLUSIVE)
        self.assertEqual(result.items, ())
        self.assertEqual(result.findings[0].code, "MIXED_COMPANY_CURRENCY")

    def test_service_item_allows_missing_quantity_unit(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for row in payload["entities"]["A_OperationalAcctgDocItemCube"]:
            row["IsServicePurchaseOrder"] = True
            row["PurchaseOrderQuantityUnit"] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = GrIrAnalyzer().analyze(
                EvidenceSnapshot.load(path), QueryParameters("1000", date(2026, 8, 1))
            )
        self.assertTrue(result.items[0].service_item)

    def test_reversal_uses_accounting_debit_credit_sign_once(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        reversal = dict(payload["entities"]["A_OperationalAcctgDocItemCube"][0])
        reversal["AccountingDocument"] = "1900000002"
        reversal["DebitCreditCode"] = "H"
        reversal["IsReversal"] = True
        reversal["AmountInCompanyCodeCurrency"] = "100"
        payload["entities"]["A_OperationalAcctgDocItemCube"].append(reversal)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = GrIrAnalyzer().analyze(
                EvidenceSnapshot.load(path), QueryParameters("1000", date(2026, 8, 1))
            )
        self.assertEqual(str(result.items[0].residual_amount), "100")


if __name__ == "__main__":
    unittest.main()
