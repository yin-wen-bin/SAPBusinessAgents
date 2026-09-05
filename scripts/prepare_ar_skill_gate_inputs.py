"""Prepare ignored Skill gate inputs without printing customer identifiers.

The helper reads encrypted independent snapshots and writes only structured
inputs beneath an explicitly supplied ignored artifact directory. Stdout
contains counts and one-way customer hashes only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from scripts.direct_sap_read import read_encrypted_rows


def _text(row, field):
    return str(row.get(field) or "").strip()


def _customer_hash(value):
    return "sha256:" + hashlib.sha256(("ar-gate-customer:" + value).encode()).hexdigest()


def prepare(mhnd_snapshot: Path, customer_case: Path, output: Path, *, as_of: date) -> dict:
    if output.exists():
        raise ValueError("skill_gate_input_directory_immutable")
    rows = read_encrypted_rows(mhnd_snapshot)
    counts = Counter(
        _text(row, "KUNNR")
        for row in rows
        if _text(row, "KOART") == "D" and _text(row, "BUKRS") == "1710" and _text(row, "KUNNR")
    )
    if not counts:
        raise ValueError("test_data_gap_dunning_nonzero")
    ambiguous_customers = set()
    sequences: dict[tuple[str, str, str], set[str]] = {}
    for row in rows:
        customer = _text(row, "KUNNR")
        area = _text(row, "MABER") or _text(row, "SMABER")
        key = (customer, area, _text(row, "LAUFD"))
        sequences.setdefault(key, set()).add(_text(row, "LAUFI"))
    for (customer, _area, _day), run_ids in sequences.items():
        if len(run_ids) > 1:
            ambiguous_customers.add(customer)
    nonzero = next(
        (value for value in sorted(counts, key=lambda value: (-counts[value], value))
         if value not in ambiguous_customers),
        None,
    )
    if nonzero is None:
        raise ValueError("test_data_gap_dunning_unambiguous_nonzero")
    case = json.loads(customer_case.read_text(encoding="utf-8"))
    candidates = [str(value).strip() for value in case.get("input", {}).get("customers", [])]
    zero = next((value for value in candidates if value and value not in counts), None)
    if zero is None:
        raise ValueError("test_data_gap_dunning_complete_zero")
    output.mkdir(parents=True)
    payloads = {
        "dunning-nonzero-input.json": {
            "schema_version": 1,
            "company_code": "1710",
            "customers": [nonzero],
            "as_of": as_of.isoformat(),
        },
        "dunning-zero-input.json": {
            "schema_version": 1,
            "company_code": "1710",
            "customers": [zero],
            "as_of": as_of.isoformat(),
        },
    }
    for name, payload in payloads.items():
        (output / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "nonzero_event_count": counts[nonzero],
        "nonzero_customer_hash": _customer_hash(nonzero),
        "zero_customer_hash": _customer_hash(zero),
        "as_of": as_of.isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mhnd-snapshot", type=Path, required=True)
    parser.add_argument("--customer-case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = prepare(
        args.mhnd_snapshot.resolve(),
        args.customer_case.resolve(),
        args.output.resolve(),
        as_of=args.as_of,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
