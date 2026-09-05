from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import calendar
import re


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _date_value(value):
    text = _text(value)
    sap_v2 = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", text)
    if sap_v2 is not None:
        milliseconds = int(sap_v2.group(1))
        if milliseconds < -62135596800000 or milliseconds > 253402300799999:
            return None
        return (
            datetime(1970, 1, 1, tzinfo=timezone.utc)
            + timedelta(milliseconds=milliseconds)
        ).date()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    if month < 1 or month > 12:
        return None
    if day < 1 or day > calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def _decimal(value):
    text = _text(value)
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text) is None:
        return None
    return Decimal(text)


def _truthy(value):
    return value is True or _text(value).lower() in {"true", "x", "1", "yes"}


def _walk_rows(value, step_id, active_step):
    rows = []
    if isinstance(value, dict):
        current_step = active_step
        if step_id in value and isinstance(value.get(step_id), dict):
            current_step = True
        if current_step:
            for field in ("rows", "results"):
                candidate = value.get(field)
                if isinstance(candidate, list):
                    rows.extend(item for item in candidate if isinstance(item, dict))
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                rows.extend(_walk_rows(child, step_id, current_step or key == step_id))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                rows.extend(_walk_rows(child, step_id, active_step))
    return rows


def _rows(value, step_id):
    return _walk_rows(value, step_id, False)


def _source_flags(value):
    flags = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_complete" and isinstance(child, bool):
                flags.append(child)
            elif key not in {"restricted_artifact_ref", "artifacts"}:
                flags.extend(_source_flags(child))
    elif isinstance(value, list):
        for child in value:
            flags.extend(_source_flags(child))
    return flags


def _failed_values(value):
    values = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "failed_filter_values" and isinstance(child, list):
                values.update(_text(item) for item in child if _text(item))
            elif key not in {"restricted_artifact_ref", "artifacts"}:
                values.update(_failed_values(child))
    elif isinstance(value, list):
        for child in value:
            values.update(_failed_values(child))
    return values


def _document_rows(rows, company_code, ledger, fiscal_year, document):
    return [
        row for row in rows
        if _text(row.get("CompanyCode")) == company_code
        and _text(row.get("Ledger")) == ledger
        and _text(row.get("FiscalYear")) == fiscal_year
        and _text(row.get("AccountingDocument")) == document
    ]


def _history_events(history, row, requested_area):
    events = history.get("events") if isinstance(history, dict) else []
    if not isinstance(events, list):
        return []
    matches = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if _text(event.get("company_code")) != _text(row.get("CompanyCode")):
            continue
        if _text(event.get("fiscal_year")) != _text(row.get("FiscalYear")):
            continue
        if _text(event.get("accounting_document")) != _text(row.get("AccountingDocument")):
            continue
        if _text(event.get("accounting_document_item")) != _text(row.get("AccountingDocumentItem")):
            continue
        if requested_area and _text(event.get("dunning_area")) != requested_area:
            continue
        matches.append(event)
    return matches


def _latest_history(events):
    if not events:
        return None, False
    dated = []
    for event in events:
        effective = _date_value(event.get("effective_dunning_date"))
        run_date = _date_value(event.get("dunning_run_date"))
        chosen = effective if effective is not None else run_date
        if chosen is not None:
            dated.append((chosen, event))
    if not dated:
        return None, True
    maximum = max(item[0] for item in dated)
    latest = [item[1] for item in dated if item[0] == maximum]
    ambiguous = len(latest) != 1 or any(
        _text(item.get("sequence_status")) == "ambiguous" for item in latest
    )
    return latest[0] if len(latest) == 1 else None, ambiguous


def _amount_map(values):
    return {key: format(amount, "f") for key, amount in sorted(values.items())}


def _stage(identifier, zh, en, count, state):
    return {
        "id": identifier,
        "title": {"zh": zh, "en": en},
        "state": state,
        "evidence_count": count,
    }


def evaluate(inputs):
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    requested = run_input.get("customers") if isinstance(run_input.get("customers"), list) else []
    customers = [_text(item) for item in requested]
    cutoff = _date_value(run_input.get("as_of"))
    business_date = _date_value(run_input.get("business_date"))
    requested_area = _text(run_input.get("dunning_area"))
    historical = cutoff is not None and business_date is not None and cutoff < business_date

    collection = evidence.get("collect_ar_evidence") if isinstance(evidence.get("collect_ar_evidence"), dict) else {}
    history = evidence.get("historical_dunning") if isinstance(evidence.get("historical_dunning"), dict) else {}
    raw_items = [
        row for row in _rows(collection, "customer_items")
        if _text(row.get("FinancialAccountType")) == "D" and _truthy(row.get("IsOpenItemManaged"))
    ]
    clearing_documents = _rows(collection, "clearing_document_evidence")
    reversal_documents = _rows(collection, "clearing_reversal_documents")
    current_master = _rows(collection, "customer_dunning")

    flags = _source_flags(collection)
    collection_complete = bool(flags) and all(flags)
    history_completeness = history.get("completeness") if isinstance(history.get("completeness"), dict) else {}
    history_complete = (
        not historical
        or (
            history.get("status") == "complete"
            and history_completeness.get("source_complete") is True
            and history_completeness.get("evidence_complete") is True
        )
    )
    source_complete = collection_complete and history_complete
    global_gaps = set(_text(item) for item in inputs.get("known_gaps", []) if _text(item))
    ledger_scope = evidence.get("ledger_scope") if isinstance(evidence.get("ledger_scope"), dict) else {}
    global_gaps.update(_text(item) for item in ledger_scope.get("evidence_gaps", []) if _text(item))
    resolved_ledger = _text(ledger_scope.get("ledger"))

    def with_ledger_context(rows):
        normalized = []
        for source_row in rows:
            row = dict(source_row)
            source_ledger = _text(row.get("Ledger"))
            if not source_ledger and resolved_ledger:
                row["Ledger"] = resolved_ledger
            elif source_ledger and resolved_ledger and source_ledger != resolved_ledger:
                global_gaps.add("fi_nonleading_ledger_row")
                continue
            normalized.append(row)
        return normalized

    raw_items = with_ledger_context(raw_items)
    clearing_documents = with_ledger_context(clearing_documents)
    reversal_documents = with_ledger_context(reversal_documents)
    if not collection_complete:
        global_gaps.add("ar_source_incomplete")
    if historical and not history_complete:
        global_gaps.add("historical_dunning_source_incomplete")

    canonical = {}
    conflicts = set()
    for row in raw_items:
        key = tuple(_text(row.get(field)) for field in (
            "CompanyCode", "Ledger", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"
        ))
        if not all(key):
            conflicts.add(key)
        elif key in canonical and canonical[key] != row:
            conflicts.add(key)
        else:
            canonical[key] = row
    if conflicts:
        global_gaps.add("fi_business_key_conflict")
    failed_customers = _failed_values(collection)

    customer_results = []
    worklist = []
    status_order = {"inconclusive": 0, "attention": 1, "normal": 2}
    for customer in customers:
        scoped = [row for row in canonical.values() if _text(row.get("Customer")) == customer]
        master = [
            row for row in current_master
            if _text(row.get("Customer")) == customer
            and (not requested_area or _text(row.get("DunningArea")) == requested_area)
        ] if not historical else []
        customer_gaps = set(global_gaps)
        if customer in failed_customers:
            customer_gaps.add("customer_query_chunk_failed")
        open_rows = []
        ordinary_overdue = {}
        credit_balances = {}
        special_count = 0
        for row in scoped:
            posting = _date_value(row.get("PostingDate"))
            if cutoff is None or posting is None:
                customer_gaps.add("posting_or_cutoff_date_missing")
                continue
            if posting > cutoff:
                continue
            clearing = _date_value(row.get("ClearingDate"))
            clearing_year = _text(row.get("ClearingDocFiscalYear"))
            clearing_document = _text(row.get("ClearingAccountingDocument"))
            clearing_rows = _document_rows(
                clearing_documents,
                _text(row.get("CompanyCode")),
                _text(row.get("Ledger")),
                clearing_year,
                clearing_document,
            ) if clearing_year and clearing_document else []
            reversal_refs = {
                (_text(item.get("ReverseDocumentFiscalYear")), _text(item.get("ReverseDocument")))
                for item in clearing_rows
                if _text(item.get("ReverseDocumentFiscalYear")) and _text(item.get("ReverseDocument"))
            }
            reversed_flag = _truthy(row.get("ClearingIsReversed")) or bool(reversal_refs)
            reversal_date = None
            historical_open_status = "open"
            if clearing is not None and clearing <= cutoff:
                if not reversed_flag:
                    continue
                if len(reversal_refs) != 1:
                    customer_gaps.add("historical_clearing_reversal_date_missing")
                    continue
                reversal_year, reversal_document = next(iter(reversal_refs))
                reversal_rows = _document_rows(
                    reversal_documents,
                    _text(row.get("CompanyCode")),
                    _text(row.get("Ledger")),
                    reversal_year,
                    reversal_document,
                )
                reversal_dates = {
                    value for item in reversal_rows
                    if (value := _date_value(item.get("PostingDate"))) is not None
                }
                if len(reversal_dates) != 1:
                    customer_gaps.add("historical_clearing_reversal_date_missing")
                    continue
                reversal_date = next(iter(reversal_dates))
                if reversal_date > cutoff:
                    continue
                historical_open_status = "reopened_by_reversal"

            amount = _decimal(row.get("AmountInTransactionCurrency"))
            currency = _text(row.get("TransactionCurrency"))
            if amount is None or not currency:
                customer_gaps.add("amount_or_currency_missing")
                continue
            due = _date_value(row.get("NetDueDate") or row.get("DueCalculationBaseDate"))
            overdue_days = max(0, (cutoff - due).days) if due is not None else None
            if due is None:
                aging_bucket = "unknown"
                customer_gaps.add("due_date_missing")
            elif overdue_days == 0:
                aging_bucket = "not_due"
            elif overdue_days <= 30:
                aging_bucket = "1_30"
            elif overdue_days <= 60:
                aging_bucket = "31_60"
            elif overdue_days <= 90:
                aging_bucket = "61_90"
            else:
                aging_bucket = "over_90"

            dunning_level = "0"
            last_dunning_date = None
            dunning_block = ""
            dunning_status = "not_dunned"
            if historical:
                related_events = _history_events(history, row, requested_area)
                latest_event, ambiguous = _latest_history(related_events)
                if ambiguous:
                    customer_gaps.add("historical_dunning_sequence_ambiguous")
                    dunning_status = "unknown"
                elif latest_event is not None:
                    dunning_level = _text(latest_event.get("dunning_level")) or "0"
                    last_dunning_date = _date_value(
                        latest_event.get("effective_dunning_date") or latest_event.get("dunning_run_date")
                    )
                    dunning_block = _text(latest_event.get("dunning_blocking_reason"))
                    dunning_status = "confirmed_before_cutoff"
            else:
                dunning_level = _text(row.get("DunningLevel")) or "0"
                last_dunning_date = _date_value(row.get("LastDunningDate"))
                dunning_block = _text(row.get("DunningBlockingReason"))
                if dunning_level not in {"", "0"} and last_dunning_date is not None:
                    dunning_status = "confirmed_current"

            debit_credit = _text(row.get("DebitCreditCode")).upper()
            special_code = _text(row.get("SpecialGLCode"))
            record = {
                "company_code": _text(row.get("CompanyCode")),
                "ledger": _text(row.get("Ledger")),
                "fiscal_year": _text(row.get("FiscalYear")),
                "accounting_document": _text(row.get("AccountingDocument")),
                "accounting_document_item": _text(row.get("AccountingDocumentItem")),
                "customer": customer,
                "customer_result_status": "found",
                "posting_date": posting.isoformat(),
                "due_date": due.isoformat() if due is not None else None,
                "overdue_days": overdue_days,
                "aging_bucket": aging_bucket,
                "amount": format(amount, "f"),
                "currency": currency,
                "debit_credit_indicator": debit_credit,
                "special_gl_code": special_code or None,
                "clearing_date": clearing.isoformat() if clearing is not None else None,
                "clearing_document": clearing_document or None,
                "clearing_reversal_date": reversal_date.isoformat() if reversal_date is not None else None,
                "historical_open_status": historical_open_status,
                "dunning_level": dunning_level,
                "last_dunning_date": last_dunning_date.isoformat() if last_dunning_date is not None else None,
                "dunning_blocking_reason": dunning_block or None,
                "dunning_as_of_status": dunning_status,
            }
            open_rows.append(record)
            worklist.append(record)
            if special_code:
                special_count += 1
            elif debit_credit in {"H", "C", "CREDIT"} or amount < 0:
                credit_balances[currency] = credit_balances.get(currency, Decimal(0)) + amount
            elif overdue_days is not None and overdue_days > 0:
                ordinary_overdue[currency] = ordinary_overdue.get(currency, Decimal(0)) + amount

        master_areas = {_text(row.get("DunningArea")) for row in master}
        if not historical and not requested_area and len(master_areas) > 1:
            customer_gaps.add("dunning_area_ambiguous")
        blocked = any(_text(row.get("DunningBlock")) for row in master) or any(
            _text(item.get("dunning_blocking_reason")) for item in open_rows
        )
        attention = bool(ordinary_overdue or special_count or blocked)
        customer_complete = source_complete and not customer_gaps
        status = "inconclusive" if not customer_complete else "attention" if attention else "normal"
        customer_results.append({
            "customer": customer,
            "customer_result_status": "found" if scoped or master else "no_open_items_or_master_data",
            "business_status": status,
            "open_item_count": len(open_rows),
            "ordinary_overdue_amounts": _amount_map(ordinary_overdue),
            "credit_balance_amounts": _amount_map(credit_balances),
            "special_gl_item_count": special_count,
            "dunning_blocked": blocked,
            "dunning_areas": sorted(master_areas),
            "historical_dunning_master_status": "not_assessed" if historical else "current",
            "source_complete": source_complete,
            "evidence_complete": customer_complete,
            "evidence_gaps": sorted(customer_gaps),
            "items": open_rows,
        })

    worklist.sort(key=lambda row: (
        0 if row.get("dunning_blocking_reason") else 1,
        -int(row.get("overdue_days") or 0),
        -int(row.get("dunning_level") or 0) if _text(row.get("dunning_level")).isdigit() else 0,
        _text(row.get("currency")),
        -abs(_decimal(row.get("amount")) or Decimal(0)),
        _text(row.get("fiscal_year")),
        _text(row.get("accounting_document")),
        _text(row.get("accounting_document_item")),
    ))
    customer_results.sort(key=lambda item: (status_order[item["business_status"]], item["customer"]))
    counts = {
        status: sum(1 for item in customer_results if item["business_status"] == status)
        for status in status_order
    }
    evidence_complete = source_complete and not global_gaps and all(
        item["evidence_complete"] for item in customer_results
    )
    reported_gaps = sorted(global_gaps | {
        gap for item in customer_results for gap in item["evidence_gaps"]
    })
    business_status = "inconclusive" if counts["inconclusive"] else "attention" if counts["attention"] else "normal"
    stages = [
        _stage("receivables", "客户应收", "Customer receivables", len(raw_items), "confirmed" if collection_complete else "unknown"),
        _stage(
            "dunning",
            "历史催收事件" if historical else "当前催收状态",
            "Historical dunning events" if historical else "Current dunning status",
            len(history.get("events", [])) if historical and isinstance(history.get("events"), list) else len(current_master),
            "confirmed" if history_complete and collection_complete else "unknown",
        ),
        _stage("worklist", "催收工作清单", "Collection worklist", len(worklist), "confirmed" if evidence_complete else "unknown"),
    ]
    limitations = ["historical_dunning_master_snapshot_not_available"] if historical else []
    report = {
        "title": {"zh": "应收账款催收分析", "en": "AR collection analysis"},
        "tone": "warning" if business_status != "normal" else "positive",
        "business_status": business_status,
        "headline": {
            "zh": f"已检查 {len(customer_results)} 个客户，其中 {counts['attention']} 个需要催收处理",
            "en": f"Reviewed {len(customer_results)} customer(s); {counts['attention']} require collection follow-up",
        },
        "overview": {
            "zh": "按截止日重建应收项目；历史基准日使用已执行催收事件，当前客户主数据不冒充历史快照。",
            "en": "Receivables were reconstructed as of the cutoff; historical dates use executed dunning events, never current master data as a historical snapshot.",
        },
        "stages": stages,
        "customer_results": customer_results,
        "worklist": worklist,
        "missing_evidence": reported_gaps,
        "limitations": limitations,
        "next_actions": {
            "zh": ["优先处理冻结、超期天数最长和催收级别最高的项目；特殊总账项目单独人工复核。"],
            "en": ["Prioritize blocked, longest-overdue, and highest-level dunning items; review special G/L items separately."],
        },
    }
    workflow_output = {
        "requested_customer_count": len(customers),
        "result_customer_count": len(customer_results),
        "normal_customer_count": counts["normal"],
        "attention_customer_count": counts["attention"],
        "inconclusive_customer_count": counts["inconclusive"],
        "customer_results": customer_results,
        "worklist_artifact": {"name": "ar-collection-worklist.csv", "row_count": len(worklist)},
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_status": business_status,
        "business_report": report,
    }
    return {
        "rule_id": "ar_collection_deterministic_v3",
        "status": "complete" if business_status != "inconclusive" else "inconclusive",
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": evidence_complete,
        "evidence_gaps": reported_gaps,
        "business_report": report,
        "workflow_output": workflow_output,
    }
