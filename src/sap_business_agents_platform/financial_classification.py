"""Run-owned ADT classification proof over the existing managed connection.

Current T074U is supplementary configuration evidence. Historical classification
comes from the exact posted BKPF document, never invented configuration dates.
Raw ADT rows remain encrypted; all reads are selected by owned OData item keys.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path

from .runtime_contract import RuntimeContractError
from .source_semantics import day, profile

KIND = 'adt-posted-item-classification/1'
TOOL = 'sap_financial_classification_read'
SKILL = 'sap-adt-table-export'
KEYS = ('CompanyCode', 'FiscalYear', 'AccountingDocument', 'AccountingDocumentItem')


def fail(code):
    raise RuntimeContractError(code)


def connection_binding(broker):
    """Inspect only the approved Skill's own default selection; no caller config.

    Resolve through the very implementation used by its entrypoint. Never
    expose/copy credentials, select another profile, or construct a SAP client.
    """
    record = broker._approved_skill(SKILL)
    script = Path(record['entrypoint']).parent / 'scripts/adt_table_export.py'
    name = '_sapba_managed_adt_identity_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        fail('classification_connection_identity_unavailable')
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        values, internal = module.load_internal_configuration()
        connection = module._resolve_profile(values, internal).connection
        settings = broker.settings
        if (not connection.verify or not settings.sap_verify_ssl
                or not settings.sap_client or connection.client != settings.sap_client
                or connection.base_url.rstrip('/') != settings.sap_base_url.rstrip('/')):
            fail('classification_connection_identity_mismatch')
        identity = json.dumps([connection.base_url.rstrip('/'), connection.client]).encode()
        return {'target_sha256': hashlib.sha256(identity).hexdigest(),
                'implementation_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def source(broker, run_id, ref, contract):
    calls = broker.store.list_harness_tool_calls(run_id)
    call = next((c for c in calls if c.get('evidence_ref') == ref
        and c.get('tool_name') == 'sap_query_execute' and c.get('status') == 'completed'
        and (c.get('output') or {}).get('ok') is True), None)
    if call is None:
        fail('classification_source_not_owned')
    plan = (call.get('safe_input') or {}).get('plan') or {}
    records = [r for r in contract['query_contract']['evidence_bindings']['configuration_sources']
        if r.get('kind') == KIND and all(r.get(k) == plan.get(k)
            for k in ('service_name', 'odata_version', 'entity_set'))]
    if len(records) != 1:
        fail('classification_source_not_registered')
    raw, meta = broker._read_evidence(run_id, ref)
    from .harness import _extract_rows
    rows = _extract_rows(raw)
    p = profile(plan, broker.store.get_harness_state(run_id)['source_semantics_catalog'])
    if (not p or tuple(p['technical_key']) != KEYS or meta.get('source_type') != 'sap_live'
            or meta.get('source_complete') is not True or call['output'].get('source_complete') is not True
            or raw.get('rows_redacted') or raw.get('restricted_artifact_ref')
            or len(rows) != call['output'].get('row_count') or len(rows) > 5000):
        fail('classification_source_incomplete')
    required = set(KEYS) | {'PostingDate', 'FinancialAccountType', 'SpecialGLCode'}
    if any(not required <= r.keys() for r in rows):
        fail('classification_source_fields_missing')
    if len({tuple(r[k] for k in KEYS) for r in rows}) != len(rows):
        fail('classification_source_key_duplicate')
    return call, rows, p


def task(object_name, fields, filters, limit):
    return {'schema_version': 1, 'source_type': 'table', 'object': object_name,
            'fields': fields, 'filters': filters, 'max_rows': limit}


def eq(field, value):
    return {'field': field, 'operator': 'eq', 'value': value}


def one_of(field, values):
    return {'field': field, 'operator': 'in', 'values': sorted(values)}


def recipes(rows):
    special = [r for r in rows if r['SpecialGLCode'] not in ('', None)]
    if not special:
        return []
    if len(special) > 500 or any(any(not isinstance(r[k], str) or not r[k] for k in KEYS)
                               or day(r['PostingDate']) is None for r in special):
        fail('classification_source_bounds_invalid')
    accounts = {r['FinancialAccountType'] for r in special}
    codes = {r['SpecialGLCode'] for r in special}
    if (accounts - {'D', 'K'} or len(codes) > 30
            or any(not isinstance(c, str) or len(c) != 1 for c in codes)):
        fail('classification_code_invalid')
    result = [task('T074U', ['KOART', 'UMSKZ', 'UMSKS', 'MERKP'],
                   [one_of('KOART', accounts), one_of('UMSKZ', codes)], 200),
              task('DD07T', ['DOMNAME', 'DOMVALUE_L', 'DDTEXT', 'DDLANGUAGE'],
                   [eq('DOMNAME', 'BSTAT'), eq('DDLANGUAGE', 'E'), eq('AS4LOCAL', 'A')], 100)]
    groups = {}
    for row in special:
        groups.setdefault((row['CompanyCode'], row['FiscalYear']), set()).add(row['AccountingDocument'])
    if len(groups) > 10:
        fail('classification_source_bounds_invalid')
    for (company, year), documents in sorted(groups.items()):
        if len(documents) > 100:
            fail('classification_source_bounds_invalid')
        result.append(task('BKPF', ['BUKRS', 'BELNR', 'GJAHR', 'BSTAT', 'BUDAT'],
            [eq('BUKRS', company), eq('GJAHR', year), one_of('BELNR', documents)], len(documents) + 1))
    return result


def filter_identity(item):
    operator = str(item.get('operator') or item.get('option') or '').lower()
    value = sorted(item['values']) if operator == 'in' else item.get('value')
    return item.get('field'), operator, str(item.get('sign', 'I')).upper(), json.dumps(value, sort_keys=True)


def complete_rows(raw, payload, rows):
    completeness, scope = raw.get('completeness') or {}, raw.get('scope') or {}
    if (raw.get('skill_id') != SKILL or raw.get('read_only') is not True or raw.get('validated') is not True
            or raw.get('status') != 'complete' or completeness.get('source_complete') is not True
            or completeness.get('paging_complete') is not True or completeness.get('truncated') is True
            or raw.get('validation_issues') or raw.get('row_count') != len(rows)
            or scope.get('source_type') != 'table' or scope.get('object') != payload['object']
            or set(scope.get('fields') or []) != set(payload['fields'])
            or scope.get('max_rows') != payload['max_rows']
            or sorted(filter_identity(f) for f in scope.get('filters') or []) !=
               sorted(filter_identity(f) for f in payload['filters'])
            or not (raw.get('source') or {}).get('metadata_validated_at')
            or not any(a.get('type') == 'metadata' and a.get('sha256') for a in raw.get('artifacts') or [])
            or not any(a.get('type') == 'output_manifest' and a.get('verified') is True for a in raw.get('artifacts') or [])
            or any(not isinstance(r, dict) or not set(payload['fields']) <= r.keys() for r in rows)):
        fail('classification_adt_evidence_incomplete')
    for row in rows:
        for f in payload['filters']:
            values = f.get('values', [f.get('value')])
            # Metadata support fields may be elided by an otherwise exact projection.
            if f['field'] in row and row[f['field']] not in values:
                fail('classification_adt_scope_mismatch')
    return rows


def decode(rows, p, reads, *, evidence_ref, source_ref, client):
    expected = recipes(rows)
    if len(reads) != len(expected):
        fail('classification_proof_incomplete')
    decoded = [complete_rows(raw, payload, actual) for (raw, actual), payload in zip(reads, expected)]
    configuration, domains, *headers = decoded
    if (not any(r['DOMVALUE_L'] == 'S' and r['DDTEXT'] == 'Noted items' for r in domains)
            or not any(r['DOMVALUE_L'] == '' and r['DDTEXT'] == 'Normal document' for r in domains)):
        fail('classification_domain_semantics_unverified')
    config = {}
    for r in configuration:
        k = (r['KOART'], r['UMSKZ'])
        if k in config or r.get('MANDT') != client or r['MERKP'] not in ('', 'X'):
            fail('classification_configuration_ambiguous')
        config[k] = r
    by_key = {}
    for group in headers:
        for r in group:
            k = tuple(r[f] for f in ('BUKRS', 'GJAHR', 'BELNR'))
            if k in by_key or r.get('MANDT') != client:
                fail('classification_document_ambiguous')
            by_key[k] = r
    result = []
    for row in rows:
        if row['SpecialGLCode'] in ('', None):
            continue
        header = by_key.get(tuple(row[k] for k in KEYS[:3]))
        config_row = config.get((row['FinancialAccountType'], row['SpecialGLCode']))
        if (not header or not config_row or document_day(header['BUDAT']) != day(row['PostingDate'])
                or header['BSTAT'] not in ('', 'S')):
            fail('classification_historical_evidence_unproven')
        result.append({'account_type': row['FinancialAccountType'], 'code': row['SpecialGLCode'],
            'company_code': row['CompanyCode'], 'record_key': {k: row[k] for k in KEYS},
            'source_id': p['id'], 'posting_date': day(row['PostingDate']).isoformat(),
            'noted_item': header['BSTAT'] == 'S', 'proof_basis': 'posted_document_status',
            'current_configuration_noted_item': config_row['MERKP'] == 'X',
            'verified': True, 'evidence_ref': evidence_ref, 'source_evidence_ref': source_ref})
    return result


def document_day(value):
    # ADT's canonical DATS string differs from OData's date representation.
    if isinstance(value, str) and len(value) == 8 and value.isdigit():
        value = value[:4] + '-' + value[4:6] + '-' + value[6:]
    return day(value)


async def read(broker, run_id, arguments):
    if set(arguments) != {'evidence_ref'} or not isinstance(arguments['evidence_ref'], str):
        fail('classification_arguments_invalid')
    contract = broker.report_contract(run_id)
    ref = arguments['evidence_ref']
    _, rows, p = source(broker, run_id, ref, contract)
    payloads = recipes(rows)
    if not payloads:
        return {'ok': True, 'classification_required': False, 'source_evidence_ref': ref}
    attempted = {c['tool_name'] for c in broker.store.list_harness_tool_calls(run_id) if c['status'] == 'completed'}
    if not {'sap_catalog_search', 'sap_schema_get', 'sap_query_validate', 'sap_query_execute'} <= attempted:
        fail('classification_odata_prerequisite_missing')
    identity = connection_binding(broker)
    reads = []
    for payload in payloads:
        broker._ensure_writable(run_id)
        if broker.review_deadline(run_id)['deadline_phase'] != 'querying':
            fail('harness_finalization_only')
        broker.skills.validate_input(SKILL, payload)
        output = await broker.skills.execute(SKILL, payload)
        broker._ensure_writable(run_id)
        actual = output.get('rows') if isinstance(output.get('rows'), list) else []
        public, _ = broker.restricted_artifacts.materialize_skill_output(
            run_id=run_id, skill_id=SKILL, output=output, skill_contract=broker.skills.get(SKILL))
        child = broker._save_evidence(run_id, 'sap_skill', public)
        broker.store.append_event(run_id, 'financial_classification_evidence_read',
            {'object': payload['object'], 'evidence_ref': child, 'source_complete': output.get('status') == 'complete'})
        complete_rows(output, payload, actual)
        reads.append({'input': payload, 'evidence_ref': child})
    if connection_binding(broker) != identity or broker.report_contract(run_id)['digest'] != contract['digest']:
        fail('classification_binding_changed')
    bundle = {'version': KIND, 'run_id': run_id, 'report_contract_digest': contract['digest'],
              'source_complete': True, 'source_evidence_ref': ref, 'connection_binding': identity, 'reads': reads}
    parent = broker._save_evidence(run_id, 'sap_skill', bundle)
    proofs = load_proof(broker, run_id, parent, contract)
    return {'ok': True, 'source_type': 'sap_skill', 'claim_scope': 'customer_business_fact',
            'source_complete': True, 'evidence_ref': parent, 'row_count': len(proofs),
            'source_evidence_ref': ref, 'proof_basis': 'posted_document_status',
            'classification_counts': {kind: sum(c['noted_item'] is noted for c in proofs)
                                      for kind, noted in [('noted_item', True), ('posted_special', False)]},
            'configuration_time': 'current_only', 'privacy_projection': 'derived_classification_only',
            'next_action': 'Recompute sap_money_aggregate on the original evidence, then validate the report. '
                           'Preserve all original rows and scope. Current configuration is not historical validity.'}


def load_proof(broker, run_id, ref, contract):
    raw, meta = broker._read_evidence(run_id, ref)
    if (meta.get('source_type') != 'sap_skill' or meta.get('source_complete') is not True
            or raw.get('version') != KIND or raw.get('run_id') != run_id
            or raw.get('report_contract_digest') != contract['digest']
            or raw.get('connection_binding') != connection_binding(broker)):
        fail('classification_proof_binding_invalid')
    source_ref = raw.get('source_evidence_ref')
    _, rows, p = source(broker, run_id, source_ref, contract)
    payloads = recipes(rows)
    reads = raw.get('reads')
    if not isinstance(reads, list) or [r.get('input') for r in reads] != payloads:
        fail('classification_proof_scope_invalid')
    inputs = []
    for item in reads:
        output, child_meta = broker._read_evidence(run_id, item['evidence_ref'])
        if child_meta.get('source_type') != 'sap_skill' or child_meta.get('source_complete') is not True:
            fail('classification_proof_incomplete')
        inputs.append((output, broker._internal_evidence_rows(run_id, output)))
    return decode(rows, p, inputs, evidence_ref=ref, source_ref=source_ref, client=broker.settings.sap_client)
