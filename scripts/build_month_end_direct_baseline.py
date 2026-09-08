from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from scripts import direct_sap_read
except ImportError:  # pragma: no cover - direct script execution
    import direct_sap_read


JsonObject = dict[str, Any]
ROOT = Path(__file__).resolve().parents[1]


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _text(row: JsonObject, field: str) -> str:
    return str(row.get(field) or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "x", "yes"}


def _request(
    source_id: str,
    service_name: str,
    entity_set: str,
    select_fields: list[str],
    filter_expression: str,
    order_by: list[str],
    *,
    max_rows: int = 30_000,
) -> JsonObject:
    return {
        "source_id": source_id,
        "service_name": service_name,
        "service_path": f"/sap/opu/odata/sap/{service_name}",
        "odata_version": "2.0",
        "entity_set": entity_set,
        "select_fields": select_fields,
        "filter": filter_expression,
        "order_by": order_by,
        "page_size": min(max_rows, 5_000),
        "max_rows": max_rows,
    }


def _odata_source(
    profile: JsonObject,
    request: JsonObject,
    artifact_root: Path,
    *,
    primary: bool = False,
) -> tuple[JsonObject, list[JsonObject]]:
    output = artifact_root / str(request["source_id"])
    manifest = direct_sap_read.run(profile, request, output, encrypt_rows=True)
    if manifest.get("source_complete") is not True or manifest.get("paging_complete") is not True:
        raise RuntimeError(f"direct OData source {request['source_id']} is incomplete")
    rows = direct_sap_read.read_encrypted_rows(output)
    source = {
        **manifest,
        "access_method": "odata_get",
        "http_method": "GET",
        "semantic_read_only": True,
        "primary": primary,
    }
    return source, rows


def _odata_expected_empty_source(
    profile: JsonObject,
    request: JsonObject,
    artifact_root: Path,
) -> tuple[JsonObject, list[JsonObject]]:
    """Prove a source is empty when its analytical synthetic key is not queryable.

    API_GLACCOUNTLINEITEM advertises ``ID`` as its EDM key but rejects selecting or
    ordering by that synthetic property. The reviewed composite order is sufficient
    for an exhausted zero-row page because no record boundary can be unstable.
    """

    request = direct_sap_read._validate_request(request)
    base, client, get = direct_sap_read._client(profile)
    service_root = base + str(request["service_path"])
    metadata_url = service_root + "/$metadata?" + urllib.parse.urlencode({"sap-client": client})
    metadata = get(metadata_url, accept="application/xml")
    fields, _stable_keys = direct_sap_read._schema(metadata, str(request["entity_set"]))
    selected = [str(item) for item in request["select_fields"]]
    ordered = [str(item) for item in request["order_by"]]
    missing = sorted((set(selected) | set(ordered)) - set(fields))
    if missing:
        raise RuntimeError(f"live metadata is missing expected fields: {missing}")
    params = {
        "$format": "json",
        "$select": ",".join(selected),
        "$filter": request["filter"],
        "$orderby": ",".join(f"{field} asc" for field in ordered),
        "$top": "1",
        "sap-client": client,
    }
    url = service_root + "/" + str(request["entity_set"]) + "?" + urllib.parse.urlencode(params)
    payload = json.loads(get(url))
    container = payload.get("d") or {}
    rows = [dict(item) for item in container.get("results") or [] if isinstance(item, dict)]
    if rows or container.get("__next"):
        raise RuntimeError(
            f"direct OData source {request['source_id']} was expected to be empty but returned data"
        )
    output = artifact_root / str(request["source_id"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "metadata.edmx").write_bytes(metadata)
    restricted = direct_sap_read.write_encrypted_rows(output, rows)
    manifest = {
        "source_id": request["source_id"],
        "service_name": request["service_name"],
        "odata_version": request["odata_version"],
        "entity_set": request["entity_set"],
        "access_method": "odata_get",
        "http_method": "GET",
        "semantic_read_only": True,
        "schema_hash": direct_sap_read._hash_bytes(metadata),
        "query_hash": _hash({key: request[key] for key in request if key != "source_id"}),
        "stable_order_by": ordered,
        "paging_complete": True,
        "source_complete": True,
        "row_count": 0,
        "page_count": 1,
        "primary": False,
        "restricted_artifact": restricted,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest, rows


def _load_adt_module(skillhub_root: Path):
    module_path = (
        skillhub_root
        / "skills"
        / "Common"
        / "sap-adt-table-export"
        / "scripts"
        / "adt_table_export.py"
    )
    module_name = "sapba_direct_adt_table_export"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sap-adt-table-export")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _adt_source(
    module: Any,
    task: JsonObject,
    artifact_root: Path,
    *,
    primary: bool = False,
) -> tuple[JsonObject, list[JsonObject]]:
    profiles, internal_values = module.load_internal_configuration()
    result = module.execute(task, profiles, internal_values=internal_values)
    if (
        result.get("status") != "complete"
        or result.get("read_only") is not True
        or result.get("validated") is not True
        or (result.get("completeness") or {}).get("source_complete") is not True
        or (result.get("completeness") or {}).get("paging_complete") is not True
        or result.get("validation_issues")
    ):
        codes = [str(item.get("code") or "") for item in result.get("validation_issues") or []]
        raise RuntimeError(f"direct ADT source {task['object']} is incomplete: {codes}")
    rows = [dict(row) for row in result.get("rows") or []]
    output = artifact_root / f"adt-{str(task['object']).lower()}"
    restricted = direct_sap_read.write_encrypted_rows(output, rows)
    metadata_hashes = sorted(
        str(item.get("sha256") or "")
        for item in result.get("artifacts") or []
        if item.get("type") in {"metadata", "structure_metadata", "data_element_metadata"}
    )
    generated_query_hashes = sorted(
        str(item.get("sha256") or "")
        for item in result.get("artifacts") or []
        if item.get("type") == "generated_query"
    )
    scope = result.get("scope") or {}
    source = {
        "source_id": f"adt_{str(task['object']).lower()}",
        "provider": "sap-adt-table-export",
        "object": str(task["object"]),
        "fields": list(task["fields"]),
        "access_method": "adt_data_preview",
        "http_method": "POST",
        "semantic_read_only": True,
        "schema_hash": _hash(metadata_hashes),
        "query_hash": _hash({"scope": scope, "generated_query_hashes": generated_query_hashes}),
        "stable_order_by": [str(item.get("field") or "") for item in scope.get("order_by") or []],
        "paging_complete": True,
        "source_complete": True,
        "row_count": len(rows),
        "primary": primary,
        "restricted_artifact": restricted,
        "observed_at": result.get("completed_at"),
    }
    if not source["stable_order_by"]:
        raise RuntimeError(f"direct ADT source {task['object']} has no stable order")
    (output / "manifest.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return source, rows


def _period_range_covers(row: JsonObject, fiscal_year: int, period: int, suffix: str) -> bool:
    try:
        start_year = int(_text(row, f"FRYE{suffix}"))
        start_period = int(_text(row, f"FRPE{suffix}"))
        end_year = int(_text(row, f"TOYE{suffix}"))
        end_period = int(_text(row, f"TOPE{suffix}"))
    except ValueError:
        return False
    return (start_year, start_period) <= (fiscal_year, period) <= (end_year, end_period)


def _depreciation_complete(rows: list[JsonObject], fiscal_year: int, period: int) -> bool:
    for row in rows:
        try:
            year = int(_text(row, "AFBLGJ"))
            completed_period = int(_text(row, "AFBLPE"))
        except ValueError:
            continue
        if year > fiscal_year or (year == fiscal_year and completed_period >= period):
            return True
    return False


def _mm_period_complete(rows: list[JsonObject], fiscal_year: int, period: int) -> bool:
    for row in rows:
        try:
            year = int(_text(row, "LFGJA"))
            current_period = int(_text(row, "LFMON"))
        except ValueError:
            continue
        if (year, current_period) > (fiscal_year, period):
            return True
    return False


def build(
    case_path: Path,
    output: Path,
    artifacts: Path,
    profile_path: Path,
    skillhub_root: Path,
) -> JsonObject:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    values = case.get("input") or {}
    company = str(values.get("company_code") or "").strip()
    fiscal_year = int(values.get("fiscal_year"))
    period = int(values.get("period"))
    as_of = date.fromisoformat(str(values.get("as_of") or ""))
    ledger = str(values.get("ledger") or "").strip()
    profile_id = str(values.get("profile_id") or "").strip()
    if (company, fiscal_year, period, as_of.isoformat(), ledger, profile_id) != (
        "1010",
        2026,
        9,
        "2026-09-08",
        "0L",
        "test-default-1010",
    ):
        raise ValueError("this canonical baseline is restricted to test-default-1010 / 2026 period 9")

    profile = direct_sap_read._load_object(profile_path.resolve())
    profile["timeout_ms"] = max(180_000, int(profile.get("timeout_ms") or 0))
    artifacts.mkdir(parents=True, exist_ok=True)
    sources: list[JsonObject] = []

    company_source, company_rows = _odata_source(
        profile,
        _request(
            "month_end_company",
            "API_COMPANYCODE_SRV",
            "A_CompanyCode",
            ["CompanyCode", "CompanyCodeName", "Currency", "FiscalYearVariant", "ControllingArea"],
            "CompanyCode eq '1010'",
            ["CompanyCode"],
            max_rows=10,
        ),
        artifacts,
        primary=True,
    )
    sources.append(company_source)
    company_matches = [row for row in company_rows if _text(row, "CompanyCode") == company]
    if not company_matches or any(
        (_text(row, "Currency"), _text(row, "FiscalYearVariant"), _text(row, "ControllingArea"))
        != ("EUR", "K4", "A000")
        for row in company_matches
    ):
        raise RuntimeError("live company metadata does not match the validated profile identity")

    ledger_source, ledger_rows = _odata_source(
        profile,
        _request(
            "month_end_ledgers",
            "API_LEDGER_SRV",
            "A_Ledger",
            ["Ledger", "IsLeadingLedger", "LedgerApplication", "LedgerSubApplication"],
            "Ledger ne ''",
            ["Ledger"],
            max_rows=100,
        ),
        artifacts,
    )
    sources.append(ledger_source)
    leading = {_text(row, "Ledger") for row in ledger_rows if _truthy(row.get("IsLeadingLedger"))}
    leading.discard("")
    if leading != {ledger}:
        raise RuntimeError(f"live ledger metadata did not resolve exactly {ledger}")

    period_start = date(fiscal_year, period, 1)
    gl_fields = [
        "Ledger", "CompanyCode", "FiscalYear", "FiscalPeriod", "AccountingDocument",
        "AccountingDocumentItem", "LedgerGLLineItem", "AccountingDocumentType", "PostingDate",
        "ClearingDate", "ClearingAccountingDocument", "Supplier", "Customer",
        "FinancialAccountType", "GLAccount", "IsOpenItemManaged", "DebitCreditCode",
        "AssignmentReference", "PurchasingDocument", "PurchasingDocumentItem", "CostCenter",
        "OrderID", "PartnerCostCenter", "DepreciationFiscalPeriod",
        "JrnlPeriodEndClosingRunLogUUID", "AmountInCompanyCodeCurrency", "CompanyCodeCurrency",
    ]
    gl_order = [
        "Ledger", "CompanyCode", "FiscalYear", "AccountingDocument",
        "AccountingDocumentItem", "LedgerGLLineItem",
    ]
    gl_source, gl_rows = _odata_expected_empty_source(
        profile,
        _request(
            "month_end_gl_items",
            "API_GLACCOUNTLINEITEM",
            "GLAccountLineItem",
            gl_fields,
            (
                "CompanyCode eq '1010' and Ledger eq '0L' and FiscalYear eq '2026' "
                "and FiscalPeriod eq '9' and "
                f"PostingDate ge datetime'{period_start.isoformat()}T00:00:00' and "
                f"PostingDate le datetime'{as_of.isoformat()}T23:59:59'"
            ),
            gl_order,
        ),
        artifacts,
    )
    sources.append(gl_source)

    due_fields = [
        "Ledger", "CompanyCode", "FiscalYear", "FiscalPeriod", "AccountingDocument",
        "AccountingDocumentItem", "AccountingDocumentType", "PostingDate", "NetDueDate",
        "ClearingDate", "IsCleared", "Supplier", "PaymentBlockingReason", "GLAccount",
        "DebitCreditCode", "AmountInCompanyCodeCurrency", "CompanyCodeCurrency",
    ]
    due_source, due_rows = _odata_source(
        profile,
        _request(
            "month_end_due_items",
            "API_OPLACCTGDOCITEMCUBE_SRV",
            "A_OperationalAcctgDocItemCube",
            due_fields,
            (
                "CompanyCode eq '1010' and Ledger eq '0L' and FinancialAccountType eq 'K' and "
                f"PostingDate ge datetime'1900-01-01T00:00:00' and "
                f"PostingDate le datetime'{as_of.isoformat()}T23:59:59'"
            ),
            ["Ledger", "CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
        ),
        artifacts,
    )
    sources.append(due_source)

    billing_source, billing_rows = _odata_source(
        profile,
        _request(
            "month_end_billing_documents",
            "API_BILLING_DOCUMENT_SRV",
            "A_BillingDocument",
            [
                "BillingDocument", "CompanyCode", "FiscalYear", "BillingDocumentDate",
                "BillingDocumentIsCancelled", "CancelledBillingDocument", "AccountingDocument",
                "AccountingPostingStatus", "AccountingTransferStatus", "TransactionCurrency",
                "TotalNetAmount",
            ],
            (
                "CompanyCode eq '1010' and "
                f"BillingDocumentDate ge datetime'{period_start.isoformat()}T00:00:00' and "
                f"BillingDocumentDate le datetime'{as_of.isoformat()}T23:59:59'"
            ),
            ["BillingDocument"],
        ),
        artifacts,
    )
    sources.append(billing_source)

    grir_source, grir_rows = _odata_expected_empty_source(
        profile,
        _request(
            "month_end_grir_seed",
            "API_GLACCOUNTLINEITEM",
            "GLAccountLineItem",
            [
                "Ledger", "CompanyCode", "FiscalYear", "AccountingDocument",
                "AccountingDocumentItem", "LedgerGLLineItem", "GLAccount",
                "PurchasingDocument", "PurchasingDocumentItem", "PostingDate",
                "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "DebitCreditCode",
            ],
            (
                "CompanyCode eq '1010' and Ledger eq '0L' and GLAccount eq '19110000' and "
                f"PostingDate ge datetime'{period_start.isoformat()}T00:00:00' and "
                f"PostingDate le datetime'{as_of.isoformat()}T23:59:59'"
            ),
            gl_order,
        ),
        artifacts,
    )
    sources.append(grir_source)
    if gl_rows or due_rows or billing_rows or grir_rows:
        raise RuntimeError(
            "the canonical direct evaluator is intentionally fail-closed for non-empty operational evidence"
        )

    adt = _load_adt_module(skillhub_root.resolve())
    adt_tasks = [
        {
            "schema_version": 1,
            "source_type": "table",
            "object": "TABA",
            "fields": ["BUKRS", "AFBLGJ", "AFBLPE"],
            "filters": [{"field": "BUKRS", "operator": "eq", "value": company}],
            "max_rows": 200,
        },
        {
            "schema_version": 1,
            "source_type": "table",
            "object": "T001",
            "fields": ["BUKRS", "OPVAR"],
            "filters": [{"field": "BUKRS", "operator": "eq", "value": company}],
            "max_rows": 2,
        },
    ]
    adt_rows: dict[str, list[JsonObject]] = {}
    for task in adt_tasks:
        source, rows = _adt_source(adt, task, artifacts)
        sources.append(source)
        adt_rows[str(task["object"])] = rows
    period_variants = {_text(row, "OPVAR") for row in adt_rows["T001"]}
    period_variants.discard("")
    if period_variants != {"1010"}:
        raise RuntimeError("T001 did not resolve the expected posting-period variant")

    for task in (
        {
            "schema_version": 1,
            "source_type": "table",
            "object": "T001B",
            "fields": [
                "BUKRS", "MKOAR", "FRYE1", "FRPE1", "TOYE1", "TOPE1",
                "FRYE2", "FRPE2", "TOYE2", "TOPE2",
            ],
            "filters": [{"field": "BUKRS", "operator": "in", "values": sorted(period_variants)}],
            "max_rows": 200,
        },
        {
            "schema_version": 1,
            "source_type": "table",
            "object": "MARV",
            "fields": ["BUKRS", "LFGJA", "LFMON"],
            "filters": [{"field": "BUKRS", "operator": "eq", "value": company}],
            "max_rows": 10,
        },
    ):
        source, rows = _adt_source(adt, task, artifacts)
        sources.append(source)
        adt_rows[str(task["object"])] = rows

    depreciation_complete = _depreciation_complete(adt_rows["TABA"], fiscal_year, period)
    fi_period_interpretable = any(
        _period_range_covers(row, fiscal_year, period, "1")
        or _period_range_covers(row, fiscal_year, period, "2")
        for row in adt_rows["T001B"]
    )
    if not fi_period_interpretable:
        raise RuntimeError("T001B did not provide an interpretable current period range")
    mm_complete = _mm_period_complete(adt_rows["MARV"], fiscal_year, period)

    statuses = [
        "passed",  # AP_OVERDUE_ITEMS
        "passed",  # AR_UNAPPLIED_RECEIPTS
        "passed",  # GL_UNRECONCILED_ITEMS
        "passed",  # MM_GRIR_AGED_ITEMS
        "passed",  # MM_GRIR_ADJUSTMENTS_PENDING
        "passed" if depreciation_complete else "attention",
        "not_assessed",  # GL_FX_VALUATION_PENDING
        "passed",  # GL_AUTO_CLEARING_PENDING
        "passed",  # GL_PERIOD_CONTROL_ISSUE: current period is not yet at period end
        "passed" if mm_complete else "attention",
        "passed",  # CO_UNALLOCATED_COSTS
        "passed",  # SD_BILLING_TRANSFER_ERRORS
    ]
    metrics = {
        "checks_total": len(statuses),
        "checks_passed": statuses.count("passed"),
        "checks_attention": statuses.count("attention"),
        "checks_not_assessed": statuses.count("not_assessed"),
        "checks_error": 0,
    }
    normalized = {
        "records": [
            {
                "scope": {
                    "company_code": company,
                    "fiscal_year": str(fiscal_year),
                    "period": period,
                    "as_of": as_of.isoformat(),
                    "ledger": ledger,
                },
                "business_status": "inconclusive",
                "source_complete": True,
                "checklist_complete": False,
                "evidence_complete": False,
            }
        ],
        "metrics": metrics,
        "limitations": ["fx_valuation_run_status_source_unavailable"],
        "source_complete": True,
        "evidence_complete": False,
        "business_complete": False,
        "business_status": "inconclusive",
        "evidence_gap_codes": [],
    }
    baseline = {
        "schema_version": "3.0",
        "runtime": "codex_app_direct_sap",
        "used_sap_business_agents": False,
        "sources": sources,
        "normalized_result": normalized,
        "result_hash": _hash(normalized),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return baseline


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the independent live baseline for test-default-1010.")
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path.home() / ".codex" / "secure" / "sap-direct-readonly.json",
    )
    parser.add_argument(
        "--skillhub-root",
        type=Path,
        default=Path(r"C:\Users\wenbi\Documents\SAPSkillhub"),
    )
    args = parser.parse_args()
    baseline = build(args.case.resolve(), args.output.resolve(), args.artifacts.resolve(), args.profile, args.skillhub_root)
    print(
        json.dumps(
            {
                "result_hash": baseline["result_hash"],
                "source_count": len(baseline["sources"]),
                "metrics": baseline["normalized_result"]["metrics"],
                "limitations": baseline["normalized_result"]["limitations"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
