"""Offline, revision-bound business-contract checks for new Agent candidates.

Definitions are reviewable requirements, not executable natural-language rules.
Only the small explicitly declared invariant vocabulary is evaluated here.
Historical packages do not opt into these runtime checks implicitly.
"""
from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator

from .acceptance import canonical_hash, agent_execution_digest

VERSION = "1.0"
SUPPORTED_REQUIRED_ASSESSMENTS = {"inventory_fifo"}

def compile_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    value = (manifest.get("execution") or {}).get("acceptance") or {}
    output_schema = (manifest.get("execution") or {}).get("outputSchema") or {}
    output_properties = output_schema.get("properties") if isinstance(output_schema, dict) else {}
    output_properties = output_properties if isinstance(output_properties, dict) else {}
    record_array_schema = output_properties.get("records") or {}
    record_items = record_array_schema.get("items") if isinstance(record_array_schema, dict) else {}
    record_properties = record_items.get("properties") if isinstance(record_items, dict) else {}
    record_properties = record_properties if isinstance(record_properties, dict) else {}

    if value.get("contractVersion") == VERSION:
        record_properties = (record_schema(manifest).get("items") or {}).get("properties") or {}
    boolean_fields = list(value.get("booleanFields") or [])
    for field in value.get("facts") or []:
        schema = record_properties.get(str(field)) or output_properties.get(str(field)) or {}
        field_type = schema.get("type") if isinstance(schema, dict) else None
        declared_types = field_type if isinstance(field_type, list) else [field_type]
        if "boolean" in declared_types and str(field) not in boolean_fields:
            boolean_fields.append(str(field))

    status_schema = output_properties.get("business_status") or {}
    status_values = (
        list(status_schema.get("enum") or [])
        if isinstance(status_schema, dict)
        else []
    )
    return {
        **contract_details(manifest),
        "schema_version": value.get("schemaVersion", "1.0"),
        "business_keys": list(value.get("businessKeys") or []),
        "facts": list(value.get("facts") or []),
        "metrics": list(value.get("metrics") or []),
        "required_limitations": list(value.get("requiredLimitations") or []),
        "required_assessments_declared": "requiredAssessments" in value,
        "required_assessments": (
            list(value.get("requiredAssessments") or [])
            if isinstance(value.get("requiredAssessments"), list)
            else []
        ),
        "decimal_fields": list(value.get("decimalFields") or []),
        "currency_fields": list(value.get("currencyFields") or []),
        "unit_fields": list(value.get("unitFields") or []),
        "decimal_metrics": list(value.get("decimalMetricIds") or []),
        "field_aliases": copy.deepcopy(value.get("fieldAliases") or {}),
        "field_extractors": copy.deepcopy(value.get("fieldExtractors") or {}),
        "input_defaults": copy.deepcopy(value.get("inputDefaults") or {}),
        "constant_defaults": copy.deepcopy(value.get("constantDefaults") or {}),
        "fact_definitions": copy.deepcopy(value.get("factDefinitions") or {}),
        "date_fields": list(value.get("dateFields") or []),
        "code_set_fields": list(value.get("codeSetFields") or []),
        "zero_pad_fields": copy.deepcopy(value.get("zeroPadFields") or {}),
        "boolean_fields": boolean_fields,
        "currency_from_decimal": copy.deepcopy(value.get("currencyFromDecimal") or {}),
        "value_mappings": copy.deepcopy(value.get("valueMappings") or {}),
        "limitation_keywords": copy.deepcopy(value.get("limitationKeywords") or {}),
        "summary_record": value.get("summaryRecord") is True,
        "business_status_from_metric": copy.deepcopy(value.get("businessStatusFromMetric") or {}),
        "limitations_from_metrics": copy.deepcopy(value.get("limitationsFromMetrics") or {}),
        "blank_value_keywords": copy.deepcopy(value.get("blankValueKeywords") or {}),
        "preserve_literal_values": list(value.get("preserveLiteralValues") or []),
        "blocking_limitations": list(value.get("blockingLimitations") or []),
        "ignored_notice_keywords": list(value.get("ignoredNoticeKeywords") or []),
        "metric_value_mappings": copy.deepcopy(value.get("metricValueMappings") or {}),
        "zero_fact_when_metric_zero": copy.deepcopy(value.get("zeroFactWhenMetricZero") or {}),
        "record_scope": value.get("recordScope") or "",
        "metric_definitions": copy.deepcopy(value.get("metricDefinitions") or {}),
        "business_status_definition": value.get("businessStatusDefinition") or "",
        "business_status_from_any_positive_metric": copy.deepcopy(value.get("businessStatusFromAnyPositiveMetric") or {}),
        "blank_business_key_fields": list(value.get("blankBusinessKeyFields") or []),
        "composite_blank_fields": list(value.get("compositeBlankFields") or []),
        "nonblocking_observation_codes": list(value.get("nonBlockingObservationCodes") or []),
        "test_data_qualification_definition": value.get("testDataQualificationDefinition") or "",
        "composite_key_parts": copy.deepcopy(value.get("compositeKeyParts") or {}),
        "business_status_values": [str(item) for item in status_values if str(item)],
    }
TECHNICAL = {"status", "successful_source_count", "tool_call_count", "query_count", "elapsed_seconds"}


def authoring_contract_guidance() -> str:
    return """Business acceptance contract for new or behavior-changing fixed Agents:
Use execution.acceptance.contractVersion='1.0'. Author the business output and
acceptance definition together. Declare recordScope, recordDefinition (record grain),
scopeDefinition (inclusion, exclusion and evidence requirements), factDefinitions for
every business key/fact, metricDefinitions (deduplication, zero vs unknown), and
businessStatusDefinition. Explicitly declare requiredAssessments (an empty array means
that no specialized assessment is required; inventory_fifo is currently supported).
Expose schema-typed canonical records and scalar metrics,
business_status with an enum, source_complete, evidence_complete and business_complete.
Use enums for business assessments. Keep technical status and successful_source_count
outside acceptance facts/metrics. Inputs are scope, not automatic business record keys.
Build business_report.records/metrics from canonical output. Never invent missing keys,
evidence or defaults to pass. metricInvariants optionally supports count_records or sum
of a numeric record field only. Include required limitation codes as report findings.
Ask a concrete clarification when scope or business semantics is ambiguous. The platform
checks the current trial before acceptance; definitions alone do not prove correctness.
See docs/agent-authoring-standard.md. Preserve legacy contracts for truly documentation-only
upgrades whose execution and rules are unchanged; do not modify validation certificates.
"""


class BusinessContractError(ValueError):
    def __init__(self, issues: list[dict[str, Any]], stage: str = "output") -> None:
        super().__init__("agent_acceptance_contract_not_ready")
        self.code = "agent_acceptance_contract_not_ready"
        self.issues = issues
        self.stage = stage


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def record_schema(manifest: dict[str, Any]) -> dict[str, Any]:
    execution = manifest.get("execution") or {}
    scope = (execution.get("acceptance") or {}).get("recordScope") or "records"
    schema = execution.get("outputSchema") or {}
    for part in str(scope).split("."):
        schema = (schema.get("properties") or {}).get(part) or {}
    return schema


def contract_issues(manifest: dict[str, Any]) -> list[dict[str, str]]:
    try:
        return _contract_issues(manifest)
    except (TypeError, AttributeError, KeyError, ValueError):
        return [_issue("contract_definition_missing", "/execution/acceptance", "The business definition has an invalid structure; repair it before acceptance.")]


def _contract_issues(manifest: dict[str, Any]) -> list[dict[str, str]]:
    execution = manifest.get("execution") or {}
    value = execution.get("acceptance") or {}
    base = "/execution/acceptance"
    issues: list[dict[str, str]] = []
    def add(code: str, path: str, text: str) -> None:
        issues.append(_issue(code, path, text))
    if value.get("contractVersion") != VERSION:
        add("contract_version_required", base + "/contractVersion", "Complete the versioned business acceptance contract before formal acceptance.")
    if "requiredAssessments" not in value:
        add(
            "contract_required_assessments_missing",
            base + "/requiredAssessments",
            "Explicitly declare specialized assessment requirements; use an empty array when none are required.",
        )
    elif not isinstance(value.get("requiredAssessments"), list):
        add(
            "contract_required_assessments_invalid",
            base + "/requiredAssessments",
            "requiredAssessments must be an array of supported assessment identifiers.",
        )
    else:
        assessments = value.get("requiredAssessments") or []
        if len(assessments) != len(set(str(item) for item in assessments)):
            add(
                "contract_required_assessments_invalid",
                base + "/requiredAssessments",
                "requiredAssessments must not contain duplicate identifiers.",
            )
        for index, assessment in enumerate(assessments):
            if not isinstance(assessment, str) or assessment not in SUPPORTED_REQUIRED_ASSESSMENTS:
                add(
                    "contract_required_assessment_unknown",
                    f"{base}/requiredAssessments/{index}",
                    "The specialized assessment identifier is not supported by this platform version.",
                )
    for name in ("recordDefinition", "scopeDefinition", "businessStatusDefinition"):
        if not isinstance(value.get(name), str) or not value[name].strip():
            add("contract_definition_missing", base + "/" + name, "Define the record grain, scope and business-status semantics.")
    records = record_schema(manifest)
    properties = (records.get("items") or {}).get("properties") or {}
    required = set((records.get("items") or {}).get("required") or [])
    if records.get("type") != "array" or not properties:
        add("contract_records_missing", "/execution/outputSchema", "Declare typed canonical records at recordScope.")
    fields = list(value.get("businessKeys") or []) + list(value.get("facts") or [])
    if not value.get("businessKeys"):
        add("contract_keys_missing", base + "/businessKeys", "Declare stable business keys.")
    for name in fields:
        schema = properties.get(name) or {}
        types = schema.get("type")
        types = types if isinstance(types, list) else [types]
        if name not in required or not set(types) <= {"string", "integer", "boolean", "null"} or len(set(types) - {"null"}) != 1:
            add("contract_field_unmapped", "/execution/outputSchema/" + str(name), "A compared field must be a required typed canonical record field.")
        if name in (value.get("businessKeys") or []) and "null" in types:
            add("contract_key_nullable", base + "/businessKeys", "A stable business key cannot be nullable.")
        if schema.get("enum") and ("string" not in types or any(not isinstance(v, str) for v in schema["enum"])):
            add("contract_enum_missing", "/execution/outputSchema/" + name, "Classification enums must use explicit string codes.")
        if any(key in schema for key in ("$ref", "anyOf", "oneOf", "allOf")):
            add("contract_field_unmapped", "/execution/outputSchema/" + name, "Only explicit primitive types are supported by the bounded projection.")
        if name in TECHNICAL:
            add("contract_technical_fact", base + "/facts", "Keep execution status and query diagnostics outside business comparisons.")
        if (name.endswith("status") or name.endswith("assessment")) and not schema.get("enum"):
            add("contract_enum_missing", "/execution/outputSchema/" + str(name), "Declare allowed business-state values.")
        if not str((value.get("factDefinitions") or {}).get(name) or "").strip():
            add("contract_fact_definition_missing", base + "/factDefinitions/" + str(name), "Define the field meaning and evidence source.")
    root = (execution.get("outputSchema") or {}).get("properties") or {}
    root_required = set((execution.get("outputSchema") or {}).get("required") or [])
    for name in value.get("metrics") or []:
        schema = root.get(name) or {}
        types = schema.get("type")
        types = types if isinstance(types, list) else [types]
        if name not in root_required or not set(types) <= {"integer", "string", "null"} or len(set(types) - {"null"}) != 1 or ("string" in types and name not in (value.get("decimalMetricIds") or [])):
            add("contract_metric_unmapped", "/execution/outputSchema/" + str(name), "Expose each compared metric as a typed output.")
        if name in TECHNICAL:
            add("contract_technical_metric", base + "/metrics", "Query-path-dependent counts are audit data, not business metrics.")
        if not str((value.get("metricDefinitions") or {}).get(name) or "").strip():
            add("contract_metric_definition_missing", base + "/metricDefinitions/" + str(name), "Define counting scope, deduplication, zero and unknown.")
    for name in ("business_status", "source_complete", "evidence_complete", "business_complete"):
        if name not in root_required or (root.get(name) or {}).get("type") != ("string" if name == "business_status" else "boolean"):
            add("contract_root_unmapped", "/execution/outputSchema/" + name, "Expose business status and independent completeness fields.")
    if not (root.get("business_status") or {}).get("enum"):
        add("contract_enum_missing", "/execution/outputSchema/business_status", "Declare allowed root business states.")
    for name, rule in (value.get("metricInvariants") or {}).items():
        if name not in (value.get("metrics") or []) or not isinstance(rule, dict) or rule.get("operation") not in {"count_records", "sum"}:
            add("contract_invariant_invalid", base + "/metricInvariants/" + name, "Only count_records and sum of a declared numeric record field are supported.")
        elif rule["operation"] == "sum" and (rule.get("field") not in fields or (properties.get(rule.get("field")) or {}).get("type") != "integer"):
            add("contract_invariant_invalid", base + "/metricInvariants/" + name, "The summed field must be a declared numeric record field.")
    return issues


def contract_details(manifest: dict[str, Any]) -> dict[str, Any]:
    """Additional normalized metadata shared by the CLI and campaign adapter."""
    execution = manifest.get("execution") or {}
    value = execution.get("acceptance") or {}
    if value.get("contractVersion") != VERSION:
        return {}
    props = (record_schema(manifest).get("items") or {}).get("properties") or {}
    root = (execution.get("outputSchema") or {}).get("properties") or {}
    fields = list(dict.fromkeys([*(value.get("businessKeys") or []), *(value.get("facts") or [])]))
    return {
        "contract_version": VERSION,
        "record_definition": value.get("recordDefinition", ""),
        "scope_definition": value.get("scopeDefinition", ""),
        "record_schemas": {name: copy.deepcopy(props.get(name) or {}) for name in fields},
        "metric_schemas": {name: copy.deepcopy(root.get(name) or {}) for name in value.get("metrics") or []},
        "integer_fields": [name for name in fields if "integer" in _types(props.get(name) or {})],
        "enum_values": {name: props[name]["enum"] for name in fields if (props.get(name) or {}).get("enum")},
        "nullable_fields": [name for name in fields if "null" in _types(props.get(name) or {})],
        "metric_invariants": copy.deepcopy(value.get("metricInvariants") or {}),
        "required_assessments_declared": "requiredAssessments" in value,
        "required_assessments": (
            list(value.get("requiredAssessments") or [])
            if isinstance(value.get("requiredAssessments"), list)
            else []
        ),
    }


def _types(schema: dict[str, Any]) -> list[str]:
    value = schema.get("type")
    return value if isinstance(value, list) else [value]


def normalized_issues(value: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, str]]:
    issues = []
    records = value.get("records")
    if not isinstance(records, list):
        return [_issue("contract_records_missing", "/records", "Canonical records must be an array.")]
    keys = contract.get("business_keys") or []
    seen = set()
    for i, row in enumerate(records):
        if not isinstance(row, dict):
            issues.append(_issue("contract_record_invalid", f"/records/{i}", "A record must be an object."))
            continue
        for name, schema in (contract.get("record_schemas") or {}).items():
            if name not in row or not Draft202012Validator(schema).is_valid(row[name]):
                issues.append(_issue("contract_field_invalid", f"/records/{i}/{name}", "Missing field, wrong type or undeclared business-state value."))
        key = tuple("" if row.get(name) is None else str(row[name]) for name in keys)
        if not all(key) or key in seen:
            issues.append(_issue("contract_business_key_invalid", f"/records/{i}", "Business keys must be present and unique."))
        seen.add(key)
    metrics = value.get("metrics") or {}
    for name, schema in (contract.get("metric_schemas") or {}).items():
        if name not in metrics or not Draft202012Validator(schema).is_valid(metrics[name]):
            issues.append(_issue("contract_metric_invalid", "/metrics/" + name, "Missing metric or invalid metric type."))
    for name, rule in (contract.get("metric_invariants") or {}).items():
        actual = metrics.get(name)
        if actual is None and value.get("business_complete") is False:
            continue
        expected = len(records) if rule.get("operation") == "count_records" else None
        if rule.get("operation") == "sum":
            values = [row.get(rule.get("field")) for row in records if isinstance(row, dict)]
            if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in values):
                expected = sum(values)
        if expected is None or actual != expected:
            issues.append(_issue("contract_metric_invariant_failed", "/metrics/" + name, "The metric does not match its declared record-count or sum invariant."))
    if value.get("business_status") not in (contract.get("business_status_values") or []):
        issues.append(_issue("contract_business_status_invalid", "/business_status", "Undeclared root business status."))
    for name in ("source_complete", "evidence_complete", "business_complete"):
        if not isinstance(value.get(name), bool):
            issues.append(_issue("contract_completeness_invalid", "/" + name, "Completeness must be explicit."))
    return issues


def normalize_fixed(result: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    output = result.get("workflow_output") or {}
    records: Any = output
    for part in str(contract.get("record_scope") or "records").split("."):
        records = records.get(part) if isinstance(records, dict) else None
    report = output.get("business_report") or {}
    # Reports may live beside the output on legacy renderers, but canonical
    # fields always come from the schema-bound public workflow output.
    if not report:
        report = next((r.get("business_report") for r in reversed(result.get("rule_results") or []) if r.get("business_report")), {})
    required = set(contract.get("required_limitations") or [])
    limitations = set(report.get("limitations") or [])
    for finding in report.get("findings") or []:
        limitations.update(code for code in required if code.casefold() == str(finding.get("code") or "").casefold())
    gaps = list(output.get("evidence_gap_codes") or [])
    value = {
        "records": copy.deepcopy(records),
        "metrics": {name: output.get(name) for name in contract.get("metrics") or []},
        **{name: output.get(name) for name in ("business_status", "source_complete", "evidence_complete", "business_complete")},
        "limitations": sorted(limitations), "evidence_gap_codes": gaps,
    }
    issues = normalized_issues(value, contract)
    if report.get("records") != records:
        issues.append(_issue("contract_report_records_mismatch", "/business_report/records", "The displayed report must derive from the canonical records."))
    report_metrics = {item.get("id"): item.get("value") for item in report.get("metrics") or []}
    for name, metric in value["metrics"].items():
        if name not in report_metrics or report_metrics[name] != metric:
            issues.append(_issue("contract_report_metric_mismatch", "/business_report/metrics/" + name, "The displayed metric must equal the canonical output."))
    if not required <= limitations:
        issues.append(_issue("contract_limitation_missing", "/business_report/findings", "Expose all required business limitations."))
    if issues:
        raise BusinessContractError(issues, "fixed_agent")
    return value


def result_payload(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    if callable(getattr(result, "model_dump", None)):
        return result.model_dump(mode="json")
    return {"workflow_output": getattr(result, "workflow_output", None) or {},
            "rule_results": getattr(result, "rule_results", None) or []}


def readiness(manifest: dict[str, Any], revision: int, *, result: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = contract_issues(manifest)
    contract = manifest.get("execution", {}).get("acceptance") or {}
    if not issues and result is not None:
        try:
            normalize_fixed(result, compile_contract(manifest))
        except BusinessContractError as exc:
            issues.extend(exc.issues)
    return {"contract_version": contract.get("contractVersion"), "revision": revision,
            "execution_digest": agent_execution_digest(manifest),
            "contract_digest": canonical_hash(contract), "output_schema_digest": canonical_hash(manifest.get("execution", {}).get("outputSchema")),
            "status": "needs_input" if issues else "ready" if result is not None else "trial_required",
            "definition_ready": not contract_issues(manifest), "output_checked": result is not None,
            "issues": issues}
