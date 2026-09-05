"""Select a historical-clearing acceptance case without disclosing customer data.

The input is an encrypted, independently captured FI customer-item snapshot.
The selected business identifier is written only to an ignored CanonicalTestCase;
stdout contains counts and one-way digests only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from scripts.direct_sap_read import read_encrypted_rows
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from direct_sap_read import read_encrypted_rows


def _text(row, field):
    return str(row.get(field) or "").strip()


def _date(value):
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if text.startswith("/Date("):
        milliseconds = int(text[6:].split(")", 1)[0].split("+", 1)[0])
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)).date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "x", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fi-snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--company-code", default="1710")
    parser.add_argument("--as-of", default="2023-11-30")
    parser.add_argument("--business-date", default="2026-09-05")
    args = parser.parse_args()
    cutoff = date.fromisoformat(args.as_of)
    rows = read_encrypted_rows(args.fi_snapshot.resolve())
    by_customer: dict[str, list[dict]] = {}
    reversed_customers: set[str] = set()
    for row in rows:
        customer = _text(row, "Customer")
        if customer and _truthy(row.get("ClearingIsReversed")):
            reversed_customers.add(customer)
        posting = _date(row.get("PostingDate"))
        clearing = _date(row.get("ClearingDate"))
        if (
            _text(row, "CompanyCode") == args.company_code
            and customer
            and posting is not None and posting <= cutoff
            and clearing is not None and clearing <= cutoff
            and _text(row, "ClearingAccountingDocument")
            and _text(row, "ClearingDocFiscalYear")
        ):
            by_customer.setdefault(customer, []).append(row)
    by_customer = {
        customer: values for customer, values in by_customer.items()
        if customer not in reversed_customers
    }
    if not by_customer:
        raise SystemExit("test_data_gap: no historical customer with a complete clearing reference")
    # Prefer the smallest complete relationship set. This keeps the mandatory
    # live case bounded while still proving the historical clearing timeline.
    customer, matched = min(by_customer.items(), key=lambda item: (len(item[1]), item[0]))
    case = {
        "schema_version": "2.0",
        "case_id": "ar-historical-clearing-live",
        "agent_id": "ar-collection",
        "question": {
            "zh": "按历史截止日核对应收项目的清账时间线和催收事件。",
            "en": "Reconstruct AR clearing timelines and dunning events at a historical cutoff.",
        },
        "input": {
            "company_code": args.company_code,
            "customers": [customer],
            "as_of": args.as_of,
            "business_date": args.business_date,
        },
        "business_conditions": {
            "scope": "historical_clearing_timeline",
            "sample_source": "live_discovery",
        },
        "expected_grain": [
            "company_code", "ledger", "fiscal_year", "accounting_document",
            "accounting_document_item",
        ],
        "expected_output": {
            "record_fields": [
                "company_code", "ledger", "fiscal_year", "accounting_document",
                "accounting_document_item", "customer", "posting_date", "due_date",
                "aging_bucket", "clearing_date", "last_dunning_date",
                "dunning_as_of_status", "amount", "currency", "business_status",
            ],
            "metric_ids": [
                "requested_customer_count", "result_customer_count",
                "normal_customer_count", "attention_customer_count",
                "inconclusive_customer_count",
            ],
            "minimum_primary_evidence_rows": 1,
            "allow_empty_result": False,
            "evidence_scope": "complete",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(customer.encode("utf-8")).hexdigest()
    print(json.dumps({"case_id": case["case_id"], "cleared_item_count": len(matched), "customer_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
