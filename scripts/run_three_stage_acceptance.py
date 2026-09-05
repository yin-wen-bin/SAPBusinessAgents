from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from sap_business_agents_platform.acceptance import (
    CanonicalTestCase,
    SemanticComparison,
    agent_execution_digest,
    canonical_hash,
    compare_semantic_results,
    validate_direct_baseline,
)
from sap_business_agents_platform.app import create_app
from sap_business_agents_platform.config import Settings
from sap_business_agents_platform.models import RunCreate, RunMode, TERMINAL_STATUSES


JsonObject = dict[str, Any]


def _compare(expected: JsonObject, actual: JsonObject, contract: JsonObject) -> SemanticComparison:
    try:
        return compare_semantic_results(expected, actual, contract)
    except ValueError as exc:
        return SemanticComparison(
            verdict="MISMATCH",
            expected_hash=canonical_hash(expected),
            actual_hash=canonical_hash(actual),
            differences=(
                {
                    "code": "acceptance_normalization_invalid",
                    "message": str(exc)[:1000],
                },
            ),
        )

def _load_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_sensitive_inputs_from_stdin(enabled: bool) -> dict[str, str]:
    if not enabled:
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("--sensitive-input-stdin requires a JSON object on stdin")
    value = json.loads(raw)
    if not isinstance(value, dict) or not value:
        raise ValueError("sensitive input payload must be a non-empty JSON object")
    normalized: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str) or not item.strip():
            raise ValueError("sensitive input names and values must be non-blank strings")
        normalized[name] = item.strip()
    return normalized


def _localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("en") or value.get("zh") or "")
    return str(value or "")


def _normalize_run(
    run: JsonObject,
    case: CanonicalTestCase,
    contract: JsonObject,
) -> JsonObject:
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    projection = result.get("acceptance_projection")
    if isinstance(projection, dict):
        return _normalize_acceptance_projection(projection, case, contract)
    rule_results = [item for item in result.get("rule_results") or [] if isinstance(item, dict)]
    reports = [item for item in rule_results if isinstance(item.get("business_report"), dict)]
    if reports:
        rule_result = reports[-1]
        report = rule_result["business_report"]
        workflow_output = (
            rule_result.get("workflow_output")
            if isinstance(rule_result.get("workflow_output"), dict)
            else result.get("workflow_output")
            if isinstance(result.get("workflow_output"), dict)
            else {}
        )
        record_scope = str(contract.get("record_scope") or "").strip()
        if record_scope:
            scoped_root = workflow_output
            if not isinstance(scoped_root, dict):
                raise ValueError("recordScope requires a structured workflow output")
            report_records = _extract_record_scope(scoped_root, record_scope)
        else:
            report_records = [
                dict(item)
                for item in report.get("records") or []
                if isinstance(item, dict)
            ]
        if not report_records:
            acceptance_tables = [
                item
                for item in report.get("action_tables") or []
                if isinstance(item, dict) and item.get("acceptance_records") is True
            ]
            if len(acceptance_tables) > 1:
                raise ValueError("business report contains multiple acceptance-record tables")
            if acceptance_tables:
                report_records = [
                    dict(item)
                    for item in acceptance_tables[0].get("rows") or []
                    if isinstance(item, dict)
                ]
        report_metrics = {
            str(item.get("id") or item.get("name")): item.get("value")
            for item in report.get("metrics") or []
            if isinstance(item, dict) and (item.get("id") or item.get("name"))
        }
        # Deterministic Agents expose their acceptance metrics as typed output
        # ports even when the business report intentionally omits a redundant
        # metric card.  Prefer the explicit report value, then the validated
        # workflow output; never infer a missing metric from prose.
        for metric_id in contract.get("metrics") or []:
            if metric_id not in report_metrics and metric_id in workflow_output:
                report_metrics[str(metric_id)] = workflow_output[metric_id]

        result_completeness = (
            result.get("completeness")
            if isinstance(result.get("completeness"), dict)
            else {}
        )

        def completeness_value(field: str) -> bool:
            direct = rule_result.get(field)
            if isinstance(direct, bool):
                return direct
            workflow = workflow_output.get(field)
            if isinstance(workflow, bool):
                return workflow
            return bool(result_completeness.get(field))

        report_limitations = [
            str(item)
            for item in [
                *((report.get("missing_evidence") or []) if contract.get("schema_version") != "2.0" else []),
                *(report.get("limitations") or []),
            ]
            if str(item)
        ]
        overview_text = _localized(report.get("overview"))
        for code, keywords in (contract.get("limitation_keywords") or {}).items():
            if any(str(keyword).casefold() in overview_text.casefold() for keyword in keywords or []):
                report_limitations.append(str(code))
        normalized = {
            "records": report_records,
            "metrics": report_metrics,
            "limitations": list(dict.fromkeys(report_limitations)),
            "source_complete": completeness_value("source_complete"),
            "evidence_complete": completeness_value("evidence_complete"),
            "business_complete": completeness_value("business_complete"),
            "business_status": str(rule_result.get("business_status") or ""),
            "evidence_gap_codes": list(
                dict.fromkeys(
                    str(item)
                    for item in [
                        *(rule_result.get("evidence_gaps") or []),
                        *(rule_result.get("missing_evidence") or []),
                        *((report.get("missing_evidence") or []) if contract.get("schema_version") == "2.0" else []),
                    ]
                    if str(item)
                )
            ),
        }
        return _finalize_normalized(
            normalized,
            case,
            contract,
            business_status=str(rule_result.get("business_status") or ""),
            records_are_canonical=True,
        )

    presentation = result.get("presentation") if isinstance(result.get("presentation"), dict) else {}
    records: list[JsonObject] = []
    metrics: JsonObject = {}
    limitations: list[str] = []
    source_flags: list[bool] = []
    key_value_record: JsonObject = {}
    expected_grain = set(case.expected_grain)
    expected_record_fields = expected_grain | {
        str(item)
        for name in (
            "facts",
            "decimal_fields",
            "currency_fields",
            "unit_fields",
            "date_fields",
        )
        for item in contract.get(name) or []
        if str(item)
    }
    aliases = _field_aliases(contract)
    for block in presentation.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("source_complete"), bool):
            source_flags.append(bool(block["source_complete"]))
        if block.get("type") == "table":
            raw_columns = [
                str(item.get("key") or "")
                for item in block.get("columns") or []
                if isinstance(item, dict)
            ]
            columns = [
                aliases.get(_field_token(key), key)
                for key in raw_columns
            ]
            # Harness presentations can contain operational stage tables as well
            # as the business-record table.  Only the table that carries the
            # canonical stable grain may participate in semantic acceptance.
            if expected_record_fields and not expected_record_fields.issubset(set(columns)):
                continue
            for row in block.get("rows") or []:
                values = row.get("values") if isinstance(row, dict) else None
                if isinstance(values, list) and len(values) == len(columns):
                    records.append(
                        {key: _localized(value) for key, value in zip(raw_columns, values)}
                    )
        elif block.get("type") == "key_value":
            for item in block.get("entries") or []:
                if not isinstance(item, dict):
                    continue
                canonical = aliases.get(_field_token(_localized(item.get("label"))))
                if canonical:
                    key_value_record[canonical] = _localized(item.get("value"))
        elif block.get("type") == "metrics":
            for item in block.get("metrics") or []:
                if isinstance(item, dict) and item.get("id"):
                    metric = aliases.get(_field_token(item["id"]), str(item["id"]))
                    metrics[metric] = _localized(item.get("value"))
        elif block.get("claim_scope") == "diagnostic" or block.get("type") == "notice":
            warning_notice = (
                block.get("type") == "notice"
                and block.get("tone") in {"warning", "error"}
            )
            values = block.get("items") or ([block.get("text")] if block.get("text") else [])
            for item in values:
                text = _localized(item)
                explicit_codes = [
                    str(code)
                    for code in (contract.get("limitation_keywords") or {})
                    if str(code).casefold() in text.casefold()
                ]
                if _notice_is_ignored(text, contract) and not explicit_codes:
                    continue
                informational_business_notice = (
                    block.get("type") == "notice"
                    and block.get("tone") not in {"warning", "error"}
                )
                codes = (
                    explicit_codes
                    if informational_business_notice
                    else _limitation_codes(text, contract)
                )
                # Informational diagnostic notices (for example a positive
                # source-completeness statement) are not limitations unless
                # they match a declared limitation code. Warning/error notices
                # remain auditable even when no code is configured yet.
                if warning_notice or codes != [text.strip()]:
                    limitations.extend(codes)
    if key_value_record and (
        not expected_grain or expected_grain.issubset(set(key_value_record))
    ):
        records.append(key_value_record)
    normalized = {
        "records": records,
        "metrics": metrics,
        "limitations": list(dict.fromkeys(item for item in limitations if item)),
        "source_complete": (
            bool((result.get("completeness") or {}).get("source_complete"))
            if isinstance((result.get("completeness") or {}).get("source_complete"), bool)
            else bool(source_flags) and all(source_flags)
        ),
        "evidence_complete": bool(
            (result.get("completeness") or {}).get("evidence_complete")
        ),
        "business_complete": bool(
            (result.get("completeness") or {}).get("business_complete")
        ),
        "business_status": "",
        "evidence_gap_codes": list(
            dict.fromkeys(str(item) for item in limitations if str(item))
        ),
    }
    return _finalize_normalized(normalized, case, contract, business_status="")


_RECORD_SCOPE_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[\])?$")


def _extract_record_scope(root: JsonObject, path: str) -> list[JsonObject]:
    """Resolve a deliberately small JSON path and inherit scalar parent context."""

    segments = path.split(".")
    if not segments or any(not _RECORD_SCOPE_SEGMENT.fullmatch(item) for item in segments):
        raise ValueError("recordScope contains an unsupported JSON path")
    current: list[tuple[Any, JsonObject]] = [(root, {})]
    for segment in segments:
        is_array = segment.endswith("[]")
        field = segment[:-2] if is_array else segment
        next_values: list[tuple[Any, JsonObject]] = []
        for value, inherited in current:
            if not isinstance(value, dict) or field not in value:
                raise ValueError(f"recordScope field {field} is unavailable")
            parent_scalars = {
                str(key): item
                for key, item in value.items()
                if not isinstance(item, (dict, list)) and key != field
            }
            context = {**inherited, **parent_scalars}
            selected = value[field]
            if is_array:
                if not isinstance(selected, list):
                    raise ValueError(f"recordScope field {field} is not an array")
                for item in selected:
                    if not isinstance(item, dict):
                        raise ValueError(f"recordScope field {field} contains a non-object")
                    next_values.append((item, context))
            else:
                if not isinstance(selected, dict):
                    raise ValueError(f"recordScope field {field} is not an object")
                next_values.append((selected, context))
        current = next_values
    return [{**inherited, **value} for value, inherited in current if isinstance(value, dict)]


def _normalize_acceptance_projection(
    projection: JsonObject,
    case: CanonicalTestCase,
    contract: JsonObject,
) -> JsonObject:
    required = {
        "records",
        "metrics",
        "business_status",
        "source_complete",
        "evidence_complete",
        "business_complete",
        "evidence_gap_codes",
        "evidence_refs",
    }
    if set(projection) != required:
        raise ValueError("acceptance_projection has unexpected or missing fields")
    if not isinstance(projection.get("records"), list) or any(
        not isinstance(item, dict) for item in projection.get("records") or []
    ):
        raise ValueError("acceptance_projection.records must be an object array")
    if not isinstance(projection.get("metrics"), dict):
        raise ValueError("acceptance_projection.metrics must be an object")
    for field in ("source_complete", "evidence_complete", "business_complete"):
        if not isinstance(projection.get(field), bool):
            raise ValueError(f"acceptance_projection.{field} must be boolean")
    for field in ("evidence_gap_codes", "evidence_refs"):
        if not isinstance(projection.get(field), list) or any(
            not str(item).strip() for item in projection.get(field) or []
        ):
            raise ValueError(f"acceptance_projection.{field} must be an identifier array")
    if projection.get("records") and not projection.get("evidence_refs"):
        raise ValueError("acceptance_projection records require verified evidence references")
    projected_codes = list(dict.fromkeys(str(item) for item in projection["evidence_gap_codes"]))
    declared_limitations = {
        str(item) for item in contract.get("required_limitations") or [] if str(item)
    }
    # The legacy acceptance projection has one diagnostic-code array.  Split
    # contract-declared scope limitations from actual evidence gaps before the
    # semantic comparison so a non-blocking limitation cannot lower completeness.
    limitation_codes = [item for item in projected_codes if item in declared_limitations]
    evidence_gap_codes = [item for item in projected_codes if item not in declared_limitations]
    return _finalize_normalized(
        {
            "records": [dict(item) for item in projection["records"]],
            "metrics": dict(projection["metrics"]),
            "limitations": limitation_codes,
            "source_complete": projection["source_complete"],
            "evidence_complete": projection["evidence_complete"],
            "business_complete": projection["business_complete"],
            "business_status": str(projection.get("business_status") or ""),
            "evidence_gap_codes": evidence_gap_codes,
        },
        case,
        contract,
        business_status=str(projection.get("business_status") or ""),
        records_are_canonical=True,
    )


def _field_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())


def _field_aliases(contract: JsonObject) -> dict[str, str]:
    fields = {
        str(item)
        for name in (
            "business_keys",
            "facts",
            "metrics",
            "decimal_fields",
            "currency_fields",
            "unit_fields",
        )
        for item in contract.get(name) or []
    }
    aliases = {_field_token(field): field for field in fields}
    for canonical, values in (contract.get("field_aliases") or {}).items():
        aliases[_field_token(canonical)] = str(canonical)
        for value in values or []:
            aliases[_field_token(value)] = str(canonical)
    return aliases


def _normalize_record(
    original: JsonObject,
    case: CanonicalTestCase,
    contract: JsonObject,
) -> JsonObject:
    aliases = _field_aliases(contract)
    record = {
        aliases.get(_field_token(key), str(key)): value
        for key, value in original.items()
    }
    token_values = {_field_token(key): value for key, value in record.items()}
    for canonical, extractor in (contract.get("field_extractors") or {}).items():
        if not isinstance(extractor, dict):
            continue
        if (
            extractor.get("always") is not True
            and record.get(canonical) not in {None, ""}
        ):
            continue
        configured_sources = extractor.get("source")
        source_names = (
            configured_sources
            if isinstance(configured_sources, list)
            else [configured_sources]
        )
        source = next(
            (
                token_values[_field_token(source_name)]
                for source_name in source_names
                if _field_token(source_name) in token_values
            ),
            None,
        )
        text = _localized(source).strip()
        value: Any = None
        contains = extractor.get("contains")
        if isinstance(contains, dict):
            lowered = text.casefold()
            value = next(
                (
                    mapped
                    for needle, mapped in contains.items()
                    if str(needle).casefold() in lowered
                ),
                None,
            )
        pattern = extractor.get("pattern")
        if value is None and isinstance(pattern, str) and pattern:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(1) if match.lastindex else match.group(0)
        record[str(canonical)] = extractor.get("default", "") if value is None else value
    for canonical, input_name in (contract.get("input_defaults") or {}).items():
        if record.get(canonical) in {None, ""}:
            record[canonical] = case.input.get(str(input_name), "")
    for canonical, constant in (contract.get("constant_defaults") or {}).items():
        if record.get(canonical) in {None, ""}:
            record[canonical] = constant
    null_values = {"—", "-", "–", "none", "null", "n/a"}
    for field, current in tuple(record.items()):
        if str(current or "").strip().casefold() in null_values:
            record[field] = ""
    for field in contract.get("date_fields") or []:
        text = str(record.get(field) or "").strip()
        record[field] = (
            ""
            if text.casefold() in null_values or text.startswith(("—", "–"))
            else text[:10]
        )
    for field in contract.get("code_set_fields") or []:
        codes = sorted(set(re.findall(r"\b\d{2}\b", str(record.get(field) or ""))))
        record[field] = ";".join(codes)
    for field, width in (contract.get("zero_pad_fields") or {}).items():
        text = str(record.get(field) or "").strip()
        if text.isdigit():
            record[field] = text.zfill(int(width))
    for field in contract.get("boolean_fields") or []:
        current = record.get(field)
        if isinstance(current, bool) or current is None:
            continue
        text = str(current).strip().casefold()
        if text.startswith(("yes", "true", "是")):
            record[field] = True
        elif text.startswith(("no", "false", "否")):
            record[field] = False
    for field in contract.get("decimal_fields") or []:
        text = str(record.get(field) or "").strip()
        match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
        if match:
            record[field] = match.group(0).replace(",", "")
        currency_field = (contract.get("currency_from_decimal") or {}).get(field)
        if currency_field and not record.get(currency_field):
            currency_match = re.search(r"\b([A-Z]{3})\b", text.upper())
            if currency_match:
                record[currency_field] = currency_match.group(1)
    for field, mapping in (contract.get("value_mappings") or {}).items():
        current = str(record.get(field) or "").strip()
        mapped = (mapping or {}).get(current.casefold())
        if mapped is not None:
            record[field] = mapped
    for field, parts in (contract.get("composite_key_parts") or {}).items():
        current = record.get(field)
        if not isinstance(current, str) or "|" in current or "=" not in current:
            continue
        labelled = {
            _field_token(label): value.strip()
            for item in current.split(";")
            if "=" in item
            for label, value in [item.split("=", 1)]
        }
        ordered: list[str] = []
        complete = True
        for part in parts or []:
            if not isinstance(part, dict):
                complete = False
                break
            aliases = [part.get("name"), *(part.get("aliases") or [])]
            value = next(
                (
                    labelled[_field_token(alias)]
                    for alias in aliases
                    if _field_token(alias) in labelled
                ),
                None,
            )
            if value is None:
                complete = False
                break
            ordered.append(value)
        if complete and ordered:
            record[field] = "|".join(ordered)
    for field in contract.get("composite_blank_fields") or []:
        current = record.get(field)
        if not isinstance(current, str) or "|" not in current:
            continue
        blank_tokens = {
            "",
            "blank",
            "empty",
            "none",
            "null",
            "n/a",
            "<blank>",
            "(blank)",
            "<empty>",
            "(empty)",
        }
        record[field] = "|".join(
            "(blank)" if segment.strip().casefold() in blank_tokens else segment.strip()
            for segment in current.split("|")
        )
    for field, keywords in (contract.get("blank_value_keywords") or {}).items():
        current = str(record.get(field) or "").strip().casefold()
        if current and any(str(item).casefold() in current for item in keywords or []):
            record[field] = ""
    return record


def _finalize_normalized(
    value: JsonObject,
    case: CanonicalTestCase,
    contract: JsonObject,
    *,
    business_status: str,
    records_are_canonical: bool = False,
) -> JsonObject:
    records = [item for item in value.get("records") or [] if isinstance(item, dict)]
    metrics = value.get("metrics") if isinstance(value.get("metrics"), dict) else {}
    limitations = list(
        dict.fromkeys(str(item) for item in value.get("limitations") or [] if str(item))
    )
    for metric, mapping in (contract.get("limitations_from_metrics") or {}).items():
        raw_metric = metrics.get(metric)
        state = "missing" if raw_metric in {None, ""} else "value"
        if state == "value":
            match = re.fullmatch(
                r"[-+]?\d[\d,]*(?:\.\d+)?",
                str(raw_metric).strip(),
            )
            if match and Decimal(match.group(0).replace(",", "")) == 0:
                state = "zero"
        limitation = mapping.get(state) if isinstance(mapping, dict) else None
        if limitation and str(limitation) not in limitations:
            limitations.append(str(limitation))
    derived_business_status = False
    status_from_any = contract.get("business_status_from_any_positive_metric")
    if isinstance(status_from_any, dict) and status_from_any.get("metrics"):
        selected_values: list[Decimal] = []
        for metric_id in status_from_any.get("metrics") or []:
            raw_metric = metrics.get(str(metric_id))
            match = re.search(
                r"[-+]?\d[\d,]*(?:\.\d+)?",
                "" if raw_metric is None else str(raw_metric).strip(),
            )
            if match:
                selected_values.append(Decimal(match.group(0).replace(",", "")))
        if len(selected_values) == len(status_from_any.get("metrics") or []):
            business_status = str(
                status_from_any.get("positive" if any(value > 0 for value in selected_values) else "zero")
                or business_status
            )
            derived_business_status = True
    force_capability_blocked = bool(
        set(contract.get("blocking_limitations") or []) & set(limitations)
    )
    if force_capability_blocked:
        business_status = "capability_blocked"
    defaults = {
        canonical: case.input.get(str(input_name), "")
        for canonical, input_name in (contract.get("input_defaults") or {}).items()
    }
    defaults.update(contract.get("constant_defaults") or {})
    for fact_field, metric_id in (contract.get("zero_fact_when_metric_zero") or {}).items():
        raw_metric = metrics.get(str(metric_id))
        metric_text = "" if raw_metric is None else str(raw_metric).strip()
        match = re.fullmatch(r"[-+]?\d[\d,]*(?:\.\d+)?", metric_text)
        if match and Decimal(match.group(0).replace(",", "")) == 0:
            defaults.setdefault(str(fact_field), "0")
    if business_status:
        defaults.setdefault("business_status", business_status)
    status_rule = contract.get("business_status_from_metric")
    if not defaults.get("business_status") and isinstance(status_rule, dict):
        metric_id = str(status_rule.get("metric") or "")
        raw_metric = metrics.get(metric_id)
        if raw_metric not in {None, ""}:
            match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(raw_metric))
            if match:
                metric_value = float(match.group(0).replace(",", ""))
                defaults["business_status"] = str(
                    status_rule.get("zero" if metric_value == 0 else "nonzero") or ""
                )
    record_contract = (
        {**contract, "field_aliases": {}}
        if records_are_canonical
        else contract
    )
    if not records and contract.get("summary_record") is True:
        records = [_normalize_record(defaults, case, record_contract)]
    else:
        records = [
            _normalize_record({**defaults, **record}, case, record_contract)
            for record in records
        ]
    if force_capability_blocked:
        for record in records:
            record["business_status"] = "capability_blocked"
    elif derived_business_status:
        for record in records:
            record["business_status"] = business_status
    value["records"] = records
    for metric in contract.get("decimal_metrics") or []:
        raw = metrics.get(metric)
        if isinstance(raw, str):
            normalized_text = raw.strip().casefold()
            if normalized_text in {
                "unknown",
                "unavailable",
                "undetermined",
                "not determined",
                "not available",
                "not calculable",
                "not calculated",
                "null",
            } or normalized_text.startswith(("unknown ", "unavailable ", "not available ", "not calculated ")):
                metrics[metric] = None
                continue
            match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", raw)
            if match:
                metrics[metric] = match.group(0).replace(",", "")
    for metric, raw in list(metrics.items()):
        mapping = (contract.get("metric_value_mappings") or {}).get(metric) or {}
        mapping_key = str(raw or "").strip().casefold()
        if mapping_key in mapping:
            metrics[metric] = mapping[mapping_key]
            continue
        if isinstance(raw, str) and raw.strip().casefold() in {
            "unknown",
            "unavailable",
            "undetermined",
            "not determined",
            "not available",
            "not calculable",
            "null",
        }:
            metrics[metric] = None
    value["metrics"] = metrics
    value["limitations"] = limitations
    return value


def _limitation_codes(text: str, contract: JsonObject) -> list[str]:
    lowered = text.casefold()
    matches: list[str] = []
    for code, keywords in (contract.get("limitation_keywords") or {}).items():
        if str(code).casefold() in lowered or any(
            str(keyword).casefold() in lowered for keyword in keywords or []
        ):
            matches.append(str(code))
    return list(dict.fromkeys(matches)) or [text.strip()]


def _notice_is_ignored(text: str, contract: JsonObject) -> bool:
    lowered = text.casefold()
    return any(
        str(keyword).casefold() in lowered
        for keyword in contract.get("ignored_notice_keywords") or []
    )


async def _wait_api(client: httpx.AsyncClient, api_url: str, run_id: str, timeout: int) -> JsonObject:
    queue_deadline = time.monotonic() + max(timeout * 5, 1800)
    active_deadline: float | None = None
    while True:
        response = await client.get(f"{api_url}/api/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run.get("status") in {item.value for item in TERMINAL_STATUSES}:
            return run
        now = time.monotonic()
        if run.get("status") == "queued":
            if now >= queue_deadline:
                raise TimeoutError(f"run {run_id} did not leave the queue within the campaign queue budget")
        else:
            active_deadline = active_deadline or (now + timeout)
            if now >= active_deadline:
                raise TimeoutError(f"run {run_id} did not finish within {timeout}s of starting")
        await asyncio.sleep(1)


def _acceptance_prompt(case: CanonicalTestCase, contract: JsonObject) -> str:
    """Append only the value-free reporting contract required for comparison."""
    grain = ", ".join(str(item) for item in contract.get("business_keys") or [])
    fact_fields = list(
        dict.fromkeys(
            str(item)
            for name in (
                "facts",
                "decimal_fields",
                "currency_fields",
                "unit_fields",
                "date_fields",
            )
            for item in contract.get(name) or []
            if str(item)
        )
    )
    facts = ", ".join(fact_fields)
    metrics = ", ".join(str(item) for item in contract.get("metrics") or [])
    limitations = ", ".join(
        str(item) for item in contract.get("required_limitations") or []
    )
    blocking_limitations = ", ".join(
        str(item) for item in contract.get("blocking_limitations") or []
    )
    instructions = [
        "Acceptance reporting contract (do not alter the business conditions or infer values):",
        f"- Return every business record at stable grain [{grain}] and include business_status on each record.",
        "- business_status is the deterministic business conclusion at the declared record grain, never a raw SAP status field. The projection root is the conservative overall conclusion.",
        f"- Use these canonical fact field identifiers exactly: [{facts or 'none'}].",
        f"- Use these canonical metric identifiers exactly and do not omit zero values: [{metrics or 'none'}].",
        "- If a metric cannot be established because required evidence is unavailable, return null; never substitute zero for unknown.",
        "- For additive quantity or amount facts, a complete exact source query with zero rows establishes 0; an unavailable or non-attributable source establishes null.",
        "- A validated structured source whose public record array is empty and whose source_complete and evidence_complete flags are both true is an authoritative complete-zero result. Do not require a nonempty artifact, do not add a materialization gap, and set all record-count metrics for that declared scope to 0.",
        "- Put homogeneous records in a table, aggregate values in metrics, and preserve SAP source completeness separately from display pagination.",
        "- This is an acceptance-mode run. Populate acceptance_projection with the same complete canonical records and metrics shown in the validated presentation.",
        "- acceptance_projection must separately report business_status, source_complete, evidence_complete, business_complete, evidence_gap_codes, and only run-scoped verified evidence_refs.",
        "- Never infer acceptance_projection from prose and never omit a required record merely because it is normal, zero, or not_found.",
    ]
    record_scope = str(contract.get("record_scope") or "").strip()
    if record_scope:
        instructions.extend(
            [
                f"- Record scope: {record_scope}",
                "- The record scope is authoritative. Include only objects selected by that path in acceptance_projection.records; do not add parent summaries, empty-result customer placeholders, or aggregate rows as canonical records. Preserve those outcomes in the declared metrics and visible report instead.",
            ]
        )
    for metric_id, definition in (contract.get("metric_definitions") or {}).items():
        instructions.append(f"- Metric {metric_id}: {definition}")
    for fact_id, definition in (contract.get("fact_definitions") or {}).items():
        instructions.append(f"- Fact {fact_id}: {definition}")
    qualification_definition = str(
        contract.get("test_data_qualification_definition") or ""
    ).strip()
    if qualification_definition:
        instructions.append(
            f"- Test-data qualification rule: {qualification_definition}"
        )
    nonblocking_codes = ", ".join(
        str(item) for item in contract.get("nonblocking_observation_codes") or []
    )
    if nonblocking_codes:
        instructions.append(
            "- Treat these diagnostic codes as non-blocking observations, not evidence "
            f"limitations or capability blockers: [{nonblocking_codes}]."
        )
    composite_blank_fields = ", ".join(
        str(item) for item in contract.get("composite_blank_fields") or []
    )
    blank_business_key_fields = ", ".join(
        str(item) for item in contract.get("blank_business_key_fields") or []
    )
    if blank_business_key_fields:
        instructions.append(
            "- These business-key segments may legitimately be blank; preserve them as "
            f"blank rather than inventing an identifier: [{blank_business_key_fields}]."
        )
    if composite_blank_fields:
        instructions.append(
            "- In these composite fields, represent every missing key segment exactly "
            f"as (blank): [{composite_blank_fields}]."
        )
    for field, parts in (contract.get("composite_key_parts") or {}).items():
        names = [
            str(item.get("name"))
            for item in parts or []
            if isinstance(item, dict) and item.get("name")
        ]
        if names:
            instructions.append(
                f"- Composite field {field} must contain values only, joined by | in this "
                f"exact part order (do not include part labels): [{' | '.join(names)}]."
            )
    status_definition = str(contract.get("business_status_definition") or "").strip()
    if status_definition:
        instructions.append(f"- business_status rule: {status_definition}")
    if blocking_limitations:
        instructions.append(
            f"- Use business_status=capability_blocked when any of these blocking limitations remains unresolved: [{blocking_limitations}]."
        )
    if limitations:
        instructions.append(
            "- Always include these required limitation codes in the result; they describe "
            f"scope boundaries even when the source queries are complete: [{limitations}]."
        )
    return f"{case.question['en']}\n\n" + "\n".join(instructions)


def _projection_spec(contract: JsonObject) -> JsonObject:
    from sap_business_agents_platform.acceptance_projection import AcceptanceProjectionSpec
    fields = list(dict.fromkeys(str(item) for key in (
        "business_keys", "facts", "decimal_fields", "currency_fields", "unit_fields", "date_fields"
    ) for item in contract.get(key) or []))
    return AcceptanceProjectionSpec(
        record_fields=fields,
        metric_fields=list(contract.get("metrics") or []),
        decimal_fields=list(contract.get("decimal_fields") or []),
        decimal_metrics=list(contract.get("decimal_metrics") or []),
        boolean_fields=list(contract.get("boolean_fields") or []),
    ).model_dump(mode="json")


def _validate_projection_matches_visible_report(
    run: JsonObject,
    projection: JsonObject,
    case: CanonicalTestCase,
    contract: JsonObject,
) -> SemanticComparison:
    visible_run = copy.deepcopy(run)
    result = visible_run.get("result")
    if isinstance(result, dict):
        result.pop("acceptance_projection", None)
    visible = _normalize_run(visible_run, case, contract)
    expected = {
        "records": projection.get("records") or [],
        "metrics": projection.get("metrics") or {},
        "limitations": [],
        "source_complete": True,
    }
    actual = {
        "records": visible.get("records") or [],
        "metrics": visible.get("metrics") or {},
        "limitations": [],
        "source_complete": True,
    }
    relaxed = {
        **contract,
        "schema_version": "2.0",
        "required_limitations": [],
    }
    return _compare(expected, actual, relaxed)


async def _run_free_query(
    api_url: str,
    case: CanonicalTestCase,
    contract: JsonObject,
    timeout: int,
    *,
    sensitive_inputs: dict[str, str],
) -> JsonObject:
    # Reporting instructions stay value-free; explicit user query inputs are
    # supplied separately. Never include secure business values or baseline facts.
    public_input = {key: value for key, value in case.input.items()
                    if key != "receipt_reference"}
    query = (_acceptance_prompt(case, contract) + "\n\nCanonical query inputs: "
             + json.dumps(public_input, ensure_ascii=False))
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
        response = await client.post(
            f"{api_url}/api/runs",
            # The live campaign stores both languages.  Use the English form for
            # the machine-driven run so a damaged legacy zh encoding cannot
            # silently change the business conditions being accepted.
            json={
                "mode": "free_query",
                "query": query,
                "acceptanceSpec": _projection_spec(contract),
                "sensitiveInputs": sensitive_inputs,
            },
        )
        response.raise_for_status()
        return await _wait_api(client, api_url, str(response.json()["run_id"]), timeout)


async def _read_completed_free_query(api_url: str, run_id: str) -> JsonObject:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
        response = await client.get(f"{api_url}/api/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
    if run.get("mode") != "free_query" or run.get("status") not in {
        item.value for item in TERMINAL_STATUSES
    }:
        raise ValueError("--free-run-id must identify a terminal free-query run")
    return run


async def _run_fixed(
    root: Path,
    output: Path,
    case: CanonicalTestCase,
    timeout: int,
    agent_snapshot: JsonObject,
    *,
    rules_source: str | None,
    sensitive_inputs: dict[str, str],
) -> JsonObject:
    settings = Settings.from_env(root)
    isolated = replace(
        settings,
        data_root=output / "fixed-runtime",
        draft_root=output / "fixed-drafts",
        free_query_runtime="planner_legacy",
        max_run_seconds=timeout,
        enforce_agent_acceptance=False,
    )
    app = create_app(isolated)
    async with app.router.lifespan_context(app):
        coordinator = app.state.coordinator
        run_id = await coordinator.submit_agent_snapshot(
            agent_snapshot,
            case.input,
            sensitive_inputs=sensitive_inputs,
            rules_source=rules_source,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = app.state.store.get_run(run_id)
            if run.status in TERMINAL_STATUSES:
                return run.model_dump(mode="json")
            await asyncio.sleep(0.5)
    raise TimeoutError(f"fixed Agent {case.agent_id} did not finish within {timeout}s")


def _read_fixed_result(path: Path, case: CanonicalTestCase) -> JsonObject:
    result = _load_json(path)
    if result.get("mode") != "agent" or result.get("agent_id") != case.agent_id:
        raise ValueError("--fixed-result must identify the requested fixed Agent")
    if result.get("input") != case.input:
        raise ValueError("--fixed-result input does not match the CanonicalTestCase")
    if not result.get("run_id") or not result.get("completed_at"):
        raise ValueError("--fixed-result must be a completed fixed-Agent result artifact")
    completeness = result.get("completeness") if isinstance(result.get("completeness"), dict) else {}
    status = (
        "completed"
        if completeness.get("source_complete") is True and completeness.get("business_complete") is True
        else "inconclusive"
    )
    return {
        "run_id": result["run_id"],
        "mode": "agent",
        "status": status,
        "result": result,
    }


async def _main(args: argparse.Namespace) -> int:
    root = Path(args.repository).resolve()
    sensitive_inputs = _read_sensitive_inputs_from_stdin(args.sensitive_input_stdin)
    case = CanonicalTestCase.from_dict(_load_json(Path(args.case).resolve()))
    baseline_payload = _load_json(Path(args.baseline).resolve())
    baseline = validate_direct_baseline(baseline_payload, case)
    manifest_path = (
        Path(args.agent_snapshot).resolve()
        if args.agent_snapshot
        else root / "agents" / args.module / case.agent_id / "agent.json"
    )
    manifest = _load_json(manifest_path)
    if manifest.get("slug") != case.agent_id:
        raise ValueError("candidate Agent snapshot does not match the CanonicalTestCase")
    if args.agent_version and manifest.get("version") != args.agent_version:
        raise ValueError("candidate Agent version does not match the campaign pin")
    rules_source = (
        Path(args.rules_source).resolve().read_text(encoding="utf-8")
        if args.rules_source
        else None
    )
    execution_digest = agent_execution_digest(manifest, rules_source)
    if args.agent_execution_digest and execution_digest != args.agent_execution_digest:
        raise ValueError("candidate Agent execution digest does not match the campaign pin")
    contract_value = manifest["execution"]["acceptance"]
    acceptance_contract_digest = canonical_hash(contract_value)
    if (
        args.acceptance_contract_digest
        and acceptance_contract_digest != args.acceptance_contract_digest
    ):
        raise ValueError("candidate Agent acceptance contract digest does not match the campaign pin")
    contract = {
        "schema_version": contract_value.get("schemaVersion", "1.0"),
        "business_keys": contract_value["businessKeys"],
        "facts": contract_value["facts"],
        "metrics": contract_value["metrics"],
        "required_limitations": contract_value["requiredLimitations"],
        "decimal_fields": contract_value.get("decimalFields") or [],
        "currency_fields": contract_value.get("currencyFields") or [],
        "unit_fields": contract_value.get("unitFields") or [],
        "decimal_metrics": contract_value.get("decimalMetricIds") or [],
        "field_aliases": contract_value.get("fieldAliases") or {},
        "field_extractors": contract_value.get("fieldExtractors") or {},
        "input_defaults": contract_value.get("inputDefaults") or {},
        "constant_defaults": contract_value.get("constantDefaults") or {},
        "fact_definitions": contract_value.get("factDefinitions") or {},
        "date_fields": contract_value.get("dateFields") or [],
        "code_set_fields": contract_value.get("codeSetFields") or [],
        "zero_pad_fields": contract_value.get("zeroPadFields") or {},
        "boolean_fields": contract_value.get("booleanFields") or [],
        "currency_from_decimal": contract_value.get("currencyFromDecimal") or {},
        "value_mappings": contract_value.get("valueMappings") or {},
        "limitation_keywords": contract_value.get("limitationKeywords") or {},
        "summary_record": contract_value.get("summaryRecord") is True,
        "business_status_from_metric": contract_value.get("businessStatusFromMetric") or {},
        "limitations_from_metrics": contract_value.get("limitationsFromMetrics") or {},
        "blank_value_keywords": contract_value.get("blankValueKeywords") or {},
        "blocking_limitations": contract_value.get("blockingLimitations") or [],
        "ignored_notice_keywords": contract_value.get("ignoredNoticeKeywords") or [],
        "metric_value_mappings": contract_value.get("metricValueMappings") or {},
        "zero_fact_when_metric_zero": contract_value.get("zeroFactWhenMetricZero") or {},
        "record_scope": contract_value.get("recordScope") or "",
        "metric_definitions": contract_value.get("metricDefinitions") or {},
        "business_status_definition": contract_value.get("businessStatusDefinition") or "",
        "business_status_from_any_positive_metric": contract_value.get("businessStatusFromAnyPositiveMetric") or {},
        "blank_business_key_fields": contract_value.get("blankBusinessKeyFields") or [],
        "composite_blank_fields": contract_value.get("compositeBlankFields") or [],
        "nonblocking_observation_codes": contract_value.get("nonBlockingObservationCodes") or [],
        "test_data_qualification_definition": contract_value.get("testDataQualificationDefinition") or "",
        "composite_key_parts": contract_value.get("compositeKeyParts") or {},
    }
    contract["required_limitations"] = list(
        dict.fromkeys(
            [
                *(contract.get("required_limitations") or []),
                *(baseline.get("limitations") or []),
            ]
        )
    )
    output = Path(args.output).resolve()
    if (output / "acceptance.json").exists():
        raise ValueError("acceptance_artifact_immutable")
    output.mkdir(parents=True, exist_ok=True)
    anchor_before = None
    anchor_profile = getattr(args, "anchor_profile", None)
    if anchor_profile:
        try:
            from scripts.acceptance_source_anchors import capture
        except ModuleNotFoundError:  # pragma: no cover - direct script execution
            from acceptance_source_anchors import capture
        anchor_before = await asyncio.to_thread(
            capture, baseline_payload, _load_json(Path(anchor_profile).resolve()), output / "anchors-before"
        )
        if anchor_before["verdict"] != "PASS":
            raise ValueError("sap_source_changed_before_acceptance")

    if args.skip_free_query:
        free_run = {"run_id": None, "status": "not_required", "result": {}}
        free_normalized = dict(baseline)
        free_comparison = _compare(baseline, free_normalized, contract)
    else:
        free_run = (
            await _read_completed_free_query(args.api_url.rstrip("/"), args.free_run_id)
            if args.free_run_id
            else await _run_free_query(
                args.api_url.rstrip("/"),
                case,
                contract,
                args.free_timeout,
                sensitive_inputs=sensitive_inputs,
            )
        )
        free_normalized = _normalize_run(free_run, case, contract)
        free_comparison = _compare(baseline, free_normalized, contract)
        free_projection = (free_run.get("result") or {}).get("acceptance_projection")
        if not isinstance(free_projection, dict):
            free_comparison = SemanticComparison(
                verdict="MISMATCH",
                expected_hash=free_comparison.expected_hash,
                actual_hash=free_comparison.actual_hash,
                differences=tuple(
                    [
                        *free_comparison.differences,
                        {"code": "free_query_acceptance_projection_missing"},
                    ]
                ),
            )
        else:
            report_comparison = _validate_projection_matches_visible_report(
                free_run, free_normalized, case, contract
            )
            if report_comparison.verdict != "MATCH":
                free_comparison = SemanticComparison(
                    verdict="MISMATCH",
                    expected_hash=free_comparison.expected_hash,
                    actual_hash=free_comparison.actual_hash,
                    differences=tuple(
                        [
                            *free_comparison.differences,
                            {
                                "code": "acceptance_projection_report_mismatch",
                                "differences": list(report_comparison.differences),
                            },
                        ]
                    ),
                )

    fixed_run: JsonObject | None = None
    fixed_normalized: JsonObject = {"blocked": True}
    fixed_comparison = _compare(baseline, fixed_normalized, contract)
    if free_comparison.verdict == "MATCH":
        fixed_run = (
            _read_fixed_result(Path(args.fixed_result).resolve(), case)
            if args.fixed_result
            else await _run_fixed(
                root,
                output,
                case,
                args.fixed_timeout,
                manifest,
                rules_source=rules_source,
                sensitive_inputs=sensitive_inputs,
            )
        )
        fixed_normalized = _normalize_run(fixed_run, case, contract)
        fixed_comparison = _compare(baseline, fixed_normalized, contract)

    matched = free_comparison.verdict == fixed_comparison.verdict == "MATCH"
    blocking_limitations = {
        str(item) for item in contract.get("blocking_limitations") or [] if str(item)
    }
    observed_limitations = {
        str(item) for item in baseline.get("limitations") or [] if str(item)
    }
    qualification = (
        baseline_payload.get("qualification")
        if isinstance(baseline_payload.get("qualification"), dict)
        else {}
    )
    qualification_blockers = {
        str(item)
        for item in qualification.get("reasons") or []
        if qualification.get("status") == "blocked" and str(item)
    }
    evidenced_blockers = (blocking_limitations & observed_limitations) | qualification_blockers
    externally_blocked = bool(evidenced_blockers)
    artifact = {
        "schema_version": "2.0",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "case": case.as_dict(),
        "direct_baseline": {
            "runtime": baseline_payload.get("runtime"),
            "used_sap_business_agents": baseline_payload.get("used_sap_business_agents"),
            "http_methods": baseline_payload.get("http_methods") or [baseline_payload.get("http_method")],
            "nonblocking_observations": [
                dict(item)
                for item in baseline_payload.get("nonblocking_observations") or []
                if isinstance(item, dict)
            ],
            "sources": [
                {
                    key: source.get(key)
                    for key in (
                        "source_id",
                        "access_method",
                        "http_method",
                        "semantic_read_only",
                        "service_name",
                        "odata_version",
                        "entity_set",
                        "object",
                        "schema_hash",
                        "query_hash",
                        "row_count",
                        "page_count",
                        "stable_order_by",
                        "paging_complete",
                        "source_complete",
                        "primary",
                    )
                }
                for source in baseline_payload.get("sources") or []
                if isinstance(source, dict)
            ],
            "supplemental_sources": [
                {
                    key: source.get(key)
                    for key in (
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
                    )
                }
                for source in baseline_payload.get("supplemental_sources") or []
                if isinstance(source, dict)
            ],
            "qualification": baseline_payload.get("qualification"),
        },
        "hashes": {
            "agent_execution_digest": execution_digest,
            "acceptance_contract_digest": acceptance_contract_digest,
            "case_input_hash": args.case_input_hash or canonical_hash(case.input),
            "business_date": args.business_date or str(
                case.input.get("business_date") or case.input.get("as_of") or ""
            ),
            "skill_gate_snapshot_hash": args.skill_gate_snapshot_hash,
            "sap_metadata_fingerprint": args.sap_metadata_fingerprint,
            "agent_catalog_digest": args.agent_catalog_digest,
            "codex_direct_baseline_hash": canonical_hash(baseline),
            "free_query_hash": canonical_hash(free_normalized),
            "adjudicated_result_hash": canonical_hash(baseline),
            "fixed_agent_hash": canonical_hash(fixed_normalized),
        },
        "free_query": {
            "run_id": free_run.get("run_id"),
            "status": free_run.get("status"),
            "runtime": free_run.get("runtime"),
            "comparison": free_comparison.as_dict(),
            "normalized_result": free_normalized,
        },
        "fixed_agent": {
            "run_id": fixed_run.get("run_id") if fixed_run else None,
            "status": fixed_run.get("status") if fixed_run else "not_run",
            "comparison": fixed_comparison.as_dict(),
            "normalized_result": fixed_normalized,
        },
        "blocking_limitations": sorted(evidenced_blockers),
        "verdict": (
            "BLOCKED"
            if matched and externally_blocked
            else "PASS"
            if matched
            else "FAIL"
            if "MISMATCH" in {free_comparison.verdict, fixed_comparison.verdict}
            else "BLOCKED"
        ),
    }
    if anchor_before is not None:
        try:
            from scripts.acceptance_source_anchors import capture, summarize
        except ModuleNotFoundError:  # pragma: no cover - direct script execution
            from acceptance_source_anchors import capture, summarize
        anchor_after = await asyncio.to_thread(
            capture, baseline_payload, _load_json(Path(anchor_profile).resolve()), output / "anchors-after"
        )
        artifact["source_anchors"] = summarize(baseline_payload, anchor_before, anchor_after)
        artifact["source_anchors"]["before_artifact"] = str(output / "anchors-before" / "anchor.json")
        artifact["source_anchors"]["after_artifact"] = str(output / "anchors-after" / "anchor.json")
        artifact["source_anchors"]["before_artifact_hash"] = canonical_hash(anchor_before)
        artifact["source_anchors"]["after_artifact_hash"] = canonical_hash(anchor_after)
        if artifact["source_anchors"]["verdict"] != "PASS":
            artifact["verdict"] = "BLOCKED"
            artifact.setdefault("validation_issues", []).append(
                {"code": "sap_source_changed_during_acceptance", "classification": "environment"}
            )
    observed_runtime_hash = canonical_hash(artifact["free_query"].get("runtime"))
    if (
        args.runtime_snapshot_hash
        and not args.skip_free_query
        and observed_runtime_hash != args.runtime_snapshot_hash
    ):
        artifact["verdict"] = "FAIL"
        artifact.setdefault("validation_issues", []).append(
            {"code": "runtime_snapshot_drift"}
        )
    artifact["hashes"]["runtime_snapshot_hash"] = (
        args.runtime_snapshot_hash or observed_runtime_hash
    )
    artifact["hashes"]["reuse_fingerprint"] = canonical_hash(
        {
            key: artifact["hashes"].get(key)
            for key in (
                "case_input_hash",
                "business_date",
                "codex_direct_baseline_hash",
                "agent_execution_digest",
                "acceptance_contract_digest",
                "runtime_snapshot_hash",
                "agent_catalog_digest",
                "skill_gate_snapshot_hash",
                "sap_metadata_fingerprint",
            )
        }
    )
    (output / "acceptance.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"verdict": artifact["verdict"], "output": str(output)}, ensure_ascii=False))
    return 0 if artifact["verdict"] == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run direct-baseline, free-query, and fixed-Agent acceptance.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--case", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8765")
    parser.add_argument("--free-run-id")
    parser.add_argument("--fixed-result")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--baseline-timeout", type=int, default=600)
    parser.add_argument("--free-timeout", type=int, default=1800)
    parser.add_argument("--fixed-timeout", type=int, default=600)
    parser.add_argument("--agent-snapshot")
    parser.add_argument("--rules-source")
    parser.add_argument("--agent-version")
    parser.add_argument("--agent-execution-digest")
    parser.add_argument("--acceptance-contract-digest")
    parser.add_argument("--case-input-hash")
    parser.add_argument("--business-date")
    parser.add_argument("--skill-gate-snapshot-hash")
    parser.add_argument("--sap-metadata-fingerprint")
    parser.add_argument("--agent-catalog-digest")
    parser.add_argument("--runtime-snapshot-hash")
    parser.add_argument("--skip-free-query", action="store_true")
    parser.add_argument("--anchor-profile", help="Independent read-only profile for fresh before/after source checks.")
    parser.add_argument(
        "--sensitive-input-stdin",
        action="store_true",
        help="Read a JSON object of protected business inputs from stdin.",
    )
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
