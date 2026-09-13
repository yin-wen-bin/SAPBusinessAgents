"""Offline authoring checks. These are not live evidence or acceptance.

Run against candidates, not historical catalog snapshots. In particular, an
optional form field must not become an unconditional execution dependency.
"""
from __future__ import annotations

import re
from typing import Any

_INPUT = re.compile(r"\{\{\s*input\.([A-Za-z0-9_]+)(\?)?\s*\}\}")

_TECHNICAL_OUTPUT_FIELDS = {
    "status",
    "business_status",
    "source_complete",
    "evidence_complete",
    "business_complete",
    "successful_source_count",
    "evidence_gap_codes",
    "evidence_gaps",
    "missing_evidence",
}


def is_empty_filter_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def validate_filter_omission(value: Any) -> str:
    """Return the input field guarded by an explicit whole-filter omission."""
    if not isinstance(value, dict) or "field" not in value or "value" not in value:
        raise ValueError("agent_optional_filter_invalid")
    marker = value.get("omitIfEmpty")
    match = _INPUT.fullmatch(marker) if isinstance(marker, str) else None
    if not match or not match.group(2):
        raise ValueError("agent_optional_filter_invalid")
    source = _INPUT.fullmatch(value["value"]) if isinstance(value["value"], str) else None
    if not source or source.group(1) != match.group(1):
        raise ValueError("agent_optional_filter_invalid")
    return match.group(1)


def candidate_input_issues(manifest: dict[str, Any]) -> list[dict[str, str]]:
    execution = manifest.get("execution") or {}
    schema = execution.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    # Branch-dependent fields need branch-aware execution tests. Do not claim
    # root.required alone proves they are optional in every supported branch.
    conditional = set()
    for keyword in ("oneOf", "anyOf", "allOf"):
        for branch in schema.get(keyword) or []:
            conditional.update(branch.get("required") or [])
    guaranteed = required | {k for k, v in properties.items() if "default" in v or v.get("x-sapba-server-default")}
    issues: list[dict[str, str]] = []

    def add(code: str, path: str, field: str = "") -> None:
        issues.append({"code": code, "path": path, "field": field})

    def walk(value: Any, path: str, guarded: frozenset[str] = frozenset(), *, in_filters: bool = False) -> None:
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}/{i}", guarded, in_filters=in_filters)
        elif isinstance(value, dict):
            if "omitIfEmpty" in value:
                try:
                    if not in_filters:
                        raise ValueError("agent_optional_filter_invalid")
                    name = validate_filter_omission(value)
                    if name not in properties:
                        raise ValueError("agent_optional_filter_invalid")
                    guarded = guarded | {name}
                except ValueError:
                    add("agent_optional_filter_invalid", path)
            for key, child in value.items():
                walk(child, f"{path}/{key}", guarded, in_filters=in_filters or key == "filters")
        elif isinstance(value, str):
            for match in _INPUT.finditer(value):
                name, optional = match.groups()
                if name not in properties:
                    add("agent_input_reference_unknown", path, name)
                elif not optional and name not in guaranteed | conditional | guarded:
                    add("agent_optional_input_unguarded", path, name)
                elif in_filters and optional and name not in guarded:
                    add("agent_optional_filter_unguarded", path, name)

    for i, step in enumerate(execution.get("steps") or []):
        # Conditional steps require execution-path checks; avoid falsely treating
        # a guarded step as unconditionally executed.
        if step.get("when") is None:
            walk(step.get("request") or step.get("inputMapping") or {}, f"/manifest/execution/steps/{i}/request")
    return issues


def candidate_business_output_issues(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Require SAP-reading drafts to expose a deterministic business result.

    This is an authoring-only gate. Published immutable Agents continue to use
    their frozen packages and acceptance records. Completeness/status fields
    remain useful, but they cannot by themselves make a SAP trial meaningful to
    a business user.
    """

    execution = manifest.get("execution") or {}
    steps = [item for item in execution.get("steps") or [] if isinstance(item, dict)]
    if not any(str(item.get("executor") or "") in {"sap_read", "skill"} for item in steps):
        return []
    business_rules = [
        item
        for item in steps
        if str(item.get("executor") or "") == "rule"
        and str(item.get("operation") or "") not in {"", "evidence_summary"}
    ]
    properties = (execution.get("outputSchema") or {}).get("properties") or {}
    business_fields = [
        str(name)
        for name, schema in properties.items()
        if str(name) not in _TECHNICAL_OUTPUT_FIELDS
        and isinstance(schema, dict)
        and not (
            isinstance(schema.get("x-sapba-display"), dict)
            and schema["x-sapba-display"].get("visible") is False
        )
    ]
    if business_rules and business_fields:
        return []
    return [
        {
            "code": "agent_business_output_contract_missing",
            "path": "/manifest/execution/outputSchema",
            "message": (
                "A SAP-reading draft needs a deterministic business rule and at least "
                "one non-technical business output; evidence_summary alone is insufficient."
            ),
        }
    ]


def trial_business_output_summary(result: Any) -> dict[str, Any]:
    """Inspect the public deterministic result without exposing raw evidence."""

    workflow_output = getattr(result, "workflow_output", None) or {}
    report = (
        workflow_output.get("business_report")
        if isinstance(workflow_output, dict)
        and isinstance(workflow_output.get("business_report"), dict)
        else None
    )
    if report is None and isinstance(workflow_output, dict) and workflow_output:
        # Some legacy deterministic outputs intentionally keep the report beside
        # the schema-bound output.  It is public only when the run also produced
        # a substantive workflow output and Presentation; an unmapped rule
        # result on its own must never satisfy this gate.
        report = next(
            (
                item.get("business_report")
                for item in (getattr(result, "rule_results", None) or [])
                if isinstance(item, dict)
                and isinstance(item.get("business_report"), dict)
            ),
            None,
        )
    presentation = getattr(result, "presentation", None)
    if hasattr(presentation, "model_dump"):
        presentation = presentation.model_dump(mode="json")
    blocks = presentation.get("blocks") if isinstance(presentation, dict) else None
    explicit_report = isinstance(report, dict) and bool(report)
    report_content = bool(
        explicit_report
        and (
            report.get("metrics") is not None
            or report.get("records") is not None
            or report.get("action_tables") is not None
            or report.get("evidence_tables") is not None
            or report.get("summary_sections") is not None
            or report.get("stages") is not None
            or report.get("findings") is not None
            or report.get("next_actions") is not None
        )
    )
    presentation_content = bool(
        isinstance(blocks, list)
        and any(
            isinstance(block, dict)
            and (
                block.get("metrics")
                or block.get("rows")
                or block.get("entries")
                or block.get("items")
                or (
                    block.get("text")
                    and str((block.get("text") or {}).get("zh") if isinstance(block.get("text"), dict) else block.get("text")).strip()
                    not in {"查询已经结束。", "Query completed."}
                )
            )
            for block in blocks
        )
    )
    counts: list[int] = []
    if explicit_report:
        if isinstance(report.get("records"), list):
            counts.append(len(report["records"]))
        for table_name in ("action_tables", "evidence_tables"):
            for table in report.get(table_name) or []:
                if not isinstance(table, dict):
                    continue
                if isinstance(table.get("total_rows"), int):
                    counts.append(max(0, table["total_rows"]))
                elif isinstance(table.get("rows"), list):
                    counts.append(len(table["rows"]))
    available = bool(report_content and presentation_content)
    issues = [] if available else [
        {
            "code": "agent_trial_business_output_missing",
            "message": "SAP evidence was read, but the draft did not produce a public business report and presentation.",
        }
    ]
    return {
        "business_output_available": available,
        "business_record_count": max(counts, default=0),
        "business_output_issues": issues,
    }
