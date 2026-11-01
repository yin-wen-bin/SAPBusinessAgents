"""Report coverage regressions: synthetic owned rows; no SAP/model calls."""
import asyncio
import copy
import pytest

from tests.test_source_reassessment_closure import reviewed, source_read, configuration_read
from tests.test_runtime_report_contract import make_broker, report
from tests.test_report_verification_v2 import table
from tests.test_shared_query_money_chain import row, seed, metric
from sap_business_agents_platform.source_semantics import digest


def test_full_row_set_proof_is_separate_from_currency_metrics(tmp_path):
    async def scenario():
        broker, store, run, token = make_broker(tmp_path, query='Synthetic report as of 2020-12-31')
        rows = [row(), row(AccountingDocument='D2', ClearingDate='2020-02-01',
                           ClearingAccountingDocument='CLEAR', ClearingDocFiscalYear='2020')]
        ref = seed(broker, store, run, rows)
        calc = await broker.handle(run, token, 'sap_money_aggregate', {'evidence_ref': ref})
        row_set = calc['row_sets'][0]
        assert row_set['count'] == '2' and row_set['amount'] is None and row_set['currency'] is None
        assert row_set['row_keys_digest'] == digest(sorted([[r[k] for k in
            ('CompanyCode','FiscalYear','AccountingDocument','AccountingDocumentItem')] for r in rows], key=str))
        assert row_set['id'] not in {m['id'] for m in calc['metrics']}
        block = table(ref, rows)
        summary = {'type':'metrics','metrics':[dict(metric(calc), id='m'+str(i),
            value={'zh':m['amount'],'en':m['amount']}, calculation_item=m['id'], currency=m['currency'])
            for i,m in enumerate(calc['metrics'])]}
        candidate = report(schema_version='2.0', blocks=[block, summary])
        unbound = await broker.handle(run, token, 'sap_final_report_validate', {'report':candidate})
        assert unbound['ok'] and unbound['verification']['coverage']['status'] == 'needs_evidence'
        block['row_set_binding'] = {'kind':'calculation', 'calculation_ref':calc['calculation_ref'],
            'calculation_item':row_set['id'], 'calculation_field':'count', 'currency':None}
        bound = await broker.handle(run, token, 'sap_final_report_validate',
            {'report':candidate, 'tool_call_id':'bound'})
        assert bound['ok'] and bound['verification']['coverage']['status'] == 'verified'
        # Missing/foreign rows still fail, even with an intact full-source receipt.
        bad = copy.deepcopy(candidate); bad['blocks'][0]['rows'].pop()
        mismatch = await broker.handle(run, token, 'sap_final_report_validate',
            {'report':bad, 'tool_call_id':'missing-row'})
        assert mismatch['ok'] and mismatch['verification']['coverage']['status'] == 'needs_evidence'
        bad = copy.deepcopy(candidate); bad['blocks'][0]['row_set_binding']['calculation_item'] = 'forged'
        forged = await broker.handle(run, token, 'sap_final_report_validate',
            {'report':bad, 'tool_call_id':'forged-row-set'})
        assert not forged['ok']
    asyncio.run(scenario())


@pytest.mark.parametrize('reference_kind', ['configuration', 'foreign'])
def test_configuration_reference_never_replaces_full_business_source(tmp_path, reviewed, reference_kind):
    async def scenario():
        broker, store, run, token = make_broker(tmp_path, query='Synthetic financial report as of 2020-12-31')
        ref, original = source_read(broker, store, run)
        config = configuration_read(broker, store, run)
        block = table(ref, [row(SpecialGLCode='X')])
        block['evidence_refs'].append(config if reference_kind == 'configuration' else 'ev_'+'0'*24)
        result = await broker.handle(run, token, 'sap_final_report_validate',
            {'report':report(schema_version='2.0', blocks=[block])})
        if reference_kind == 'configuration':
            assert result['ok'], result.get('validation_issues')
            assert result['verification']['coverage']['status'] == 'verified'
            receipt = result['verification']['scope_receipt']
            assert receipt['roles'][config] == 'configuration'
            assert receipt['covering_evidence_refs'] == [ref]
        else:
            assert not result['ok'] or result['verification']['coverage']['status'] == 'needs_evidence'
        assert store.list_harness_tool_calls(run)[0] == original
    asyncio.run(scenario())
