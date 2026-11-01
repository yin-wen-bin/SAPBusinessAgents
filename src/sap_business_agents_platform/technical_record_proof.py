"""Owned live metadata proves literal record identity, never economic meaning."""
from __future__ import annotations

import re


def technical_profile(broker, run_id, source_call):
    plan = (source_call.get("safe_input") or {}).get("plan") or {}
    identity = {k: plan.get(k) for k in ("service_name", "odata_version", "entity_set")}
    candidates = []
    for call in broker.store.list_harness_tool_calls(run_id):
        output = call.get("output") or {}
        data = output.get("data") or {}
        if (call.get("tool_name") != "sap_schema_get" or call.get("status") != "completed"
                or output.get("ok") is not True or data.get("schema_authority") is not True
                or data.get("fields_truncated") is True):
            continue
        if call.get("created_at") and source_call.get("created_at") and call["created_at"] > source_call["created_at"]:
            continue
        provider = (source_call.get("output") or {}).get("provider_id")
        if provider and data.get("provider_id") != provider:
            continue
        for entity in data.get("entities") or []:
            if (any(entity.get(k) != value for k, value in identity.items())
                    or entity.get("runtime_available") is not True or entity.get("executable") is not True):
                continue
            keys = entity.get("key_fields") or []
            if (not keys or len(keys) != len(set(keys))
                    or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) for k in keys)):
                continue
            fields = {f["field_name"]: f for f in data.get("fields") or []
                      if f.get("field_name") and all(f.get(k) == value for k, value in identity.items())
                      and f.get("runtime_available") is True and f.get("executable") is True}
            if not set(keys) <= fields.keys():
                continue
            candidates.append({"technical_key": list(keys), "amount_pairs": {}, "technical_only": True,
                               "fields": fields})
    if not candidates or any(p["technical_key"] != candidates[0]["technical_key"] for p in candidates):
        return None
    return candidates[-1]


def record_profile(broker, run_id, source_call):
    from .source_semantics import profile
    state = broker.store.get_harness_state(run_id)
    plan = (source_call.get("safe_input") or {}).get("plan") or {}
    return profile(plan, state["source_semantics_catalog"]) or technical_profile(broker, run_id, source_call)
