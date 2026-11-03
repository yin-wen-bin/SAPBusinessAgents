"""Retain account-item dimensions while compiling deterministic read plans.

This adds projection fields only. The Provider still checks every field against
live metadata and enforces source semantics; filters and row membership do not
change. Free-query plans continue through their explicit repair contract.
"""
from __future__ import annotations

import copy
from typing import Any

from .source_semantics import catalog, material_plan, profile


def retain_account_item_dimensions(plan: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(plan)
    frozen = catalog()
    children = result.get("steps")
    targets = children if isinstance(children, list) else [result]
    inherited = {key: value for key, value in result.items() if key != "steps"}
    for step in targets:
        if not isinstance(step, dict):
            continue
        effective = {**inherited, **step}
        selected = effective.get("select_fields")
        if (str(effective.get("http_method") or "GET").upper() != "GET"
                or not isinstance(selected, list) or not selected
                or any(not isinstance(field, str) or "(" in field for field in selected)
                or any(effective.get(key) for key in ("aggregates", "group_by", "$apply", "apply"))):
            continue
        contract = effective.get("output_contract") or {}
        if contract.get("display_grain") not in (None, "source_detail"):
            continue
        source = profile(effective, frozen)
        # Ledger views need an explicitly confirmed ledger scope. This compiler
        # does not choose that scope or turn summary queries into detail queries.
        if not source or source["view"] != "account_items" or not material_plan(effective, source):
            continue
        step["select_fields"] = selected + [field for field in source["detail_fields"] if field not in selected]
    return result
