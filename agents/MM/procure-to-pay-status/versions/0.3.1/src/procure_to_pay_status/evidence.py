"""Map a sanitized SAP read evidence snapshot to the P2P table contract."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .model import P2PTables, SapRow


class EvidenceValidationError(ValueError):
    """Raised when an evidence snapshot is incomplete or semantically unsafe."""


_REQUIRED_ENTITIES = (
    "A_PurchaseOrder",
    "A_PurchaseOrderItem",
    "A_MaterialDocumentHeader",
    "A_MaterialDocumentItem",
    "A_SupplierInvoice",
    "A_SuplrInvcItemPurOrdRef",
    "A_OperationalAcctgDocItemCube",
)


def _text(row: Mapping[str, Any], name: str) -> str:
    return str(row.get(name, "") or "").strip()


def _sap_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc).strftime("%Y%m%d")
    iso_candidate = text[:10]
    try:
        return datetime.fromisoformat(iso_candidate).strftime("%Y%m%d")
    except ValueError:
        compact = text.replace("-", "")[:8]
        return compact if len(compact) == 8 and compact.isdigit() else ""


def _flag(value: Any) -> str:
    return "X" if str(value or "").strip().upper() in {"1", "TRUE", "X", "YES", "Y"} else ""


def _absolute_amount(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(abs(Decimal(str(value).replace(",", ""))))
    except InvalidOperation as exc:
        raise EvidenceValidationError(f"Evidence contains an invalid amount: {value!r}") from exc


def _dedupe(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> tuple[SapRow, ...]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(str(row.get(field, "") or "").strip() for field in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return tuple(result)


class EvidenceP2PDataSource:
    """Read a complete, sanitized evidence snapshot produced by orchestration."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise EvidenceValidationError("Evidence root must be a JSON object")
        completeness = payload.get("completeness", {})
        if not isinstance(completeness, dict) or completeness.get("complete") is not True:
            raise EvidenceValidationError("Evidence snapshot is not marked complete")
        entities = payload.get("entities")
        if not isinstance(entities, dict):
            raise EvidenceValidationError("Evidence snapshot must contain an entities object")
        self.metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        self.mapping_warnings: list[str] = []
        self._entities: dict[str, list[dict[str, Any]]] = {}
        for name in _REQUIRED_ENTITIES:
            rows = entities.get(name, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise EvidenceValidationError(f"Evidence entity {name} must be a list of objects")
            self._entities[name] = rows

    def load_purchase_order(self, po_number: str) -> P2PTables:
        headers = [row for row in self._entities["A_PurchaseOrder"] if _text(row, "PurchaseOrder") == po_number]
        items = [row for row in self._entities["A_PurchaseOrderItem"] if _text(row, "PurchaseOrder") == po_number]
        if not headers or not items:
            raise EvidenceValidationError(f"Evidence does not contain PO {po_number} header and items")

        ekko = tuple(
            {
                "EBELN": _text(row, "PurchaseOrder"),
                "BUKRS": _text(row, "CompanyCode"),
                "LIFNR": _text(row, "Supplier"),
                "WAERS": _text(row, "DocumentCurrency"),
                "BEDAT": _sap_date(row.get("PurchaseOrderDate")),
                "BSART": _text(row, "PurchaseOrderType"),
            }
            for row in headers
        )
        po_currency = _text(headers[0], "DocumentCurrency")
        po_units: dict[tuple[str, str], str] = {}
        ekpo_rows: list[dict[str, Any]] = []
        for row in items:
            key = (_text(row, "PurchaseOrder"), _text(row, "PurchaseOrderItem").zfill(5))
            unit = _text(row, "PurchaseOrderQuantityUnit")
            po_units[key] = unit
            ekpo_rows.append(
                {
                    "EBELN": key[0],
                    "EBELP": key[1],
                    "MATNR": _text(row, "Material"),
                    "TXZ01": _text(row, "PurchaseOrderItemText"),
                    "WERKS": _text(row, "Plant"),
                    "LGORT": _text(row, "StorageLocation"),
                    "MENGE": _text(row, "OrderQuantity"),
                    "MEINS": unit,
                    "LOEKZ": _text(row, "PurchasingDocumentDeletionCode"),
                    "ELIKZ": _flag(row.get("IsCompletelyDelivered")),
                }
            )

        material_items = [
            row for row in self._entities["A_MaterialDocumentItem"] if _text(row, "PurchaseOrder") == po_number
        ]
        material_keys = {(_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")) for row in material_items}
        material_headers = [
            row
            for row in self._entities["A_MaterialDocumentHeader"]
            if (_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")) in material_keys
        ]
        mkpf = tuple(
            {
                "MBLNR": _text(row, "MaterialDocument"),
                "MJAHR": _text(row, "MaterialDocumentYear"),
                "BUDAT": _sap_date(row.get("PostingDate")),
            }
            for row in material_headers
        )
        mseg_rows: list[dict[str, Any]] = []
        for row in material_items:
            key = (_text(row, "PurchaseOrder"), _text(row, "PurchaseOrderItem").zfill(5))
            entry_unit = _text(row, "EntryUnit")
            if po_units.get(key) and entry_unit and po_units[key] != entry_unit:
                raise EvidenceValidationError(
                    f"Unit mismatch for PO {key[0]} item {key[1]}: {po_units[key]} != {entry_unit}"
                )
            mseg_rows.append(
                {
                    "MBLNR": _text(row, "MaterialDocument"),
                    "MJAHR": _text(row, "MaterialDocumentYear"),
                    "ZEILE": _text(row, "MaterialDocumentItem"),
                    "EBELN": key[0],
                    "EBELP": key[1],
                    "BWART": _text(row, "GoodsMovementType"),
                    "MENGE": _text(row, "QuantityInEntryUnit"),
                    "MEINS": entry_unit,
                    "SHKZG": _text(row, "DebitCreditCode"),
                    "STBLG": _text(row, "ReversedMaterialDocument"),
                }
            )

        invoice_headers = {
            (_text(row, "SupplierInvoice"), _text(row, "FiscalYear")): row
            for row in self._entities["A_SupplierInvoice"]
        }
        invoice_items = [
            row for row in self._entities["A_SuplrInvcItemPurOrdRef"] if _text(row, "PurchaseOrder") == po_number
        ]
        invoice_keys = {(_text(row, "SupplierInvoice"), _text(row, "FiscalYear")) for row in invoice_items}
        rbkp_rows: list[dict[str, Any]] = []
        for key in invoice_keys:
            row = invoice_headers.get(key)
            if row is None:
                self.mapping_warnings.append(f"Supplier invoice {key[0]}/{key[1]} header is missing")
                continue
            currency = _text(row, "DocumentCurrency")
            if po_currency and currency and po_currency != currency:
                raise EvidenceValidationError(
                    f"Currency mismatch for invoice {key[0]}/{key[1]}: {po_currency} != {currency}"
                )
            rbkp_rows.append(
                {
                    "BELNR": key[0],
                    "GJAHR": key[1],
                    "BUKRS": _text(row, "CompanyCode"),
                    "RBSTAT": _text(row, "SupplierInvoiceStatus"),
                    "BLDAT": _sap_date(row.get("DocumentDate")),
                    "BUDAT": _sap_date(row.get("PostingDate")),
                    "WAERS": currency,
                    "STBLG": _text(row, "ReverseDocument"),
                }
            )
        rseg_rows: list[dict[str, Any]] = []
        for row in invoice_items:
            key = (_text(row, "PurchaseOrder"), _text(row, "PurchaseOrderItem").zfill(5))
            unit = _text(row, "PurchaseOrderQuantityUnit")
            if po_units.get(key) and unit and po_units[key] != unit:
                raise EvidenceValidationError(
                    f"Unit mismatch for invoice on PO {key[0]} item {key[1]}: {po_units[key]} != {unit}"
                )
            invoice_key = (_text(row, "SupplierInvoice"), _text(row, "FiscalYear"))
            header = invoice_headers.get(invoice_key, {})
            item_currency = _text(row, "DocumentCurrency")
            if po_currency and item_currency and po_currency != item_currency:
                raise EvidenceValidationError(
                    f"Currency mismatch for invoice item {invoice_key[0]}/{invoice_key[1]}: "
                    f"{po_currency} != {item_currency}"
                )
            credit = _flag(header.get("SupplierInvoiceIsCreditMemo")) == "X"
            rseg_rows.append(
                {
                    "BELNR": invoice_key[0],
                    "GJAHR": invoice_key[1],
                    "BUZEI": _text(row, "SupplierInvoiceItem"),
                    "EBELN": key[0],
                    "EBELP": key[1],
                    "MENGE": _text(row, "QuantityInPurchaseOrderUnit"),
                    "MEINS": unit,
                    "WRBTR": _absolute_amount(row.get("SupplierInvoiceItemAmount")),
                    "SHKZG": "H" if credit else "S",
                }
            )

        fi_source = [
            row
            for row in self._entities["A_OperationalAcctgDocItemCube"]
            if _text(row, "PurchasingDocument") == po_number
            or _text(row, "AccountingDocument")
            in {
                _text(candidate, "ClearingAccountingDocument")
                for candidate in self._entities["A_OperationalAcctgDocItemCube"]
                if _text(candidate, "PurchasingDocument") == po_number
            }
        ]
        bkpf_rows: list[dict[str, Any]] = []
        bseg_rows: list[dict[str, Any]] = []
        for row in fi_source:
            company = _text(row, "CompanyCode")
            document = _text(row, "AccountingDocument")
            year = _text(row, "FiscalYear")
            supplier_invoice = _text(row, "SupplierInvoice")
            supplier_invoice_year = _text(row, "SupplierInvoiceFiscalYear")
            bkpf_rows.append(
                {
                    "BUKRS": company,
                    "BELNR": document,
                    "GJAHR": year,
                    "BLART": _text(row, "AccountingDocumentType"),
                    "BUDAT": _sap_date(row.get("PostingDate")),
                    "AWTYP": "RMRP" if supplier_invoice else "",
                    "AWKEY": f"{supplier_invoice}{supplier_invoice_year}" if supplier_invoice else "",
                }
            )
            transaction_currency = _text(row, "TransactionCurrency")
            if po_currency and transaction_currency and _text(row, "PurchasingDocument") == po_number:
                if po_currency != transaction_currency:
                    raise EvidenceValidationError(
                        f"Currency mismatch for FI document {document}/{year}: {po_currency} != {transaction_currency}"
                    )
            bseg_rows.append(
                {
                    "BUKRS": company,
                    "BELNR": document,
                    "GJAHR": year,
                    "BUZEI": _text(row, "AccountingDocumentItem"),
                    "KOART": "K" if _text(row, "Supplier") else "",
                    "LIFNR": _text(row, "Supplier"),
                    "WRBTR": _absolute_amount(row.get("AmountInTransactionCurrency")),
                    "SHKZG": _text(row, "DebitCreditCode"),
                    "AUGBL": _text(row, "ClearingAccountingDocument"),
                    "AUGGJ": _text(row, "ClearingDocFiscalYear"),
                    "AUGDT": _sap_date(row.get("ClearingDate")),
                    "ZLSPR": _text(row, "PaymentBlockingReason"),
                    "FAEDT": _sap_date(row.get("NetDueDate")),
                    "EBELN": _text(row, "PurchasingDocument"),
                    "EBELP": _text(row, "PurchasingDocumentItem").zfill(5),
                    "LIV_BELNR": supplier_invoice,
                    "LIV_GJAHR": supplier_invoice_year,
                    "REBZG": _text(row, "PartialPaymentReference"),
                    "REBZJ": _text(row, "PartialPaymentReferenceFiscalYear"),
                }
            )

        return P2PTables(
            ekko=ekko,
            ekpo=tuple(ekpo_rows),
            mkpf=mkpf,
            mseg=tuple(mseg_rows),
            rbkp=tuple(rbkp_rows),
            rseg=tuple(rseg_rows),
            bkpf=_dedupe(bkpf_rows, ("BUKRS", "BELNR", "GJAHR")),
            bseg=_dedupe(bseg_rows, ("BUKRS", "BELNR", "GJAHR", "BUZEI")),
        )
