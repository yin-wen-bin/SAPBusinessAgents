from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    canonical_hash,
    validate_direct_baseline,
)
from scripts.build_ar_cash_application_direct_baseline import build as build_cash
from scripts.build_ar_cash_application_direct_baseline import _posting_status
from scripts.build_ar_collection_direct_baseline import build as build_collection
from scripts.build_ar_collection_direct_baseline import _open_at_cutoff
from scripts.build_ar_collection_direct_baseline import _independent_dunning_events
from datetime import date
from scripts.direct_sap_read import write_encrypted_rows


def test_independent_baseline_rebuilds_reversal_timeline_using_exact_fiscal_tuple():
    item = {"CompanyCode": "1710", "ClearingDate": "2024-01-01", "ClearingIsReversed": True,
            "ClearingDocFiscalYear": "2024", "ClearingAccountingDocument": "C1"}
    clearing = [{"CompanyCode": "1710", "FiscalYear": "2024", "AccountingDocument": "C1",
                 "ReverseDocumentFiscalYear": "2025", "ReverseDocument": "R1"}]
    reversal = [{"CompanyCode": "1710", "FiscalYear": "2025", "AccountingDocument": "R1", "PostingDate": "2025-02-01"}]
    assert _open_at_cutoff(item, date(2024, 12, 31), clearing, reversal) == (False, None)
    assert _open_at_cutoff(item, date(2025, 2, 1), clearing, reversal) == (True, None)
    wrong_year = [{**reversal[0], "FiscalYear": "2024"}]
    assert _open_at_cutoff(item, date(2025, 2, 1), clearing, wrong_year) == (False, "historical_clearing_reversal_date_missing")
    assert _open_at_cutoff(item, date(2025, 2, 1), [], reversal) == (False, "historical_clearing_reversal_date_missing")


def _case(path: Path, *, agent_id: str, input_value: dict, record_fields: list[str]) -> Path:
    payload = {
        "schema_version": "2.0",
        "case_id": "fixture",
        "agent_id": agent_id,
        "question": {"zh": "测试", "en": "Test"},
        "input": input_value,
        "business_conditions": {},
        "expected_grain": record_fields[:1],
        "expected_output": {
            "record_fields": record_fields,
            "metric_ids": [],
            "minimum_primary_evidence_rows": 0 if agent_id == "ar-cash-application" else 1,
            "allow_empty_result": True,
            "evidence_scope": "complete",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_collection_direct_baseline_uses_encrypted_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_path = _case(
        tmp_path / "case.json",
        agent_id="ar-collection",
        input_value={
            "company_code": "1710",
            "customers": ["100001"],
            "as_of": "2026-09-04",
            "business_date": "2026-09-04",
        },
        record_fields=["company_code"],
    )
    calls = iter(
        [
            (
                {
                    "source_id": "ledger", "service_name": "ledger", "entity_set": "ledger",
                    "access_method": "odata_get", "http_method": "GET",
                    "semantic_read_only": True, "schema_hash": "sha256:" + "a" * 64,
                    "query_hash": "sha256:" + "b" * 64, "stable_order_by": ["Ledger"],
                    "paging_complete": True, "source_complete": True, "row_count": 1,
                    "page_count": 1, "primary": False, "restricted_rows_hash": "sha256:" + "c" * 64,
                },
                [{"Ledger": "0L", "IsLeadingLedger": True}],
            ),
            (
                {
                    "source_id": "items", "service_name": "items", "entity_set": "items",
                    "access_method": "odata_get", "http_method": "GET",
                    "semantic_read_only": True, "schema_hash": "sha256:" + "d" * 64,
                    "query_hash": "sha256:" + "e" * 64,
                    "stable_order_by": ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
                    "paging_complete": True, "source_complete": True, "row_count": 1,
                    "page_count": 1, "primary": True, "restricted_rows_hash": "sha256:" + "f" * 64,
                },
                [{
                    "CompanyCode": "1710", "Ledger": "", "FiscalYear": "2026",
                    "AccountingDocument": "1800000001", "AccountingDocumentItem": "1",
                    "Customer": "100001", "PostingDate": "2026-01-01", "IsOpenItemManaged": True,
                    "NetDueDate": "2026-01-31", "AmountInTransactionCurrency": "100.00",
                    "TransactionCurrency": "USD", "DebitCreditCode": "S",
                    "SpecialGLCode": "", "DunningLevel": "1", "LastDunningDate": "2026-08-01",
                }],
            ),
            (
                {
                    "source_id": "master", "service_name": "master", "entity_set": "master",
                    "access_method": "odata_get", "http_method": "GET",
                    "semantic_read_only": True, "schema_hash": "sha256:" + "1" * 64,
                    "query_hash": "sha256:" + "2" * 64,
                    "stable_order_by": ["Customer", "CompanyCode", "DunningArea"],
                    "paging_complete": True, "source_complete": True, "row_count": 1,
                    "page_count": 1, "primary": False, "restricted_rows_hash": "sha256:" + "3" * 64,
                },
                [{"Customer": "100001", "CompanyCode": "1710", "DunningArea": "", "DunningBlock": ""}],
            ),
        ]
    )
    monkeypatch.setattr(
        "scripts.build_ar_collection_direct_baseline._source",
        lambda *_args, **_kwargs: next(calls),
    )
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    output = tmp_path / "baseline.json"

    baseline = build_collection(case_path, profile, output, tmp_path / "artifacts")

    assert baseline["schema_version"] == "3.0"
    assert baseline["normalized_result"]["business_status"] == "attention"
    assert baseline["normalized_result"]["records"][0]["ledger"] == "0L"
    validate_direct_baseline(baseline, CanonicalTestCase.from_dict(json.loads(case_path.read_text())))


def test_cash_zero_baseline_requires_frozen_complete_adt_manifest(tmp_path: Path) -> None:
    case_path = _case(
        tmp_path / "case.json",
        agent_id="ar-cash-application",
        input_value={
            "company_code": "1710", "date_from": "2026-08-01", "date_to": "2026-08-02",
        },
        record_fields=["company_code"],
    )
    rows_path = tmp_path / "rows.json"
    rows_path.write_text("[]\n", encoding="utf-8")
    raw_hash = "sha256:" + hashlib.sha256(rows_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "source.json"
    manifest_path.write_text(
        json.dumps(
            {
                "object": "I_ArBankStatementItem",
                "access_method": "adt_data_preview",
                "http_method": "POST",
                "semantic_read_only": True,
                "schema_hash": "sha256:" + "a" * 64,
                "query_hash": "sha256:" + "b" * 64,
                "stable_order_by": ["CompanyCode", "BankStatementShortID", "BankStatementItem"],
                "paging_complete": True,
                "source_complete": True,
                "scope": {
                    "company_code": "1710", "date_from": "2026-08-01",
                    "date_to": "2026-08-02", "receipt_reference_supplied": False,
                },
                "raw_sha256": raw_hash,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "baseline.json"

    baseline = build_cash(
        case_path, rows_path, manifest_path, output, tmp_path / "artifacts"
    )

    normalized = baseline["normalized_result"]
    assert normalized["records"] == []
    assert normalized["business_status"] == "normal"
    assert normalized["evidence_complete"] is True
    assert not (tmp_path / "artifacts" / "bank_raw" / "rows.json").exists()
    assert (tmp_path / "artifacts" / "bank_raw" / "rows.ndjson.aesgcm").is_file()
    validate_direct_baseline(baseline, CanonicalTestCase.from_dict(json.loads(case_path.read_text())))


def test_cash_baseline_rejects_source_manifest_scope_drift(tmp_path: Path) -> None:
    case_path = _case(
        tmp_path / "case.json",
        agent_id="ar-cash-application",
        input_value={
            "company_code": "1710", "date_from": "2026-08-01", "date_to": "2026-08-02",
        },
        record_fields=["company_code"],
    )
    rows_path = tmp_path / "rows.json"
    rows_path.write_text("[]", encoding="utf-8")
    manifest = {
        "object": "I_ArBankStatementItem", "access_method": "adt_data_preview",
        "http_method": "POST", "semantic_read_only": True,
        "schema_hash": "sha256:" + "a" * 64, "query_hash": "sha256:" + "b" * 64,
        "stable_order_by": ["CompanyCode", "BankStatementShortID", "BankStatementItem"],
        "paging_complete": True, "source_complete": True,
        "scope": {"company_code": "9999", "date_from": "2026-08-01", "date_to": "2026-08-02", "receipt_reference_supplied": False},
        "raw_sha256": "sha256:" + hashlib.sha256(rows_path.read_bytes()).hexdigest(),
    }
    manifest_path = tmp_path / "source.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="scope"):
        build_cash(case_path, rows_path, manifest_path, tmp_path / "out.json", tmp_path / "artifacts")


def test_cash_nonzero_baseline_expands_exact_fi_relationship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_path = _case(
        tmp_path / "case.json",
        agent_id="ar-cash-application",
        input_value={
            "company_code": "1710", "date_from": "2026-08-01",
            "date_to": "2026-08-02", "business_date": "2026-09-04",
        },
        record_fields=["company_code"],
    )
    snapshot = tmp_path / "bank"
    bank_rows = [{
        "COMPANYCODE": "1710", "BANKSTATEMENTSHORTID": "1",
        "BANKSTATEMENTITEM": "1", "VALUEDATE": "20260801",
        "AMOUNTINTRANSACTIONCURRENCY": "100.00", "TRANSACTIONCURRENCY": "USD",
        "DEBITCREDITCODE": "H", "BANKSTATEMENTSTATUS": "8",
        "ISCOMPLETED": "X", "ISINPROCESS": "", "POSTINGERRORSTATUS": "0",
        "BANKSTATEMENTITEMLIFECYCSTS": "M", "SUBLEDGERDOCUMENT": "1400000001",
        "FISCALYEAR": "2026", "BANKREFERENCE": "",
    }]
    artifact = write_encrypted_rows(snapshot, bank_rows)
    manifest = {
        "source_id": "bank", "object": "I_ARBANKSTATEMENTITEM",
        "access_method": "adt_data_preview", "http_method": "POST",
        "semantic_read_only": True, "query_hash": "sha256:" + "a" * 64,
        "schema_hash": "sha256:" + "b" * 64,
        "metadata_sha256": "sha256:" + "b" * 64,
        "stable_order_by": ["CompanyCode", "BankStatementShortID", "BankStatementItem"],
        "paging_complete": True, "source_complete": True, "row_count": 1,
        "rows_hash": canonical_hash(bank_rows),
        "spec": {
            "object": "I_ARBANKSTATEMENTITEM", "fields": list(bank_rows[0]),
            "filters": [
                {"field": "CompanyCode", "operator": "=", "value": "1710"},
                {"field": "DebitCreditCode", "operator": "=", "value": "H"},
                {"field": "ValueDate", "operator": ">=", "value": "20260801"},
                {"field": "ValueDate", "operator": "<=", "value": "20260802"},
            ],
            "row_limit": 100,
        },
        "restricted_artifact": artifact,
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    payment = {
        "CompanyCode": "1710", "Ledger": "0L", "FiscalYear": "2026",
        "AccountingDocument": "1400000001", "AccountingDocumentItem": "1",
        "FinancialAccountType": "D", "Customer": "100001", "SpecialGLCode": "",
        "ClearingAccountingDocument": "", "ClearingDocFiscalYear": "",
        "AmountInTransactionCurrency": "-100.00", "TransactionCurrency": "USD",
        "PostingDate": "2026-08-01",
    }
    invoice = {
        "CompanyCode": "1710", "Ledger": "0L", "FiscalYear": "2026",
        "AccountingDocument": "1800000001", "AccountingDocumentItem": "1",
        "FinancialAccountType": "D", "Customer": "100001", "SpecialGLCode": "",
        "ClearingAccountingDocument": "1400000001", "ClearingDocFiscalYear": "2026",
        "AmountInTransactionCurrency": "100.00", "TransactionCurrency": "USD",
        "PostingDate": "2026-07-01",
    }
    def source(source_id, rows):
        return ({
            "source_id": source_id, "service_name": "svc", "entity_set": "set",
            "access_method": "odata_get", "http_method": "GET",
            "semantic_read_only": True, "schema_hash": "sha256:" + "c" * 64,
            "query_hash": "sha256:" + "d" * 64,
            "stable_order_by": ["CompanyCode"], "paging_complete": True,
            "source_complete": True, "row_count": len(rows), "page_count": 1,
            "primary": False, "restricted_rows_hash": "sha256:" + "e" * 64,
        }, rows)
    calls = iter([
        source("ledger", [{"Ledger": "0L", "IsLeadingLedger": True}]),
        source("payment", [payment]),
        source("invoice", [invoice]),
    ])
    monkeypatch.setattr(
        "scripts.build_ar_cash_application_direct_baseline._odata_source",
        lambda *_args, **_kwargs: next(calls),
    )
    baseline = build_cash(
        case_path, None, None, tmp_path / "out.json", tmp_path / "artifacts",
        bank_snapshot=snapshot, profile_path=profile,
    )
    result = baseline["normalized_result"]
    assert result["records"][0]["cash_application_status"] == "confirmed"
    assert result["records"][0]["customer"] == "100001"
    assert result["evidence_complete"] is True
    assert result["business_status"] == "normal"


def test_direct_bank_status_zero_and_set_to_done_are_completed() -> None:
    assert _posting_status(
        {
            "IsCompleted": "X", "IsInProcess": "", "PostingErrorStatus": "0",
            "BankStatementItemLifeCycSts": "M",
        },
        "8",
    ) == "completed"
    with pytest.raises(ValueError, match="unknown posting status"):
        _posting_status(
            {
                "IsCompleted": "X", "IsInProcess": "", "PostingErrorStatus": "9",
                "BankStatementItemLifeCycSts": "G",
            },
            "8",
        )


def test_independent_dunning_events_join_header_and_mark_same_day_sequence_ambiguous() -> None:
    common = {
        "MANDT": "100", "LAUFD": "20250901", "KOART": "D", "BUKRS": "1710",
        "KUNNR": "100001", "LIFNR": "", "CPDKY": "", "SKNRZE": "",
        "SMABER": "01", "SMAHSK": "",
    }
    items = [
        {**common, "LAUFI": run, "MABER": "01", "BELNR": "1800000001", "GJAHR": "2025",
         "BUZEI": "001", "MAHNN": level, "MAHNS": "0", "MANSP": "", "UMSKZ": "",
         "DMSHB": "100.00", "WAERS": "USD"}
        for run, level in (("A", "1"), ("B", "2"))
    ]
    headers = [
        {**common, "LAUFI": run, "BUSAB": "", "AUSDT": "20250902", "WAERS": "USD"}
        for run in ("A", "B")
    ]
    events = _independent_dunning_events(
        items,
        headers,
        company="1710",
        customers=["100001"],
        cutoff=date(2025, 9, 30),
        dunning_area="01",
    )
    assert len(events) == 2
    assert {event["sequence_status"] for event in events} == {"ambiguous"}
    assert {event["effective_dunning_date"] for event in events} == {"2025-09-02"}


def test_collection_historical_baseline_requires_independent_snapshots(tmp_path: Path) -> None:
    case_path = _case(
        tmp_path / "case.json",
        agent_id="ar-collection",
        input_value={
            "company_code": "1710",
            "customers": ["100001"],
            "as_of": "2025-09-30",
            "business_date": "2026-09-04",
        },
        record_fields=["company_code"],
    )
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="independent MHND and MHNK"):
        build_collection(case_path, profile, tmp_path / "out.json", tmp_path / "artifacts")
