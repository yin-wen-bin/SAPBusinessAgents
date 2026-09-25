"""Narrow, evidence-checked DDIC label projection for authoring sessions.

This is not a general ADT table read. The approved Skill executes two exact,
bounded DDIC lookups and only the derived labels leave this module.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .skills import SkillError


_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_]{0,29}\Z")
_LANGUAGE = {"zh": "1", "en": "E"}


class DdicLabelError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _exact_filter(field: str, value: str) -> dict[str, str]:
    return {"field": field, "operator": "eq", "value": value}


def _input(object_name: str, fields: list[str], filters: list[dict[str, str]], max_rows: int) -> dict[str, Any]:
    return {"schema_version": 1, "source_type": "table", "object": object_name,
            "fields": fields, "filters": filters + [_exact_filter("AS4LOCAL", "A"),
                                                _exact_filter("AS4VERS", "0000")],
            "max_rows": max_rows}


async def field_labels(skills: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    table = str(arguments.get("table") or "").strip().upper()
    field = str(arguments.get("field") or "").strip().upper()
    languages = arguments.get("languages", ["zh", "en"])
    if not _IDENTIFIER.fullmatch(table) or not _IDENTIFIER.fullmatch(field):
        raise DdicLabelError("ddic_identifier_invalid")
    if not isinstance(languages, list) or not languages or len(languages) > 2 or set(languages) - set(_LANGUAGE):
        raise DdicLabelError("ddic_language_invalid")
    if len(set(languages)) != len(languages):
        raise DdicLabelError("ddic_language_invalid")
    try:
        contract = skills.get("sap-adt-table-export")
    except Exception as exc:
        raise DdicLabelError("ddic_skill_unavailable") from exc
    if not all(contract.get(key) is True for key in ("read_only", "validated", "available")):
        raise DdicLabelError("ddic_skill_unavailable")

    async def lookup(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            skills.validate_input("sap-adt-table-export", payload)
            result = await skills.execute("sap-adt-table-export", payload)
        except SkillError as exc:
            code = str(exc.code).casefold()
            if any(term in code for term in ("permission", "forbidden", "unauthorized", "access_denied")):
                raise DdicLabelError("ddic_permission_denied") from exc
            raise DdicLabelError("ddic_skill_execution_failed") from exc
        if (not isinstance(result, dict) or result.get("skill_id") != "sap-adt-table-export"
                or result.get("read_only") is not True or result.get("validated") is not True
                or result.get("status") != "complete"
                or (result.get("completeness") or {}).get("source_complete") is not True
                or (result.get("completeness") or {}).get("paging_complete") is not True
                or result.get("validation_issues") or not isinstance(result.get("rows"), list)
                or result.get("row_count") != len(result["rows"])):
            raise DdicLabelError("ddic_evidence_incomplete")
        scope = result.get("scope") or {}
        if str(scope.get("object") or "").upper() != payload["object"]:
            raise DdicLabelError("ddic_scope_mismatch")
        for requested in payload["filters"]:
            if not any(isinstance(actual, dict)
                       and str(actual.get("field") or "").upper() == requested["field"]
                       and str(actual.get("operator") or actual.get("option") or "").upper() == "EQ"
                       and str(actual.get("value") or "").strip().upper() == requested["value"].upper()
                       and str(actual.get("sign") or "I").upper() == "I"
                       for actual in scope.get("filters") or []):
                raise DdicLabelError("ddic_scope_mismatch")
        return result

    source = await lookup(_input("DD03L", ["TABNAME", "FIELDNAME", "ROLLNAME"],
                                 [_exact_filter("TABNAME", table), _exact_filter("FIELDNAME", field)], 2))
    rows = [row for row in source["rows"] if isinstance(row, dict)
            and str(row.get("TABNAME") or "").upper() == table
            and str(row.get("FIELDNAME") or "").upper() == field]
    elements = {str(row.get("ROLLNAME") or "").strip().upper() for row in rows if row.get("ROLLNAME")}
    if len(rows) != 1 or len(elements) != 1:
        raise DdicLabelError("ddic_data_element_unresolved")
    element = next(iter(elements))
    if not _IDENTIFIER.fullmatch(element):
        raise DdicLabelError("ddic_data_element_unresolved")
    labels: dict[str, str | None] = {}
    evidence: dict[str, str] = {"field_definition": _digest(source)}
    for language in languages:
        language_code = _LANGUAGE[language]
        result = await lookup(_input("DD04T", ["ROLLNAME", "DDLANGUAGE", "SCRTEXT_M"],
                                     [_exact_filter("ROLLNAME", element),
                                      _exact_filter("DDLANGUAGE", language_code)], 2))
        matches = [row for row in result["rows"] if isinstance(row, dict)
                   and str(row.get("ROLLNAME") or "").upper() == element
                   and str(row.get("DDLANGUAGE") or "").upper() == language_code]
        if len(matches) > 1:
            raise DdicLabelError("ddic_label_ambiguous")
        labels[language] = str(matches[0].get("SCRTEXT_M") or "").strip() or None if matches else None
        evidence[language] = _digest(result)
    return {"ok": True, "table": table, "field": field, "data_element": element,
            "labels": labels, "missing_translations": [lang for lang, label in labels.items() if not label],
            "source_complete": True, "evidence_sha256": evidence}


def _digest(value: dict[str, Any]) -> str:
    # The derived hash proves which verified tool response was used, without
    # exposing its rows or local artifact paths to the assistant.
    safe = {key: value.get(key) for key in ("skill_id", "status", "scope", "row_count", "completeness", "artifacts", "rows")}
    return "sha256:" + hashlib.sha256(json.dumps(safe, sort_keys=True, default=str).encode()).hexdigest()
