"""Safe constraint diagnostics for private verification, never execution policy.

Do not echo model arguments, filter values, business rows or native logs.
Only fixed field paths, booleans, bounded counts and a payload digest survive.
"""
from __future__ import annotations

import hashlib
import json

from workbuddy_validation_cases import CASES, ENTITY, FIELDS, SERVICE
from sap_business_agents_platform.runtime_query_contract import plans


def rejected_plan_diagnostic(plan, *, case, purchase_order):
    result = {"code": "verification_read_scope_rejected", "tool": "sap_query_execute",
              "case": case if case in CASES else "unknown", "issues": []}
    try:
        encoded = json.dumps(plan, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        result["request_plan_digest"] = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
    except (TypeError, ValueError):
        result["request_plan_digest"] = None
    try:
        steps = plans(plan)
    except (ValueError, TypeError, KeyError, AttributeError):
        result["issues"].append({"path": "/plan", "constraint": "supported_plan_structure"})
        return result
    result["step_count"] = min(len(steps), 100)
    if len(steps) != 1:
        result["issues"].append({"path": "/plan", "constraint": "one_direct_step"})
        return result
    step = steps[0]
    filters = step.get("filters") or []
    selected = step.get("select_fields") or []
    matches = {
        "service": step.get("service_name") == SERVICE,
        "version": step.get("odata_version") == "2.0",
        "entity": step.get("entity_set") == ENTITY,
        "method": step.get("http_method", "GET") == "GET",
        "fields": set(selected) == FIELDS,
        "direct": step.get("plan_kind") in {None, "direct"},
        "filter": len(filters) == 1 and filters[0].get("field") == "PurchaseOrder"
            and filters[0].get("operator", "eq") == "eq" and filters[0].get("value") == purchase_order,
        "no_derived_scope": not any(step.get(key) for key in (
            "filter_from_previous", "function_parameters", "tuple_filters", "partition")),
    }
    result["authorized_scope_matches"] = matches
    result["selected_field_count"] = min(len(selected), 100)
    result["filter_count"] = min(len(filters), 100)
    for name, path in {"service": "/service_name", "version": "/odata_version", "entity": "/entity_set",
                       "method": "/http_method", "fields": "/select_fields", "direct": "/plan_kind",
                       "filter": "/filters", "no_derived_scope": "/plan"}.items():
        if not matches[name]:
            result["issues"].append({"path": path, "constraint": "frozen_read_scope"})
    top = step.get("top")
    result["read_limit"] = {"present": "top" in step, "is_null": top is None,
                            "bounded_integer": type(top) is int and 0 <= top <= 100000}
    if result["read_limit"]["bounded_integer"]:
        result["read_limit"]["value"] = top
    limit_matches = top == 1 if case == "limited_sample" else top is None
    if not limit_matches:
        result["issues"].append({"path": "/top", "constraint": "one_row_read_limit"
                                 if case == "limited_sample" else "no_explicit_truncation"})
    if not result["issues"]:
        result["issues"].append({"path": "/plan", "constraint": "verification_prerequisite_or_scope"})
    return result
