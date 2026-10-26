"""Provider-neutral source grain, economic kind and comparison evidence.

No network, model, candidate code or mutable qualification storage. API-specific
roles live in reviewed data, not supplier/document/indicator special cases.
Original rows are never repaired, discarded, de-duplicated or re-signed here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

VERSION = "sapba.source-semantics/1"
KINDS = {"posted_normal", "posted_special", "noted_item", "balance_carryforward", "unknown"}
CATALOG_PATH = Path(__file__).resolve().parents[2] / "config/sap-source-semantics.json"
_SCOPE = ContextVar("source_semantics_scope", default=None)


@contextmanager
def bind_scope(definition=None, confirmed_filters=None, *, configurations=()):
    if definition_issues(definition):
        raise ValueError("source_scope_definition_invalid")
    token = _SCOPE.set({"definition": copy.deepcopy(definition), "filters": copy.deepcopy(confirmed_filters or {}),
                       "configurations": copy.deepcopy(configurations)})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def material_plan(step, p):
    selected = set(step.get("select_fields") or [])
    return not selected or bool(selected.intersection(p["amount_pairs"])) or "AccountingDocument" in selected


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()


def catalog(path=None):
    value = json.loads(Path(path or CATALOG_PATH).read_text(encoding="utf-8"))
    if value.get("version") != VERSION or not isinstance(value.get("sources"), list):
        raise ValueError("source_semantics_catalog_invalid")
    identities = [(s["service_name"], s["odata_version"], s["entity_set"]) for s in value["sources"]]
    if len(set(identities)) != len(identities):
        raise ValueError("source_semantics_catalog_invalid")
    body = {**value, "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    return {**body, "digest": digest(body)}


def verify_catalog(value):
    body = {k: v for k, v in value.items() if k != "digest"}
    if (value.get("version") != VERSION or digest(body) != value.get("digest")
            or value.get("implementation_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()):
        raise ValueError("source_semantics_binding_invalid")


def steps(plan):
    if not isinstance(plan, dict):
        return []
    if isinstance(plan.get("steps"), list):
        return [{**{k:v for k,v in plan.items() if k != "steps"}, **s}
                for s in plan["steps"] if isinstance(s, dict)]
    return [plan]


def profile(step, frozen):
    verify_catalog(frozen)
    return next((s for s in frozen["sources"] if all(step.get(k) == s[k]
                 for k in ("service_name", "odata_version", "entity_set"))), None)


def issue(code, path="/select_fields", **details):
    # Structural names only; never reflect source row values or raw error text.
    actions = {
        "source_detail_grain_incomplete": "Retain the live-validated technical key and classification dimensions. Do not remove filters or deduplicate rows.",
        "source_amount_currency_pair_required": "Select the exact amount and its corresponding currency together.",
        "source_summary_scope_unverified": "A ledger summary needs an explicitly confirmed ledger scope; do not silently select one.",
        "source_scope_definition_required": "Review and freeze sourceSemantics in a new business-contract revision before formal acceptance; do not change the source to obtain MATCH.",
    }
    return {"code": code, "path": path, "classification": "source_semantics",
            "repair_action": actions.get(code, "Preserve the evidence and report the precise scope, grain or configuration gap; never manufacture a comparable result."), **details}


def plan_issues(plan, frozen, definition=None):
    scope = _SCOPE.get() or {}
    definition = definition or scope.get("definition")
    result = []
    for pos, step in enumerate(steps(plan)):
        p = profile(step, frozen)
        if not p:
            continue
        if not material_plan(step, p):
            continue  # A bounded identifier lookup does not claim item grain.
        selected = set(step.get("select_fields") or [])
        # Empty $select means the provider's full live projection. Do not guess
        # fields at planning time; the returned evidence is checked below.
        if not selected:
            continue
        output = step.get("output_contract") or {}
        summary = output.get("mode") == "explicit" and output.get("display_grain") == "ledger_account_summary"
        required = p["summary_fields"] if summary else p["detail_fields"]
        missing = sorted(set(required) - selected)
        if missing:
            result.append(issue("source_detail_grain_incomplete", step_index=pos,
                                required_fields=missing, knowledge_ref=p["knowledge_ref"]))
        for amount, currency in p["amount_pairs"].items():
            if amount in selected and currency not in selected:
                result.append(issue("source_amount_currency_pair_required", step_index=pos,
                                    required_fields=[currency]))
        if summary and p["ledger_fields"] and not (definition or {}).get("ledgerScope"):
            result.append(issue("source_summary_scope_unverified", "/output_contract/display_grain", step_index=pos))
        if p["ledger_fields"]:
            for f in step.get("filters") or []:
                if f.get("field") not in p["ledger_fields"]:
                    continue
                approved = (scope.get("filters") or {}).get(f["field"])
                if approved is None and f["field"] == "Ledger":
                    approved = (definition or {}).get("ledgerScope")
                values = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
                if not approved or f.get("operator", "eq") not in {"eq", "in"} or not set(values) <= set(approved):
                    result.append(issue("source_ledger_scope_changed", "/filters", step_index=pos))
    return result


def definition_issues(value):
    if value is None:
        return []
    expected = {"version", "view", "ledgerScope", "categories", "configurationTime"}
    if (not isinstance(value, dict) or set(value) != expected or value.get("version") != "1.0"
            or value.get("view") not in {"account_items", "ledger_lines", "ledger_account_summary"}
            or value.get("configurationTime") not in {"current", "posting_date"}
            or not isinstance(value.get("categories"), list) or not value["categories"]
            or any(not isinstance(v, str) or v not in KINDS for v in value["categories"])
            or len(set(value["categories"])) != len(value["categories"])
            or (value.get("ledgerScope") is not None and (not isinstance(value["ledgerScope"], list)
                or not value["ledgerScope"] or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", v) for v in value["ledgerScope"])))):
        return [issue("source_scope_definition_invalid", "/execution/acceptance/sourceSemantics")]
    return []


def manifest_profiles(manifest, frozen=None):
    frozen = frozen or catalog()
    found = []
    def walk(v):
        if isinstance(v, dict):
            p = profile(v, frozen)
            if p:
                found.append(p["id"])
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
    walk((manifest.get("execution") or {}).get("steps") or [])
    return sorted(set(found))


def day(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", value):
        value = value[:10]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, str) and (m := re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", value)):
        try:
            return datetime.fromtimestamp(int(m[1]) / 1000, timezone.utc).date()
        except (ValueError, OverflowError):
            return None
    return None


def historical_state(row, p, cutoff):
    boundary = day(cutoff)
    posting = day(row.get(p["posting_date_field"]))
    if boundary is None or posting is None or posting > boundary:
        return "unknown"
    clearing_raw = row.get(p["clearing_date_field"])
    clearing = day(clearing_raw)
    if clearing:
        if clearing < posting:
            return "unknown"
        return "cleared_at_cutoff" if clearing <= boundary else "open_at_cutoff"
    # A missing field is not a blank field. Contradictory clearing evidence
    # cannot be converted to an open item, nor can a current IsCleared flag.
    required = (p["clearing_date_field"], p["clearing_document_field"], p["clearing_year_field"])
    if not all(k in row for k in required) or clearing_raw not in (None, ""):
        return "unknown"
    if row[required[1]] not in (None, "") or str(row[required[2]]) not in ("", "0", "0000", "None"):
        return "unknown"
    return "open_at_cutoff"


def classify(row, p, configurations=(), *, configuration_time="posting_date"):
    carry = p["carryforward"]
    matches = [str(row.get(k)) in values for k, values in carry.items()]
    if matches and all(matches):
        return "balance_carryforward", []
    if matches and any(matches):
        return "unknown", [issue("source_classification_conflict", "/rows")]
    marker = row.get(p["noted_item_field"])
    if marker is True or marker == "X":
        return "noted_item", []
    code = row.get(p["special_code_field"])
    if p["special_code_field"] not in row:
        return "unknown", [issue("source_classification_evidence_missing", "/rows")]
    if code in (None, ""):
        return "posted_normal", []
    if p["noted_item_field"] in row and (marker is False or marker == ""):
        return "posted_special", []
    applicable = [c for c in configurations if c.get("account_type") == row.get(p["account_type_field"])
                  and c.get("code") == code and c.get("verified") is True and c.get("evidence_ref")
                  and ("company_code" not in c or c['company_code'] == row.get('CompanyCode'))
                  and (configuration_time == "current" or (day(c.get("valid_from")) is not None
                      and day(c.get("valid_to")) is not None and day(row.get(p["posting_date_field"])) is not None
                      and day(c["valid_from"]) <= day(row[p["posting_date_field"]]) <= day(c["valid_to"])))]
    if len(applicable) != 1 or not isinstance(applicable[0].get("noted_item"), bool):
        return "unknown", [issue("source_configuration_applicability_missing", "/rows")]
    return ("noted_item" if applicable[0]["noted_item"] else "posted_special"), []


def assess(step, rows, frozen, *, source_complete, cutoff=None, configurations=None, definition=None):
    p = profile(step, frozen)
    if not p or not material_plan(step, p):
        return None
    definition = definition or (_SCOPE.get() or {}).get("definition")
    if configurations is None:
        configurations = (_SCOPE.get() or {}).get('configurations', ())
    if cutoff is None:
        upper = [day(f.get("value")) for f in step.get("filters") or []
                 if f.get("field") == p["posting_date_field"] and f.get("operator") == "le"]
        upper = [v for v in upper if v is not None]
        cutoff = min(upper).isoformat() if upper else None
    issues = plan_issues(step, frozen, definition)
    classified, seen, totals = [], set(), {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(issue("source_row_invalid", f"/rows/{index}"))
            continue
        missing = sorted(set(p["detail_fields"]) - row.keys())
        if missing:
            issues.append(issue("source_detail_grain_incomplete", f"/rows/{index}", required_fields=missing))
        key = [row.get(k) for k in p["technical_key"]]
        if any(v is None or v == "" for v in key):
            issues.append(issue("source_technical_key_missing", f"/rows/{index}"))
        elif digest(key) in seen:
            issues.append(issue("source_technical_key_duplicate", f"/rows/{index}"))
        seen.add(digest(key))
        kind, findings = classify(row, p, configurations, configuration_time=("posting_date" if cutoff else
            (definition or {}).get("configurationTime", "posting_date")))
        issues.extend(findings)
        status = "not_applicable" if kind == "balance_carryforward" else historical_state(row, p, cutoff) if cutoff else "unknown"
        item = {"row_index": index, "source_key": key, "record_kind": kind, "historical_status": status,
                "amounts": {}, "ledger": [row.get(k) for k in p["ledger_fields"]]}
        for amount, currency in p["amount_pairs"].items():
            if amount not in row:
                continue
            try:
                if not isinstance(row[amount], (str, int)) or isinstance(row[amount], bool):
                    raise InvalidOperation()
                number = Decimal(row[amount])
                if not number.is_finite() or not isinstance(row.get(currency), str) or not row[currency]:
                    raise InvalidOperation()
            except (InvalidOperation, ValueError):
                issues.append(issue("source_amount_or_currency_invalid", f"/rows/{index}"))
                continue
            item["amounts"][amount] = {"amount": str(number), "currency": row[currency]}
            group = (*item["ledger"], kind, status, amount, row[currency])
            totals[group] = totals.get(group, Decimal(0)) + number
        classified.append(item)
    return {"version": VERSION, "catalog_digest": frozen["digest"], "source_id": p["id"],
        "view": p["view"], "coverage": list(p["coverage"]), "technical_key": list(p["technical_key"]),
        "source_complete": source_complete is True, "business_complete": source_complete is True and not issues
            and all(r["record_kind"] != "unknown" and (not cutoff or r["historical_status"] != "unknown") for r in classified),
        "row_count": len(rows), "records": classified,
        "observed_ledgers": sorted({str(r.get("Ledger")) for r in rows if isinstance(r, dict) and r.get("Ledger") is not None}),
        "grouped_amounts": [{"ledger": list(k[:-4]), "record_kind": k[-4], "historical_status": k[-3],
            "amount_field": k[-2], "currency": k[-1], "amount": str(v)} for k,v in sorted(totals.items(), key=lambda x:str(x[0]))],
        "issues": list({digest(i): i for i in issues}.values()),
        "retention": "all_original_rows_preserved", "derived_balance_total": None}


def public_assessment(value):
    # Complete rows remain in the original evidence, not an unbounded extra
    # tool response. Do not represent a truncated aggregate preview as complete.
    result = {k: copy.deepcopy(v) for k,v in value.items() if k not in {"records", "grouped_amounts"}}
    result["grouped_amounts"] = copy.deepcopy(value["grouped_amounts"][:200])
    result["grouped_amounts_truncated"] = len(value["grouped_amounts"]) > 200
    result["record_kind_counts"] = {kind: sum(r["record_kind"] == kind for r in value["records"]) for kind in sorted(KINDS)}
    return result


def plan_context(plan, frozen=None):
    frozen = frozen or catalog()
    return [{"source_id": p["id"], "view": p["view"], "coverage": p["coverage"],
             "catalog_digest": frozen["digest"], "issues": plan_issues(s, frozen)}
            for s in steps(plan) if (p := profile(s, frozen)) and material_plan(s, p)]


def contexts_from_run(run):
    """Read platform tool/evidence envelopes, never model-authored projections."""
    result, contexts = run.get("result") or {}, []
    calls = result.get('tool_calls') or []
    latest_report = next((c for c in reversed(calls) if (c.get('tool') or c.get('tool_name')) == 'sap_final_report_validate'
        and c.get('status') == 'completed'), {})
    envelope = latest_report.get('source_reassessment') or (latest_report.get('output') or {}).get('_source_reassessment')
    if envelope:
        from .source_reassessment import effective_assessments
        if not isinstance(envelope, dict) or not isinstance(envelope.get('receipts'), list):
            contexts = [{'business_complete':False,'issues':[issue('source_reassessment_invalid','/source_semantics')]}]
        else:
            # This path reads saved server-owned report output. A digest alone
            # never grants execution or authorizes a model-provided receipt.
            run_id = run.get('run_id') or result.get('run_id')
            contexts = effective_assessments(calls, envelope['receipts'], run_id=run_id, binding=envelope.get('binding'))
    for call in ([] if envelope else calls):
        if not call.get("evidence_ref"):
            continue
        actual = call.get("source_semantics") or (call.get("output") or {}).get("source_semantics")
        if actual:
            contexts.extend(copy.deepcopy(actual))
        else:
            contexts.extend(plan_context((call.get("input") or {}).get("plan") or {}))
    for evidence in result.get("evidence") or []:
        payload = evidence.get("payload") or {}
        if isinstance(payload, dict):
            contexts.extend(copy.deepcopy(payload.get("source_semantics") or []))
    return list({digest(c):c for c in contexts}.values())


def comparison_issues(expected, actual, definition):
    if not expected and not actual and not definition:
        return []
    issues = definition_issues(definition)
    if not definition:
        issues.append(issue("source_scope_definition_required", "/source_semantics"))
    for side, values in (("expected", expected), ("actual", actual)):
        for c in values:
            issues.extend(c.get("issues") or [])
            if c.get("business_complete") is False:
                issues.append(issue("source_business_completeness_unproven", "/source_semantics", side=side))
            if c.get("view") != (definition or {}).get("view"):
                issues.append(issue("source_view_not_comparable", "/source_semantics", side=side))
            if definition and not set(definition["categories"]) - {"unknown"} <= set(c.get("coverage") or []):
                issues.append(issue("source_category_coverage_gap", "/source_semantics", side=side))
    if bool(expected) != bool(actual):
        issues.append(issue("source_comparison_evidence_missing", "/source_semantics"))
    if definition and (not expected or not actual):
        issues.append(issue("source_comparison_evidence_missing", "/source_semantics"))
    if {c.get("catalog_digest") for c in expected} != {c.get("catalog_digest") for c in actual}:
        issues.append(issue("source_semantics_binding_changed", "/source_semantics"))
    return list({digest(i):i for i in issues}.values())


def projection_issues(projection, contexts, definition):
    if not definition or not isinstance(projection, dict) or not contexts:
        return []
    # Do not let a report drop categories/rows or substitute a collapsed entry-
    # view key. Multiple overlapping read scopes need an explicit mapping;
    # "take the last result" and silent deduplication are not safe mappings.
    if len(contexts) != 1:
        return [issue("source_join_mapping_unproven", "/records")]
    value = contexts[0]
    records = projection.get("records") or []
    if len(records) != value.get("row_count"):
        return [issue("source_projection_grain_mismatch", "/records")]
    counts = {kind: sum(r.get("record_kind") == kind for r in records if isinstance(r, dict)) for kind in KINDS}
    if counts != value.get("record_kind_counts"):
        return [issue("source_projection_category_mismatch", "/records")]
    return []
