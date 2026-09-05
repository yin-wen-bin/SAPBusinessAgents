from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sap_business_agents_platform.acceptance import canonical_hash

try:
    from scripts.build_material_shortage_direct_baseline import _request
    from scripts.direct_sap_read import (
        read_encrypted_rows,
        run as direct_run,
        write_encrypted_rows,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from build_material_shortage_direct_baseline import _request
    from direct_sap_read import read_encrypted_rows, run as direct_run, write_encrypted_rows


JsonObject = dict[str, Any]
SAP_V2_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")
REVERSAL = {
    "": "not_reversed", "0": "not_reversed", "2": "not_reversed",
    "7": "not_reversed", "8": "not_reversed", "P": "not_reversed",
    "A": "not_reversed", "S": "not_reversed", "R": "reversed",
    "Q": "reversal_in_process", "E": "reversal_failed",
}
POSTING_ERROR = {"": "none", "0": "none", "1": "error"}
LIFECYCLE = {
    "": "not_completed", "O": "not_completed", "R": "not_completed",
    "P": "partially_applied", "Y": "completed_on_account",
    "G": "completed", "M": "completed_set_to_done",
}
FI_FIELDS = [
    "CompanyCode", "Ledger", "FiscalYear", "FiscalPeriod",
    "AccountingDocument", "AccountingDocumentItem", "Customer",
    "FinancialAccountType", "GLAccount", "PostingDate", "NetDueDate",
    "ClearingDate", "ClearingIsReversed", "IsOpenItemManaged",
    "DebitCreditCode", "IsCleared", "ClearingAccountingDocument",
    "ClearingDocFiscalYear", "AccountingDocumentType",
    "AmountInTransactionCurrency", "TransactionCurrency",
    "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "SpecialGLCode",
    "AssignmentReference", "Reference1IDByBusinessPartner", "DocumentReferenceID",
]


def _read_sensitive_reference_from_stdin(enabled: bool) -> str | None:
    if not enabled:
        return None
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("--sensitive-input-stdin requires a JSON object on stdin")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"receipt_reference"}:
        raise ValueError("sensitive input must contain only receipt_reference")
    reference = value.get("receipt_reference")
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("receipt_reference must be a non-blank string")
    return reference.strip()


def _load_object(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_rows(path: Path) -> list[JsonObject]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("direct bank source must be a JSON array of raw ADT rows")
    return [dict(item) for item in value]


def _literal(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[0-9A-Za-z_-]+", text):
        raise ValueError("direct baseline input contains an unsafe SAP identifier")
    return "'" + text.replace("'", "''") + "'"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "x", "yes"}


def _odata_source(
    profile: JsonObject,
    request: JsonObject,
    artifacts: Path,
    *,
    primary: bool = False,
) -> tuple[JsonObject, list[JsonObject]]:
    target = artifacts / str(request["source_id"])
    manifest_path = target / "manifest.json"
    if manifest_path.is_file() and (target / "rows.ndjson.aesgcm").is_file():
        manifest = _load_object(manifest_path)
        expected = canonical_hash({key: request[key] for key in request if key != "source_id"})
        if manifest.get("query_hash") != expected or manifest.get("source_complete") is not True:
            raise ValueError("cached direct source does not match the immutable query")
    else:
        manifest = direct_run(profile, request, target, encrypt_rows=True)
    rows = read_encrypted_rows(target)
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
            "source_snapshot_ref": str(target.resolve()),
            "source_snapshot_hash": manifest.get("rows_hash"),
            "observed_at": manifest.get("observed_at"),
            "restricted_rows_hash": (manifest.get("restricted_artifact") or {}).get(
                "ciphertext_sha256"
            ),
        },
        rows,
    )


def _adt_snapshot(path: Path) -> tuple[JsonObject, list[JsonObject]]:
    manifest = _load_object(path / "manifest.json")
    required = {
        "source_id", "object", "access_method", "http_method", "semantic_read_only",
        "query_hash", "schema_hash", "metadata_sha256", "stable_order_by",
        "paging_complete", "source_complete", "row_count", "rows_hash", "spec",
    }
    if not required.issubset(manifest):
        raise ValueError("direct bank snapshot contract is incomplete")
    if (
        str(manifest.get("object") or "").upper() != "I_ARBANKSTATEMENTITEM"
        or manifest.get("access_method") != "adt_data_preview"
        or manifest.get("http_method") != "POST"
        or manifest.get("semantic_read_only") is not True
        or manifest.get("paging_complete") is not True
        or manifest.get("source_complete") is not True
        or manifest.get("schema_hash") != manifest.get("metadata_sha256")
    ):
        raise ValueError("direct bank snapshot is not a complete independent ADT source")
    for field in ("query_hash", "schema_hash", "metadata_sha256", "rows_hash"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get(field) or "")):
            raise ValueError(f"direct bank snapshot {field} is invalid")
    rows = read_encrypted_rows(path)
    if len(rows) != manifest.get("row_count") or canonical_hash(rows) != manifest.get("rows_hash"):
        raise ValueError("direct bank snapshot rows do not match their manifest")
    return manifest, rows


def _adt_source(manifest: JsonObject, snapshot: Path) -> JsonObject:
    return {
        "source_id": manifest["source_id"],
        "object": manifest["object"],
        "access_method": "adt_data_preview",
        "http_method": "POST",
        "semantic_read_only": True,
        "schema_hash": manifest["schema_hash"],
        "query_hash": manifest["query_hash"],
        "stable_order_by": manifest["stable_order_by"],
        "paging_complete": True,
        "source_complete": True,
        "row_count": int(manifest["row_count"]),
        "page_count": 1,
        "primary": True,
        "source_snapshot_ref": str(snapshot.resolve()),
        "source_snapshot_hash": manifest["rows_hash"],
        "observed_at": manifest.get("observed_at"),
        "restricted_rows_hash": (manifest.get("restricted_artifact") or {}).get(
            "ciphertext_sha256"
        ),
    }


def _fi_document_key(row: JsonObject) -> tuple[str, str, str]:
    return (_text(row, "CompanyCode"), _text(row, "FiscalYear"), _text(row, "AccountingDocument"))


def _fi_item_key(row: JsonObject, leading_ledger: str) -> tuple[str, str, str, str, str]:
    return (
        _text(row, "CompanyCode"), _text(row, "Ledger") or leading_ledger,
        _text(row, "FiscalYear"), _text(row, "AccountingDocument"),
        _text(row, "AccountingDocumentItem"),
    )


def _tuple_predicate(fields: tuple[str, str, str], values: list[tuple[str, str, str]]) -> str:
    return " or ".join(
        "(" + " and ".join(
            f"{field} eq {_literal(value)}" for field, value in zip(fields, item)
        ) + ")"
        for item in values
    )


def _read_tuple_rows(
    profile: JsonObject,
    tuples: set[tuple[str, str, str]],
    artifacts: Path,
    prefix: str,
    *,
    fields: tuple[str, str, str] = ("CompanyCode", "FiscalYear", "AccountingDocument"),
) -> tuple[list[JsonObject], list[JsonObject]]:
    sources: list[JsonObject] = []
    rows: list[JsonObject] = []
    ordered = sorted(tuples)
    for offset in range(0, len(ordered), 10):
        group = ordered[offset:offset + 10]
        source, found = _odata_source(
            profile,
            _request(
                f"{prefix}_{offset // 10}",
                "API_OPLACCTGDOCITEMCUBE_SRV",
                "A_OperationalAcctgDocItemCube",
                FI_FIELDS,
                _tuple_predicate(fields, group),
                ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
            ),
            artifacts,
        )
        if fields == ("CompanyCode", "FiscalYear", "AccountingDocument"):
            returned = {_fi_document_key(row) for row in found}
        else:
            returned = {
                (_text(row, fields[0]), _text(row, fields[1]), _text(row, fields[2]))
                for row in found
            }
        if not returned.issubset(set(group)):
            raise ValueError("direct FI tuple response escaped the requested scope")
        sources.append(source)
        rows.extend(found)
    return sources, rows


def _row(row: JsonObject, name: str) -> Any:
    for key, value in row.items():
        if str(key).replace("_", "").casefold() == name.replace("_", "").casefold():
            return value
    return None


def _text(row: JsonObject, name: str) -> str:
    return str(_row(row, name) or "").strip()


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
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _posting_status(row: JsonObject, raw_status: str) -> str:
    completed = _text(row, "IsCompleted").upper()
    in_process = _text(row, "IsInProcess").upper()
    posting_error = _text(row, "PostingErrorStatus")
    lifecycle = _text(row, "BankStatementItemLifeCycSts")
    if completed not in {"", "X"} or in_process not in {"", "X"}:
        raise ValueError("direct bank source contains an unknown completion flag")
    if posting_error not in POSTING_ERROR or lifecycle not in LIFECYCLE:
        raise ValueError("direct bank source contains an unknown posting status")
    if POSTING_ERROR[posting_error] == "error" or raw_status in {"A", "E"}:
        return "posting_failed"
    if in_process == "X" or raw_status in {"P", "Q"} or LIFECYCLE[lifecycle] == "partially_applied":
        return "in_process"
    if completed == "X" or raw_status in {"8", "S", "R"} or LIFECYCLE[lifecycle] in {
        "completed", "completed_on_account", "completed_set_to_done"
    }:
        return "completed"
    if raw_status in {"", "0", "2", "7"} and LIFECYCLE[lifecycle] == "not_completed":
        return "not_completed"
    raise ValueError("direct bank source contains an unknown posting status")


def _bank_scope_proven(manifest: JsonObject, company: str, start: date, end: date) -> bool:
    spec = manifest.get("spec") if isinstance(manifest.get("spec"), dict) else {}
    filters = spec.get("filters") if isinstance(spec.get("filters"), list) else []
    predicates = [
        (
            str(item.get("field") or "").casefold(),
            str(item.get("operator") or ""),
            str(item.get("value") or ""),
        )
        for item in filters if isinstance(item, dict)
    ]
    return (
        ("companycode", "=", company) in predicates
        and ("debitcreditcode", "=", "H") in predicates
        and any(field == "valuedate" and operator == ">=" and value <= start.strftime("%Y%m%d")
                for field, operator, value in predicates)
        and any(field == "valuedate" and operator == "<=" and value >= end.strftime("%Y%m%d")
                for field, operator, value in predicates)
    )


def build(
    case_path: Path,
    bank_rows_path: Path | None,
    source_manifest_path: Path | None,
    output: Path,
    artifacts: Path,
    *,
    bank_snapshot: Path | None = None,
    profile_path: Path | None = None,
    reference_supplied: bool = False,
    reference_value: str | None = None,
) -> JsonObject:
    case = _load_object(case_path)
    if case.get("schema_version") != "2.0" or case.get("agent_id") != "ar-cash-application":
        raise ValueError("case must be an ar-cash-application CanonicalTestCase v2")
    values = case.get("input") if isinstance(case.get("input"), dict) else {}
    company = str(values.get("company_code") or "").strip()
    start = date.fromisoformat(str(values.get("date_from") or ""))
    end = date.fromisoformat(str(values.get("date_to") or ""))
    if not company or start > end or (end - start).days > 31:
        raise ValueError("cash-application case has an invalid company or date range")
    if (bank_snapshot is None) == (bank_rows_path is None or source_manifest_path is None):
        raise ValueError("provide either an encrypted bank snapshot or the legacy row/manifest pair")
    if reference_value is not None:
        reference_value = reference_value.strip()
        if not reference_value:
            raise ValueError("receipt reference cannot be blank")
        reference_supplied = True
    if bank_snapshot is not None:
        source_manifest, raw_rows = _adt_snapshot(bank_snapshot)
        if not _bank_scope_proven(source_manifest, company, start, end):
            raise ValueError("direct bank snapshot does not prove the requested scope")
        bank_source = _adt_source(source_manifest, bank_snapshot)
    else:
        assert bank_rows_path is not None and source_manifest_path is not None
        raw_rows = _load_rows(bank_rows_path)
        source_manifest = _load_object(source_manifest_path)
        required_manifest = {
            "object", "access_method", "http_method", "semantic_read_only",
            "schema_hash", "query_hash", "stable_order_by", "paging_complete",
            "source_complete", "scope", "raw_sha256",
        }
        if set(source_manifest) != required_manifest:
            raise ValueError("direct bank source manifest has unexpected or missing fields")
        if (
            source_manifest.get("object") != "I_ArBankStatementItem"
            or source_manifest.get("access_method") != "adt_data_preview"
            or source_manifest.get("http_method") != "POST"
            or source_manifest.get("semantic_read_only") is not True
            or source_manifest.get("paging_complete") is not True
            or source_manifest.get("source_complete") is not True
        ):
            raise ValueError("direct bank source manifest is not a complete semantic read-only snapshot")
        for field in ("schema_hash", "query_hash", "raw_sha256"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(source_manifest.get(field) or "")):
                raise ValueError(f"direct bank source manifest {field} is invalid")
        actual_raw_hash = "sha256:" + hashlib.sha256(bank_rows_path.read_bytes()).hexdigest()
        if source_manifest["raw_sha256"] != actual_raw_hash:
            raise ValueError("direct bank source rows do not match their frozen manifest")
        scope = source_manifest.get("scope") if isinstance(source_manifest.get("scope"), dict) else {}
        if {
            "company_code": company,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "receipt_reference_supplied": reference_supplied,
        } != scope:
            raise ValueError("direct bank source scope does not match the CanonicalTestCase")
        bank_source = {
            "source_id": "bank_receipts_adt",
            "object": "I_ArBankStatementItem",
            "access_method": "adt_data_preview",
            "http_method": "POST",
            "semantic_read_only": True,
            "schema_hash": source_manifest["schema_hash"],
            "query_hash": source_manifest["query_hash"],
            "stable_order_by": source_manifest["stable_order_by"],
            "paging_complete": True,
            "source_complete": True,
            "row_count": 0,
            "page_count": 1,
            "primary": True,
            "source_snapshot_hash": actual_raw_hash,
        }
    required_keys = {
        "COMPANYCODE", "BANKSTATEMENTSHORTID", "BANKSTATEMENTITEM",
        "VALUEDATE", "AMOUNTINTRANSACTIONCURRENCY", "TRANSACTIONCURRENCY",
        "DEBITCREDITCODE", "BANKSTATEMENTSTATUS", "ISCOMPLETED", "ISINPROCESS",
        "POSTINGERRORSTATUS", "BANKSTATEMENTITEMLIFECYCSTS",
    }
    observed_schema = {str(key).upper() for row in raw_rows for key in row}
    if raw_rows and not required_keys.issubset(observed_schema):
        raise ValueError("direct bank source is missing required ADT fields")
    period_rows = []
    for row in raw_rows:
        value_date = _date(_row(row, "ValueDate"))
        if _text(row, "CompanyCode") != company or value_date is None:
            continue
        if (
            start <= value_date <= end
            and _text(row, "DebitCreditCode") == "H"
        ):
            period_rows.append(row)
    scoped = [
        row for row in period_rows
        if reference_value is None or _text(row, "BankReference") == reference_value
    ]
    keys = [
        (_text(row, "CompanyCode"), _text(row, "BankStatementShortID"), _text(row, "BankStatementItem"))
        for row in period_rows
    ]
    if any(not all(key) for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("direct bank source has an invalid or duplicate stable business key")
    period_rows.sort(key=lambda row: (
        _text(row, "CompanyCode"),
        _text(row, "BankStatementShortID"),
        _text(row, "BankStatementItem"),
    ))
    scoped.sort(key=lambda row: (
        _text(row, "CompanyCode"),
        _text(row, "BankStatementShortID"),
        _text(row, "BankStatementItem"),
    ))
    artifacts.mkdir(parents=True, exist_ok=True)
    bank_snapshot_target = artifacts / "bank_raw"
    encrypted = write_encrypted_rows(bank_snapshot_target, period_rows)
    bank_source["row_count"] = len(period_rows)
    bank_source["restricted_rows_hash"] = encrypted["ciphertext_sha256"]
    if bank_snapshot is not None:
        # The discovery snapshot can cover years of data. Materialize a
        # case-scoped encrypted source and replay contract so before/after
        # anchors compare the exact period without preserving a sensitive
        # receipt reference in metadata.
        original_spec = source_manifest.get("spec") if isinstance(source_manifest.get("spec"), dict) else {}
        exact_spec = {
            "object": "I_ARBANKSTATEMENTITEM",
            "fields": list(original_spec.get("fields") or []),
            "filters": [
                {"field": "CompanyCode", "operator": "=", "value": company},
                {"field": "DebitCreditCode", "operator": "=", "value": "H"},
                {"field": "ValueDate", "operator": ">=", "value": start.strftime("%Y%m%d")},
                {"field": "ValueDate", "operator": "<=", "value": end.strftime("%Y%m%d")},
            ],
            "row_limit": max(100, len(period_rows) + 1),
        }
        query_hash = canonical_hash(exact_spec)
        rows_hash = canonical_hash(period_rows)
        source_id = "adt_i_arbankstatementitem_" + query_hash[7:19]
        scoped_manifest = {
            "source_id": source_id,
            "object": "I_ARBANKSTATEMENTITEM",
            "access_method": "adt_data_preview",
            "http_method": "POST",
            "semantic_read_only": True,
            "endpoint": "/sap/bc/adt/datapreview/freestyle",
            "query_hash": query_hash,
            "stable_order_by": source_manifest["stable_order_by"],
            "source_complete": True,
            "paging_complete": True,
            "row_count": len(period_rows),
            "total_rows": len(period_rows),
            "observed_at": source_manifest.get("observed_at"),
            "restricted_artifact": encrypted,
            "metadata_path": source_manifest.get("metadata_path"),
            "metadata_sha256": source_manifest["metadata_sha256"],
            "schema_hash": source_manifest["schema_hash"],
            "rows_hash": rows_hash,
            "result_columns_hash": source_manifest.get("result_columns_hash"),
            "spec": exact_spec,
        }
        (bank_snapshot_target / "manifest.json").write_text(
            json.dumps(scoped_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        source_metadata = bank_snapshot / "metadata.source"
        if source_metadata.is_file():
            shutil.copyfile(source_metadata, bank_snapshot_target / "metadata.source")
        bank_source.update(
            {
                "source_id": source_id,
                "query_hash": query_hash,
                "source_snapshot_ref": str(bank_snapshot_target.resolve()),
                "source_snapshot_hash": rows_hash,
            }
        )

    sources: list[JsonObject] = [bank_source]
    profile = _load_object(profile_path) if profile_path is not None else None
    leading_ledger = ""
    payment_rows: list[JsonObject] = []
    direct_invoice_rows: list[JsonObject] = []
    subsequent_clearing_rows: list[JsonObject] = []
    subsequent_invoice_rows: list[JsonObject] = []
    # Expand every syntactically valid subledger-document reference so the
    # independent baseline can preserve confirmed FI identity facts even when
    # the bank item itself is not eligible for cash-application assessment.
    # Eligibility is applied below when deriving cash_application_status; it
    # must not silently narrow the evidence snapshot.
    referenced_document_tuples = {
        (company, _text(row, "FiscalYear"), _text(row, "SubledgerDocument"))
        for row in scoped
        if re.fullmatch(r"\d{1,10}", _text(row, "SubledgerDocument"))
        and re.fullmatch(r"(?!0000)\d{4}", _text(row, "FiscalYear"))
    }
    if referenced_document_tuples:
        if profile is None:
            raise ValueError("nonzero bank baseline requires an independent OData profile for FI expansion")
        ledger_source, ledger_rows = _odata_source(
            profile,
            _request(
                "cash_leading_ledger", "API_LEDGER_SRV", "A_Ledger",
                ["Ledger", "IsLeadingLedger", "LedgerApplication", "LedgerSubApplication"],
                "IsLeadingLedger eq true", ["Ledger"], max_rows=100,
            ),
            artifacts,
        )
        sources.append(ledger_source)
        ledgers = {_text(row, "Ledger") for row in ledger_rows if _truthy(_row(row, "IsLeadingLedger"))}
        ledgers.discard("")
        if len(ledgers) != 1:
            raise ValueError("direct baseline could not resolve exactly one leading ledger")
        leading_ledger = next(iter(ledgers))
        payment_sources, payment_rows = _read_tuple_rows(
            profile, referenced_document_tuples, artifacts, "cash_payment_documents"
        )
        sources.extend(payment_sources)
        direct_tuples = {(company_code, fiscal_year, document)
                         for company_code, fiscal_year, document in referenced_document_tuples}
        direct_sources, direct_invoice_rows = _read_tuple_rows(
            profile,
            direct_tuples,
            artifacts,
            "cash_direct_invoices",
            fields=("CompanyCode", "ClearingDocFiscalYear", "ClearingAccountingDocument"),
        )
        sources.extend(direct_sources)
        subsequent_refs = {
            (_text(row, "CompanyCode"), _text(row, "ClearingDocFiscalYear"),
             _text(row, "ClearingAccountingDocument"))
            for row in payment_rows
            if _text(row, "ClearingAccountingDocument")
            and _text(row, "ClearingDocFiscalYear")
        }
        if subsequent_refs:
            # A self-reference is a business ambiguity for the affected receipt,
            # not a transport failure for the complete source snapshot. Keep it
            # for per-record classification but do not query the same document
            # as a second-hop clearing document.
            queryable_subsequent_refs = subsequent_refs - referenced_document_tuples
        else:
            queryable_subsequent_refs = set()
        if queryable_subsequent_refs:
            clearing_sources, subsequent_clearing_rows = _read_tuple_rows(
                profile, queryable_subsequent_refs, artifacts, "cash_subsequent_clearing"
            )
            sources.extend(clearing_sources)
            invoice_sources, subsequent_invoice_rows = _read_tuple_rows(
                profile,
                queryable_subsequent_refs,
                artifacts,
                "cash_subsequent_invoices",
                fields=("CompanyCode", "ClearingDocFiscalYear", "ClearingAccountingDocument"),
            )
            sources.extend(invoice_sources)

    fi_rows = [*payment_rows, *direct_invoice_rows, *subsequent_clearing_rows, *subsequent_invoice_rows]
    fi_by_key: dict[tuple[str, str, str, str, str], JsonObject] = {}
    for row in fi_rows:
        row_ledger = _text(row, "Ledger") or leading_ledger
        if leading_ledger and row_ledger != leading_ledger:
            raise ValueError("direct FI response contains a non-leading ledger row")
        key = _fi_item_key(row, leading_ledger)
        if not all(key):
            raise ValueError("direct FI response contains an incomplete business key")
        normalized_row = {**row, "Ledger": row_ledger}
        if key in fi_by_key and fi_by_key[key] != normalized_row:
            raise ValueError("direct FI response contains a conflicting business key")
        fi_by_key[key] = normalized_row

    records: list[JsonObject] = []
    gaps: set[str] = set()
    for row in scoped:
        raw_status = _text(row, "BankStatementStatus")
        if raw_status not in REVERSAL:
            gaps.add("bank_status_mapping_unknown")
            continue
        amount = _decimal(_row(row, "AmountInTransactionCurrency"))
        currency = _text(row, "TransactionCurrency")
        value_date = _date(_row(row, "ValueDate"))
        if amount is None or not currency or value_date is None:
            gaps.add("bank_required_field_missing")
            continue
        posting = _posting_status(row, raw_status)
        reversal = REVERSAL[raw_status]
        related_document = _text(row, "SubledgerDocument")
        fiscal_year = _text(row, "FiscalYear")
        valid_document = bool(
            re.fullmatch(r"\d{1,10}", related_document)
            and re.fullmatch(r"(?!0000)\d{4}", fiscal_year)
        )
        active = posting == "completed" and reversal == "not_reversed"
        matching = [item for item in payment_rows if _fi_document_key(item) == (company, fiscal_year, related_document)]
        customer_lines = [
            item for item in matching
            if _text(item, "FinancialAccountType") == "D" and _text(item, "Customer")
        ]
        customers = {_text(item, "Customer") for item in customer_lines}
        special = [item for item in customer_lines if _text(item, "SpecialGLCode")]
        ordinary = [item for item in customer_lines if not _text(item, "SpecialGLCode")]
        direct_invoices = [
            item for item in direct_invoice_rows
            if _text(item, "CompanyCode") == company
            and _text(item, "ClearingDocFiscalYear") == fiscal_year
            and _text(item, "ClearingAccountingDocument") == related_document
            and _text(item, "FinancialAccountType") == "D"
            and not _text(item, "SpecialGLCode")
        ]
        later_refs = {
            (_text(item, "CompanyCode"), _text(item, "ClearingDocFiscalYear"),
             _text(item, "ClearingAccountingDocument"))
            for item in matching
            if _text(item, "ClearingAccountingDocument")
        }
        relationship_ambiguous = any(not year or not document for _company, year, document in later_refs)
        later_refs = {item for item in later_refs if item[1] and item[2]}
        if (company, fiscal_year, related_document) in later_refs:
            relationship_ambiguous = True
        returned_clearing = {_fi_document_key(item) for item in subsequent_clearing_rows}
        if not later_refs.issubset(returned_clearing):
            relationship_ambiguous = True
        multihop_invoices = [
            item for item in subsequent_invoice_rows
            if (
                _text(item, "CompanyCode"), _text(item, "ClearingDocFiscalYear"),
                _text(item, "ClearingAccountingDocument")
            ) in later_refs
            and _text(item, "FinancialAccountType") == "D"
            and not _text(item, "SpecialGLCode")
        ]
        confirmed_invoices = [*direct_invoices, *multihop_invoices]
        if customers and any(_text(item, "Customer") not in customers for item in confirmed_invoices):
            relationship_ambiguous = True
        if not active:
            cash_status = "not_assessed"
            business = "attention"
            reason = reversal if reversal != "not_reversed" else posting
        elif not valid_document or not matching or not customer_lines:
            cash_status = "pending"
            business = "attention"
            reason = "customer_subledger_document_missing"
        elif len(customer_lines) != 1 or len(customers) != 1 or relationship_ambiguous:
            cash_status = "ambiguous"
            business = "attention"
            reason = "customer_subledger_line_not_unique" if len(customer_lines) != 1 else "clearing_relationship_ambiguous"
        elif not ordinary and special:
            cash_status = "pending"
            business = "attention"
            reason = "special_gl_only"
        elif confirmed_invoices:
            cash_status = "confirmed"
            business = "normal"
            reason = "sap_clearing_relationship_confirmed"
        else:
            receipt_amount = abs(amount)
            candidate_rows = [
                item for item in fi_by_key.values()
                if _text(item, "FinancialAccountType") == "D"
                and not _text(item, "SpecialGLCode")
                and _text(item, "Customer") in customers
                and _fi_document_key(item) != (company, fiscal_year, related_document)
                and _text(item, "TransactionCurrency") == currency
                and abs(_decimal(_row(item, "AmountInTransactionCurrency")) or Decimal(0)) == receipt_amount
                and (_date(_row(item, "PostingDate")) or date.max) <= (value_date or date.min)
            ]
            cash_status = "candidate" if len(candidate_rows) == 1 else "ambiguous" if len(candidate_rows) > 1 else "not_found"
            business = "attention"
            reason = "unique_amount_candidate" if len(candidate_rows) == 1 else "multiple_candidates" if candidate_rows else "no_relationship_or_candidate"
        records.append(
            {
                "company_code": company,
                "statement_id": _text(row, "BankStatementShortID"),
                "statement_item": _text(row, "BankStatementItem"),
                "value_date": value_date.isoformat(),
                "amount": format(amount, "f"),
                "currency": currency,
                "posting_status": posting,
                "reversal_status": reversal,
                "subledger_document": related_document if valid_document else None,
                "fiscal_year": fiscal_year if valid_document else None,
                "customer": next(iter(customers), None) if len(customers) == 1 else None,
                "cash_application_status": cash_status,
                "business_status": business,
                "reason_code": reason,
                "confirmed_invoice_count": len(confirmed_invoices),
                "special_gl_item_count": len(special),
            }
        )
    source_complete = all(source.get("source_complete") is True for source in sources)
    evidence_complete = source_complete and not gaps
    if not records:
        business_status = "attention" if reference_supplied else "normal"
    elif gaps or any(item["business_status"] == "inconclusive" for item in records):
        business_status = "inconclusive"
    elif any(item["business_status"] == "attention" for item in records):
        business_status = "attention"
    else:
        business_status = "normal"
    status_set = {str(item["cash_application_status"]) for item in records}
    aggregate_cash_status = next(
        (status for status in (
            "unknown", "ambiguous", "pending", "candidate", "not_found", "not_assessed", "confirmed"
        ) if status in status_set),
        "not_assessed" if business_status != "inconclusive" else "unknown",
    )
    normalized = {
        "records": records,
        "metrics": {
            "source_receipt_count": len(scoped),
            "materialized_receipt_count": len(records),
            "unresolved_receipt_count": sum(
                item["cash_application_status"] != "confirmed" for item in records
            ),
            "confirmed_receipt_count": sum(
                item["cash_application_status"] == "confirmed" for item in records
            ),
            "attention_receipt_count": sum(
                item["business_status"] == "attention" for item in records
            ),
            "inconclusive_receipt_count": sum(
                item["business_status"] == "inconclusive" for item in records
            ),
        },
        "business_status": business_status,
        "receipt_search_status": "found" if records else "not_found",
        "cash_application_status": aggregate_cash_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": evidence_complete,
        "evidence_gap_codes": sorted(gaps),
        "limitations": ["fi_clearing_is_not_bank_settlement"],
    }
    baseline = {
        "schema_version": "3.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "sources": sources,
        "normalized_result": normalized,
        "result_hash": canonical_hash(normalized),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an independent encrypted ADT bank-receipt baseline."
    )
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--bank-rows", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--bank-snapshot", type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex/secure/sap-direct-readonly.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--reference-supplied", action="store_true")
    parser.add_argument(
        "--sensitive-input-stdin",
        action="store_true",
        help="Read receipt_reference from a JSON object on stdin without exposing it in argv.",
    )
    args = parser.parse_args()
    reference_value = _read_sensitive_reference_from_stdin(args.sensitive_input_stdin)
    if args.reference_supplied and reference_value is not None:
        raise ValueError("use either --reference-supplied or --sensitive-input-stdin")
    result = build(
        args.case.resolve(),
        args.bank_rows.resolve() if args.bank_rows else None,
        args.source_manifest.resolve() if args.source_manifest else None,
        args.output.resolve(),
        args.artifacts.resolve(),
        bank_snapshot=args.bank_snapshot.resolve() if args.bank_snapshot else None,
        profile_path=args.profile.resolve() if args.profile and args.profile.is_file() else None,
        reference_supplied=args.reference_supplied or reference_value is not None,
        reference_value=reference_value,
    )
    normalized = result["normalized_result"]
    print(
        json.dumps(
            {
                "record_count": len(normalized["records"]),
                "business_status": normalized["business_status"],
                "source_complete": normalized["source_complete"],
                "evidence_complete": normalized["evidence_complete"],
                "result_hash": result["result_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if normalized["evidence_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
