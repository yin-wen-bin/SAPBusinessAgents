"""Pure reference rule for the approved draft, not a published Agent or SAP evidence."""
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal
import re


KEYS = ('CompanyCode', 'FiscalYear', 'AccountingDocument', 'AccountingDocumentItem')
DECIMAL = re.compile(r'^-?[0-9]+(?:\.[0-9]+)?$')
ISO = re.compile(r'^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T.*)?$')
SAP = re.compile(r'^/Date\((-?[0-9]+)(?:[+-][0-9]{4})?\)/$')


def text(value):
    return str(value).strip() if value is not None else ''


def date(value):
    value = text(value)
    match = ISO.fullmatch(value)
    if match:
        year, month, day = [int(x) for x in match.groups()]
        if 1 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= monthrange(year, month)[1]:
            return value[:10]
    match = SAP.fullmatch(value)
    if match and -62135596800000 <= int(match[1]) <= 253402300799000:
        return datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc).date().isoformat()
    return None


def evaluate(inputs):
    run_input = inputs.get('run_input') or {}
    source = inputs.get('evidence') or {}
    data = source.get('data') or {}
    rows = data.get('results') if isinstance(data.get('results'), list) else None
    complete = (source.get('ok') is True and data.get('source_complete') is True
                and data.get('source_truncated') is not True and rows is not None)
    rows = rows or []
    cutoff = date(run_input.get('posting_date_to'))
    supplier = text(run_input.get('supplier'))
    gaps = set()
    if not complete:
        gaps.add('source_incomplete')
    if not cutoff or not supplier:
        gaps.add('input_scope_missing')
    unique, conflicts = {}, set()
    duplicates = 0
    for row in rows:
        key = tuple(text(row.get(k)) for k in KEYS)
        if not all(key):
            gaps.add('business_key_missing')
        elif key in unique:
            # Transport-only OData metadata does not change business identity.
            prior = {k: v for k, v in unique[key].items() if k != '__metadata'}
            current = {k: v for k, v in row.items() if k != '__metadata'}
            if prior == current:
                duplicates += 1
            else:
                conflicts.add(key)
                gaps.add('business_key_conflict')
        else:
            unique[key] = row
    records, totals, invoices = [], {}, {}
    count, invoice_count = 0, 0
    for key in sorted(unique):
        row = unique[key]
        classification = 'unknown'
        amount_text = text(row.get('AmountInTransactionCurrency'))
        amount = Decimal(amount_text) if DECIMAL.fullmatch(amount_text) else None
        currency = text(row.get('TransactionCurrency'))
        posted, cleared = date(row.get('PostingDate')), date(row.get('ClearingDate'))
        clearing_text = text(row.get('ClearingDate'))
        # Blank and the SAP zero date are explicit no-clearing representations.
        no_date = clearing_text in {'', '0000-00-00', '00000000'}
        clearing_document = text(row.get('ClearingAccountingDocument'))
        clearing_year = text(row.get('ClearingDocFiscalYear'))
        no_document = clearing_document in {'', '0000000000'}
        no_year = clearing_year in {'', '0', '0000'}
        invoice = text(row.get('AccountingDocumentType')) == 'RE'
        if key in conflicts:
            amount, currency = None, ''
        elif any(name not in row for name in ('ClearingDate', 'ClearingAccountingDocument', 'ClearingDocFiscalYear')):
            gaps.add('clearing_evidence_missing')
        elif not cutoff or not supplier or not posted:
            gaps.add('posting_date_or_scope_missing')
        elif text(row.get('Supplier')) != supplier or text(row.get('FinancialAccountType')) != 'K' or posted > cutoff:
            gaps.add('source_scope_mismatch')
        elif cleared and not no_document and not no_year:
            classification = 'cleared_at_cutoff' if cleared <= cutoff else 'open_at_cutoff'
        elif no_date and no_document and no_year:
            classification = 'open_at_cutoff'
        else:
            gaps.add('clearing_evidence_conflict')
        if amount is None or not currency:
            gaps.add('amount_or_currency_missing')
        if not text(row.get('AccountingDocumentType')):
            gaps.add('document_type_missing')
        if classification == 'open_at_cutoff':
            count += 1
            if invoice:
                invoice_count += 1
            if amount is not None and currency:
                # This OData measure is already signed. Never multiply again
                # by DebitCreditCode; never net different currencies together.
                totals[currency] = totals.get(currency, Decimal(0)) + amount
                if invoice:
                    invoices[currency] = invoices.get(currency, Decimal(0)) + amount
        records.append({'company_code': key[0], 'fiscal_year': key[1],
            'accounting_document': key[2], 'accounting_document_item': key[3],
            'posting_date': posted if key not in conflicts else None,
            'clearing_date': cleared if key not in conflicts else None,
            'document_type': text(row.get('AccountingDocumentType')) if key not in conflicts else None,
            'classification': classification, 'invoice_verification': invoice if key not in conflicts else None,
            'amount': format(amount, 'f') if amount is not None else None, 'currency': currency or None})
    evidence_complete = complete and not gaps
    metrics = [{'currency': code, 'open_amount': format(totals[code], 'f'),
                'invoice_verification_amount': format(invoices.get(code, Decimal(0)), 'f')}
               for code in sorted(totals)]
    status = 'needs_evidence' if not evidence_complete else 'open_items' if count else 'no_open_items'
    output = {'records': records, 'open_item_count': count if evidence_complete else None,
        'invoice_verification_count': invoice_count if evidence_complete else None,
        'amounts_by_currency': metrics, 'business_status': status, 'source_complete': complete,
        'evidence_complete': evidence_complete, 'business_complete': evidence_complete,
        'duplicate_count': duplicates, 'limitations': sorted(gaps)}
    columns = [{'key': key, 'label': {'zh': zh, 'en': en}} for key, zh, en in (
        ('company_code', '公司代码', 'Company code'), ('fiscal_year', '会计年度', 'Fiscal year'),
        ('accounting_document', '会计凭证', 'Accounting document'), ('accounting_document_item', '行项目', 'Item'),
        ('classification', '截止日状态', 'Status at cutoff'), ('document_type', '单据类型', 'Document type'),
        ('amount', '带符号金额', 'Signed amount'), ('currency', '币种', 'Currency'))]
    report = {'schema_version': '1.0', 'records': records,
        'title': {'zh': '供应商历史未清项目与发票校验子集', 'en': 'Historical supplier open items and invoice-verification subset'},
        'headline': {'zh': '证据不足，不能断言完整业务结论。' if not evidence_complete else '已完成截止日未清项目计算。',
                     'en': 'Evidence insufficient for a complete conclusion.' if not evidence_complete else 'Historical open-item calculation completed.'},
        'metrics': [{'id': name, 'label': {'zh': zh, 'en': en}, 'value': output[name]}
                    for name, zh, en in (('open_item_count', '供应商未清项目数', 'Supplier open-item count'),
                                        ('invoice_verification_count', 'RE 发票校验子集数', 'RE invoice-verification subset count'))],
        'business_status': status, 'source_complete': complete, 'evidence_complete': evidence_complete,
        'limitations': sorted(gaps), 'amounts_by_currency': metrics,
        'evidence_tables': [{'id': 'supplier_items', 'title': {'zh': '规范项目明细', 'en': 'Canonical items'},
            'columns': columns, 'rows': records, 'total_rows': len(records), 'source_complete': complete}],
        'missing_evidence': [{'code': gap} for gap in sorted(gaps)]}
    output['business_report'] = report
    return {**output, 'workflow_output': output}
