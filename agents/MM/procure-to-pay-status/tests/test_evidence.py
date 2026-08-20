from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from procure_to_pay_status.analyzer import P2PAnalyzer
from procure_to_pay_status.evidence import EvidenceP2PDataSource, EvidenceValidationError
from procure_to_pay_status.model import ItemStatus, QueryParameters


def _payload(*, currency: str = "CNY", complete: bool = True, payment_type: str = "KZ") -> dict:
    return {
        "schema_version": "1.0",
        "metadata": {"run_id": "test-run", "source": "embedded_sap_odata", "as_of": "2026-08-09"},
        "completeness": {"complete": complete},
        "entities": {
            "A_PurchaseOrder": [
                {
                    "PurchaseOrder": "4500007777",
                    "CompanyCode": "1000",
                    "Supplier": "100001",
                    "DocumentCurrency": "CNY",
                    "PurchaseOrderDate": "2026-07-01T00:00:00",
                    "PurchaseOrderType": "NB",
                }
            ],
            "A_PurchaseOrderItem": [
                {
                    "PurchaseOrder": "4500007777",
                    "PurchaseOrderItem": "10",
                    "Material": "MAT1",
                    "PurchaseOrderItemText": "Evidence item",
                    "Plant": "1000",
                    "StorageLocation": "0001",
                    "OrderQuantity": "10",
                    "PurchaseOrderQuantityUnit": "EA",
                    "IsCompletelyDelivered": True,
                }
            ],
            "A_MaterialDocumentHeader": [
                {"MaterialDocument": "5000000001", "MaterialDocumentYear": "2026", "PostingDate": "2026-07-02"}
            ],
            "A_MaterialDocumentItem": [
                {
                    "MaterialDocument": "5000000001",
                    "MaterialDocumentYear": "2026",
                    "MaterialDocumentItem": "1",
                    "PurchaseOrder": "4500007777",
                    "PurchaseOrderItem": "10",
                    "GoodsMovementType": "101",
                    "QuantityInEntryUnit": "10",
                    "EntryUnit": "EA",
                    "DebitCreditCode": "S",
                }
            ],
            "A_SupplierInvoice": [
                {
                    "SupplierInvoice": "5100000001",
                    "FiscalYear": "2026",
                    "CompanyCode": "1000",
                    "SupplierInvoiceStatus": "5",
                    "DocumentCurrency": currency,
                    "PostingDate": "2026-07-03",
                    "SupplierInvoiceIsCreditMemo": False,
                }
            ],
            "A_SuplrInvcItemPurOrdRef": [
                {
                    "SupplierInvoice": "5100000001",
                    "FiscalYear": "2026",
                    "SupplierInvoiceItem": "1",
                    "PurchaseOrder": "4500007777",
                    "PurchaseOrderItem": "10",
                    "QuantityInPurchaseOrderUnit": "10",
                    "PurchaseOrderQuantityUnit": "EA",
                    "SupplierInvoiceItemAmount": "100",
                    "DocumentCurrency": currency,
                }
            ],
            "A_OperationalAcctgDocItemCube": [
                {
                    "CompanyCode": "1000",
                    "FiscalYear": "2026",
                    "AccountingDocument": "1900000001",
                    "AccountingDocumentItem": "1",
                    "AccountingDocumentType": "RE",
                    "Supplier": "100001",
                    "PurchasingDocument": "4500007777",
                    "PurchasingDocumentItem": "10",
                    "SupplierInvoice": "5100000001",
                    "SupplierInvoiceFiscalYear": "2026",
                    "AmountInTransactionCurrency": "-100",
                    "TransactionCurrency": "CNY",
                    "DebitCreditCode": "H",
                    "ClearingAccountingDocument": "2000000001",
                    "ClearingDocFiscalYear": "2026",
                    "ClearingDate": "2026-07-10",
                    "NetDueDate": "2026-07-20",
                },
                {
                    "CompanyCode": "1000",
                    "FiscalYear": "2026",
                    "AccountingDocument": "2000000001",
                    "AccountingDocumentItem": "1",
                    "AccountingDocumentType": payment_type,
                    "Supplier": "100001",
                    "AmountInTransactionCurrency": "100",
                    "TransactionCurrency": "CNY",
                    "DebitCreditCode": "S",
                },
            ],
        },
    }


class EvidenceDataSourceTests(unittest.TestCase):
    def _write(self, payload: dict, directory: str) -> Path:
        path = Path(directory) / "evidence.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_maps_complete_paid_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = EvidenceP2PDataSource(self._write(_payload(), directory))
            report = P2PAnalyzer().analyze(
                source.load_purchase_order("4500007777"),
                QueryParameters("4500007777"),
                as_of=date(2026, 8, 9),
            )
        item = report.items[0]
        self.assertEqual(item.status, ItemStatus.PAID)
        self.assertEqual(item.documents.material_documents, ("5000000001/2026",))
        self.assertEqual(item.documents.clearing_documents, ("2000000001/2026",))

    def test_custom_payment_document_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = EvidenceP2PDataSource(self._write(_payload(payment_type="ZZ"), directory))
            tables = source.load_purchase_order("4500007777")
            default_item = P2PAnalyzer().analyze(
                tables, QueryParameters("4500007777"), as_of=date(2026, 8, 9)
            ).items[0]
            configured_item = P2PAnalyzer({"ZZ"}).analyze(
                tables, QueryParameters("4500007777"), as_of=date(2026, 8, 9)
            ).items[0]
        self.assertEqual(default_item.status, ItemStatus.INVOICED_NOT_PAID)
        self.assertEqual(configured_item.status, ItemStatus.PAID)

    def test_rejects_incomplete_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvidenceValidationError, "not marked complete"):
                EvidenceP2PDataSource(self._write(_payload(complete=False), directory))

    def test_rejects_currency_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = EvidenceP2PDataSource(self._write(_payload(currency="USD"), directory))
            with self.assertRaisesRegex(EvidenceValidationError, "Currency mismatch"):
                source.load_purchase_order("4500007777")


if __name__ == "__main__":
    unittest.main()
