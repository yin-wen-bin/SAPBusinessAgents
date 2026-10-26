"""Append-only source reassessment lineage, shared by both Runtime providers.

Receipts are produced only by the owned-evidence Broker, not by model inputs.
Digests detect changes; they are not an authorization token or a business PASS.
Historical consumers inspect saved server results, never re-run old jobs.
"""
import copy
import re

VERSION = 'sapba.source-reassessment/1'


def _digest(value):
    from .source_semantics import digest
    return digest(value)


def assessment(call):
    return call.get('source_semantics') or (call.get('output') or {}).get('source_semantics') or []


def make_receipt(*, run_id, binding, source, after, configurations, definition):
    before = assessment(source)
    body = {'version': VERSION, 'run_id': run_id, 'binding': binding,
        'source_call_id': source['call_id'], 'source_evidence_ref': source['evidence_ref'],
        'source_plan_digest': _digest((source.get('input') or source.get('safe_input') or {}).get('plan') or {}),
        'before_digest': _digest(before), 'after': copy.deepcopy(after),
        'configuration_generation': _digest(configurations),
        'configuration_evidence_refs': sorted({c['evidence_ref'] for c in configurations}),
        'definition_digest': _digest(definition)}
    return {**body, 'receipt_ref': 'reassessment_' + _digest(body).split(':')[-1]}


def valid_receipt(receipt, source, *, run_id, binding, known_refs):
    """Check saved lineage. Execution still requires fresh Broker verification."""
    if not isinstance(receipt, dict): return False
    body = {k:v for k,v in receipt.items() if k != 'receipt_ref'}
    before, after = assessment(source), receipt.get('after')
    if (not isinstance(binding, str) or re.fullmatch(r'sha256:[0-9a-f]{64}', binding) is None
            or source.get('status') != 'completed' or (source.get('tool') or source.get('tool_name')) != 'sap_query_execute'
            or receipt.get('version') != VERSION or receipt.get('run_id') != run_id
            or receipt.get('binding') != binding or receipt.get('source_call_id') != source.get('call_id')
            or receipt.get('source_evidence_ref') != source.get('evidence_ref')
            or receipt.get('before_digest') != _digest(before)
            or receipt.get('source_plan_digest') != _digest((source.get('input') or source.get('safe_input') or {}).get('plan') or {})
            or receipt.get('receipt_ref') != 'reassessment_' + _digest(body).split(':')[-1]
            or not isinstance(after, list) or len(before) != len(after) or not before
            or not isinstance(receipt.get('configuration_evidence_refs'), list)
            or not receipt['configuration_evidence_refs']
            or any(not isinstance(ref, str) or ref not in known_refs for ref in receipt['configuration_evidence_refs'])):
        return False
    immutable = ('source_id','catalog_digest','view','coverage','technical_key','row_count','source_complete','retention','observed_ledgers')
    return all(isinstance(a, dict) and isinstance(b, dict)
        and b.get('source_evidence_ref') == receipt['source_evidence_ref']
        and (b.get('business_complete') is not True or (b.get('source_complete') is True and not b.get('issues')))
        and all(a.get(k) == b.get(k) for k in immutable)
        for a,b in zip(before,after))


def effective_assessments(calls, receipts, *, run_id, binding):
    """Return a projection; neither original calls nor receipts are modified."""
    from .source_semantics import plan_context, issue
    by_id = {c.get('call_id'):c for c in calls}
    known = {c.get('evidence_ref') for c in calls if c.get('status') == 'completed'
        and (c.get('tool') or c.get('tool_name')) == 'sap_query_execute' and c.get('evidence_ref')}
    replaced, invalid = {}, False
    for receipt in receipts:
        source = by_id.get(receipt.get('source_call_id')) if isinstance(receipt, dict) else None
        if not source or not valid_receipt(receipt, source, run_id=run_id, binding=binding, known_refs=known):
            invalid = True
            continue
        key = receipt['source_call_id']
        # Conflicting receipts are not resolved by timestamps or 'last wins'.
        if key in replaced and replaced[key] != receipt['after']:
            invalid = True
            continue
        replaced[key] = receipt['after']
    contexts = []
    for call in calls:
        if not call.get('evidence_ref'): continue
        actual = replaced.get(call.get('call_id'), assessment(call))
        contexts.extend(copy.deepcopy(actual) if actual else plan_context((call.get('input') or call.get('safe_input') or {}).get('plan') or {}))
    if invalid:
        contexts.append({'business_complete':False,'issues':[issue('source_reassessment_invalid','/source_semantics')]})
    return list({_digest(c):c for c in contexts}.values())
