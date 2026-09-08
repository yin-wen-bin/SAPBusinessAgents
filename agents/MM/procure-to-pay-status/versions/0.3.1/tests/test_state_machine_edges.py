from __future__ import annotations

from datetime import date
import unittest

from procure_to_pay_status.analyzer import P2PAnalyzer
from procure_to_pay_status.model import ItemStatus, P2PTables, QueryParameters


HEADER = {"EBELN": "4500009999", "BUKRS": "1000", "LIFNR": "V1", "WAERS": "CNY"}
ITEM = {
    "EBELN": "4500009999",
    "EBELP": "00010",
    "MATNR": "M1",
    "TXZ01": "Test",
    "WERKS": "P1",
    "MENGE": "10",
    "MEINS": "EA",
}


class StateMachineEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = P2PAnalyzer()
        self.query = QueryParameters("4500009999")

    def test_ekbe_fallback_nets_reversal(self) -> None:
        tables = P2PTables(
            ekko=(HEADER,),
            ekpo=(ITEM,),
            ekbe=(
                {"EBELN": "4500009999", "EBELP": "00010", "BEWTP": "E", "BELNR": "5001", "GJAHR": "2026", "MENGE": "10", "SHKZG": "S"},
                {"EBELN": "4500009999", "EBELP": "00010", "BEWTP": "E", "BELNR": "5002", "GJAHR": "2026", "MENGE": "2", "SHKZG": "H"},
            ),
        )
        item = self.analyzer.analyze(tables, self.query, as_of=date(2026, 7, 22)).items[0]
        self.assertEqual(item.received_quantity, 8)
        self.assertEqual(item.status, ItemStatus.PARTIALLY_RECEIVED)
        self.assertIn("GR_FROM_EKBE", {finding.code for finding in item.findings})

    def test_parked_invoice_is_not_counted_as_posted(self) -> None:
        tables = P2PTables(
            ekko=(HEADER,),
            ekpo=(ITEM,),
            mseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "MBLNR": "5001", "MJAHR": "2026", "MENGE": "10", "SHKZG": "S"},
            ),
            mkpf=({"MBLNR": "5001", "MJAHR": "2026"},),
            rseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "BELNR": "5101", "GJAHR": "2026", "MENGE": "10", "WRBTR": "100", "SHKZG": "S"},
            ),
            rbkp=({"BELNR": "5101", "GJAHR": "2026", "RBSTAT": "1"},),
        )
        item = self.analyzer.analyze(tables, self.query, as_of=date(2026, 7, 22)).items[0]
        self.assertEqual(item.status, ItemStatus.RECEIVED_NOT_INVOICED)
        self.assertEqual(item.invoiced_quantity, 0)
        self.assertIn("INVOICE_NOT_POSTED", {finding.code for finding in item.findings})

    def test_partial_invoice_is_a_distinct_state(self) -> None:
        tables = P2PTables(
            ekko=(HEADER,),
            ekpo=(ITEM,),
            mkpf=({"MBLNR": "5001", "MJAHR": "2026"},),
            mseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "MBLNR": "5001", "MJAHR": "2026", "MENGE": "10", "SHKZG": "S"},
            ),
            rseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "BELNR": "5101", "GJAHR": "2026", "MENGE": "5", "WRBTR": "50", "SHKZG": "S"},
            ),
            rbkp=({"BELNR": "5101", "GJAHR": "2026", "RBSTAT": "5"},),
        )
        item = self.analyzer.analyze(tables, self.query, as_of=date(2026, 7, 22)).items[0]
        self.assertEqual(item.status, ItemStatus.PARTIALLY_INVOICED)
        self.assertEqual(item.invoiced_quantity, 5)

    def test_baseline_date_is_not_treated_as_net_due_date(self) -> None:
        tables = P2PTables(
            ekko=(HEADER,),
            ekpo=(ITEM,),
            mkpf=({"MBLNR": "5001", "MJAHR": "2026"},),
            mseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "MBLNR": "5001", "MJAHR": "2026", "MENGE": "10", "SHKZG": "S"},
            ),
            rseg=(
                {"EBELN": "4500009999", "EBELP": "00010", "BELNR": "5101", "GJAHR": "2026", "MENGE": "10", "WRBTR": "100", "SHKZG": "S"},
            ),
            rbkp=({"BELNR": "5101", "GJAHR": "2026", "RBSTAT": "5"},),
            bkpf=({"BUKRS": "1000", "BELNR": "1901", "GJAHR": "2026", "AWTYP": "RMRP", "AWKEY": "51012026"},),
            bseg=({"BUKRS": "1000", "BELNR": "1901", "GJAHR": "2026", "BUZEI": "001", "KOART": "K", "WRBTR": "100", "SHKZG": "H", "ZFBDT": "20260101"},),
        )
        item = self.analyzer.analyze(tables, self.query, as_of=date(2026, 7, 22)).items[0]
        self.assertNotIn("PAYMENT_OVERDUE", {finding.code for finding in item.findings})


if __name__ == "__main__":
    unittest.main()
