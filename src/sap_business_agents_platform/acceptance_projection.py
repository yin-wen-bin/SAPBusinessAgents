"""Bounded, value-free contracts for acceptance-only Runtime output.

No arbitrary JSON Schema or baseline values are admitted from a client. The
contract affects reporting only; it never changes tool permissions or evidence.
"""
from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceProjectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["1.0"] = "1.0"
    record_fields: list[str] = Field(min_length=1, max_length=100)
    metric_fields: list[str] = Field(default_factory=list, max_length=100)
    decimal_fields: list[str] = Field(default_factory=list, max_length=100)
    decimal_metrics: list[str] = Field(default_factory=list, max_length=100)
    boolean_fields: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_fields(self) -> "AcceptanceProjectionSpec":
        for names in (self.record_fields, self.metric_fields, self.decimal_fields,
                      self.decimal_metrics, self.boolean_fields):
            if len(names) != len(set(names)) or any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", name)
                or name == "evidence_refs" for name in names
            ):
                raise ValueError("acceptance fields must be unique canonical identifiers")
        if not set(self.decimal_fields + self.boolean_fields) <= set(self.record_fields):
            raise ValueError("typed acceptance fields must be declared record fields")
        if not set(self.decimal_metrics) <= set(self.metric_fields):
            raise ValueError("decimal metrics must be declared metrics")
        if set(self.decimal_fields) & set(self.boolean_fields):
            raise ValueError("acceptance field types conflict")
        return self


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


def projection_schema(spec: AcceptanceProjectionSpec) -> dict[str, Any]:
    refs = {"type": "array", "items": {"type": "string"}, "minItems": 1}
    fields = {
        name: {"type": ["boolean" if name in spec.boolean_fields else "string", "null"]}
        for name in spec.record_fields
    }
    fields["evidence_refs"] = refs
    return _object({
        "records": {"type": "array", "items": _object(fields), "maxItems": 200},
        "metrics": _object({name: {"type": ["string" if name in spec.decimal_metrics
                                               else "integer", "null"]}
                            for name in spec.metric_fields}),
        "business_status": {"type": "string"},
        "source_complete": {"type": "boolean"},
        "evidence_complete": {"type": "boolean"},
        "business_complete": {"type": "boolean"},
        "evidence_gap_codes": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": refs,
    })


def output_schema(base: dict[str, Any], raw_spec: Any) -> dict[str, Any]:
    result = copy.deepcopy(base)
    if raw_spec is not None:
        spec = AcceptanceProjectionSpec.model_validate(raw_spec)
        result["properties"]["acceptance_projection"] = {
            "anyOf": [projection_schema(spec), {"type": "null"}]
        }
    # Strict structured output requires every property in required, including
    # optional nullable fields (not just nested additionalProperties=false).
    result["required"] = list(result["properties"])
    return result


def validate_projection(raw_spec: Any, value: Any, known: dict[str, Any]) -> list[dict[str, str]]:
    spec = AcceptanceProjectionSpec.model_validate(raw_spec)
    if not Draft202012Validator(projection_schema(spec)).is_valid(value):
        return [{"code": "acceptance_projection_schema_invalid"}]
    references = list(value["evidence_refs"])
    for row in value["records"]:
        references.extend(row["evidence_refs"])
    if any(ref not in known or known[ref].get("source_type") not in {"sap_live", "sap_skill"}
           for ref in references):
        return [{"code": "acceptance_projection_evidence_reference_rejected"}]
    for rows, names in ((value["records"], spec.decimal_fields),
                        ([value["metrics"]], spec.decimal_metrics)):
        for row in rows:
            for name in names:
                if row[name] is None:
                    continue
                try:
                    if not Decimal(row[name]).is_finite():
                        raise InvalidOperation
                except InvalidOperation:
                    return [{"code": "acceptance_projection_decimal_invalid"}]
    if value["source_complete"] and any(known[ref].get("source_complete") is not True
                                        for ref in references):
        return [{"code": "acceptance_projection_source_completeness_overstated"}]
    return []


def visible_projection_issues(raw_spec: Any, value: dict[str, Any], report: dict[str, Any]) -> list[dict[str, str]]:
    """Compare exact canonical cells in both languages, never infer from prose.

The acceptance report includes canonical identifiers as column keys/metric IDs.
Localized labels remain user friendly; unknown values use an explicit null.
"""
    spec = AcceptanceProjectionSpec.model_validate(raw_spec)
    blocks = report.get("blocks") or []
    def cell(item: Any, locale: str) -> str:
        return str(item.get(locale, "") if isinstance(item, dict) else item).strip()
    def normalized(value: Any, decimal: bool = False) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return str(value).lower()
        if decimal:
            try:
                return str(Decimal(str(value)).normalize())
            except InvalidOperation:
                pass
        return str(value).strip()
    for locale in ("zh", "en"):
        actual = []
        for block in blocks:
            keys = [col.get("key") for col in block.get("columns") or []]
            if not set(spec.record_fields) <= set(keys):
                continue
            for row in block.get("rows") or []:
                cells = dict(zip(keys, row.get("values") or []))
                actual.append(tuple(normalized(cell(cells[name], locale), name in spec.decimal_fields)
                                    for name in spec.record_fields))
        expected = [tuple(normalized(row[name], name in spec.decimal_fields)
                          for name in spec.record_fields) for row in value["records"]]
        if sorted(actual) != sorted(expected):
            return [{"code": "acceptance_projection_visible_records_mismatch"}]
        metrics = {item.get("id"): cell(item.get("value"), locale)
                   for block in blocks for item in block.get("metrics") or []}
        expected_metrics = {**value["metrics"], **{name: value[name] for name in (
            "business_status", "source_complete", "evidence_complete", "business_complete")}}
        for name, expected_value in expected_metrics.items():
            if name not in metrics or normalized(metrics[name], name in spec.decimal_metrics) != normalized(expected_value, name in spec.decimal_metrics):
                return [{"code": "acceptance_projection_visible_metrics_mismatch"}]
        for gap in value["evidence_gap_codes"]:
            if not any(cell(entry.get("value"), locale) == gap
                       for block in blocks for entry in block.get("entries") or []):
                return [{"code": "acceptance_projection_visible_gap_missing"}]
    return []
