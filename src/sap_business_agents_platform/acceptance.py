from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .managed_rules import source_digest


JsonObject = dict[str, Any]
ComparisonVerdict = Literal["MATCH", "MISMATCH", "BLOCKED"]


@dataclass(frozen=True)
class CanonicalTestCase:
    schema_version: str
    case_id: str
    agent_id: str
    question: JsonObject
    input: JsonObject
    business_conditions: JsonObject
    expected_grain: tuple[str, ...]
    expected_output: JsonObject | None = None

    @classmethod
    def from_dict(cls, value: JsonObject) -> "CanonicalTestCase":
        base_fields = {
            "schema_version",
            "case_id",
            "agent_id",
            "question",
            "input",
            "business_conditions",
            "expected_grain",
        }
        version = str(value.get("schema_version") or "")
        required = base_fields | ({"expected_output"} if version == "2.0" else set())
        if version not in {"1.0", "2.0"}:
            raise ValueError("canonical test case schema_version must be 1.0 or 2.0")
        if set(value) != required:
            raise ValueError("canonical test case has unexpected or missing fields")
        question = value.get("question")
        if not isinstance(question, dict) or not all(
            str(question.get(locale) or "").strip() for locale in ("zh", "en")
        ):
            raise ValueError("canonical test case question must be bilingual")
        if not isinstance(value.get("input"), dict) or not isinstance(
            value.get("business_conditions"), dict
        ):
            raise ValueError("canonical test case input and business_conditions must be objects")
        grain = value.get("expected_grain")
        if not isinstance(grain, list) or not grain or any(not str(item).strip() for item in grain):
            raise ValueError("canonical test case expected_grain is required")
        expected_output = value.get("expected_output")
        if version == "2.0":
            _validate_expected_output(expected_output, tuple(str(item) for item in grain))
        return cls(
            schema_version=version,
            case_id=str(value["case_id"]),
            agent_id=str(value["agent_id"]),
            question=dict(question),
            input=dict(value["input"]),
            business_conditions=dict(value["business_conditions"]),
            expected_grain=tuple(str(item) for item in grain),
            expected_output=dict(expected_output) if isinstance(expected_output, dict) else None,
        )

    def as_dict(self) -> JsonObject:
        result = {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "agent_id": self.agent_id,
            "question": self.question,
            "input": self.input,
            "business_conditions": self.business_conditions,
            "expected_grain": list(self.expected_grain),
        }
        if self.schema_version == "2.0":
            result["expected_output"] = dict(self.expected_output or {})
        return result


def validate_direct_baseline(
    value: JsonObject,
    case: CanonicalTestCase | None = None,
) -> JsonObject:
    if value.get("runtime") != "codex_app_direct_sap":
        raise ValueError("baseline runtime must be codex_app_direct_sap")
    if value.get("used_sap_business_agents") is not False:
        raise ValueError("direct baseline must attest used_sap_business_agents=false")
    baseline_version = str(value.get("schema_version") or "1.0")
    is_v2 = baseline_version == "2.0"
    is_v3 = baseline_version == "3.0"
    if is_v3:
        sources = value.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("direct baseline v3 sources are required")
        for index, source in enumerate(sources):
            _validate_baseline_v3_source(source, index)
        if value.get("supplemental_sources") is not None:
            raise ValueError("direct baseline v3 must describe all reads in sources")
    elif is_v2:
        methods = value.get("http_methods")
        if not isinstance(methods, list) or not methods or set(methods) != {"GET"}:
            raise ValueError("direct baseline v2 must contain GET-only http_methods")
        sources = value.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("direct baseline v2 sources are required")
        for index, source in enumerate(sources):
            _validate_baseline_source(source, index)
        supplemental_sources = value.get("supplemental_sources")
        if supplemental_sources is not None:
            if not isinstance(supplemental_sources, list) or not supplemental_sources:
                raise ValueError("direct baseline supplemental_sources must be a non-empty array")
            for index, source in enumerate(supplemental_sources):
                _validate_supplemental_adt_source(source, index)
        _validate_baseline_qualification(value.get("qualification"), sources)
    elif value.get("http_method") != "GET":
        raise ValueError("direct baseline must be GET-only")
    normalized = value.get("normalized_result")
    if not isinstance(normalized, dict):
        raise ValueError("direct baseline normalized_result is required")
    _validate_normalized_result(normalized, version=baseline_version)
    expected_hash = canonical_hash(normalized)
    if value.get("result_hash") != expected_hash:
        raise ValueError("direct baseline result_hash does not match normalized_result")
    if is_v3:
        if normalized.get("source_complete") is not True and not (
            normalized.get("evidence_gap_codes") or normalized.get("limitations")
        ):
            raise ValueError(
                "an incomplete direct baseline v3 requires explicit evidence gaps or limitations"
            )
        if case is not None and case.schema_version == "2.0":
            _validate_case_evidence(value, normalized, case)
    elif is_v2:
        if normalized.get("source_complete") is not True:
            evidence_scope = (
                (case.expected_output or {}).get("evidence_scope")
                if case is not None and case.schema_version == "2.0"
                else None
            )
            if evidence_scope != "bounded" or not normalized.get("limitations"):
                raise ValueError(
                    "an incomplete direct baseline v2 requires bounded evidence scope and explicit limitations"
                )
        if case is not None and case.schema_version == "2.0":
            _validate_case_evidence(value, normalized, case)
    else:
        _require_sha256(value.get("schema_hash"), "direct baseline schema_hash")
    return normalized


def _validate_baseline_qualification(value: Any, sources: list[Any]) -> None:
    """Validate an optional live-sample qualification separately from SAP facts."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "status",
        "reasons",
        "evidence_source_ids",
        "evidence_hash",
    }:
        raise ValueError("direct baseline qualification has unexpected or missing fields")
    if value.get("status") not in {"qualified", "blocked"}:
        raise ValueError("direct baseline qualification.status is invalid")
    reasons = value.get("reasons")
    if not isinstance(reasons, list) or any(not str(item).strip() for item in reasons):
        raise ValueError("direct baseline qualification.reasons must be an array of identifiers")
    if value.get("status") == "blocked" and not reasons:
        raise ValueError("blocked direct baseline qualification requires a reason")
    evidence_source_ids = value.get("evidence_source_ids")
    if not isinstance(evidence_source_ids, list) or not evidence_source_ids:
        raise ValueError("direct baseline qualification.evidence_source_ids is required")
    known_source_ids = {
        str(source.get("source_id") or "")
        for source in sources
        if isinstance(source, dict)
    }
    if not {str(item) for item in evidence_source_ids}.issubset(known_source_ids):
        raise ValueError("direct baseline qualification references an unknown source")
    _require_sha256(value.get("evidence_hash"), "direct baseline qualification.evidence_hash")


def _validate_expected_output(value: Any, grain: tuple[str, ...]) -> None:
    if not isinstance(value, dict):
        raise ValueError("canonical test case v2 expected_output is required")
    required = {
        "record_fields",
        "metric_ids",
        "minimum_primary_evidence_rows",
        "allow_empty_result",
        "evidence_scope",
    }
    if set(value) != required:
        raise ValueError("canonical test case v2 expected_output has unexpected or missing fields")
    record_fields = value.get("record_fields")
    if not isinstance(record_fields, list) or not record_fields:
        raise ValueError("expected_output.record_fields must be a non-empty array")
    if not set(grain).issubset({str(item) for item in record_fields}):
        raise ValueError("expected_output.record_fields must include expected_grain")
    metrics = value.get("metric_ids")
    if not isinstance(metrics, list) or any(not str(item).strip() for item in metrics):
        raise ValueError("expected_output.metric_ids must be an array of identifiers")
    minimum_rows = value.get("minimum_primary_evidence_rows")
    if not isinstance(minimum_rows, int) or isinstance(minimum_rows, bool) or minimum_rows < 0:
        raise ValueError("minimum_primary_evidence_rows must be a non-negative integer")
    if not isinstance(value.get("allow_empty_result"), bool):
        raise ValueError("allow_empty_result must be boolean")
    if minimum_rows == 0 and value.get("allow_empty_result") is not True:
        raise ValueError(
            "minimum_primary_evidence_rows may be zero only when empty results are allowed"
        )
    if value.get("evidence_scope") not in {"complete", "bounded"}:
        raise ValueError("evidence_scope must be complete or bounded")


def _validate_baseline_source(value: Any, index: int) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"direct baseline sources[{index}] must be an object")
    required = {
        "source_id",
        "service_name",
        "odata_version",
        "entity_set",
        "schema_hash",
        "query_hash",
        "row_count",
        "page_count",
        "stable_order_by",
        "paging_complete",
        "source_complete",
        "primary",
    }
    if set(value) != required:
        raise ValueError(f"direct baseline sources[{index}] has unexpected or missing fields")
    for field in ("source_id", "service_name", "entity_set"):
        if not str(value.get(field) or "").strip():
            raise ValueError(f"direct baseline sources[{index}].{field} is required")
    if value.get("odata_version") not in {"2.0", "4.0"}:
        raise ValueError(f"direct baseline sources[{index}].odata_version is invalid")
    _require_sha256(value.get("schema_hash"), f"direct baseline sources[{index}].schema_hash")
    _require_sha256(value.get("query_hash"), f"direct baseline sources[{index}].query_hash")
    for field in ("row_count", "page_count"):
        number = value.get(field)
        if not isinstance(number, int) or isinstance(number, bool) or number < (1 if field == "page_count" else 0):
            raise ValueError(f"direct baseline sources[{index}].{field} is invalid")
    order = value.get("stable_order_by")
    if not isinstance(order, list) or not order or any(not str(item).strip() for item in order):
        raise ValueError(f"direct baseline sources[{index}].stable_order_by is required")
    for field in ("paging_complete", "source_complete", "primary"):
        if not isinstance(value.get(field), bool):
            raise ValueError(f"direct baseline sources[{index}].{field} must be boolean")
    if value.get("paging_complete") is not True or value.get("source_complete") is not True:
        raise ValueError(f"direct baseline sources[{index}] is incomplete")


def _validate_baseline_v3_source(value: Any, index: int) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"direct baseline sources[{index}] must be an object")
    required = {
        "access_method",
        "http_method",
        "semantic_read_only",
        "schema_hash",
        "query_hash",
        "stable_order_by",
        "paging_complete",
        "source_complete",
    }
    if not required.issubset(value):
        raise ValueError(f"direct baseline v3 sources[{index}] is missing required fields")
    access_method = value.get("access_method")
    method = value.get("http_method")
    if access_method not in {"odata_get", "adt_data_preview"}:
        raise ValueError(f"direct baseline v3 sources[{index}].access_method is invalid")
    expected_method = "GET" if access_method == "odata_get" else "POST"
    if method != expected_method:
        raise ValueError(
            f"direct baseline v3 sources[{index}] must use {expected_method} for {access_method}"
        )
    if value.get("semantic_read_only") is not True:
        raise ValueError(
            f"direct baseline v3 sources[{index}].semantic_read_only must be true"
        )
    _require_sha256(value.get("schema_hash"), f"direct baseline v3 sources[{index}].schema_hash")
    _require_sha256(value.get("query_hash"), f"direct baseline v3 sources[{index}].query_hash")
    order = value.get("stable_order_by")
    if not isinstance(order, list) or not order or any(not str(item).strip() for item in order):
        raise ValueError(
            f"direct baseline v3 sources[{index}].stable_order_by is required"
        )
    for field in ("paging_complete", "source_complete"):
        if not isinstance(value.get(field), bool):
            raise ValueError(
                f"direct baseline v3 sources[{index}].{field} must be boolean"
            )
    for field in ("row_count", "page_count"):
        if field in value and (
            not isinstance(value[field], int)
            or isinstance(value[field], bool)
            or value[field] < (1 if field == "page_count" else 0)
        ):
            raise ValueError(
                f"direct baseline v3 sources[{index}].{field} is invalid"
            )


def _validate_supplemental_adt_source(value: Any, index: int) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"direct baseline supplemental_sources[{index}] must be an object")
    required = {
        "source_id",
        "provider",
        "object",
        "fields",
        "filter_hash",
        "manifest_hash",
        "row_count",
        "paging_complete",
        "source_complete",
        "read_only",
        "validated",
        "hash_verified",
    }
    if set(value) != required:
        raise ValueError(
            f"direct baseline supplemental_sources[{index}] has unexpected or missing fields"
        )
    if value.get("provider") != "sap-adt-table-export":
        raise ValueError(f"direct baseline supplemental_sources[{index}].provider is invalid")
    for field in ("source_id", "object"):
        if not str(value.get(field) or "").strip():
            raise ValueError(f"direct baseline supplemental_sources[{index}].{field} is required")
    fields = value.get("fields")
    if (
        not isinstance(fields, list)
        or not fields
        or len({str(item) for item in fields}) != len(fields)
        or any(not str(item).strip() for item in fields)
    ):
        raise ValueError(f"direct baseline supplemental_sources[{index}].fields is invalid")
    _require_sha256(
        value.get("filter_hash"),
        f"direct baseline supplemental_sources[{index}].filter_hash",
    )
    _require_sha256(
        value.get("manifest_hash"),
        f"direct baseline supplemental_sources[{index}].manifest_hash",
    )
    row_count = value.get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise ValueError(f"direct baseline supplemental_sources[{index}].row_count is invalid")
    for field in (
        "paging_complete",
        "source_complete",
        "read_only",
        "validated",
        "hash_verified",
    ):
        if value.get(field) is not True:
            raise ValueError(
                f"direct baseline supplemental_sources[{index}].{field} must be true"
            )


def _validate_normalized_result(value: JsonObject, *, version: str = "1.0") -> None:
    if not isinstance(value.get("records"), list):
        raise ValueError("normalized_result.records must be an array")
    if not isinstance(value.get("metrics"), dict):
        raise ValueError("normalized_result.metrics must be an object")
    if not isinstance(value.get("limitations"), list):
        raise ValueError("normalized_result.limitations must be an array")
    if not isinstance(value.get("source_complete"), bool):
        raise ValueError("normalized_result.source_complete must be boolean")
    if version == "3.0":
        if not str(value.get("business_status") or "").strip():
            raise ValueError("normalized_result.business_status is required for baseline v3")
        for field in ("evidence_complete", "business_complete"):
            if not isinstance(value.get(field), bool):
                raise ValueError(f"normalized_result.{field} must be boolean for baseline v3")
        if not isinstance(value.get("evidence_gap_codes"), list) or any(
            not str(item).strip() for item in value.get("evidence_gap_codes") or []
        ):
            raise ValueError(
                "normalized_result.evidence_gap_codes must be an identifier array for baseline v3"
            )


def _validate_case_evidence(
    baseline: JsonObject,
    normalized: JsonObject,
    case: CanonicalTestCase,
) -> None:
    expected = case.expected_output or {}
    primary_rows = sum(
        int(source.get("row_count") or 0)
        for source in baseline.get("sources") or []
        if isinstance(source, dict) and source.get("primary") is True
    )
    minimum_primary_rows = expected.get("minimum_primary_evidence_rows")
    if minimum_primary_rows is None:
        minimum_primary_rows = 1
    if primary_rows < int(minimum_primary_rows):
        raise ValueError("direct baseline does not contain enough primary evidence rows")
    records = normalized.get("records") or []
    if not expected.get("allow_empty_result") and not records:
        raise ValueError("direct baseline business result may not be empty for this case")
    required_fields = {str(item) for item in expected.get("record_fields") or []}
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not required_fields.issubset(record):
            raise ValueError(f"normalized_result.records[{index}] is missing expected fields")
    metrics = normalized.get("metrics") or {}
    missing_metrics = {str(item) for item in expected.get("metric_ids") or []} - set(metrics)
    if missing_metrics:
        raise ValueError(f"normalized_result.metrics is missing {sorted(missing_metrics)!r}")


def _require_sha256(value: Any, field: str) -> None:
    text = str(value or "")
    if not text.startswith("sha256:") or len(text) != 71:
        raise ValueError(f"{field} must be a full SHA-256")


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def agent_execution_digest(
    manifest: JsonObject,
    rules_source: str | None = None,
) -> str:
    """Hash every input that can change deterministic Agent behavior.

    Keeping the managed-rule declaration and source in the same acceptance
    fingerprint prevents a rules.py-only change from reusing an older live
    validation campaign.
    """

    return canonical_hash(
        {
            "execution": manifest.get("execution"),
            "managedRule": manifest.get("managedRule"),
            "rules": source_digest(rules_source) if rules_source is not None else None,
        }
    )


@dataclass(frozen=True)
class SemanticComparison:
    verdict: ComparisonVerdict
    expected_hash: str
    actual_hash: str
    differences: tuple[JsonObject, ...]

    def as_dict(self) -> JsonObject:
        return {
            "verdict": self.verdict,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "differences": list(self.differences),
            "comparison_hash": canonical_hash(
                {
                    "verdict": self.verdict,
                    "expected_hash": self.expected_hash,
                    "actual_hash": self.actual_hash,
                    "differences": list(self.differences),
                }
            ),
        }


def compare_semantic_results(
    expected: JsonObject,
    actual: JsonObject,
    contract: JsonObject,
) -> SemanticComparison:
    """Compare normalized live-SAP results without comparing prose or row order.

    Both operands use the acceptance artifact shape: ``records`` is a list of
    normalized business records, ``metrics`` is a mapping, and
    ``source_complete`` describes executed-source completeness.  Raw SAP rows
    and transport details intentionally do not belong to this contract.
    """

    differences: list[JsonObject] = []
    if expected.get("blocked") is True or actual.get("blocked") is True:
        return SemanticComparison(
            verdict="BLOCKED",
            expected_hash=canonical_hash(expected),
            actual_hash=canonical_hash(actual),
            differences=tuple(),
        )

    key_fields = tuple(str(item) for item in contract.get("business_keys") or [])
    if not key_fields:
        raise ValueError("acceptance contract must declare business_keys")
    fact_fields = tuple(str(item) for item in contract.get("facts") or [])
    decimal_fields = tuple(str(item) for item in contract.get("decimal_fields") or [])
    currency_fields = tuple(str(item) for item in contract.get("currency_fields") or [])
    unit_fields = tuple(str(item) for item in contract.get("unit_fields") or [])
    strict_v2 = str(contract.get("schema_version") or "1.0") in {"2.0", "3.0"}

    blank_key_fields = {
        str(item) for item in contract.get("blank_business_key_fields") or []
    }
    expected_records = _record_map(
        expected.get("records"), key_fields, "expected", blank_key_fields
    )
    actual_records = _record_map(
        actual.get("records"), key_fields, "actual", blank_key_fields
    )
    expected_keys = set(expected_records)
    actual_keys = set(actual_records)
    if expected_keys != actual_keys:
        differences.append(
            {
                "code": "record_set_mismatch",
                "missing_keys": [_key_json(item) for item in sorted(expected_keys - actual_keys)],
                "unexpected_keys": [_key_json(item) for item in sorted(actual_keys - expected_keys)],
            }
        )

    for key in sorted(expected_keys & actual_keys):
        expected_record = expected_records[key]
        actual_record = actual_records[key]
        specialized_fields = set(decimal_fields) | set(currency_fields) | set(unit_fields)
        for field in fact_fields:
            if field in specialized_fields:
                continue
            if _normalized_scalar(expected_record.get(field)) != _normalized_scalar(
                actual_record.get(field)
            ):
                differences.append(
                    {
                        "code": "fact_mismatch",
                        "key": _key_json(key),
                        "field": field,
                        "expected": expected_record.get(field),
                        "actual": actual_record.get(field),
                    }
                )
        for field in decimal_fields:
            if _decimal(expected_record.get(field)) != _decimal(actual_record.get(field)):
                differences.append(
                    {
                        "code": "decimal_mismatch",
                        "key": _key_json(key),
                        "field": field,
                        "expected": expected_record.get(field),
                        "actual": actual_record.get(field),
                    }
                )
        for field in (*currency_fields, *unit_fields):
            if str(expected_record.get(field) or "") != str(actual_record.get(field) or ""):
                differences.append(
                    {
                        "code": "currency_or_unit_mismatch",
                        "key": _key_json(key),
                        "field": field,
                        "expected": expected_record.get(field),
                        "actual": actual_record.get(field),
                    }
                )

    expected_metrics = expected.get("metrics") if isinstance(expected.get("metrics"), dict) else {}
    actual_metrics = actual.get("metrics") if isinstance(actual.get("metrics"), dict) else {}
    for field in (str(item) for item in contract.get("metrics") or []):
        decimal_metrics = {str(item) for item in contract.get("decimal_metrics") or []}
        expected_metric = expected_metrics.get(field)
        actual_metric = actual_metrics.get(field)
        metrics_match = (
            _decimal(expected_metric) == _decimal(actual_metric)
            if field in decimal_metrics
            else _normalized_metric(expected_metric) == _normalized_metric(actual_metric)
        )
        if not metrics_match:
            differences.append(
                {
                    "code": "metric_mismatch",
                    "field": field,
                    "expected": expected_metrics.get(field),
                    "actual": actual_metrics.get(field),
                }
            )

    strict_completeness = str(contract.get("schema_version") or "1.0") == "3.0" or all(
        field in expected for field in ("evidence_complete", "business_complete")
    )
    if strict_completeness:
        for field in ("source_complete", "evidence_complete", "business_complete"):
            if expected.get(field) != actual.get(field):
                differences.append(
                    {
                        "code": "completeness_mismatch",
                        "field": field,
                        "expected": expected.get(field),
                        "actual": actual.get(field),
                    }
                )
    elif expected.get("source_complete") is False and actual.get("source_complete") is True:
        differences.append({"code": "completeness_overstated"})
    if "business_status" in expected and expected.get("business_status") != actual.get(
        "business_status"
    ):
        differences.append(
            {
                "code": "business_status_mismatch",
                "expected": expected.get("business_status"),
                "actual": actual.get("business_status"),
            }
        )

    required_limitations = {
        str(item) for item in contract.get("required_limitations") or [] if str(item)
    }
    actual_limitations = {
        str(item) for item in actual.get("limitations") or [] if str(item)
    }
    missing_limitations = sorted(required_limitations - actual_limitations)
    if missing_limitations:
        differences.append(
            {"code": "required_limitations_missing", "items": missing_limitations}
        )
    if strict_v2:
        expected_limitations = {
            str(item) for item in expected.get("limitations") or [] if str(item)
        }
        missing_expected = sorted(expected_limitations - actual_limitations)
        unexpected = sorted(actual_limitations - expected_limitations)
        if missing_expected:
            differences.append(
                {"code": "baseline_limitations_missing", "items": missing_expected}
            )
        if unexpected:
            differences.append(
                {"code": "unexpected_limitations", "items": unexpected}
            )
    if "evidence_gap_codes" in expected:
        expected_gaps = {
            str(item) for item in expected.get("evidence_gap_codes") or [] if str(item)
        }
        actual_gaps = {
            str(item) for item in actual.get("evidence_gap_codes") or [] if str(item)
        }
        if expected_gaps != actual_gaps:
            differences.append(
                {
                    "code": "evidence_gap_mismatch",
                    "missing": sorted(expected_gaps - actual_gaps),
                    "unexpected": sorted(actual_gaps - expected_gaps),
                }
            )

    return SemanticComparison(
        verdict="MATCH" if not differences else "MISMATCH",
        expected_hash=canonical_hash(expected),
        actual_hash=canonical_hash(actual),
        differences=tuple(differences),
    )


def _record_map(
    value: Any,
    key_fields: tuple[str, ...],
    label: str,
    blank_key_fields: set[str] | None = None,
) -> dict[tuple[str, ...], JsonObject]:
    if not isinstance(value, list):
        raise ValueError(f"{label}.records must be an array")
    result: dict[tuple[str, ...], JsonObject] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{label}.records[{index}] must be an object")
        key = tuple(str(item.get(field) or "") for field in key_fields)
        allowed_blank = blank_key_fields or set()
        if any(not part and field not in allowed_blank for field, part in zip(key_fields, key)):
            raise ValueError(f"{label}.records[{index}] is missing a business key")
        if key in result:
            raise ValueError(f"{label}.records contains duplicate business key {key!r}")
        result[key] = item
    return result


def _key_json(value: tuple[str, ...]) -> list[str]:
    return list(value)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def _normalized_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _normalized_metric(value: Any) -> Any:
    """Compare numeric metric renderings without coercing identifiers or record facts."""

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
            return _decimal(text.replace(",", ""))
    return _normalized_scalar(value)
