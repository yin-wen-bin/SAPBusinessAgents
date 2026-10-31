"""Question-bound coverage receipts. Narrower reads cannot redefine a question."""
import re

from .models import VerificationDimension
from .source_semantics import digest


def confirmed_scope(run):
    value = (run.input or {}).get("answer_scope")
    if value is None:
        return {"filters": [], "categories": []}
    if (not isinstance(value, dict) or set(value) - {"filters", "categories"}
            or not isinstance(value.get("filters", []), list) or not isinstance(value.get("categories", []), list)):
        raise ValueError("answer_scope_invalid")
    from .source_semantics import KINDS
    if any(c not in KINDS for c in value.get("categories", [])):
        raise ValueError("answer_scope_category_invalid")
    filters = []
    for f in value.get("filters", []):
        if (not isinstance(f, dict) or set(f) != {"field", "operator", "value"}
                or not isinstance(f["field"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f["field"])
                or f["operator"] not in {"eq", "le", "ge"} or isinstance(f["value"], (dict, list, bool))
                or f["value"] is None):
            raise ValueError("answer_scope_filter_invalid")
        filters.append(dict(f))
    return {"filters": filters, "categories": list(value.get("categories", []))}


def refs_in_report(presentation):
    refs = set()
    for b in presentation.blocks:
        refs.update(b.evidence_refs)
        for item in [*b.entries, *b.metrics, *b.rows]:
            refs.update(item.evidence_refs)
        for binding in [*b.value_bindings.values(), *(v for r in b.rows for v in r.value_bindings.values())]:
            if binding.evidence_ref:
                refs.add(binding.evidence_ref)
    return refs


def same_filters(plan, read_scope, question, answer):
    """Only exact authorized dimensions plus an explicitly confirmed subset."""
    filters = plan.get("filters") or []
    actual = [(f.get("field"), f.get("operator", "eq"), str(f.get("value"))) for f in filters]
    expected = []
    for names, values in read_scope.constraints:
        if len(values) != 1:
            return False
        candidates = [(n, "eq", values[0]) for n in names if (n, "eq", values[0]) in actual]
        if not candidates:
            return False
        expected.append(candidates[0])
    bounds = [(b["fields"][0], b["operator"], b["day"].isoformat()) for b in read_scope.bounds]
    if not bounds:
        match = re.search(r"(?:截至|截止|cutoff|as of)[^0-9]{0,15}(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", question, re.I)
        if match:
            from datetime import date
            try:
                bounds = [("PostingDate", "le", date(*map(int, match.groups())).isoformat())]
            except ValueError:
                return False
    for field, op, day in bounds:
        candidates = [(field, op, v) for v in (day, day + "T00:00:00") if (field, op, v) in actual]
        if not candidates:
            return False
        expected.append(candidates[0])
    expected.extend((f["field"], f["operator"], str(f["value"])) for f in answer["filters"])
    # Additional predicates, limits and unverified joins cannot prove full scope.
    return (set(actual) == set(expected) and len(actual) == len(set(actual))
            and not plan.get("top") and not plan.get("tuple_filters") and not plan.get("filter_from_previous"))


def assess_coverage(broker, run_id, presentation, prior_issues=()):
    from .runtime_query_contract import ReadScope
    run = broker.store.get_run(run_id)
    answer = confirmed_scope(run)
    supplied = {k: v for k, v in (run.input or {}).items() if k != "answer_scope"}
    scope = broker._read_scopes.get(run_id) or ReadScope(run.query or "", supplied)
    saved = broker.store.get_harness_state(run_id).get('read_scope_binding')
    if run_id not in broker._read_scopes and saved:
        from types import SimpleNamespace
        from datetime import date
        scope = SimpleNamespace(constraints=saved['constraints'], unresolved=saved['unresolved'],
                                bounds=[{**b, 'day': date.fromisoformat(b['day'])} for b in saved['bounds']])
    used = refs_in_report(presentation)
    calls = broker.store.list_harness_tool_calls(run_id)
    sources = [c for c in calls if c.get("tool_name") in {"sap_query_execute", "sap_skill_execute"}
               and c.get("status") == "completed" and c.get("evidence_ref")]
    issues = list(prior_issues)
    covers, roles, digests = [], {}, {}
    for c in sources:
        ref, output = c["evidence_ref"], c.get("output") or {}
        roles[ref] = "final" if ref in used else "exploration"
        if ref not in used:
            continue
        raw, meta = broker._read_evidence(run_id, ref)
        digests[ref] = digest(raw)
        if (output.get("ok") is not True or output.get("source_complete") is not True
                or meta.get("source_complete") is not True):
            issues.append({"code": "source_scope_incomplete", "evidence_ref": ref})
        contexts = output.get("source_semantics") or []
        plan = (c.get("safe_input") or {}).get("plan") or {}
        if not contexts:
            from .source_semantics import assess, public_assessment
            from .harness import _extract_rows
            contexts = [public_assessment(assess(plan, _extract_rows(raw),
                broker.store.get_harness_state(run_id)['source_semantics_catalog'],
                source_complete=output.get('source_complete') is True))]
        if any(ctx.get("business_complete") is not True or ctx.get("issues") for ctx in contexts):
            issues.append({"code": "source_business_completeness_unproven", "evidence_ref": ref})
        if answer["categories"] and any(set(ctx.get("record_kind_counts", {})) and any(
                count and kind not in answer["categories"] for kind, count in ctx["record_kind_counts"].items()) for ctx in contexts):
            issues.append({"code": "source_categories_outside_answer_scope", "evidence_ref": ref})
        if c["tool_name"] == "sap_query_execute" and same_filters(plan, scope, run.query or "", answer):
            covers.append(ref)
        elif plan.get('scope_relation_ref'):
            proof = (broker.store.get_harness_state(run_id).get('scope_relations') or {}).get(plan['scope_relation_ref'])
            if proof:
                from .runtime_relations import check
                try:
                    check(broker, run_id, plan)
                except ValueError:
                    issues.append({'code':'source_relation_proof_changed','evidence_ref':ref})
                    continue
                # A permitted partial batch is not full answer coverage.
                if (plan.get('tuple_filters') != [{'fields':proof['fields'],'values':proof['tuples']}]
                        or plan.get('filters') or plan.get('top') or plan.get('filter_from_previous')):
                    continue
                source = next((s for s in sources if s['evidence_ref'] == proof['source_ref']), None)
                if source:
                    source_plan = (source.get('safe_input') or {}).get('plan') or {}
                    source_contexts = (source.get('output') or {}).get('source_semantics') or []
                    source_raw, source_meta = broker._read_evidence(run_id, proof['source_ref'])
                    from .harness import _extract_rows
                    if (digest(_extract_rows(source_raw)) == proof['source_digest']
                            and source_meta.get('source_complete') is True
                            and (source.get('output') or {}).get('source_complete') is True
                            and all(ctx.get('business_complete') is True and not ctx.get('issues') for ctx in source_contexts)
                            and same_filters(source_plan, scope, run.query or '', answer)):
                        roles[proof['source_ref']] = 'prerequisite'
                        digests[proof['source_ref']] = digest(source_raw)
                        covers.append(ref)
    missing = used - {c["evidence_ref"] for c in sources}
    if missing:
        issues.append({"code": "source_scope_evidence_not_owned"})
    if used and not covers:
        issues.append({"code": "answer_scope_coverage_unproven", "path": "/verification/coverage",
                       "repair_action": "Prove the full confirmed scope, or request an explicit user scope clarification; a narrower read does not close earlier gaps."})
    if scope.unresolved:
        issues.append({"code": "answer_scope_clarification_required"})
    state = broker.store.get_harness_state(run_id)
    receipt = {"version": "sapba.answer-scope/1", "run_id": run_id,
               "question_digest": digest({"query": run.query, "input": run.input}),
               "binding": state["report_contract"]["digest"], "answer_scope": answer,
               "roles": roles, "covering_evidence_refs": sorted(covers), "evidence_digests": digests,
               "issues": issues}
    receipt["receipt_ref"] = "scope_" + digest(receipt).split(":")[-1]
    dimension = VerificationDimension(status="needs_evidence" if issues else "verified" if used else "not_requested",
                                      verified_paths=["/answer_scope"] if used and not issues else [],
                                      issues=issues, evidence_refs=sorted(used))
    return dimension, receipt
