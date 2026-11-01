"""Evidence-bound financial arithmetic. No evaluation, I/O, SDK or SAP access.

Source-specific keys, currency pairs and classifications come from the reviewed
source-semantics registry, not model-selected row subsets or guessed SAP codes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import re
from .source_semantics import assess, profile, digest
from .runtime_contract import RuntimeContractError

VERSION = 'sapba.money-calculation/1'


def monetary_request(query):
    # Task-level applicability, not arithmetic parsing. Explicit metric bindings
    # are always checked independently, even for other languages/task wording.
    return bool(re.search(r'金额|余额|货币汇总|分币种|\b(?:amounts?|balances?|monetary)\b', query or '', re.I))


def reject(code):
    exc = RuntimeContractError(code)
    exc.detail = {'validation_issues': [{'code': code, 'path': '/', 'constraint': 'evidence_binding',
        **({'repair_action':'The proof generation changed. Explicitly recompute the same owned evidence locally '
            'with sap_money_aggregate, then bind the new calculation_ref. Preserve old calls; do not repeat SAP reads.'}
           if code == 'money_calculation_changed' else {})}]}
    raise exc


def calculate(*, run_id, binding, evidence_ref, rows, plan, frozen, source_complete, definition=None, configurations=None):
    p = profile(plan, frozen)
    if not p: reject('money_source_not_supported')
    if source_complete is not True: reject('money_source_incomplete')
    if len(rows) > 20000: reject('money_row_limit_exceeded')
    seen = {}
    pairs = {a: c for a, c in p['amount_pairs'].items() if a in plan.get('select_fields', [])}
    if not pairs: reject('money_amount_pair_missing')
    for row in rows:
        key = [row.get(k) for k in p['technical_key']]
        if any(v is None or v == '' for v in key): reject('money_key_missing')
        if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in key): reject('money_key_invalid')
        signature = digest(key)
        if signature in seen:
            reject('money_key_duplicate' if seen[signature] == digest(row) else 'money_key_conflict')
        seen[signature] = digest(row)
        for a, c in pairs.items():
            raw = row.get(a)
            if isinstance(raw, bool) or not isinstance(raw, (str, int)) or len(str(raw)) > 80:
                reject('money_amount_invalid')
            try: number = Decimal(raw)
            except (InvalidOperation, ValueError): reject('money_amount_invalid')
            if not number.is_finite() or abs(number.adjusted()) > 40 or number.as_tuple().exponent < -18:
                reject('money_amount_invalid')
            if not isinstance(row.get(c), str) or not row[c] or len(row[c]) > 16:
                reject('money_currency_missing')
    with localcontext() as ctx:
        ctx.prec = 80  # Bounds above + row limit prevent silent Decimal rounding.
        assessed = assess(plan, rows, frozen, source_complete=True, definition=definition, configurations=configurations)
    if not assessed: reject('money_source_not_supported')
    fatal = {'source_detail_grain_incomplete', 'source_technical_key_missing',
             'source_technical_key_duplicate', 'source_amount_or_currency_invalid'}
    if any(i['code'] in fatal for i in assessed['issues']): reject('money_evidence_invalid')
    groups = {}
    company_index = p['technical_key'].index('CompanyCode') if 'CompanyCode' in p['technical_key'] else None
    with localcontext() as ctx:
        ctx.prec = 80
        for r in assessed['records']:
            company = r['source_key'][company_index] if company_index is not None else None
            for amount, value in r['amounts'].items():
                if amount not in pairs: continue
                group = {'company_code': company, 'ledger': r['ledger'], 'record_kind': r['record_kind'],
                         'historical_status': r['historical_status'], 'amount_field': amount, 'currency': value['currency']}
                # Supply the rollup ourselves; the model must not add displayed
                # subtotals in prose. It is a source-row total, not an invoice
                # classification or a posted balance. Unknown kinds remain in
                # their original detailed group and in this explicit rollup.
                for projection in (group, {**group, 'record_kind': 'all_record_kinds'}):
                    slot = groups.setdefault(digest(projection), [projection, Decimal(0), []])
                    slot[1] += Decimal(value['amount'])
                    slot[2].append(r['source_key'])
    metrics = []
    for _, (group, total, covered) in sorted(groups.items()):
        item = {**group, 'amount': format(total, 'f'), 'count': str(len(covered)), 'row_keys_digest': digest(sorted(covered, key=str))}
        item['id'] = 'm_' + digest(item).split(':')[-1][:24]
        metrics.append(item)
    if not rows:
        # Complete empty evidence proves a row count, not a currency or amount.
        metrics.append({'id': 'm_empty_count', 'count': '0', 'amount': None,
                        'currency': None, 'row_keys_digest': digest([])})
    if len(metrics) > 200: reject('money_group_limit_exceeded')
    body = {'version': VERSION, 'run_id': run_id, 'binding': binding,
        'evidence_ref': evidence_ref, 'source_rows_digest': digest(rows),
        'catalog_digest': frozen['digest'], 'source_complete': True,
        'business_complete': assessed['business_complete'], 'issues': assessed['issues'],
        'row_count': len(rows), 'all_keys_digest': digest(sorted(seen)), 'metrics': metrics,
        # A complete source set can span several currency/status groups. This
        # count-only receipt proves its keys without inventing a mixed balance.
        'row_sets': [{'id': 'rows_all', 'count': str(len(rows)), 'amount': None,
                      'currency': None, 'scope': 'complete_source',
                      'row_keys_digest': digest(sorted(
                          [[r[k] for k in p['technical_key']] for r in rows], key=str))}]}
    if configurations:
        body['configuration_proofs_digest'] = digest(configurations)
        body['configuration_evidence_refs'] = sorted({c['evidence_ref'] for c in configurations})
    return {**body, 'calculation_ref': 'calc_' + digest(body).split(':')[-1][:24]}


def report_issues(presentation, calculations, *, required):
    """Validate declared numeric metrics; never parse or repair narrative money."""
    issues, bound = [], 0
    unverified = False
    for bi, block in enumerate(presentation.blocks):
        if required and block.claim_scope == 'customer_business_fact' and block.type != 'metrics':
            unverified = True  # Untyped prose/tables are not arithmetic proofs.
        for mi, metric in enumerate(block.metrics):
            requested = any((metric.calculation_ref, metric.calculation_item, metric.calculation_field, metric.currency))
            if not requested:
                unverified |= required
                continue
            record = calculations.get(metric.calculation_ref)
            item = next((m for m in (record or {}).get('metrics', []) if m['id'] == metric.calculation_item), None)
            field = metric.calculation_field
            expected_currency = item.get('currency') if item and field == 'amount' else None
            reasons = []
            if record is None: reasons.append('calculation_not_owned')
            elif item is None: reasons.append('calculation_item_not_found')
            if field not in {'amount', 'count'}: reasons.append('calculation_field_invalid')
            expected = item.get(field) if item and field in {'amount', 'count'} else None
            if item and field in {'amount', 'count'}:
                if expected is None: reasons.append('calculation_value_unavailable')
                elif metric.value.zh != expected or metric.value.en != expected:
                    reasons.append('canonical_value_required')
                if metric.currency != expected_currency:
                    reasons.append('count_currency_must_be_null' if field == 'count' else 'currency_mismatch')
                if record['evidence_ref'] not in metric.evidence_refs: reasons.append('source_reference_missing')
            if reasons:
                issues.append({'code': 'money_metric_binding_invalid',
                    'path': f'/blocks/{bi}/metrics/{mi}', 'constraint': 'calculation_binding',
                    'binding_issues': reasons,
                    **({'expected_value': expected, 'expected_currency': expected_currency} if item and field in {'amount', 'count'} else {}),
                    'repair_action': 'Use the exact calculation item, canonical value, currency and evidence reference in both locales; do not change evidence or guess amounts.'})
            else: bound += 1
    status = 'unverified' if unverified or required and not bound else 'verified_metrics_only' if bound else 'not_requested'
    return issues, status


def verification_details(presentation, status, *, accepted=False):
    """Separate arithmetic provenance from untyped narrative and business proof.

    No amount normalization and no new qualification: existing conservative
    status remains authoritative. This projection explains how to declare it.
    """
    return {'status': status, 'business_facts_verified': False,
        'unverified_business_blocks': [f'/blocks/{i}' for i, block in enumerate(presentation.blocks)
            if block.claim_scope == 'customer_business_fact' and block.type != 'metrics'],
        'unbound_metrics': [f'/blocks/{i}/metrics/{j}' for i, block in enumerate(presentation.blocks)
            for j, metric in enumerate(block.metrics) if not metric.calculation_ref],
        'repair_action': ('The report was accepted with the platform status shown here. '
            'Reuse validated_report unchanged in the terminal; a limitation does not require another report submission. '
            if accepted else 'Omit financial_verification (default not_requested) on a new candidate, '
            'or declare the platform status shown here. Validate the corrected candidate before handoff. ')
            + 'Non-metric prose and tables are not arithmetic proofs; valid metric bindings do not verify all business facts.'}
