"""Read-only projections of saved, declared business output. Never calculate business facts.

This layer has no Provider, model, SAP, filesystem or persistence dependency.
Its presentation and group counts are not execution/acceptance evidence.
"""
from __future__ import annotations

import copy
import json
from typing import Any

VERSION = "sapba.business-result-display/1"


def visible(schema: dict) -> bool:
    display = schema.get("x-sapba-display")
    display = display if isinstance(display, dict) else {}
    return (display.get("visible") is not False and display.get("hidden") is not True
            and schema.get("x-sapba-internal") is not True and schema.get("x-sapba-workflow-only") is not True)


def pair(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        return {"zh": str(value.get("zh") or value.get("en") or fallback),
                "en": str(value.get("en") or value.get("zh") or fallback)}
    text = fallback if value is None else str(value)
    return {"zh": text, "en": text}


def cell(value: Any, schema: dict) -> dict[str, str]:
    labels = (schema.get("x-sapba-display") or {}).get("labels") or {}
    if str(value) in labels:
        return pair(labels[str(value)])
    if value is None:
        return pair("—")
    if isinstance(value, bool):
        return {"zh": "是" if value else "否", "en": "Yes" if value else "No"}
    return pair(value)


def rule_record_summary(output: Any, *, execution_status: str) -> dict:
    """Distinguish a check from a rule returning an explicit business record set."""
    if execution_status != "completed" or not isinstance(output, dict):
        return {"summary_kind": "rule_check", "record_count": None, "preview_rows": [],
                "evidence_status": "unavailable"}
    rows = None
    for container in (output, output.get("workflow_output"), output.get("business_report")):
        if isinstance(container, dict) and isinstance(container.get("records"), list):
            rows = container["records"]
            break
    if rows is None:
        return {"summary_kind": "rule_check", "record_count": None, "preview_rows": [],
                "evidence_status": "not_applicable"}
    incomplete = output.get("source_complete") is False or output.get("evidence_complete") is False or output.get("business_complete") is False
    status = "partial" if incomplete else "available" if rows else "not_found"
    explicit = output.get("evidence_status") or output.get("status")
    if explicit in {"failed", "source_unavailable", "capability_blocked"}:
        status = "unavailable"
    elif explicit in {"partial", "not_found"}:
        status = explicit
    return {"summary_kind": "business_records", "record_count": len(rows), "preview_rows": [],
            "evidence_status": status}


def project(result: dict, output_schema: dict) -> dict:
    """Return a display-only copy and private in-memory paging sources."""
    result = copy.deepcopy(result)
    presentation = result.get("presentation")
    if not isinstance(presentation, dict) or not isinstance(presentation.get("blocks"), list):
        return {"result": result, "sources": {}}
    properties = output_schema.get("properties") or {}
    workflow_output = result.get("workflow_output") or {}
    report = workflow_output.get("business_report")
    if not isinstance(report, dict):
        report = next((x["business_report"] for x in result.get("rule_results", [])
                       if isinstance(x, dict) and isinstance(x.get("business_report"), dict)), {})
    sources, groups = {}, {}
    blocks = presentation["blocks"]

    def add_source(index: int, rows: list, schemas: dict, *, groupable: bool = False):
        block = blocks[index]
        columns = block.get("columns") or []
        sources[index] = {"rows": rows, "columns": columns, "schemas": schemas}
        # All cells come from saved, public business records and declared columns.
        block["rows"] = render_rows(rows[:200], columns, schemas)
        block["total_rows"] = len(rows)
        block["display_truncated"] = len(rows) > 200
        if not groupable:
            return
        for column in columns:
            key = column["key"]
            schema = schemas.get(key) or {}
            enum = schema.get("enum")
            labels = (schema.get("x-sapba-display") or {}).get("labels") or {}
            if not isinstance(enum, list) or not labels or len(enum) > 20:
                continue
            values = list(enum)
            for row in rows:
                if row.get(key) not in values:
                    values.append(row.get(key))
            if len(values) > 30 or any(isinstance(v, (dict, list)) for v in values):
                continue
            choices = [{"value": json.dumps(v, ensure_ascii=False, separators=(",", ":")),
                        "label": cell(v, schema), "count": sum(row.get(key) == v for row in rows)} for v in values]
            groups[str(index)] = {"field": key, "label": column.get("label") or pair(key),
                                  "choices": choices, "total_rows": len(rows),
                                  "source_complete": block.get("source_complete")}
            sources[index]["group"] = groups[str(index)]
            break

    record_schema = properties.get("records") or {}
    item_properties = (record_schema.get("items") or {}).get("properties") or {}
    records = report.get("records")
    if (visible(record_schema) and report.get("display_records") is not False
            and isinstance(records, list) and all(isinstance(r, dict) for r in records)):
        for index, block in enumerate(blocks):
            columns = block.get("columns") or []
            if block.get("type") != "table" or not columns or not all(c.get("key") in item_properties and visible(item_properties[c["key"]]) for c in columns):
                continue
            # Identify the exact saved public table, not a similarly shaped table.
            matched = bool(report.get("record_columns") and block.get("title") in [pair("业务记录"), {"zh": "业务记录", "en": "Business records"}])
            for table in report.get("evidence_tables", []) + report.get("action_tables", []):
                if (table.get("rows") == records and pair(table.get("title")) == block.get("title")
                        and [c.get("key") for c in table.get("columns", [])] == [c.get("key") for c in columns]):
                    matched = True
            if matched and block.get("total_rows", len(block.get("rows") or [])) == len(records):
                add_source(index, records, item_properties, groupable=True)

    conclusion = properties.get("business_status") or {}
    if conclusion and visible(conclusion) and "business_status" in workflow_output:
        entry = {"label": pair(conclusion.get("title"), "Business status"),
                 "value": cell(workflow_output["business_status"], conclusion)}
        if not any(b.get("type") == "key_value" and entry in (b.get("entries") or []) for b in blocks):
            blocks.append({"type": "key_value", "title": {"zh": "业务结论", "en": "Business conclusion"},
                           "entries": [entry], "claim_scope": "customer_business_fact"})

    # Additional declared object arrays (e.g. per-currency amounts) are tables,
    # never aggregated, coerced, sign-adjusted or combined across currencies.
    for key, schema in properties.items():
        value = workflow_output.get(key)
        items = schema.get("items") or {}
        fields = items.get("properties") or {}
        if key == "records" or not visible(schema) or not isinstance(value, list) or not fields:
            continue
        if not all(isinstance(row, dict) for row in value):
            continue
        fields = {k: s for k, s in fields.items() if visible(s)
                  and set(s.get("type") if isinstance(s.get("type"), list) else [s.get("type")])
                  <= {"string", "number", "integer", "boolean", "null"}}
        columns = [{"key": k, "label": pair(s.get("title"), k), "format": "text"} for k, s in fields.items()]
        if not columns or any(isinstance(row.get(k), (dict, list)) for row in value for k in fields):
            continue
        rendered = render_rows(value[:200], columns, fields)
        if any(b.get("type") == "table" and [c.get("key") for c in b.get("columns", [])] == list(fields)
               and b.get("rows") == rendered and b.get("total_rows") == len(value) for b in blocks):
            continue
        index = len(blocks)
        blocks.append({"type": "table", "title": pair(schema.get("title"), key), "columns": columns,
                       "rows": [], "claim_scope": "customer_business_fact",
                       "source_complete": workflow_output.get("source_complete")})
        add_source(index, value, fields)

    rule_steps = [s for s in result.get("steps", []) if s.get("executor") == "rule" and s.get("status") == "completed"]
    outputs = result.get("rule_results") or []
    # The deterministic engine appends exactly one result per completed rule,
    # in execution order. Ambiguous legacy data is not guessed or reattached.
    if len(rule_steps) == len(outputs):
        for step, output in zip(rule_steps, outputs):
            step.setdefault("execution", {}).update(rule_record_summary(output, execution_status="completed"))
    result["business_display"] = {"contract": VERSION, "status": "ready", "source": "frozen_run_snapshot",
                                   "groups": groups, "business_status": workflow_output.get("business_status"),
                                   "business_status_label": cell(workflow_output["business_status"], conclusion)
                                   if conclusion and "business_status" in workflow_output else None}
    return {"result": result, "sources": sources}


def render_rows(rows: list, columns: list, schemas: dict) -> list:
    return [{"values": [cell(row.get(c["key"]), schemas.get(c["key"]) or {}) for c in columns],
             "evidence_refs": list(row.get("_evidence_refs") or [])} for row in rows]


def table_page(view: dict, index: int, *, offset: int, limit: int, group: str | None = None) -> dict | None:
    source = view["sources"].get(index)
    if source is None:
        if group is not None:
            raise ValueError("This table has no declared grouping.")
        return None
    rows = source["rows"]
    if group is not None:
        definition = source.get("group") or {}
        if group not in {c["value"] for c in definition.get("choices", [])}:
            raise ValueError("The requested record group is not declared.")
        value = json.loads(group)
        rows = [row for row in rows if row.get(definition["field"]) == value]
    page = render_rows(rows[offset:offset+limit], source["columns"], source["schemas"])
    return {"block_index": index, "offset": offset, "limit": limit, "rows": page,
            "total_rows": len(rows), "has_next": offset + len(page) < len(rows), "group": group}
