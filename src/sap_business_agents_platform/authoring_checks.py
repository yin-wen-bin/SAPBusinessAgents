"""Offline authoring checks. These are not live evidence or acceptance.

Run against candidates, not historical catalog snapshots. In particular, an
optional form field must not become an unconditional execution dependency.
"""
from __future__ import annotations

import re
from typing import Any

_INPUT = re.compile(r"\{\{\s*input\.([A-Za-z0-9_]+)(\?)?\s*\}\}")


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
