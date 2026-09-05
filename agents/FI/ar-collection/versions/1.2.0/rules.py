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


def _item_key(value):
    """Canonicalize a numeric FI item only for cross-source key comparison."""
    text = _text(value)
    return str(int(text)) if text.isdigit() else text


def _walk_rows(value, step_id, active_step):
    rows = []
    if isinstance(value, dict):
        if active_step:
            for field in ("rows", "results"):
                candidate = value.get(field)
                if isinstance(candidate, list):
                    rows.extend(item for item in candidate if isinstance(item, dict))
            for key, child in value.items():
                if key not in {"rows", "results"} and isinstance(child, (dict, list)):
                    rows.extend(_walk_rows(child, step_id, True))
        else:
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    rows.extend(_walk_rows(child, step_id, key == step_id))
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
        if _item_key(event.get("accounting_document_item")) != _item_key(
            row.get("AccountingDocumentItem")
        ):
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
    state_labels = {
        "confirmed": _localized("已确认", "Confirmed"),
        "unknown": _localized("无法确认", "Unknown"),
    }
    return {
        "id": identifier,
        "title": {"zh": zh, "en": en},
        "label": {"zh": zh, "en": en},
        "state": state,
        "state_label": state_labels.get(state, _localized(state, state)),
        "detail": _localized(f"{count} 条", f"{count} record(s)"),
        "evidence_count": count,
    }


def _localized(zh, en):
    return {"zh": zh, "en": en}


def _priority_for_overdue(overdue_days):
    if overdue_days is None:
        return "unknown"
    if overdue_days > 90:
        return "high"
    if overdue_days > 30:
        return "medium"
    if overdue_days > 0:
        return "low"
    return "none"


def _action(
    *,
    evidence_gap=False,
    dunning_blocked=False,
    special_gl=False,
    credit_balance=False,
    overdue_days=None,
    dunning_level="0",
    dunning_status="not_dunned",
):
    """Return the deterministic, read-only business action for one AR item."""
    if evidence_gap:
        return {
            "action_required": True,
            "action_code": "resolve_evidence_gap",
            "action_priority": "high",
            "action_reason": _localized(
                "项目证据、日期、币种或业务键不完整，暂不能确定催收动作。",
                "Item evidence, dates, currency, or business keys are incomplete, so a collection action cannot yet be determined.",
            ),
            "recommended_action": _localized(
                "先补齐并核验缺失证据，再决定是否催收。",
                "Complete and verify the missing evidence before deciding whether to collect.",
            ),
        }
    if dunning_blocked:
        return {
            "action_required": True,
            "action_code": "resolve_dunning_block",
            "action_priority": "high",
            "action_reason": _localized(
                "项目或客户主数据存在催收冻结。",
                "A dunning block exists on the item or customer master data.",
            ),
            "recommended_action": _localized(
                "核实冻结原因；仅在业务批准后解除冻结，再决定是否继续催收。",
                "Verify the block reason; remove it only after business approval, then decide whether to continue collection.",
            ),
        }
    if special_gl:
        return {
            "action_required": True,
            "action_code": "review_special_gl",
            "action_priority": "medium",
            "action_reason": _localized(
                "该项目是特殊总账项目，不能并入普通贸易应收催收。",
                "This is a special G/L item and cannot be included in ordinary trade-receivable collection.",
            ),
            "recommended_action": _localized(
                "单独复核特殊总账业务性质、到期和后续处理方式。",
                "Review the special G/L business purpose, due status, and follow-up separately.",
            ),
        }
    if credit_balance:
        return {
            "action_required": True,
            "action_code": "review_credit_balance",
            "action_priority": "medium",
            "action_reason": _localized(
                "该项目为贷方余额或负金额，不应作为普通逾期应收催收。",
                "This item is a credit balance or negative amount and should not be collected as an ordinary overdue receivable.",
            ),
            "recommended_action": _localized(
                "核对贷项、未分配来款、退款或抵销关系。",
                "Review credit memo, unapplied receipt, refund, or offset relationships.",
            ),
        }
    if overdue_days is not None and overdue_days > 0:
        level = int(dunning_level) if _text(dunning_level).isdigit() else 0
        has_dunning = level > 0 or dunning_status in {
            "confirmed_current", "confirmed_before_cutoff", "confirmed_historical"
        }
        if has_dunning:
            return {
                "action_required": True,
                "action_code": "continue_dunning_follow_up",
                "action_priority": _priority_for_overdue(overdue_days),
                "action_reason": _localized(
                    f"项目已逾期 {overdue_days} 天，且存在已执行催收证据或催收级别。",
                    f"The item is {overdue_days} day(s) overdue and has an executed dunning event or dunning level.",
                ),
                "recommended_action": _localized(
                    "按现有催收级别复核付款、争议和付款承诺，并继续跟进。",
                    "Review payment, dispute, and promise-to-pay status, then continue follow-up at the established dunning level.",
                ),
            }
        return {
            "action_required": True,
            "action_code": "initiate_first_dunning",
            "action_priority": _priority_for_overdue(overdue_days),
            "action_reason": _localized(
                f"项目已逾期 {overdue_days} 天，但催收级别为0且未发现已执行催收记录。",
                f"The item is {overdue_days} day(s) overdue, but its dunning level is 0 and no executed dunning event was found.",
            ),
            "recommended_action": _localized(
                "首次催收前复核付款、争议和付款承诺；确认无误后进入首次催收。",
                "Before first dunning, review payment, dispute, and promise-to-pay status; then initiate first dunning if appropriate.",
            ),
        }
    return {
        "action_required": False,
        "action_code": "monitor_until_due",
        "action_priority": "none",
        "action_reason": _localized(
            "项目尚未到期，无需立即催收。",
            "The item is not yet due and does not require immediate collection.",
        ),
        "recommended_action": _localized(
            "监控至到期日；到期后再按付款状态决定是否催收。",
            "Monitor until the due date, then decide on collection based on payment status.",
        ),
    }


def _action_label(code):
    labels = {
        "resolve_evidence_gap": _localized("补齐证据", "Resolve evidence gaps"),
        "resolve_dunning_block": _localized("核实催收冻结", "Resolve dunning blocks"),
        "review_special_gl": _localized("复核特殊总账", "Review special G/L"),
        "review_credit_balance": _localized("复核贷方余额", "Review credit balances"),
        "initiate_first_dunning": _localized("进入首次催收", "Initiate first dunning"),
        "continue_dunning_follow_up": _localized("继续催收跟进", "Continue dunning follow-up"),
        "monitor_until_due": _localized("监控至到期", "Monitor until due"),
    }
    return labels.get(code, _localized("未知", "Unknown"))


def _action_summary(rows):
    labels = {
        code: _action_label(code)
        for code in (
            "resolve_evidence_gap",
            "resolve_dunning_block",
            "review_special_gl",
            "review_credit_balance",
            "initiate_first_dunning",
            "continue_dunning_follow_up",
            "monitor_until_due",
        )
    }
    counts = {}
    for row in rows:
        code = _text(row.get("action_code"))
        if code:
            counts[code] = counts.get(code, 0) + 1
    return [
        {"action_code": code, "count": counts[code], "label": labels[code]}
        for code in labels if counts.get(code)
    ]


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
    raw_current_master = _rows(collection, "customer_dunning")

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
    global_gaps = {_text(item) for item in inputs.get("known_gaps", []) if _text(item)}
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
    fi_conflict_customers = set()
    incomplete_key_rows = {}
    incomplete_row_signatures = set()
    conflicting_fi_keys = set()
    for row in raw_items:
        key = tuple(_text(row.get(field)) for field in (
            "CompanyCode", "Ledger", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"
        ))
        customer = _text(row.get("Customer"))
        row_signature = tuple(sorted((str(field), _text(value)) for field, value in row.items()))
        if not all(key):
            if row_signature not in incomplete_row_signatures:
                incomplete_key_rows.setdefault(customer, []).append(row)
                incomplete_row_signatures.add(row_signature)
            fi_conflict_customers.add(customer)
        elif key in conflicting_fi_keys:
            fi_conflict_customers.add(customer)
        elif key in canonical and canonical[key] != row:
            fi_conflict_customers.add(customer or _text(canonical[key].get("Customer")))
            conflict_row = {
                "CompanyCode": key[0],
                "Ledger": key[1],
                "FiscalYear": key[2],
                "AccountingDocument": key[3],
                "AccountingDocumentItem": key[4],
                "Customer": customer or _text(canonical[key].get("Customer")),
            }
            incomplete_key_rows.setdefault(_text(conflict_row.get("Customer")), []).append(conflict_row)
            canonical.pop(key, None)
            conflicting_fi_keys.add(key)
        else:
            canonical[key] = row

    master_by_key = {}
    dunning_master_conflict_customers = set()
    conflicting_master_keys = set()
    for row in raw_current_master:
        key = (
            _text(row.get("CompanyCode")),
            _text(row.get("Customer")),
            _text(row.get("DunningArea")),
        )
        customer = key[1]
        if not key[0] or not customer:
            dunning_master_conflict_customers.add(customer)
        elif key in conflicting_master_keys:
            dunning_master_conflict_customers.add(customer)
        elif key in master_by_key and master_by_key[key] != row:
            dunning_master_conflict_customers.add(customer)
            master_by_key.pop(key, None)
            conflicting_master_keys.add(key)
        else:
            master_by_key[key] = row
    current_master = list(master_by_key.values())
    failed_customers = _failed_values(collection)

    customer_results = []
    worklist = []
    all_item_records = []
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
        if customer in fi_conflict_customers:
            customer_gaps.add("fi_business_key_conflict")
        if customer in dunning_master_conflict_customers:
            customer_gaps.add("dunning_master_business_key_conflict")

        master_areas = {_text(row.get("DunningArea")) for row in master}
        if not historical and not requested_area and len(master_areas) > 1:
            customer_gaps.add("dunning_area_ambiguous")

        def unique_master_value(field):
            values = {_text(row.get(field)) for row in master if _text(row.get(field))}
            if len(values) > 1:
                customer_gaps.add("dunning_master_business_key_conflict")
                return None
            return next(iter(values)) if values else None

        dunning_procedure = unique_master_value("DunningProcedure")
        dunning_clerk = unique_master_value("DunningClerk")
        dunning_recipient = unique_master_value("DunningRecipient")
        master_blocked = any(_text(row.get("DunningBlock")) for row in master)
        assignment_status = (
            "not_assessed" if historical
            else "unknown" if "dunning_master_business_key_conflict" in customer_gaps
            else "assigned" if dunning_clerk or dunning_recipient
            else "unassigned"
        )

        open_rows = []
        ordinary_overdue = {}
        credit_balances = {}
        special_count = 0
        for row in scoped:
            item_gaps = set()
            posting = _date_value(row.get("PostingDate"))
            if cutoff is None or posting is None:
                item_gaps.add("posting_or_cutoff_date_missing")
            if posting is not None and cutoff is not None and posting > cutoff:
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
            if cutoff is not None and clearing is not None and clearing <= cutoff:
                if not reversed_flag:
                    continue
                if len(reversal_refs) != 1:
                    item_gaps.add("historical_clearing_reversal_date_missing")
                    historical_open_status = "unknown"
                else:
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
                        item_gaps.add("historical_clearing_reversal_date_missing")
                        historical_open_status = "unknown"
                    else:
                        reversal_date = next(iter(reversal_dates))
                        if reversal_date > cutoff:
                            continue
                        historical_open_status = "reopened_by_reversal"

            amount = _decimal(row.get("AmountInTransactionCurrency"))
            currency = _text(row.get("TransactionCurrency"))
            if amount is None or not currency:
                item_gaps.add("amount_or_currency_missing")
            due = _date_value(row.get("NetDueDate") or row.get("DueCalculationBaseDate"))
            overdue_days = max(0, (cutoff - due).days) if cutoff is not None and due is not None else None
            if due is None:
                aging_bucket = "unknown"
                item_gaps.add("due_date_missing")
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
                    item_gaps.add("historical_dunning_sequence_ambiguous")
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
            credit_balance = debit_credit in {"H", "C", "CREDIT"} or (amount is not None and amount < 0)
            action = _action(
                evidence_gap=bool(item_gaps),
                dunning_blocked=bool(dunning_block) or master_blocked,
                special_gl=bool(special_code),
                credit_balance=credit_balance,
                overdue_days=overdue_days,
                dunning_level=dunning_level,
                dunning_status=dunning_status,
            )
            customer_gaps.update(item_gaps)
            record = {
                "company_code": _text(row.get("CompanyCode")) or None,
                "ledger": _text(row.get("Ledger")) or None,
                "fiscal_year": _text(row.get("FiscalYear")) or None,
                "accounting_document": _text(row.get("AccountingDocument")) or None,
                "accounting_document_item": _text(row.get("AccountingDocumentItem")) or None,
                "customer": customer,
                "customer_result_status": "found",
                "posting_date": posting.isoformat() if posting is not None else None,
                "due_date": due.isoformat() if due is not None else None,
                "overdue_days": overdue_days,
                "aging_bucket": aging_bucket,
                "amount": format(amount, "f") if amount is not None else None,
                "currency": currency or None,
                "debit_credit_indicator": debit_credit or None,
                "special_gl_code": special_code or None,
                "clearing_date": clearing.isoformat() if clearing is not None else None,
                "clearing_document": clearing_document or None,
                "clearing_reversal_date": reversal_date.isoformat() if reversal_date is not None else None,
                "historical_open_status": historical_open_status,
                "dunning_level": dunning_level,
                "last_dunning_date": last_dunning_date.isoformat() if last_dunning_date is not None else None,
                "dunning_blocking_reason": dunning_block or None,
                "dunning_as_of_status": dunning_status,
                "item_evidence_complete": not item_gaps,
                "evidence_gaps": sorted(item_gaps),
                **action,
                "action_code_label": _action_label(action["action_code"]),
                "action_priority_label": {
                    "high": _localized("高", "High"),
                    "medium": _localized("中", "Medium"),
                    "low": _localized("低", "Low"),
                    "none": _localized("无需处理", "No action"),
                    "unknown": _localized("未知", "Unknown"),
                }[action["action_priority"]],
            }
            open_rows.append(record)
            all_item_records.append(record)
            if action["action_required"]:
                worklist.append(record)
            if special_code:
                special_count += 1
            elif credit_balance and amount is not None and currency:
                credit_balances[currency] = credit_balances.get(currency, Decimal(0)) + amount
            elif overdue_days is not None and overdue_days > 0 and amount is not None and currency:
                ordinary_overdue[currency] = ordinary_overdue.get(currency, Decimal(0)) + amount

        # Preserve an actionable placeholder for incomplete FI rows that could not be canonicalized.
        for row in incomplete_key_rows.get(customer, []):
            action = _action(evidence_gap=True)
            record = {
                "company_code": _text(row.get("CompanyCode")) or None,
                "ledger": _text(row.get("Ledger")) or None,
                "fiscal_year": _text(row.get("FiscalYear")) or None,
                "accounting_document": _text(row.get("AccountingDocument")) or None,
                "accounting_document_item": _text(row.get("AccountingDocumentItem")) or None,
                "customer": customer,
                "customer_result_status": "incomplete_business_key",
                "posting_date": None,
                "due_date": None,
                "overdue_days": None,
                "aging_bucket": "unknown",
                "amount": None,
                "currency": None,
                "debit_credit_indicator": None,
                "special_gl_code": None,
                "clearing_date": None,
                "clearing_document": None,
                "clearing_reversal_date": None,
                "historical_open_status": "unknown",
                "dunning_level": "0",
                "last_dunning_date": None,
                "dunning_blocking_reason": None,
                "dunning_as_of_status": "unknown",
                "item_evidence_complete": False,
                "evidence_gaps": ["fi_business_key_conflict"],
                **action,
                "action_code_label": _action_label(action["action_code"]),
                "action_priority_label": _localized("高", "High"),
            }
            open_rows.append(record)
            all_item_records.append(record)
            worklist.append(record)

        blocked = master_blocked or any(_text(item.get("dunning_blocking_reason")) for item in open_rows)
        actionable_count = sum(1 for item in open_rows if item.get("action_required") is True)
        monitor_count = sum(1 for item in open_rows if item.get("action_required") is False)
        action_summary = _action_summary(open_rows)
        attention = any(
            item.get("action_required") is True
            and item.get("action_code") != "resolve_evidence_gap"
            for item in open_rows
        )
        customer_complete = source_complete and not customer_gaps
        status = "inconclusive" if not customer_complete else "attention" if attention else "normal"
        customer_results.append({
            "customer": customer,
            "customer_result_status": "found" if scoped or master or incomplete_key_rows.get(customer) else "no_open_items_or_master_data",
            "business_status": status,
            "open_item_count": len(open_rows),
            "action_required_item_count": actionable_count,
            "monitor_item_count": monitor_count,
            "ordinary_overdue_amounts": _amount_map(ordinary_overdue),
            "credit_balance_amounts": _amount_map(credit_balances),
            "special_gl_item_count": special_count,
            "dunning_blocked": blocked,
            "dunning_areas": sorted(master_areas),
            "dunning_procedure": dunning_procedure,
            "dunning_clerk": dunning_clerk,
            "dunning_recipient": dunning_recipient,
            "assignment_status": assignment_status,
            "action_summary": action_summary,
            "historical_dunning_master_status": "not_assessed" if historical else "current",
            "source_complete": source_complete,
            "evidence_complete": customer_complete,
            "evidence_gaps": sorted(customer_gaps),
            "items": open_rows,
        })

    priority_order = {"high": 0, "medium": 1, "low": 2, "unknown": 3, "none": 4}
    worklist.sort(key=lambda row: (
        0 if row.get("action_code") in {"resolve_dunning_block", "resolve_evidence_gap"} else 1,
        priority_order.get(_text(row.get("action_priority")), 3),
        -int(row.get("overdue_days") or 0),
        -int(row.get("dunning_level") or 0) if _text(row.get("dunning_level")).isdigit() else 0,
        _text(row.get("currency")),
        -abs(_decimal(row.get("amount")) or Decimal(0)),
        _text(row.get("company_code")),
        _text(row.get("ledger")),
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
    action_count = len(worklist)
    monitor_count = sum(1 for item in all_item_records if item.get("action_required") is False)
    history_events = history.get("events") if isinstance(history.get("events"), list) else []
    history_event_keys = {
        tuple(_text(event.get(field)) for field in (
            "company_code", "customer", "dunning_area", "dunning_run_date", "dunning_run_id",
            "fiscal_year", "accounting_document", "accounting_document_item",
        ))
        for event in history_events if isinstance(event, dict)
    }
    stages = [
        _stage("receivables", "未清应收项目", "Open receivable items", len(all_item_records), "confirmed" if collection_complete else "unknown"),
        _stage(
            "dunning",
            "历史催收事件" if historical else "当前催收状态",
            "Historical dunning events" if historical else "Current dunning status",
            len(history_event_keys) if historical else len(master_by_key),
            "confirmed" if history_complete and collection_complete else "unknown",
        ),
        _stage("worklist", "催收工作清单", "Collection worklist", action_count, "confirmed" if evidence_complete else "unknown"),
    ]
    limitations = ["historical_dunning_master_snapshot_not_available"] if historical else []
    action_columns = [
        {"key": "customer", "label": _localized("客户", "Customer")},
        {"key": "company_code", "label": _localized("公司代码", "Company code")},
        {"key": "fiscal_year", "label": _localized("年度", "Fiscal year")},
        {"key": "accounting_document", "label": _localized("财务凭证", "Accounting document")},
        {"key": "accounting_document_item", "label": _localized("行项目", "Item")},
        {"key": "due_date", "label": _localized("到期日", "Due date"), "format": "date"},
        {"key": "overdue_days", "label": _localized("逾期天数", "Days overdue"), "format": "integer"},
        {"key": "amount", "label": _localized("金额", "Amount"), "format": "decimal"},
        {"key": "currency", "label": _localized("币种", "Currency")},
        {"key": "dunning_level", "label": _localized("当前催收级别", "Current dunning level")},
        {"key": "dunning_blocking_reason", "label": _localized("催收冻结", "Dunning block")},
        {"key": "action_priority_label", "label": _localized("处理优先级", "Processing priority")},
        {"key": "action_reason", "label": _localized("需要处理的原因", "Reason for action")},
        {"key": "recommended_action", "label": _localized("建议动作", "Recommended action")},
    ]
    evidence_columns = action_columns[:11] + [
        {"key": "action_required", "label": _localized("需要立即处理", "Immediate action required"), "format": "status"},
        {"key": "action_code_label", "label": _localized("处理分类", "Action category")},
    ]
    aggregate_summary = _action_summary(all_item_records)
    action_text = {
        "resolve_evidence_gap": _localized("先补齐存在缺口的项目证据。", "Resolve item evidence gaps first."),
        "resolve_dunning_block": _localized("优先核实催收冻结原因。", "Prioritize review of dunning-block reasons."),
        "review_special_gl": _localized("单独复核特殊总账项目。", "Review special G/L items separately."),
        "review_credit_balance": _localized("核对贷方余额、未分配来款、退款或抵销。", "Review credit balances, unapplied receipts, refunds, or offsets."),
        "initiate_first_dunning": _localized("首次催收前复核付款、争议和付款承诺，然后进入首次催收。", "Review payment, dispute, and promise-to-pay status before initiating first dunning."),
        "continue_dunning_follow_up": _localized("按现有催收级别继续跟进。", "Continue follow-up at the existing dunning level."),
        "monitor_until_due": _localized("未到期项目仅监控至到期日。", "Monitor not-yet-due items until their due dates."),
    }
    present_codes = [item["action_code"] for item in aggregate_summary]
    next_actions = {
        "zh": [action_text[code]["zh"] for code in present_codes],
        "en": [action_text[code]["en"] for code in present_codes],
    }
    if not next_actions["zh"]:
        next_actions = {
            "zh": ["当前没有需要立即处理的项目。"],
            "en": ["No item currently requires immediate action."],
        }
    report = {
        "title": _localized("应收账款催收处理清单", "AR collection action worklist"),
        "tone": "warning" if business_status != "normal" else "positive",
        "business_status": business_status,
        "headline": {
            "zh": f"已检查 {len(customer_results)} 个客户：{action_count} 个项目需要处理，{monitor_count} 个项目仅需监控。",
            "en": f"Reviewed {len(customer_results)} customer(s): {action_count} item(s) require action and {monitor_count} item(s) only require monitoring.",
        },
        "overview": {
            "zh": "按截止日重建应收项目并生成只读处理建议。处理优先级由SAPBusinessAgents规则定义，不是SAP原生催收优先级。",
            "en": "Receivables were reconstructed as of the cutoff and converted into read-only action recommendations. Processing priority is defined by SAPBusinessAgents rules, not native SAP dunning priority.",
        },
        "metrics": [
            {"id": "requested_customer_count", "label": _localized("已检查客户", "Customers reviewed"), "value": len(customer_results)},
            {"id": "attention_customer_count", "label": _localized("需处理客户", "Customers requiring attention"), "value": counts["attention"]},
            {"id": "action_required_item_count", "label": _localized("需要处理项目", "Items requiring action"), "value": action_count},
            {"id": "monitor_item_count", "label": _localized("仅监控项目", "Monitor-only items"), "value": monitor_count},
        ],
        "stages": stages,
        "customer_results": customer_results,
        "action_summary": aggregate_summary,
        "action_tables": [{
            "id": "ar_collection_worklist",
            "title": _localized("需要处理的催收工作清单", "Collection worklist requiring action"),
            "artifact_name": "ar-collection-worklist.csv",
            "columns": action_columns,
            "rows": worklist,
            "total_rows": action_count,
            "source_complete": source_complete,
            "empty_state": _localized("当前没有需要立即处理的项目。", "No item currently requires immediate action."),
        }],
        "evidence_tables": [{
            "id": "all_open_receivables",
            "title": _localized("全部未清应收明细", "All open receivable items"),
            "columns": evidence_columns,
            "rows": all_item_records,
            "total_rows": len(all_item_records),
            "source_complete": source_complete,
        }],
        "missing_evidence": reported_gaps,
        "limitations": limitations,
        "next_actions": next_actions,
    }
    workflow_output = {
        "requested_customer_count": len(customers),
        "result_customer_count": len(customer_results),
        "normal_customer_count": counts["normal"],
        "attention_customer_count": counts["attention"],
        "inconclusive_customer_count": counts["inconclusive"],
        "action_required_item_count": action_count,
        "monitor_item_count": monitor_count,
        "customer_results": customer_results,
        "worklist_artifact": {"name": "ar-collection-worklist.csv", "row_count": action_count},
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_status": business_status,
        "business_report": report,
    }
    return {
        "rule_id": "ar_collection_deterministic_v4",
        "status": "complete" if business_status != "inconclusive" else "inconclusive",
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": evidence_complete,
        "evidence_gaps": reported_gaps,
        "business_report": report,
        "workflow_output": workflow_output,
    }
