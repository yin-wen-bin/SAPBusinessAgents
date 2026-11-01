"""Independent typed comparisons; no model judgement or product business rules."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .common import TestAgentError, digest


def project(rows, specification):
    """A small declarative baseline projection, frozen in the plan before testing."""
    field_map = specification.get("field_map", {})
    mapped = [{**row, **{target: row.get(source) for target, source in field_map.items()}} for row in rows]
    group_by = specification.get("group_by")
    if group_by is None:
        return mapped
    aggregates = specification.get("aggregates", {})
    groups = {}
    # Currency/unit must be group keys whenever the associated measure is summed.
    for aggregate in aggregates.values():
        if aggregate.get("operation") == "sum" and not set(aggregate.get("dimensions", [])) <= set(group_by):
            raise TestAgentError("baseline_measure_dimensions_missing")
    for row in mapped:
        if any(name not in row for name in group_by):
            raise TestAgentError("baseline_group_key_missing")
        key = tuple(str(row[name]) for name in group_by)
        target = groups.setdefault(key, {name: row[name] for name in group_by})
        for name, definition in aggregates.items():
            operation = definition.get("operation")
            if operation == "count":
                target[name] = target.get(name, 0) + 1
            elif operation == "sum":
                field = definition.get("field")
                if field not in row or row[field] is None:
                    raise TestAgentError("baseline_measure_missing")
                target[name] = Decimal(str(target.get(name, "0"))) + Decimal(str(row[field]))
            else:
                raise TestAgentError("baseline_aggregate_invalid")
    return [{k: str(v) if isinstance(v, Decimal) else v for k, v in row.items()} for row in groups.values()]


def compare(baseline, observed, contract, anchor=None):
    if baseline.get("source_complete") is not True or baseline.get("source_truncated") is True:
        return {"status": "inconclusive", "code": "baseline_incomplete"}
    if observed.get("source_complete") is not True or observed.get("source_truncated") is True:
        return {"status": "inconclusive", "code": "observed_incomplete"}
    if baseline.get("scope") != observed.get("scope"):
        return {"status": "failed", "code": "query_scope_mismatch"}
    if anchor is not None:
        if anchor.get("source_complete") is not True or anchor.get("source_truncated") is True:
            return {"status": "inconclusive", "code": "source_anchor_incomplete"}
        if baseline.get("scope") != anchor.get("scope") or digest(baseline["rows"]) != digest(anchor["rows"]):
            return {"status": "inconclusive", "code": "sap_data_changed"}
    expected = project(baseline.get("rows", []), contract)
    actual = observed.get("rows", [])
    if not isinstance(actual, list) or any(not isinstance(r, dict) for r in actual):
        raise TestAgentError("observed_rows_invalid")
    keys = contract.get("keys", [])
    required = contract.get("required_fields", keys)
    if not keys or any(any(k not in row or row[k] is None for k in keys)
                       or any(k not in row for k in required) for row in actual):
        return {"status": "failed", "code": "required_fields_missing"}
    if any(any(k not in row or row[k] is None for k in keys) for row in expected):
        return {"status": "inconclusive", "code": "baseline_keys_missing"}
    def key(row):
        return tuple(str(row[k]) for k in keys)
    left, right = {key(row): row for row in expected}, {key(row): row for row in actual}
    if len(left) != len(expected) or len(right) != len(actual):
        return {"status": "failed", "code": "duplicate_business_keys"}
    kind = contract.get("type", "key_set")
    if kind not in {"key_set", "key_subset", "count_only", "count_and_key_subset"}:
        raise TestAgentError("comparison_type_invalid")
    matches = (left.keys() == right.keys() if kind == "key_set" else
               right.keys() <= left.keys() if kind in {"key_subset", "count_and_key_subset"} else True)
    if kind in {"count_only", "count_and_key_subset"}:
        matches = matches and len(expected) == len(actual)
    if not matches:
        return {"status": "failed", "code": "business_key_or_count_mismatch"}
    for row_key in right.keys() & left.keys():
        # SAP's explicit nullable clearing dates are values, not absent fields.
        # Accept null only against the identical independently read null. Never
        # turn a missing field or a null business key into a successful match.
        if any((left[row_key].get(name) is None) != (right[row_key][name] is None) for name in required):
            return {"status": "failed", "code": "required_fields_missing"}
        for name, definition in contract.get("fields", {}).items():
            if name not in left[row_key] or name not in right[row_key]:
                return {"status": "failed", "code": "comparison_field_missing", "field": name}
            a, b = left[row_key][name], right[row_key][name]
            if definition.get("type") == "decimal":
                try:
                    tolerance = Decimal(str(definition.get("tolerance", "0")))
                    valid = tolerance >= 0 and abs(Decimal(str(a)) - Decimal(str(b))) <= tolerance
                except (InvalidOperation, TypeError, ValueError):
                    valid = False
            else:
                valid = a == b
            if not valid:
                return {"status": "failed", "code": "business_value_mismatch", "field": name}
    return {"status": "passed", "code": "match", "expected_count": len(expected), "observed_count": len(actual)}
