from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.build_material_shortage_direct_baseline import _request, _run_source
    from scripts.direct_sap_read import _load_object
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from build_material_shortage_direct_baseline import _request, _run_source
    from direct_sap_read import _load_object


JsonObject = dict[str, Any]
QUANTITY_TOLERANCE = Decimal("0.001")
AMOUNT_TOLERANCE = Decimal("0.01")


def _hash_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _literal(value: Any) -> str:
    text = str(value or "").strip()
    if not text or any(character not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-" for character in text):
        raise ValueError("direct baseline input contains an unsafe SAP identifier")
    return "'" + text.replace("'", "''") + "'"


def _text(row: JsonObject, *fields: str) -> str:
    for field in fields:
        value = row.get(field)
        if value not in {None, ""}:
            return str(value).strip()
    return ""


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return (rendered.rstrip("0").rstrip(".") if "." in rendered else rendered) or "0"


def _signed(value: Any, direction: Any) -> Decimal | None:
    number = _decimal(value)
    code = str(direction or "").strip().upper()
    if number is None or code not in {"S", "H", "D", "C"}:
        return None
    return abs(number) if code in {"S", "D"} else -abs(number)


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text.startswith("/Date("):
        try:
            milliseconds = int(text[6:].split(")", 1)[0].split("+", 1)[0])
            return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date()
        except (OSError, ValueError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"true", "1", "x", "yes"}:
        return True
    if text in {"false", "0", "", "no"} and value is not None:
        return False
    return None


def _key(row: JsonObject, document_field: str, item_field: str) -> tuple[str, str] | None:
    document = _text(row, document_field)
    item = _text(row, item_field)
    return (document, item.lstrip("0") or "0") if document and item else None


def _chunks(values: list[Any], size: int = 15) -> Iterable[list[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _pair_filter(
    pairs: list[tuple[str, str]], document_field: str, item_field: str
) -> str:
    return " or ".join(
        f"({document_field} eq {_literal(document)} and {item_field} eq {_literal(item)})"
        for document, item in pairs
    )


def _document_filter(
    pairs: list[tuple[str, str]], document_field: str, year_field: str
) -> str:
    return " or ".join(
        f"({document_field} eq {_literal(document)} and {year_field} eq {_literal(year)})"
        for document, year in pairs
    )


def _collect_chunks(
    profile: JsonObject,
    artifacts: Path,
    *,
    source_prefix: str,
    service_name: str,
    entity_set: str,
    select_fields: list[str],
    order_by: list[str],
    filters: list[str],
) -> tuple[list[JsonObject], list[JsonObject]]:
    manifests: list[JsonObject] = []
    rows: list[JsonObject] = []
    for index, filter_text in enumerate(filters, start=1):
        manifest, batch = _run_source(
            profile,
            _request(
                f"{source_prefix}_{index:03d}",
                service_name,
                entity_set,
                select_fields,
                filter_text,
                order_by,
                max_rows=30000,
            ),
            artifacts,
        )
        manifests.append(manifest)
        rows.extend(batch)
    return manifests, rows


def build(case_path: Path, profile_path: Path, output: Path, artifacts: Path) -> JsonObject:
    case = _load(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "gr-ir-clearing":
        raise ValueError("case must be a gr-ir-clearing v2 case")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    if set(values) != {"company_code", "gl_account", "date_from", "date_to"}:
        raise ValueError("case input has unexpected or missing fields")
    date_from = date.fromisoformat(str(values["date_from"]))
    date_to = date.fromisoformat(str(values["date_to"]))
    if date_from > date_to:
        raise ValueError("date_from must not be after date_to")
    profile = _load_object(profile_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    sources: list[JsonObject] = []

    candidate_manifest, candidate_rows = _run_source(
        profile,
        _request(
            "candidate_grir_gl",
            "API_OPLACCTGDOCITEMCUBE_SRV",
            "A_OperationalAcctgDocItemCube",
            [
                "CompanyCode", "FiscalYear", "AccountingDocument",
                "AccountingDocumentItem", "GLAccount", "PurchasingDocument",
                "PurchasingDocumentItem", "PostingDate",
                "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "DebitCreditCode",
            ],
            (
                f"CompanyCode eq {_literal(values['company_code'])} and "
                f"GLAccount eq {_literal(values['gl_account'])} and "
                f"PostingDate ge datetime'{date_from.isoformat()}T00:00:00' and "
                f"PostingDate le datetime'{date_to.isoformat()}T23:59:59'"
            ),
            ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
            max_rows=30000,
        ),
        artifacts,
        primary=True,
    )
    sources.append(candidate_manifest)
    unassigned = [row for row in candidate_rows if _key(row, "PurchasingDocument", "PurchasingDocumentItem") is None]
    if unassigned:
        raise RuntimeError("qualified live sample contains unattributable GR/IR G/L rows")
    keys = sorted(
        {
            key
            for row in candidate_rows
            if (key := _key(row, "PurchasingDocument", "PurchasingDocumentItem")) is not None
        }
    )
    if not keys:
        raise RuntimeError("qualified live sample contains no purchase-order item")
    pair_filters = [_pair_filter(chunk, "PurchaseOrder", "PurchaseOrderItem") for chunk in _chunks(keys)]
    gl_pair_filters = [
        "CompanyCode eq " + _literal(values["company_code"])
        + " and GLAccount eq " + _literal(values["gl_account"])
        + f" and PostingDate le datetime'{date_to.isoformat()}T23:59:59' and ("
        + _pair_filter(chunk, "PurchasingDocument", "PurchasingDocumentItem") + ")"
        for chunk in _chunks(keys)
    ]

    manifests, history_rows = _collect_chunks(
        profile, artifacts, source_prefix="grir_gl_history",
        service_name="API_OPLACCTGDOCITEMCUBE_SRV",
        entity_set="A_OperationalAcctgDocItemCube",
        select_fields=[
            "CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem",
            "GLAccount", "PurchasingDocument", "PurchasingDocumentItem", "PostingDate",
            "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "DebitCreditCode",
        ],
        order_by=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
        filters=gl_pair_filters,
    )
    sources.extend(manifests)
    manifests, po_rows = _collect_chunks(
        profile, artifacts, source_prefix="purchase_order_items",
        service_name="API_PURCHASEORDER_PROCESS_SRV", entity_set="A_PurchaseOrderItem",
        select_fields=[
            "PurchaseOrder", "PurchaseOrderItem", "Material", "Plant", "OrderQuantity",
            "PurchaseOrderQuantityUnit", "OrderPriceUnit", "DocumentCurrency",
        ],
        order_by=["PurchaseOrder", "PurchaseOrderItem"], filters=pair_filters,
    )
    sources.extend(manifests)
    manifests, material_rows = _collect_chunks(
        profile, artifacts, source_prefix="material_documents",
        service_name="API_MATERIAL_DOCUMENT_SRV", entity_set="A_MaterialDocumentItem",
        select_fields=[
            "MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "PurchaseOrder",
            "PurchaseOrderItem", "Material", "Plant", "GoodsMovementType",
            "QuantityInEntryUnit", "EntryUnit", "MaterialBaseUnit", "DebitCreditCode",
            "ReversedMaterialDocument", "ReversedMaterialDocumentYear", "ReversedMaterialDocumentItem",
        ],
        order_by=["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem"], filters=pair_filters,
    )
    sources.extend(manifests)
    manifests, invoice_rows = _collect_chunks(
        profile, artifacts, source_prefix="supplier_invoice_items",
        service_name="API_SUPPLIERINVOICE_PROCESS_SRV", entity_set="A_SuplrInvcItemPurOrdRef",
        select_fields=[
            "SupplierInvoice", "FiscalYear", "SupplierInvoiceItem", "PurchaseOrder",
            "PurchaseOrderItem", "QuantityInPurchaseOrderUnit", "PurchaseOrderQuantityUnit",
            "SupplierInvoiceItemAmount", "DocumentCurrency",
        ],
        order_by=["SupplierInvoice", "FiscalYear", "SupplierInvoiceItem"], filters=pair_filters,
    )
    sources.extend(manifests)

    material_documents = sorted({(_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")) for row in material_rows})
    invoice_documents = sorted({(_text(row, "SupplierInvoice"), _text(row, "FiscalYear")) for row in invoice_rows})
    manifests, material_headers = _collect_chunks(
        profile, artifacts, source_prefix="material_document_headers",
        service_name="API_MATERIAL_DOCUMENT_SRV", entity_set="A_MaterialDocumentHeader",
        select_fields=["MaterialDocumentYear", "MaterialDocument", "PostingDate"],
        order_by=["MaterialDocumentYear", "MaterialDocument"],
        filters=[_document_filter(chunk, "MaterialDocument", "MaterialDocumentYear") for chunk in _chunks(material_documents)],
    )
    sources.extend(manifests)
    manifests, invoice_headers = _collect_chunks(
        profile, artifacts, source_prefix="supplier_invoice_headers",
        service_name="API_SUPPLIERINVOICE_PROCESS_SRV", entity_set="A_SupplierInvoice",
        select_fields=[
            "SupplierInvoice", "FiscalYear", "CompanyCode", "PostingDate",
            "SupplierInvoiceStatus", "SupplierInvoiceIsCreditMemo", "DocumentCurrency",
        ],
        order_by=["SupplierInvoice", "FiscalYear"],
        filters=[_document_filter(chunk, "SupplierInvoice", "FiscalYear") for chunk in _chunks(invoice_documents)],
    )
    sources.extend(manifests)

    po_by_key = {_key(row, "PurchaseOrder", "PurchaseOrderItem"): row for row in po_rows}
    material_by_key: dict[tuple[str, str], list[JsonObject]] = {}
    invoice_by_key: dict[tuple[str, str], list[JsonObject]] = {}
    gl_by_key: dict[tuple[str, str], list[JsonObject]] = {}
    for collection, target, document, item in (
        (material_rows, material_by_key, "PurchaseOrder", "PurchaseOrderItem"),
        (invoice_rows, invoice_by_key, "PurchaseOrder", "PurchaseOrderItem"),
        (history_rows, gl_by_key, "PurchasingDocument", "PurchasingDocumentItem"),
    ):
        for row in collection:
            row_key = _key(row, document, item)
            if row_key is not None:
                target.setdefault(row_key, []).append(row)
    material_header_by_key = {
        (_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")): row
        for row in material_headers
    }
    invoice_header_by_key = {
        (_text(row, "SupplierInvoice"), _text(row, "FiscalYear")): row
        for row in invoice_headers
    }

    records: list[JsonObject] = []
    for key in keys:
        po = po_by_key.get(key)
        if not isinstance(po, dict):
            raise RuntimeError(f"purchase-order master data is missing for qualified key {key!r}")
        receipt_quantity = Decimal("0")
        invoice_quantity = Decimal("0")
        open_amount = Decimal("0")
        units = {_text(po, "PurchaseOrderQuantityUnit").upper()}
        currencies: set[str] = set()
        has_return = False
        for row in material_by_key.get(key, []):
            header = material_header_by_key.get((_text(row, "MaterialDocument"), _text(row, "MaterialDocumentYear")))
            posting_date = _date((header or {}).get("PostingDate"))
            signed = _signed(row.get("QuantityInEntryUnit"), row.get("DebitCreditCode"))
            if posting_date is None or signed is None:
                raise RuntimeError(f"material-document evidence is incomplete for {key!r}")
            if posting_date <= date_to:
                receipt_quantity += signed
                units.add(_text(row, "EntryUnit", "MaterialBaseUnit").upper())
                has_return = has_return or _text(row, "GoodsMovementType") in {"122", "161"}
        for row in invoice_by_key.get(key, []):
            header = invoice_header_by_key.get((_text(row, "SupplierInvoice"), _text(row, "FiscalYear")))
            posting_date = _date((header or {}).get("PostingDate"))
            quantity = _decimal(row.get("QuantityInPurchaseOrderUnit"))
            credit = _truthy((header or {}).get("SupplierInvoiceIsCreditMemo"))
            amount = _decimal(row.get("SupplierInvoiceItemAmount"))
            if posting_date is None or quantity is None or credit is None:
                raise RuntimeError(f"supplier-invoice evidence is incomplete for {key!r}")
            if posting_date <= date_to:
                invoice_quantity += abs(quantity) * (Decimal("-1") if credit or (amount is not None and amount < 0) else Decimal("1"))
                units.add(_text(row, "PurchaseOrderQuantityUnit").upper())
        for row in gl_by_key.get(key, []):
            posting_date = _date(row.get("PostingDate"))
            signed = _signed(row.get("AmountInCompanyCodeCurrency"), row.get("DebitCreditCode"))
            currency = _text(row, "CompanyCodeCurrency").upper()
            if posting_date is None or signed is None or not currency:
                raise RuntimeError(f"G/L evidence is incomplete for {key!r}")
            if posting_date <= date_to:
                open_amount += signed
                currencies.add(currency)
        units.discard("")
        if len(units) != 1 or len(currencies) != 1 or not gl_by_key.get(key):
            raise RuntimeError(f"unit, currency, or G/L relationship is incomplete for {key!r}")
        difference = receipt_quantity - invoice_quantity
        if has_return and invoice_quantity - receipt_quantity > QUANTITY_TOLERANCE:
            reason = "return_pending"
        elif receipt_quantity > QUANTITY_TOLERANCE and abs(invoice_quantity) <= QUANTITY_TOLERANCE:
            reason = "gr_without_ir"
        elif invoice_quantity > QUANTITY_TOLERANCE and abs(receipt_quantity) <= QUANTITY_TOLERANCE:
            reason = "ir_without_gr"
        elif abs(difference) > QUANTITY_TOLERANCE:
            reason = "quantity_difference"
        elif abs(open_amount) > AMOUNT_TOLERANCE:
            reason = "price_difference"
        else:
            reason = "matched"
        status = "matched" if reason == "matched" else "requires_action"
        records.append(
            {
                "purchase_order": key[0],
                "purchase_order_item": key[1],
                "material": _text(po, "Material"),
                "receipt_quantity": _decimal_text(receipt_quantity),
                "invoice_quantity": _decimal_text(invoice_quantity),
                "unit": next(iter(units)),
                "quantity_difference": _decimal_text(difference),
                "gr_ir_open_amount": _decimal_text(open_amount),
                "currency": next(iter(currencies)),
                "primary_reason": reason,
                "reconciliation_status": status,
                "business_status": "normal" if status == "matched" else "attention",
            }
        )
    records.sort(key=lambda item: (item["purchase_order"], item["purchase_order_item"]))
    follow_up = sum(item["reconciliation_status"] == "requires_action" for item in records)
    metrics = {
        "examined_item_count": len(records),
        "matched_item_count": len(records) - follow_up,
        "follow_up_item_count": follow_up,
        "unknown_item_count": 0,
    }
    normalized = {"records": records, "metrics": metrics, "limitations": [], "source_complete": True}
    qualification = {
        "status": "qualified",
        "reasons": [],
        "evidence_source_ids": [source["source_id"] for source in sources],
        "evidence_hash": _hash_json(
            {
                "candidate_rows": len(candidate_rows),
                "keys": keys,
                "source_hashes": [source["query_hash"] for source in sources],
            }
        ),
    }
    baseline = {
        "schema_version": "2.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "http_methods": ["GET"],
        "sources": sources,
        "qualification": qualification,
        "result_hash": _hash_json(normalized),
        "normalized_result": normalized,
    }
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an independent GET-only GR/IR v2 baseline.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument(
        "--profile", type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    baseline = build(args.case.resolve(), args.profile.resolve(), args.output.resolve(), args.artifacts.resolve())
    print(json.dumps({"records": len(baseline["normalized_result"]["records"]), "metrics": baseline["normalized_result"]["metrics"], "result_hash": baseline["result_hash"], "http_methods": baseline["http_methods"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
