"""Reviewed candidate rule: lossless account items, no SAP or external access."""
from calendar import monthrange
from datetime import datetime, timezone
from decimal import Decimal
import re

KEYS=('CompanyCode','FiscalYear','AccountingDocument','AccountingDocumentItem')
NUMBER=re.compile(r'^-?[0-9]+(?:\.[0-9]+)?$')
ISO=re.compile(r'^(\d{4})-(\d{2})-(\d{2})(?:T00:00:00)?$')
SAP=re.compile(r'^/Date\((-?[0-9]+)(?:[+-]\d{4})?\)/$')

def text(v): return str(v).strip() if v is not None else ''

def day(v):
    v=text(v);m=ISO.fullmatch(v)
    if m:
        y,mo,d=[int(x) for x in m.groups()]
        if 1<=y<=9999 and 1<=mo<=12 and 1<=d<=monthrange(y,mo)[1]:return v[:10]
    m=SAP.fullmatch(v)
    if m and -62135596800000<=int(m[1])<=253402300799000:
        return datetime.fromtimestamp(int(m[1])/1000,timezone.utc).date().isoformat()
    return None

def kind(row):
    # Neither document prefix nor the letter of a special-GL code is proof.
    markers=[text(row.get('BusinessTransactionType'))=='RFBC',text(row.get('FiscalPeriod')) in {'0','000'},
             text(row.get('AccountingDocumentCategory'))=='C']
    if all(markers):return 'balance_carryforward','carryforward_source_markers'
    if any(markers):return 'unknown','classification_conflict'
    if row.get('IsNotedItmWithoutBalUpdt') is True or row.get('IsNotedItmWithoutBalUpdt')=='X':
        return 'noted_item','source_noted_flag'
    if 'SpecialGLCode' not in row:return 'unknown','classification_evidence_missing'
    if text(row.get('SpecialGLCode'))=='':return 'posted_normal','ordinary_source_item'
    if 'IsNotedItmWithoutBalUpdt' in row and (row['IsNotedItmWithoutBalUpdt'] is False or row['IsNotedItmWithoutBalUpdt']==''):
        return 'posted_special','source_posted_flag'
    # No supplied, independently verified historical configuration is available
    # in this immutable source plan. Never backdate the current K/F observation.
    return 'unknown','historical_special_gl_configuration_missing'

def state(row,cutoff):
    posted,cleared=day(row.get('PostingDate')),day(row.get('ClearingDate'))
    required=['ClearingDate','ClearingAccountingDocument','ClearingDocFiscalYear']
    if not cutoff or not posted or posted>cutoff:return 'unknown','posting_scope_missing_or_invalid'
    if not all(k in row for k in required):return 'unknown','clearing_evidence_missing'
    doc,year=text(row.get(required[1])),text(row.get(required[2]))
    if cleared:
        if cleared<posted or not doc or year!=cleared[:4]:return 'unknown','clearing_evidence_conflict'
        return ('cleared_at_cutoff' if cleared<=cutoff else 'open_at_cutoff'),''
    if row.get('ClearingDate') in (None,'') and doc=='' and year in {'','0','0000'}:
        return 'open_at_cutoff',''
    return 'unknown','clearing_evidence_conflict'

def evaluate(inputs):
    run=inputs.get('run_input') or {};source=inputs.get('evidence') or {};data=source.get('data') or {}
    original=data.get('results');rows=original if isinstance(original,list) else []
    complete=source.get('ok') is True and data.get('source_complete') is True and data.get('source_truncated') is not True and isinstance(original,list)
    cutoff=day(run.get('posting_date_to'));company=text(run.get('company_code'));supplier=text(run.get('supplier'))
    gaps=set();seen={};duplicates=set();duplicate_count=0
    if not complete:gaps.add('source_incomplete')
    if not cutoff or not company or not supplier:gaps.add('input_scope_missing')
    for row in rows:
        if not isinstance(row,dict):gaps.add('source_row_invalid');continue
        key=tuple(text(row.get(k)) for k in KEYS)
        if not all(key):gaps.add('business_key_missing')
        if key in seen:
            duplicate_count+=1;duplicates.add(key);gaps.add('business_key_duplicate')
            if {k:v for k,v in row.items() if k!='__metadata'}!={k:v for k,v in seen[key].items() if k!='__metadata'}:
                gaps.add('business_key_conflict')
        else:seen[key]=row
    records=[];totals={};counts={'open_item_count':0,'invoice_verification_count':0,'posted_open_item_count':0,
        'noted_open_item_count':0,'cleared_item_count':0,'unknown_category_count':0,'carryforward_count':0}
    for index,raw in enumerate(rows):
        row=raw if isinstance(raw,dict) else {};key=tuple(text(row.get(k)) for k in KEYS)
        category,basis=kind(row);status,reason=state(row,cutoff)
        if category=='balance_carryforward':status='not_applicable';reason='';counts['carryforward_count']+=1
        if category=='unknown':gaps.add(basis);counts['unknown_category_count']+=1
        if reason:gaps.add(reason)
        if text(row.get('CompanyCode'))!=company or text(row.get('Supplier'))!=supplier or text(row.get('FinancialAccountType'))!='K':
            gaps.add('source_scope_mismatch');status='unknown'
        if any(row.get(flag) in (True,'X') for flag in ('IsReversed','IsReversal','ClearingIsReset')):
            gaps.add('reversal_or_reset_history_unproven');status='unknown'
        if key in duplicates or not all(key):status='unknown'
        amount_text=text(row.get('AmountInTransactionCurrency'));currency=text(row.get('TransactionCurrency'))
        amount=Decimal(amount_text) if isinstance(row.get('AmountInTransactionCurrency'),(str,int)) and not isinstance(row.get('AmountInTransactionCurrency'),bool) and NUMBER.fullmatch(amount_text) else None
        if amount is None or not currency:gaps.add('amount_or_currency_missing')
        doc_type=text(row.get('AccountingDocumentType'))
        if not doc_type:gaps.add('document_type_missing')
        invoice=doc_type=='RE' and category in {'posted_normal','posted_special'}
        if status=='cleared_at_cutoff':counts['cleared_item_count']+=1
        if status=='open_at_cutoff':
            counts['open_item_count']+=1
            if category in {'posted_normal','posted_special'}:counts['posted_open_item_count']+=1
            if category=='noted_item':counts['noted_open_item_count']+=1
            if invoice:counts['invoice_verification_count']+=1
        if currency and amount is not None:
            group=(text(row.get('SourceLedger')) or None,text(row.get('Ledger')) or None,currency)
            if group not in totals:totals[group]={k:Decimal(0) for k in ('open_amount','invoice_verification_amount','posted_open_amount','noted_open_amount','unknown_open_amount','cleared_amount','carryforward_amount')}
            value=totals[group]
            if status=='open_at_cutoff':
                value['open_amount']+=amount
                bucket='noted_open_amount' if category=='noted_item' else 'posted_open_amount' if category in {'posted_normal','posted_special'} else 'unknown_open_amount'
                value[bucket]+=amount
                if invoice:value['invoice_verification_amount']+=amount
            if status=='cleared_at_cutoff':value['cleared_amount']+=amount
            if category=='balance_carryforward':value['carryforward_amount']+=amount
        records.append({'company_code':key[0] or None,'fiscal_year':key[1] or None,'accounting_document':key[2] or None,
            'accounting_document_item':key[3] or None,'posting_date':day(row.get('PostingDate')),'clearing_date':day(row.get('ClearingDate')),
            'document_type':doc_type or None,'classification':status,'record_kind':category,'classification_basis':basis,
            'special_gl_code':text(row.get('SpecialGLCode')) if 'SpecialGLCode' in row else None,'source_record_index':index,
            'invoice_verification':invoice,'amount':format(amount,'f') if amount is not None else None,'currency':currency or None})
    valid=complete and not gaps
    monetary=[{'source_ledger':source_ledger,'ledger':ledger,'currency':currency,
               **{key:format(value,'f') if valid else None for key,value in amount.items()}}
              for (source_ledger,ledger,currency),amount in sorted(totals.items(),key=lambda item:str(item[0]))]
    status='needs_evidence' if not valid else 'open_items' if counts['open_item_count'] else 'no_open_items'
    output={'records':records,**{key:value if valid or key in {'unknown_category_count','carryforward_count'} else None for key,value in counts.items()},
        'amounts_by_currency':monetary,'business_status':status,'source_complete':complete,'evidence_complete':valid,'business_complete':valid,
        'duplicate_count':duplicate_count,'limitations':sorted(gaps)}
    columns=[{'key':key,'label':{'zh':zh,'en':en}} for key,zh,en in (
        ('company_code','公司代码','Company code'),('fiscal_year','年度','Year'),('accounting_document','凭证','Document'),
        ('accounting_document_item','行项目','Item'),('record_kind','经济类别','Economic category'),('classification','截止日状态','Status at cutoff'),
        ('amount','带符号金额','Signed amount'),('currency','币种','Currency'))]
    output['business_report']={'schema_version':'1.0','records':records,'business_status':status,'source_complete':complete,'evidence_complete':valid,
        'title':{'zh':'供应商历史项目、实际应付与备忘请求','en':'Historical supplier items, posted AP and noted requests'},
        'headline':{'zh':'证据不足，未清数量或实际应付余额尚不能确认。' if not valid else '已区分历史未清、已清及项目经济类别。',
                    'en':'Insufficient evidence to confirm open counts or posted AP balance.' if not valid else 'Historical open, cleared and economic categories are separated.'},
        'metrics':[{'id':key,'label':{'zh':zh,'en':en},'value':output[key]} for key,zh,en in (
            ('open_item_count','全部未清项目数','All open-item count'),('posted_open_item_count','实际过账未清数','Posted open count'),
            ('noted_open_item_count','备忘待处理数','Noted pending count'),('invoice_verification_count','RE发票校验子集','RE invoice subset'),
            ('cleared_item_count','截止日已清数','Cleared count'),('unknown_category_count','类别待核实数','Unclassified count'))],
        'amounts_by_currency':monetary,'limitations':sorted(gaps),'missing_evidence':[{'code':gap} for gap in sorted(gaps)],
        'evidence_tables':[{'id':'supplier_items','title':{'zh':'完整来源项目（不删行）','en':'All source items (no rows removed)'},
            'columns':columns,'rows':records,'total_rows':len(records),'source_complete':complete}]}
    return {**output,'workflow_output':output}
