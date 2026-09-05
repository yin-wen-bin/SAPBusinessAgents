from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sap_business_agents_platform.acceptance import canonical_hash

try:
    from scripts.build_material_shortage_direct_baseline import _request
    from scripts.direct_sap_read import _load_object, read_encrypted_rows, run as direct_run
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from build_material_shortage_direct_baseline import _request
    from direct_sap_read import _load_object, read_encrypted_rows, run as direct_run


JsonObject = dict[str, Any]
SAFE_ID = re.compile(r"^[0-9A-Za-z_-]+$")
SAP_V2_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


def _load(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _literal(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not SAFE_ID.fullmatch(text):
        raise ValueError("direct baseline input contains an unsafe SAP identifier")
    return "'" + text.replace("'", "''") + "'"


def _date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    matched = SAP_V2_DATE.fullmatch(text)
    if matched:
        try:
            return (
                datetime(1970, 1, 1, tzinfo=timezone.utc)
                + timedelta(milliseconds=int(matched.group(1)))
            ).date()
        except (OverflowError, ValueError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _text(row: JsonObject, field: str) -> str:
    return str(row.get(field) or "").strip()


def _item_key(value: Any) -> str:
    """Canonicalize a numeric FI item only for cross-source key comparison."""
    text = str(value or "").strip()
    return str(int(text)) if text.isdigit() else text


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "x", "yes"}


def _source(
    profile: JsonObject,
    request: JsonObject,
    artifacts: Path,
    *,
    primary: bool,
) -> tuple[JsonObject, list[JsonObject]]:
    output = artifacts / str(request["source_id"])
    manifest_path = output / "manifest.json"
    if manifest_path.is_file() and (output / "rows.ndjson.aesgcm").is_file():
        manifest = _load(manifest_path)
        expected = canonical_hash({key: request[key] for key in request if key != "source_id"})
        if manifest.get("query_hash") != expected or manifest.get("source_complete") is not True:
            raise ValueError("cached direct source does not match the immutable query")
    else:
        manifest = direct_run(profile, request, output, encrypt_rows=True)
    rows = read_encrypted_rows(output)
    return (
        {
            "source_id": manifest["source_id"],
            "service_name": manifest["service_name"],
            "entity_set": manifest["entity_set"],
            "access_method": "odata_get",
            "http_method": "GET",
            "semantic_read_only": True,
            "schema_hash": manifest["schema_hash"],
            "query_hash": manifest["query_hash"],
            "stable_order_by": manifest["stable_order_by"],
            "paging_complete": bool(manifest["paging_complete"]),
            "source_complete": bool(manifest["source_complete"]),
            "row_count": int(manifest["row_count"]),
            "page_count": int(manifest["page_count"]),
            "primary": primary,
            "source_snapshot_ref": str(output.resolve()),
            "source_snapshot_hash": manifest.get("rows_hash"),
            "observed_at": manifest.get("observed_at"),
            "restricted_rows_hash": (manifest.get("restricted_artifact") or {}).get(
                "ciphertext_sha256"
            ),
        },
        rows,
    )


DUNNING_JOIN_FIELDS = (
    "MANDT", "LAUFD", "LAUFI", "KOART", "BUKRS", "KUNNR", "LIFNR",
    "CPDKY", "SKNRZE", "SMABER", "SMAHSK",
)


def _adt_snapshot(path: Path, expected_object: str) -> tuple[JsonObject, list[JsonObject]]:
    manifest = _load(path / "manifest.json")
    required = {
        "source_id", "object", "access_method", "http_method", "semantic_read_only",
        "query_hash", "schema_hash", "metadata_sha256", "stable_order_by",
        "paging_complete", "source_complete", "row_count", "rows_hash", "spec",
    }
    if not required.issubset(manifest):
        raise ValueError("direct_dunning_snapshot_contract_invalid")
    if (
        manifest.get("object") != expected_object
        or manifest.get("access_method") != "adt_data_preview"
        or manifest.get("http_method") != "POST"
        or manifest.get("semantic_read_only") is not True
        or manifest.get("paging_complete") is not True
        or manifest.get("source_complete") is not True
        or manifest.get("schema_hash") != manifest.get("metadata_sha256")
    ):
        raise ValueError("direct_dunning_snapshot_incomplete")
    for field in ("query_hash", "schema_hash", "metadata_sha256", "rows_hash"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get(field) or "")):
            raise ValueError("direct_dunning_snapshot_digest_invalid")
    rows = read_encrypted_rows(path)
    if len(rows) != manifest.get("row_count") or canonical_hash(rows) != manifest.get("rows_hash"):
        raise ValueError("direct_dunning_snapshot_digest_mismatch")
    return manifest, rows


def _dunning_scope_proven(manifest: JsonObject, company: str, cutoff: date) -> bool:
    spec = manifest.get("spec") if isinstance(manifest.get("spec"), dict) else {}
    filters = spec.get("filters") if isinstance(spec.get("filters"), list) else []
    predicates = {
        (str(item.get("field") or "").upper(), str(item.get("operator") or ""), str(item.get("value") or ""))
        for item in filters if isinstance(item, dict)
    }
    cutoff_value = cutoff.strftime("%Y%m%d")
    return (
        ("KOART", "=", "D") in predicates
        and ("BUKRS", "=", company) in predicates
        and any(field == "LAUFD" and operator == "<=" and value == cutoff_value
                for field, operator, value in predicates)
        and any(field == "LAUFD" and operator == ">=" and value <= "20100101"
                for field, operator, value in predicates)
    )


def _dunning_key(row: JsonObject) -> tuple[str, ...]:
    return tuple(_text(row, field) for field in DUNNING_JOIN_FIELDS)


def _independent_dunning_events(
    item_rows: list[JsonObject],
    header_rows: list[JsonObject],
    *,
    company: str,
    customers: list[str],
    cutoff: date,
    dunning_area: str,
) -> list[JsonObject]:
    headers: dict[tuple[str, ...], JsonObject] = {}
    for row in header_rows:
        key = _dunning_key(row)
        if not all(key[:6]):
            raise ValueError("direct_dunning_header_key_incomplete")
        if key in headers and headers[key] != row:
            raise ValueError("direct_dunning_header_key_conflict")
        headers[key] = row
    selected: list[tuple[JsonObject, JsonObject, date, str]] = []
    for row in item_rows:
        run_date = _date(row.get("LAUFD"))
        area = _text(row, "MABER") or _text(row, "SMABER")
        if (
            _text(row, "KOART") != "D"
            or _text(row, "BUKRS") != company
            or _text(row, "KUNNR") not in customers
            or run_date is None
            or run_date > cutoff
            or (dunning_area and area != dunning_area)
        ):
            continue
        header = headers.get(_dunning_key(row))
        if header is None:
            raise ValueError("direct_dunning_header_relationship_missing")
        selected.append((row, header, run_date, area))
    sequence_groups: dict[tuple[str, str, date], set[str]] = {}
    for row, _header, run_date, area in selected:
        sequence_groups.setdefault((_text(row, "KUNNR"), area, run_date), set()).add(
            _text(row, "LAUFI")
        )
    events = []
    for row, header, run_date, area in selected:
        amount = _decimal(row.get("DMSHB"))
        if amount is None:
            raise ValueError("direct_dunning_amount_invalid")
        effective = _date(header.get("AUSDT")) or run_date
        events.append(
            {
                "customer": _text(row, "KUNNR"),
                "company_code": company,
                "dunning_area": area,
                "dunning_run_id": _text(row, "LAUFI"),
                "dunning_run_date": run_date.isoformat(),
                "effective_dunning_date": effective.isoformat(),
                "fiscal_year": _text(row, "GJAHR"),
                "accounting_document": _text(row, "BELNR"),
                "accounting_document_item": _text(row, "BUZEI"),
                "dunning_level": _text(row, "MAHNN"),
                "old_dunning_level": _text(row, "MAHNS"),
                "dunning_blocking_reason": _text(row, "MANSP"),
                "dunning_reversal_status": "not_assessed",
                "special_gl_code": _text(row, "UMSKZ"),
                "amount": format(amount, "f"),
                "currency": _text(row, "WAERS") or _text(header, "WAERS"),
                "sequence_status": (
                    "ambiguous"
                    if len(sequence_groups[(_text(row, "KUNNR"), area, run_date)]) > 1
                    else "ordered"
                ),
            }
        )
    events.sort(key=lambda item: tuple(str(item.get(field) or "") for field in (
        "company_code", "customer", "dunning_area", "dunning_run_date",
        "dunning_run_id", "fiscal_year", "accounting_document", "accounting_document_item",
    )))
    return events


def _adt_source(manifest: JsonObject, snapshot: Path, *, primary: bool = False) -> JsonObject:
    return {
        "source_id": manifest["source_id"],
        "object": manifest["object"],
        "access_method": "adt_data_preview",
        "http_method": "POST",
        "semantic_read_only": True,
        "schema_hash": manifest["schema_hash"],
        "query_hash": manifest["query_hash"],
        "rows_hash": manifest["rows_hash"],
        "stable_order_by": manifest["stable_order_by"],
        "paging_complete": True,
        "source_complete": True,
        "row_count": int(manifest["row_count"]),
        "page_count": 1,
        "primary": primary,
        "source_snapshot_ref": str(snapshot.resolve()),
        "source_snapshot_hash": manifest["rows_hash"],
        "observed_at": manifest.get("observed_at"),
        "restricted_rows_hash": (manifest.get("restricted_artifact") or {}).get("ciphertext_sha256"),
    }


def _document_key(row):
    return (_text(row, "CompanyCode"), _text(row, "FiscalYear"), _text(row, "AccountingDocument"))


def _read_documents(profile, keys, artifacts, prefix):
    """Read exact fiscal-document tuples; never independent year/document INs."""
    sources, rows = [], []
    keys = sorted(set(keys))
    fields = ["CompanyCode", "Ledger", "FiscalYear", "AccountingDocument", "AccountingDocumentItem",
              "PostingDate", "ReverseDocument", "ReverseDocumentFiscalYear"]
    for index in range(0, len(keys), 10):
        group = keys[index:index + 10]
        predicate = " or ".join("(" + " and ".join(f"{field} eq {_literal(value)}" for field, value in zip(
            ("CompanyCode", "FiscalYear", "AccountingDocument"), key)) + ")" for key in group)
        source, found = _source(profile, _request(prefix + "_" + str(index // 10),
            "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields, predicate,
            ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"]), artifacts, primary=False)
        if any(_document_key(row) not in group for row in found):
            raise ValueError("direct_fi_tuple_relation_conflict")
        sources.append(source)
        rows.extend(found)
    return sources, rows


def _open_at_cutoff(row: JsonObject, cutoff: date, clearing_rows=None, reversal_rows=None) -> tuple[bool, str | None]:
    clearing = _date(row.get("ClearingDate"))
    if clearing is None or clearing > cutoff:
        return True, None
    reference = (_text(row, "CompanyCode"), _text(row, "ClearingDocFiscalYear"), _text(row, "ClearingAccountingDocument"))
    documents = [item for item in clearing_rows or [] if _document_key(item) == reference]
    reversals = {(_text(item, "CompanyCode"), _text(item, "ReverseDocumentFiscalYear"), _text(item, "ReverseDocument"))
                 for item in documents if _text(item, "ReverseDocumentFiscalYear") and _text(item, "ReverseDocument")}
    if _truthy(row.get("ClearingIsReversed")) or reversals:
        if len(reversals) != 1:
            return False, "historical_clearing_reversal_date_missing"
        dates = {_date(item.get("PostingDate")) for item in reversal_rows or [] if _document_key(item) in reversals}
        if len(dates) != 1 or None in dates:
            return False, "historical_clearing_reversal_date_missing"
        return next(iter(dates)) <= cutoff, None
    return False, None


def build(
    case_path: Path,
    profile_path: Path,
    output: Path,
    artifacts: Path,
    *,
    dunning_item_snapshot: Path | None = None,
    dunning_header_snapshot: Path | None = None,
) -> JsonObject:
    if output.exists():
        raise ValueError("direct_baseline_immutable")
    case = _load(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "ar-collection":
        raise ValueError("case must be an ar-collection CanonicalTestCase v2")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    company = str(values.get("company_code") or "").strip()
    customers = [str(item).strip() for item in values.get("customers") or []]
    cutoff = date.fromisoformat(str(values.get("as_of") or ""))
    business_date = date.fromisoformat(str(values.get("business_date") or ""))
    dunning_area = str(values.get("dunning_area") or "").strip()
    if not company or not customers or len(customers) > 50 or len(set(customers)) != len(customers):
        raise ValueError("case requires one company and 1-50 unique customers")
    historical = cutoff < business_date
    if cutoff > business_date:
        raise ValueError("as_of cannot be after the business date")
    if historical and (dunning_item_snapshot is None or dunning_header_snapshot is None):
        raise ValueError("historical AR baseline requires independent MHND and MHNK snapshots")
    profile = _load_object(profile_path.resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    customer_filter = " or ".join(f"Customer eq {_literal(item)}" for item in customers)

    sources: list[JsonObject] = []
    ledger_source, ledger_rows = _source(
        profile,
        _request(
            "ar_leading_ledger",
            "API_LEDGER_SRV",
            "A_Ledger",
            ["Ledger", "IsLeadingLedger", "LedgerApplication", "LedgerSubApplication"],
            "IsLeadingLedger eq true",
            ["Ledger"],
            max_rows=100,
        ),
        artifacts,
        primary=False,
    )
    sources.append(ledger_source)
    ledgers = {_text(row, "Ledger") for row in ledger_rows if _truthy(row.get("IsLeadingLedger"))}
    ledgers.discard("")
    if len(ledgers) != 1:
        raise RuntimeError("direct baseline could not resolve one leading ledger")
    leading_ledger = next(iter(ledgers))

    item_fields = [
        "CompanyCode", "Ledger", "FiscalYear", "AccountingDocument",
        "AccountingDocumentItem", "Customer", "FinancialAccountType", "PostingDate",
        "NetDueDate", "DueCalculationBaseDate", "ClearingDate", "ClearingIsReversed",
        "DebitCreditCode", "AmountInTransactionCurrency", "TransactionCurrency",
        "DunningArea", "DunningLevel", "DunningBlockingReason", "LastDunningDate",
        "SpecialGLCode",
        "IsOpenItemManaged", "ClearingDocFiscalYear", "ClearingAccountingDocument",
    ]
    item_source, item_rows = _source(
        profile,
        _request(
            "ar_customer_items",
            "API_OPLACCTGDOCITEMCUBE_SRV",
            "A_OperationalAcctgDocItemCube",
            item_fields,
            (
                f"CompanyCode eq {_literal(company)} and FinancialAccountType eq 'D' and "
                f"PostingDate le datetime'{cutoff.isoformat()}T23:59:59' and ({customer_filter})"
            ),
            ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
        ),
        artifacts,
        primary=True,
    )
    sources.append(item_source)
    clearing_keys = {(_text(row, "CompanyCode"), _text(row, "ClearingDocFiscalYear"), _text(row, "ClearingAccountingDocument"))
                     for row in item_rows if _text(row, "ClearingDocFiscalYear") and _text(row, "ClearingAccountingDocument")}
    clearing_sources, clearing_rows = _read_documents(profile, clearing_keys, artifacts, "ar_clearing")
    sources.extend(clearing_sources)
    reversal_keys = {(_text(row, "CompanyCode"), _text(row, "ReverseDocumentFiscalYear"), _text(row, "ReverseDocument"))
                     for row in clearing_rows if _text(row, "ReverseDocumentFiscalYear") and _text(row, "ReverseDocument")}
    reversal_sources, reversal_rows = _read_documents(profile, reversal_keys, artifacts, "ar_reversal")
    sources.extend(reversal_sources)
    if any(_text(row, "Ledger") not in {"", leading_ledger} for row in [*clearing_rows, *reversal_rows]):
        raise ValueError("direct_fi_ledger_relation_conflict")

    master_rows: list[JsonObject] = []
    dunning_events: list[JsonObject] = []
    if historical:
        item_manifest, dunning_item_rows = _adt_snapshot(dunning_item_snapshot, "MHND")
        header_manifest, dunning_header_rows = _adt_snapshot(dunning_header_snapshot, "MHNK")
        if not _dunning_scope_proven(item_manifest, company, cutoff) or not _dunning_scope_proven(
            header_manifest, company, cutoff
        ):
            raise ValueError("direct_dunning_snapshot_scope_unproven")
        sources.extend(
            [
                _adt_source(item_manifest, dunning_item_snapshot),
                _adt_source(header_manifest, dunning_header_snapshot),
            ]
        )
        dunning_events = _independent_dunning_events(
            dunning_item_rows,
            dunning_header_rows,
            company=company,
            customers=customers,
            cutoff=cutoff,
            dunning_area=dunning_area,
        )
    else:
        dunning_filter = f"CompanyCode eq {_literal(company)} and ({customer_filter})"
        if dunning_area:
            dunning_filter += f" and DunningArea eq {_literal(dunning_area)}"
        master_source, master_rows = _source(
            profile,
            _request(
                "ar_current_dunning",
                "API_BUSINESS_PARTNER",
                "A_CustomerDunning",
                [
                    "Customer", "CompanyCode", "DunningArea", "DunningProcedure",
                    "DunningLevel", "DunningBlock", "LastDunnedOn", "DunningClerk",
                ],
                dunning_filter,
                ["Customer", "CompanyCode", "DunningArea"],
            ),
            artifacts,
            primary=False,
        )
        sources.append(master_source)

    records: list[JsonObject] = []
    customer_statuses: dict[str, str] = {}
    gaps: set[str] = set()
    seen_keys: dict[tuple[str, str, str, str, str], JsonObject] = {}
    for row in item_rows:
        if not _truthy(row.get("IsOpenItemManaged")):
            continue
        row_ledger = _text(row, "Ledger") or leading_ledger
        if row_ledger != leading_ledger:
            continue
        key = (
            _text(row, "CompanyCode"), row_ledger, _text(row, "FiscalYear"),
            _text(row, "AccountingDocument"), _text(row, "AccountingDocumentItem"),
        )
        if not all(key):
            gaps.add("fi_business_key_incomplete")
            continue
        prior = seen_keys.get(key)
        normalized_key_row = {**row, "Ledger": row_ledger}
        if prior is not None and prior != normalized_key_row:
            gaps.add("fi_business_key_conflict")
            continue
        seen_keys[key] = normalized_key_row

    for customer in customers:
        customer_records: list[JsonObject] = []
        customer_gaps: set[str] = set()
        customer_master = [row for row in master_rows if _text(row, "Customer") == customer]
        if not dunning_area and len({_text(row, "DunningArea") for row in customer_master}) > 1:
            customer_gaps.add("dunning_area_ambiguous")
        for row in seen_keys.values():
            if _text(row, "Customer") != customer:
                continue
            posting = _date(row.get("PostingDate"))
            amount = _decimal(row.get("AmountInTransactionCurrency"))
            currency = _text(row, "TransactionCurrency")
            if posting is None or amount is None or not currency:
                customer_gaps.add("posting_amount_or_currency_missing")
                continue
            is_open, open_gap = _open_at_cutoff(row, cutoff, clearing_rows, reversal_rows)
            if open_gap:
                customer_gaps.add(open_gap)
            if not is_open:
                continue
            due = _date(row.get("NetDueDate") or row.get("DueCalculationBaseDate"))
            if due is None:
                aging = "unknown"
                overdue_days = None
                customer_gaps.add("due_date_missing")
            else:
                overdue_days = max(0, (cutoff - due).days)
                aging = (
                    "not_due" if overdue_days == 0 else "1_30" if overdue_days <= 30
                    else "31_60" if overdue_days <= 60 else "61_90"
                    if overdue_days <= 90 else "over_90"
                )
            if historical:
                related = [
                    event for event in dunning_events
                    if event["company_code"] == company
                    and event["customer"] == customer
                    and event["fiscal_year"] == _text(row, "FiscalYear")
                    and event["accounting_document"] == _text(row, "AccountingDocument")
                    and _item_key(event["accounting_document_item"])
                    == _item_key(row.get("AccountingDocumentItem"))
                ]
                latest_date = max(
                    (_date(event.get("effective_dunning_date")) for event in related),
                    default=None,
                )
                latest = [event for event in related if _date(event.get("effective_dunning_date")) == latest_date]
                if len(latest) > 1 or any(event["sequence_status"] == "ambiguous" for event in latest):
                    level, last_dunning, dunning_status = "0", None, "unknown"
                    customer_gaps.add("historical_dunning_sequence_ambiguous")
                elif latest:
                    level = latest[0]["dunning_level"] or "0"
                    last_dunning = _date(latest[0]["effective_dunning_date"])
                    dunning_status = "confirmed_before_cutoff"
                else:
                    level, last_dunning, dunning_status = "0", None, "not_dunned"
            else:
                level = _text(row, "DunningLevel") or "0"
                last_dunning = _date(row.get("LastDunningDate"))
                dunning_status = (
                    "confirmed_current"
                    if level not in {"", "0"} and last_dunning is not None
                    else "not_dunned"
                )
            customer_records.append(
                {
                    "company_code": company,
                    "ledger": leading_ledger,
                    "fiscal_year": _text(row, "FiscalYear"),
                    "accounting_document": _text(row, "AccountingDocument"),
                    "accounting_document_item": _text(row, "AccountingDocumentItem"),
                    "customer": customer,
                    "posting_date": posting.isoformat(),
                    "due_date": due.isoformat() if due else None,
                    "aging_bucket": aging,
                    "clearing_date": (
                        _date(row.get("ClearingDate")).isoformat()
                        if _date(row.get("ClearingDate")) else None
                    ),
                    "last_dunning_date": last_dunning.isoformat() if last_dunning else None,
                    "dunning_as_of_status": dunning_status,
                    "amount": format(amount, "f"),
                    "currency": currency,
                    "_attention": bool(
                        (overdue_days or 0) > 0
                        or _text(row, "SpecialGLCode")
                        or _text(row, "DunningBlockingReason")
                    ),
                }
            )
        if any(_text(row, "DunningBlock") for row in customer_master):
            for record in customer_records:
                record["_attention"] = True
        status = (
            "inconclusive" if customer_gaps else "attention"
            if any(bool(item["_attention"]) for item in customer_records) else "normal"
        )
        customer_statuses[customer] = status
        gaps.update(customer_gaps)
        for record in customer_records:
            record.pop("_attention", None)
            record["business_status"] = status
            records.append(record)

    counts = {
        status: sum(value == status for value in customer_statuses.values())
        for status in ("normal", "attention", "inconclusive")
    }
    source_complete = all(bool(source["source_complete"]) for source in sources)
    evidence_complete = source_complete and not gaps
    business_status = (
        "inconclusive" if counts["inconclusive"] or not evidence_complete else
        "attention" if counts["attention"] else "normal"
    )
    records.sort(
        key=lambda item: tuple(
            str(item.get(field) or "")
            for field in (
                "company_code", "ledger", "fiscal_year",
                "accounting_document", "accounting_document_item",
            )
        )
    )
    normalized = {
        "records": records,
        "metrics": {
            "requested_customer_count": len(customers),
            "result_customer_count": len(customers),
            "normal_customer_count": counts["normal"],
            "attention_customer_count": counts["attention"],
            "inconclusive_customer_count": counts["inconclusive"],
        },
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": evidence_complete,
        "evidence_gap_codes": sorted(gaps),
        "limitations": ["historical_dunning_master_snapshot_not_available"] if historical else [],
    }
    baseline = {
        "schema_version": "3.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "business_date": business_date.isoformat(),
        "sources": sources,
        "normalized_result": normalized,
        "result_hash": canonical_hash(normalized),
    }
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an independent encrypted GET-only AR collection baseline."
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--dunning-item-snapshot", type=Path)
    parser.add_argument("--dunning-header-snapshot", type=Path)
    args = parser.parse_args()
    result = build(
        args.case.resolve(), args.profile.resolve(), args.output.resolve(), args.artifacts.resolve(),
        dunning_item_snapshot=(args.dunning_item_snapshot.resolve() if args.dunning_item_snapshot else None),
        dunning_header_snapshot=(args.dunning_header_snapshot.resolve() if args.dunning_header_snapshot else None),
    )
    normalized = result["normalized_result"]
    print(
        json.dumps(
            {
                "source_count": len(result["sources"]),
                "record_count": len(normalized["records"]),
                "business_status": normalized["business_status"],
                "source_complete": normalized["source_complete"],
                "evidence_complete": normalized["evidence_complete"],
                "result_hash": result["result_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
