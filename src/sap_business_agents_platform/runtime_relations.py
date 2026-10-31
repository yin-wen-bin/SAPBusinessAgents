"""Reviewed, run-owned composite-key scope proofs. Never infer field aliases."""
import copy
import hashlib

from .source_semantics import digest


def validate_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("runtime_relation_registry_invalid")
    ids = set()
    for r in rules:
        required = {"id", "review_reference", "source", "target", "source_fields", "target_fields", "company_field", "cardinality", "system_context_digest"}
        if (not isinstance(r, dict) or set(r) != required or not isinstance(r['id'], str) or not r['id']
                or r['id'] in ids or not isinstance(r['review_reference'], str) or not r['review_reference']
                or r['cardinality'] not in {'one_to_one', 'many_to_one'}):
            raise ValueError("runtime_relation_registry_invalid")
        import re
        if not isinstance(r['system_context_digest'], str) or not re.fullmatch('[0-9a-f]{64}', r['system_context_digest']):
            raise ValueError('runtime_relation_system_context_required')
        for side in ('source', 'target'):
            if (not isinstance(r[side], dict) or set(r[side]) != {'service_name', 'odata_version', 'entity_set'}
                    or not all(isinstance(v, str) and v for v in r[side].values())):
                raise ValueError("runtime_relation_registry_invalid")
        a, b = r['source_fields'], r['target_fields']
        if (not isinstance(a, list) or not isinstance(b, list) or not a or len(a) != len(b)
                or len(set(a)) != len(a) or len(set(b)) != len(b)
                or any(not isinstance(v, str) or not v.isidentifier() for v in a + b)
                or r['company_field'] != 'CompanyCode' or 'CompanyCode' not in a
                or 'CompanyCode' not in b or a.index('CompanyCode') != b.index('CompanyCode')):
            raise ValueError("runtime_relation_registry_invalid")
        ids.add(r['id'])


def context_digest(broker):
    """A reviewed deployment is bound to its exact private SAP configuration."""
    path = broker.settings.sap_env_file
    if path is None or not path.is_file() or path.is_symlink():
        raise ValueError('runtime_relation_system_context_required')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve(broker, run_id, arguments):
    if set(arguments) != {'relation_id', 'evidence_ref'}:
        raise ValueError('runtime_relation_arguments_invalid')
    state = broker.store.get_harness_state(run_id)
    registry = state['report_contract']['query_contract']['evidence_bindings']
    rules = registry.get('relations', [])
    validate_rules(rules)
    rule = next((r for r in rules if r['id'] == arguments['relation_id']), None)
    if rule is None:
        raise ValueError('runtime_relation_review_required')
    if context_digest(broker) != rule['system_context_digest']:
        raise ValueError('runtime_relation_system_context_mismatch')
    scope = broker._read_scopes.get(run_id)
    if scope is None:
        raise ValueError('runtime_relation_frozen_scope_required')
    from .report_verification import evidence
    source, rows = evidence(broker, run_id, arguments['evidence_ref'])
    plan = source['safe_input']['plan']
    if (any(plan.get(k) != v for k, v in rule['source'].items()) or plan.get('top')
            or not set(rule['source_fields']) <= set(plan.get('select_fields') or [])):
        raise ValueError('runtime_relation_source_mismatch')
    scope.check(plan)
    for side in ('source', 'target'):
        entity = rule[side]
        fields = scope.schemas.get(tuple(entity[k] for k in ('service_name', 'odata_version', 'entity_set')))
        if fields is None or not set(rule[side + '_fields']) <= fields:
            raise ValueError('runtime_relation_authoritative_schema_required')
    from .source_semantics import profile
    p = profile(plan, state['source_semantics_catalog'])
    if p is None:
        raise ValueError('runtime_relation_stable_source_key_required')
    source_keys, tuples = set(), []
    for row in rows:
        key = tuple(str(row.get(k, '')) for k in p['technical_key'])
        if any(row.get(k) in (None, '') for k in p['technical_key']) or key in source_keys:
            raise ValueError('runtime_relation_source_key_invalid')
        source_keys.add(key)
        values = [row.get(k) for k in rule['source_fields']]
        if any(v is None or v == '' or isinstance(v, (list, dict, bool, float)) for v in values):
            raise ValueError('runtime_relation_key_incomplete')
        tuples.append([str(v) for v in values])
    if not tuples or len(tuples) > 500:
        raise ValueError('runtime_relation_key_limit_or_empty')
    unique = sorted({tuple(t) for t in tuples})
    if rule['cardinality'] == 'one_to_one' and len(unique) != len(tuples):
        raise ValueError('runtime_relation_cardinality_mismatch')
    proof = {'version': 'sapba.scope-relation/1', 'run_id': run_id, 'binding': state['report_contract']['digest'],
             'rule_digest': digest(rule), 'registry_digest': digest(registry), 'relation_id': rule['id'],
             'source_ref': arguments['evidence_ref'], 'source_digest': digest(rows),
             'source_plan_digest': digest(plan), 'target': rule['target'],
             'fields': rule['target_fields'], 'tuples': [list(t) for t in unique],
             'target_schema_fields': sorted(fields)}
    ref = 'relation_' + digest(proof).split(':')[-1]
    proofs = dict(state.get('scope_relations') or {})
    if ref not in proofs and len(proofs) >= 100:
        raise ValueError('runtime_relation_proof_limit')
    proofs[ref] = proof
    broker.store.update_harness_state(run_id, {'scope_relations': proofs})
    return {'ok': True, 'relation_ref': ref, 'target_plan': {**rule['target'], 'http_method': 'GET',
        'scope_relation_ref': ref, 'tuple_filters': [{'fields': proof['fields'], 'values': proof['tuples']}]},
        'source_evidence_ref': proof['source_ref'], 'review_reference': rule['review_reference']}


def check(broker, run_id, plan):
    ref = plan.get('scope_relation_ref')
    state = broker.store.get_harness_state(run_id)
    proof = (state.get('scope_relations') or {}).get(ref)
    if not proof or proof['run_id'] != run_id or proof['binding'] != state['report_contract']['digest']:
        raise ValueError('runtime_relation_not_owned')
    if ref != 'relation_' + digest(proof).split(':')[-1] or broker.report_contract(run_id)['digest'] != proof['binding']:
        raise ValueError('runtime_relation_binding_changed')
    registry = state['report_contract']['query_contract']['evidence_bindings']
    rule = next((r for r in registry.get('relations', []) if r['id'] == proof['relation_id']), None)
    if rule is None or context_digest(broker) != rule['system_context_digest'] or digest(rule) != proof['rule_digest']:
        raise ValueError('runtime_relation_system_context_mismatch')
    if digest(registry) != proof['registry_digest'] or any(plan.get(k) != v for k, v in proof['target'].items()):
        raise ValueError('runtime_relation_binding_changed')
    from .report_verification import evidence
    source, rows = evidence(broker, run_id, proof['source_ref'])
    if digest(rows) != proof['source_digest'] or digest(source['safe_input']['plan']) != proof['source_plan_digest']:
        raise ValueError('runtime_relation_source_changed')
    scope = broker._read_scopes.get(run_id)
    fields = (scope.schemas.get(tuple(proof['target'][k] for k in ('service_name', 'odata_version', 'entity_set')))
              if scope else set(proof['target_schema_fields']))
    if fields is None or not set(proof['fields']) <= fields:
        raise ValueError('runtime_relation_authoritative_schema_required')
    tuples = plan.get('tuple_filters') or []
    if len(tuples) != 1 or tuples[0]['fields'] != proof['fields']:
        raise ValueError('runtime_relation_exact_tuple_required')
    allowed = {tuple(t) for t in proof['tuples']}
    requested = [tuple(str(v) for v in t) for t in tuples[0]['values']]
    if not requested or any(len(t) != len(proof['fields']) or t not in allowed for t in requested):
        raise ValueError('runtime_relation_value_outside_scope')
    # A contradictory independent key filter must not change the proved tuple.
    if any(f.get('field') in proof['fields'] for f in plan.get('filters') or []):
        raise ValueError('runtime_relation_key_filter_conflict')
    return True
