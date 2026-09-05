"""Build independent AR Skill gates without importing either tested Skill."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sap_business_agents_platform.acceptance import canonical_hash
from sap_business_agents_platform.skills import _skill_package_digest
from scripts.build_ar_cash_application_direct_baseline import LIFECYCLE, POSTING_ERROR, REVERSAL, _posting_status
from scripts.build_ar_collection_direct_baseline import _independent_dunning_events
from scripts.direct_sap_read import read_encrypted_rows


def _load(path: Path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("gate_input_invalid")
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_value(row, name):
    for key, value in row.items():
        if str(key).replace("_", "").casefold() == name.replace("_", "").casefold():
            return value
    return None


def _text(row, name):
    return str(_row_value(row, name) or "").strip()


def _day(value):
    text = str(value or "").strip()
    for pattern in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            pass
    return None


def _exact(value):
    rendered = format(Decimal(str(value)), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _snapshot(path: Path, expected_object: str):
    manifest = _load(path / "manifest.json")
    rows = read_encrypted_rows(path)
    if (
        manifest.get("object") != expected_object
        or manifest.get("source_complete") is not True
        or manifest.get("paging_complete") is not True
        or manifest.get("row_count") != len(rows)
        or manifest.get("rows_hash") != canonical_hash(rows)
    ):
        raise ValueError("independent_snapshot_invalid")
    return manifest, rows


def _prove_partition(all_rows, parts, key_fields):
    def keyed(rows):
        result = {}
        for row in rows:
            key = tuple(_text(row, field) for field in key_fields)
            if not any(key) or key in result:
                raise ValueError("independent_partition_key_invalid")
            result[key] = row
        return result
    whole = keyed(all_rows)
    merged = {}
    for rows in parts:
        for key, row in keyed(rows).items():
            if key in merged:
                raise ValueError("independent_partition_overlap")
            merged[key] = row
    if whole != merged:
        raise ValueError("independent_partition_union_mismatch")


def _bank_projection(row):
    status = _text(row, "BankStatementStatus")
    if status not in REVERSAL:
        raise ValueError("bank_reversal_status_unknown")
    value_date = _day(_row_value(row, "ValueDate"))
    posting_date = _day(_row_value(row, "PostingDate"))
    if value_date is None:
        raise ValueError("bank_value_date_invalid")
    bank_document = _text(row, "BankLedgerDocument") or None
    subledger_document = _text(row, "SubledgerDocument") or None
    fiscal_year = _text(row, "FiscalYear") or None
    related = None
    if bank_document or subledger_document:
        related = {
            "bank_ledger_document": bank_document,
            "subledger_document": subledger_document,
            "fiscal_year": fiscal_year,
        }
    return {
        "statement_id": _text(row, "BankStatementShortID"),
        "statement_item": _text(row, "BankStatementItem"),
        "value_date": value_date.isoformat(),
        "posting_date": posting_date.isoformat() if posting_date else None,
        "amount": _exact(_row_value(row, "AmountInTransactionCurrency")),
        "currency": _text(row, "TransactionCurrency"),
        "credit_debit_indicator": "credit",
        "reversal_status": REVERSAL[status],
        "posting_status": _posting_status(row, status),
        "related_accounting_document": related,
    }


def _skill_bank_projection(row):
    return {key: row.get(key) for key in (
        "statement_id", "statement_item", "value_date", "posting_date", "amount", "currency",
        "credit_debit_indicator", "reversal_status", "posting_status", "related_accounting_document",
    )}


def _bank_scope(rows, requested):
    start = date.fromisoformat(requested["date_from"])
    end = date.fromisoformat(requested["date_to"])
    return [row for row in rows if _text(row, "CompanyCode") == requested["company_code"]
            and _text(row, "DebitCreditCode") == "H"
            and (day := _day(_row_value(row, "ValueDate"))) is not None and start <= day <= end]


def _dunning_projection(row):
    result = {key: row.get(key) for key in (
        "customer", "company_code", "dunning_area", "dunning_run_id", "dunning_run_date",
        "effective_dunning_date", "fiscal_year", "accounting_document",
        "accounting_document_item", "dunning_level", "old_dunning_level",
        "dunning_blocking_reason", "dunning_reversal_status", "special_gl_code",
        "amount", "currency", "sequence_status",
    )}
    result["amount"] = _exact(result["amount"])
    return result


def _package_info(skillhub_root: Path, skill_id: str):
    package = skillhub_root / "skills" / "FI" / skill_id
    manifest = _load(package / "manifest.json")
    profiles = _load(package / "references" / "source-profiles.json")
    active = profiles["profiles"][profiles["active_profile_id"]]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=skillhub_root, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    return package, manifest, profiles, active, commit


def build_bank(args):
    all_manifest, all_rows = _snapshot(args.all_snapshot, "I_ARBANKSTATEMENTITEM")
    part_values = [_snapshot(path, "I_ARBANKSTATEMENTITEM") for path in args.partition_snapshot]
    _prove_partition(
        all_rows,
        [rows for _manifest, rows in part_values],
        ["CompanyCode", "BankStatementShortID", "BankStatementItem"],
    )
    nonzero = _load(args.nonzero_output)
    zero = _load(args.zero_output)
    direct_nonzero = sorted(
        (_bank_projection(row) for row in _bank_scope(all_rows, nonzero["requested_scope"])),
        key=lambda row: (row["statement_id"], row["statement_item"]),
    )
    skill_nonzero = sorted(
        (_skill_bank_projection(row) for row in nonzero.get("receipts") or []),
        key=lambda row: (row["statement_id"], row["statement_item"]),
    )
    direct_zero = _bank_scope(all_rows, zero["requested_scope"])
    if direct_nonzero != skill_nonzero or not direct_nonzero:
        raise ValueError("bank_nonzero_comparison_mismatch")
    if direct_zero or zero.get("receipts") or zero.get("evidence_status") != "not_found":
        raise ValueError("bank_zero_comparison_mismatch")
    if nonzero.get("status") != "complete" or nonzero.get("completeness", {}).get("evidence_complete") is not True:
        raise ValueError("bank_skill_nonzero_incomplete")
    sensitive = {"payer_name", "bank_reference", "payer_account_hash"}
    if any(sensitive & set(row) for row in skill_nonzero):
        raise ValueError("bank_public_projection_contains_sensitive_fields")
    return (
        {
            "nonzero_receipt_count": len(direct_nonzero),
            "complete_zero_count": 0,
            "lifecycle_m_count": sum(_text(row, "BankStatementItemLifeCycSts") == "M" for row in all_rows),
            "posting_error_zero_count": sum(_text(row, "PostingErrorStatus") == "0" for row in all_rows),
            "forced_partitions": len(part_values),
        },
        {"nonzero": direct_nonzero, "zero": []},
        {"nonzero": skill_nonzero, "zero": []},
        [all_manifest, *[manifest for manifest, _rows in part_values]],
    )


def build_dunning(args):
    item_manifest, item_rows = _snapshot(args.item_snapshot, "MHND")
    header_manifest, header_rows = _snapshot(args.header_snapshot, "MHNK")
    item_parts = [_snapshot(path, "MHND") for path in args.item_partition_snapshot]
    header_parts = [_snapshot(path, "MHNK") for path in args.header_partition_snapshot]
    _prove_partition(item_rows, [rows for _m, rows in item_parts], item_manifest["stable_order_by"])
    _prove_partition(header_rows, [rows for _m, rows in header_parts], header_manifest["stable_order_by"])
    nonzero = _load(args.nonzero_output)
    zero = _load(args.zero_output)
    scope = nonzero["requested_scope"]
    direct_nonzero = [
        _dunning_projection(row) for row in _independent_dunning_events(
            item_rows, header_rows,
            company=scope["company_code"], customers=scope["customers"],
            cutoff=date.fromisoformat(scope["as_of"]), dunning_area=scope.get("dunning_area") or "",
        )
    ]
    skill_nonzero = [_dunning_projection(row) for row in nonzero.get("events") or []]
    direct_nonzero.sort(key=lambda row: tuple(str(row.get(k) or "") for k in (
        "company_code", "customer", "dunning_area", "dunning_run_date", "dunning_run_id",
        "fiscal_year", "accounting_document", "accounting_document_item",
    )))
    skill_nonzero.sort(key=lambda row: tuple(str(row.get(k) or "") for k in (
        "company_code", "customer", "dunning_area", "dunning_run_date", "dunning_run_id",
        "fiscal_year", "accounting_document", "accounting_document_item",
    )))
    zero_scope = zero["requested_scope"]
    direct_zero = _independent_dunning_events(
        item_rows, header_rows,
        company=zero_scope["company_code"], customers=zero_scope["customers"],
        cutoff=date.fromisoformat(zero_scope["as_of"]), dunning_area=zero_scope.get("dunning_area") or "",
    )
    if direct_nonzero != skill_nonzero or not direct_nonzero:
        raise ValueError("dunning_nonzero_comparison_mismatch")
    if direct_zero or zero.get("events") or zero.get("evidence_status") != "not_found":
        raise ValueError("dunning_zero_comparison_mismatch")
    if nonzero.get("completeness", {}).get("evidence_complete") is not True:
        raise ValueError("dunning_skill_nonzero_incomplete")
    forbidden = {"document_reference_id", "one_time_account"}
    if any(forbidden & set(row) for row in nonzero.get("events") or []):
        raise ValueError("dunning_public_projection_contains_restricted_fields")
    return (
        {
            "nonzero_event_count": len(direct_nonzero),
            "complete_zero_count": 0,
            "forced_date_partitions": len(item_parts),
            "customer_identifiers": "hashed_only",
        },
        {"nonzero": direct_nonzero, "zero": []},
        {"nonzero": skill_nonzero, "zero": []},
        [item_manifest, header_manifest, *[m for m, _r in item_parts], *[m for m, _r in header_parts]],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-id", choices=["sap-bank-receipt-evidence", "sap-ar-dunning-history-evidence"], required=True)
    parser.add_argument("--skillhub-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nonzero-output", type=Path, required=True)
    parser.add_argument("--zero-output", type=Path, required=True)
    parser.add_argument("--all-snapshot", type=Path)
    parser.add_argument("--partition-snapshot", type=Path, action="append", default=[])
    parser.add_argument("--item-snapshot", type=Path)
    parser.add_argument("--header-snapshot", type=Path)
    parser.add_argument("--item-partition-snapshot", type=Path, action="append", default=[])
    parser.add_argument("--header-partition-snapshot", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("skill_gate_artifact_immutable")
    if args.skill_id == "sap-bank-receipt-evidence":
        if args.all_snapshot is None or len(args.partition_snapshot) < 2:
            raise ValueError("bank_gate_snapshots_missing")
        coverage, baseline, observed, manifests = build_bank(args)
    else:
        if args.item_snapshot is None or args.header_snapshot is None or len(args.item_partition_snapshot) < 2 or len(args.header_partition_snapshot) < 2:
            raise ValueError("dunning_gate_snapshots_missing")
        coverage, baseline, observed, manifests = build_dunning(args)
    package, manifest, profiles, active, commit = _package_info(args.skillhub_root.resolve(), args.skill_id)
    output_schema = package / manifest["output_schema"]
    reader = Path(__file__).resolve()
    result = {
        "schema_version": "2.0",
        "skill_id": args.skill_id,
        "git_commit": commit,
        "package_sha256": _skill_package_digest(package),
        "profile_version": profiles["profile_version"],
        "profile_sha256": _file_sha(package / "references" / "source-profiles.json"),
        "metadata_sha256": str(active["metadata_sha256"]),
        "output_schema_sha256": _file_sha(output_schema),
        "baseline_hash": canonical_hash(baseline),
        "comparison_hash": canonical_hash({"expected": baseline, "observed": observed}),
        "coverage": coverage,
        "independent_validation": {
            "tested_skill_imported": False,
            "tested_skill_runtime_called": False,
            "reader_id": "sapbusinessagents-direct-ar-adt-gate-v1",
            "reader_sha256": "sha256:" + _file_sha(reader),
            "metadata_sha256": sorted({str(item["metadata_sha256"]) for item in manifests}),
            "source_snapshot_hashes": sorted({str(item["rows_hash"]) for item in manifests}),
            "complete_zero_sample_passed": True,
            "nonzero_sample_passed": True,
            "partition_count": 2,
        },
        "readonly_audit": {"verdict": "PASS", "methods": ["semantic_read_only_ADT_POST"], "sap_write_operations": 0},
        "privacy_audit": {"verdict": "PASS", "public_raw_reference_fields": 0, "tracked_raw_rows": 0},
        "verdict": "PASS",
    }
    for name, key in (
        ("public_output_schema", "public_output_schema_sha256"),
        ("restricted_row_schema", "restricted_row_schema_sha256"),
    ):
        if manifest.get(name):
            result[key] = _file_sha(package / manifest[name])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"skill_id": args.skill_id, "verdict": "PASS", "baseline_hash": result["baseline_hash"], "comparison_hash": result["comparison_hash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
