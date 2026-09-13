from decimal import Decimal
from datetime import datetime, timezone
import re


POSITIVE_MOVEMENTS = {"101", "103", "105", "107", "109", "121", "123", "162"}
NEGATIVE_MOVEMENTS = {"102", "104", "106", "108", "110", "122", "124", "161"}
RECEIPT_MOVEMENTS = POSITIVE_MOVEMENTS | NEGATIVE_MOVEMENTS
DECIMAL_TEXT = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
SAP_DATE = re.compile(r"^/Date\((-?[0-9]+)(?:[+-][0-9]{4})?\)/$")


def _text(row, *names):
    for name in names:
        value = row.get(name) if isinstance(row, dict) else None
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _decimal(value):
    text = str(value).strip() if value is not None else ""
    return Decimal(text) if DECIMAL_TEXT.fullmatch(text) else None


def _decimal_text(value):
    if value is None:
        return None
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _date_text(value):
    text = str(value).strip() if value is not None else ""
    if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return text[:10]
    match = SAP_DATE.fullmatch(text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, timezone.utc).date().isoformat()
    return ""


def _truthy(value):
    return value is True or str(value or "").strip().upper() in {"1", "TRUE", "X", "Y", "YES"}


def _step_results(evidence):
    if not isinstance(evidence, dict):
        return {}
    direct = evidence.get("step_results")
    if isinstance(direct, dict):
        return direct
    data = evidence.get("data")
    if isinstance(data, dict) and isinstance(data.get("step_results"), dict):
        return data["step_results"]
    payload = evidence.get("payload")
    if isinstance(payload, dict):
        return _step_results(payload)
    return {}


def _rows(steps, name):
    value = steps.get(name)
    if not isinstance(value, dict):
        return []
    return [row for row in value.get("results") or [] if isinstance(row, dict)]


def _source_complete(steps, required):
    return all(
        isinstance(steps.get(name), dict)
        and steps[name].get("ok") is not False
        and steps[name].get("source_complete") is True
        and steps[name].get("source_truncated") is not True
        and not steps[name].get("failed_filter_values")
        for name in required
    )


def _row_signature(row):
    return tuple(sorted((str(key), str(value)) for key, value in row.items() if key != "__metadata"))


def _dedupe(rows, key_names):
    unique = {}
    conflicts = set()
    missing = 0
    for row in rows:
        key = tuple(_text(row, name) for name in key_names)
        if not all(key):
            missing += 1
        elif key in unique and _row_signature(unique[key]) != _row_signature(row):
            conflicts.add(key)
        elif key not in unique:
            unique[key] = row
    return unique, conflicts, missing


def _movement_quantity(row, order_unit):
    entry_unit = _text(row, "EntryUnit").upper()
    base_unit = _text(row, "MaterialBaseUnit").upper()
    quantity = _decimal(row.get("QuantityInEntryUnit")) if entry_unit == order_unit else None
    if quantity is None and base_unit == order_unit:
        quantity = _decimal(row.get("QuantityInBaseUnit"))
    return quantity


def _movement_sign(row):
    movement = _text(row, "GoodsMovementType")
    debit_credit = _text(row, "DebitCreditCode").upper()
    if movement in POSITIVE_MOVEMENTS:
        return Decimal(1)
    if movement in NEGATIVE_MOVEMENTS:
        return Decimal(-1)
    if debit_credit in {"S", "D"}:
        return Decimal(1)
    if debit_credit in {"H", "C"}:
        return Decimal(-1)
    return None


def _localized_gap(code):
    labels = {
        "source_incomplete": ("SAP查询源未完整返回。", "The SAP query source did not return completely."),
        "business_key_incomplete": ("部分记录缺少稳定业务键。", "Some records are missing stable business keys."),
        "business_key_conflict": ("相同业务键返回了冲突内容。", "Conflicting content was returned for the same business key."),
        "purchase_order_item_missing": ("计划行缺少对应采购订单项目。", "A schedule line has no matching purchase-order item."),
        "purchase_order_header_missing": ("采购订单项目缺少对应抬头。", "A purchase-order item has no matching header."),
        "schedule_line_evidence_missing": ("采购订单项目缺少完整计划行证据。", "A purchase-order item has no complete schedule-line evidence."),
        "quantity_invalid": ("计划数量或收货数量无效。", "A schedule or receipt quantity is invalid."),
        "receipt_unit_conflict": ("采购订单单位与收货单位不可直接比较。", "The purchase-order and receipt units are not directly comparable."),
        "receipt_direction_unknown": ("无法确认物料移动的收货或冲销方向。", "The receipt or reversal direction of a material movement is unknown."),
        "material_document_header_missing": ("物料凭证项目缺少对应抬头。", "A material-document item has no matching header."),
        "negative_net_receipt": ("冲销后的净收货数量为负数。", "Net receipt quantity is negative after reversals."),
    }
    value = labels.get(code, (code, code))
    return {"zh": value[0], "en": value[1]}


def evaluate(inputs):
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    evidence = inputs.get("evidence") if isinstance(inputs.get("evidence"), dict) else {}
    steps = _step_results(evidence)
    required_steps = (
        "target_schedule_lines",
        "purchase_order_items",
        "purchase_order_headers",
        "all_schedule_lines",
        "material_document_items",
        "material_document_headers",
    )
    source_complete = _source_complete(steps, required_steps)
    gaps = set()
    if not source_complete:
        gaps.add("source_incomplete")

    schedules, schedule_conflicts, missing_schedule_keys = _dedupe(
        _rows(steps, "target_schedule_lines"),
        ("PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"),
    )
    all_schedules, all_schedule_conflicts, missing_all_schedule_keys = _dedupe(
        _rows(steps, "all_schedule_lines"),
        ("PurchasingDocument", "PurchasingDocumentItem", "ScheduleLine"),
    )
    items, item_conflicts, missing_item_keys = _dedupe(
        _rows(steps, "purchase_order_items"), ("PurchaseOrder", "PurchaseOrderItem")
    )
    headers, header_conflicts, missing_header_keys = _dedupe(
        _rows(steps, "purchase_order_headers"), ("PurchaseOrder",)
    )
    movements, movement_conflicts, missing_movement_keys = _dedupe(
        _rows(steps, "material_document_items"),
        ("MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem"),
    )
    material_headers, material_header_conflicts, missing_material_header_keys = _dedupe(
        _rows(steps, "material_document_headers"), ("MaterialDocumentYear", "MaterialDocument")
    )
    if any((missing_schedule_keys, missing_all_schedule_keys, missing_item_keys, missing_header_keys, missing_movement_keys, missing_material_header_keys)):
        gaps.add("business_key_incomplete")
    conflict_sets = (schedule_conflicts, all_schedule_conflicts, item_conflicts, header_conflicts, movement_conflicts, material_header_conflicts)
    if any(conflict_sets):
        gaps.add("business_key_conflict")

    requested_date = _date_text(run_input.get("schedule_line_delivery_date"))
    requested_org = _text(run_input, "purchasing_organization")
    target_rows = [row for row in schedules.values() if _date_text(row.get("ScheduleLineDeliveryDate")) == requested_date]
    selected_targets = []
    for row in target_rows:
        purchase_order = _text(row, "PurchasingDocument")
        header = headers.get((purchase_order,))
        if header is not None and (not requested_org or _text(header, "PurchasingOrganization") == requested_org):
            selected_targets.append(row)
        elif not requested_org:
            gaps.add("purchase_order_header_missing")

    schedules_by_item = {}
    for row in all_schedules.values():
        key = (_text(row, "PurchasingDocument"), _text(row, "PurchasingDocumentItem"))
        schedules_by_item.setdefault(key, []).append(row)
    movements_by_item = {}
    for row in movements.values():
        movement_type = _text(row, "GoodsMovementType")
        if movement_type in RECEIPT_MOVEMENTS or _text(row, "DebitCreditCode"):
            key = (_text(row, "PurchaseOrder"), _text(row, "PurchaseOrderItem"))
            if all(key):
                movements_by_item.setdefault(key, []).append(row)

    target_keys = {
        (_text(row, "PurchasingDocument"), _text(row, "PurchasingDocumentItem"), _text(row, "ScheduleLine"))
        for row in selected_targets
    }
    records = []
    processed_items = set()
    item_gaps = {}
    for target in sorted(selected_targets, key=lambda row: (_text(row, "PurchasingDocument"), _text(row, "PurchasingDocumentItem"), _text(row, "ScheduleLine"))):
        item_key = (_text(target, "PurchasingDocument"), _text(target, "PurchasingDocumentItem"))
        if item_key in processed_items:
            continue
        processed_items.add(item_key)
        current_gaps = set()
        item = items.get(item_key)
        header = headers.get((item_key[0],))
        item_schedules = schedules_by_item.get(item_key) or []
        if item is None:
            current_gaps.add("purchase_order_item_missing")
        if header is None:
            current_gaps.add("purchase_order_header_missing")
        if not item_schedules:
            current_gaps.add("schedule_line_evidence_missing")
        order_unit = _text(item or {}, "PurchaseOrderQuantityUnit").upper()
        receipt_total = Decimal(0)
        latest_posting_date = ""
        for movement in movements_by_item.get(item_key) or []:
            quantity = _movement_quantity(movement, order_unit)
            sign = _movement_sign(movement)
            if quantity is None or quantity < 0 or not order_unit:
                current_gaps.add("receipt_unit_conflict" if order_unit else "quantity_invalid")
                continue
            if sign is None:
                current_gaps.add("receipt_direction_unknown")
                continue
            receipt_total += quantity * sign
            document_key = (_text(movement, "MaterialDocumentYear"), _text(movement, "MaterialDocument"))
            document_header = material_headers.get(document_key)
            if document_header is None:
                current_gaps.add("material_document_header_missing")
            else:
                posting_date = _date_text(document_header.get("PostingDate"))
                if posting_date > latest_posting_date:
                    latest_posting_date = posting_date
        if receipt_total < 0:
            current_gaps.add("negative_net_receipt")
        remaining_receipt = max(receipt_total, Decimal(0))
        allocations = {}
        for schedule in sorted(item_schedules, key=lambda row: (_date_text(row.get("ScheduleLineDeliveryDate")) or "9999-12-31", _text(row, "ScheduleLine"))):
            schedule_key = (_text(schedule, "PurchasingDocument"), _text(schedule, "PurchasingDocumentItem"), _text(schedule, "ScheduleLine"))
            scheduled = _decimal(schedule.get("ScheduleLineOrderQuantity"))
            schedule_unit = _text(schedule, "PurchaseOrderQuantityUnit").upper() or order_unit
            if scheduled is None or scheduled < 0 or not schedule_unit or schedule_unit != order_unit:
                current_gaps.add("quantity_invalid" if scheduled is None or scheduled < 0 else "receipt_unit_conflict")
                allocations[schedule_key] = None
            else:
                allocated = min(remaining_receipt, scheduled)
                allocations[schedule_key] = (scheduled, allocated)
                remaining_receipt -= allocated
        item_gaps[item_key] = current_gaps
        gaps.update(current_gaps)
        for schedule in item_schedules:
            schedule_key = (_text(schedule, "PurchasingDocument"), _text(schedule, "PurchasingDocumentItem"), _text(schedule, "ScheduleLine"))
            if schedule_key not in target_keys:
                continue
            allocation = allocations.get(schedule_key)
            if current_gaps or allocation is None:
                scheduled = _decimal(schedule.get("ScheduleLineOrderQuantity"))
                received = None
                open_quantity = None
                status = "inconclusive"
            else:
                scheduled, received = allocation
                open_quantity = max(scheduled - received, Decimal(0))
                status = "fully_received" if open_quantity == 0 else "partially_received" if received > 0 else "not_received"
            records.append({
                "purchase_order": item_key[0],
                "purchase_order_item": item_key[1],
                "schedule_line": schedule_key[2],
                "schedule_line_delivery_date": _date_text(schedule.get("ScheduleLineDeliveryDate")),
                "supplier": _text(header or {}, "Supplier") or None,
                "purchasing_organization": _text(header or {}, "PurchasingOrganization") or None,
                "purchasing_group": _text(header or {}, "PurchasingGroup") or None,
                "plant": _text(item or {}, "Plant") or None,
                "material": _text(item or {}, "Material") or None,
                "scheduled_quantity": _decimal_text(scheduled),
                "received_quantity": _decimal_text(received),
                "open_quantity": _decimal_text(open_quantity),
                "unit": order_unit or None,
                "latest_goods_receipt_posting_date": latest_posting_date or None,
                "delivery_complete": _truthy((item or {}).get("IsCompletelyDelivered")),
                "receipt_status": status,
            })

    records.sort(key=lambda row: (row["purchase_order"], row["purchase_order_item"], row["schedule_line"]))
    counts = {
        "fully_received": sum(row["receipt_status"] == "fully_received" for row in records),
        "partially_received": sum(row["receipt_status"] == "partially_received" for row in records),
        "not_received": sum(row["receipt_status"] == "not_received" for row in records),
        "inconclusive": sum(row["receipt_status"] == "inconclusive" for row in records),
    }
    evidence_complete = bool(source_complete and not gaps)
    business_status = "inconclusive" if not evidence_complete else "attention" if counts["partially_received"] or counts["not_received"] else "normal"
    columns = [
        ("purchase_order", "采购订单", "Purchase order", "text"),
        ("purchase_order_item", "项目", "Item", "text"),
        ("schedule_line", "计划行", "Schedule line", "text"),
        ("schedule_line_delivery_date", "计划行交货日期", "Schedule-line delivery date", "date"),
        ("supplier", "供应商", "Supplier", "text"),
        ("purchasing_organization", "采购组织", "Purchasing organization", "text"),
        ("purchasing_group", "采购组", "Purchasing group", "text"),
        ("plant", "工厂", "Plant", "text"),
        ("material", "物料", "Material", "text"),
        ("scheduled_quantity", "计划数量", "Scheduled quantity", "decimal"),
        ("received_quantity", "已收货数量", "Received quantity", "decimal"),
        ("open_quantity", "未收货数量", "Open quantity", "decimal"),
        ("unit", "单位", "Unit", "text"),
        ("latest_goods_receipt_posting_date", "项目最新收货过账日", "Latest item receipt posting date", "date"),
        ("delivery_complete", "SAP交货完成标识", "SAP delivery-complete flag", "status"),
        ("receipt_status", "入库状态", "Receipt status", "status"),
    ]
    headline = {
        "zh": f"已检查{len(records)}条目标日期采购订单计划行：{counts['fully_received']}条全部入库，{counts['partially_received']}条部分入库，{counts['not_received']}条未入库",
        "en": f"Checked {len(records)} purchase-order schedule lines: {counts['fully_received']} fully received, {counts['partially_received']} partially received, and {counts['not_received']} not received",
    }
    status_labels = {
        "fully_received": {"zh": "全部入库", "en": "Fully received"},
        "partially_received": {"zh": "部分入库", "en": "Partially received"},
        "not_received": {"zh": "未入库", "en": "Not received"},
        "inconclusive": {"zh": "无法确认", "en": "Inconclusive"},
    }
    report_records = []
    for record in records:
        report_record = dict(record)
        report_record["receipt_status"] = status_labels[record["receipt_status"]]
        report_records.append(report_record)
    report = {
        "headline": headline,
        "overview": {
            "zh": "结果按采购订单、项目和计划行展示。实际物料凭证净收货量按全部计划行的日期和计划行号依次分配；SAP交货完成标识单独展示，不替代收货证据。",
            "en": "Results are shown by purchase order, item, and schedule line. Net material-document receipts are allocated across all schedule lines by date and line number; the SAP delivery-complete flag is shown separately and does not replace receipt evidence.",
        },
        "metrics": [
            {"id": "schedule_line_count", "label": {"zh": "目标计划行", "en": "Target schedule lines"}, "value": len(records)},
            {"id": "fully_received_count", "label": {"zh": "全部入库", "en": "Fully received"}, "value": counts["fully_received"]},
            {"id": "partially_received_count", "label": {"zh": "部分入库", "en": "Partially received"}, "value": counts["partially_received"]},
            {"id": "not_received_count", "label": {"zh": "未入库", "en": "Not received"}, "value": counts["not_received"]},
            {"id": "inconclusive_count", "label": {"zh": "无法确认", "en": "Inconclusive"}, "value": counts["inconclusive"]},
        ],
        "record_columns": [{"key": key, "label": {"zh": zh, "en": en}, "format": value_format} for key, zh, en, value_format in columns],
        "records": report_records,
        "missing_evidence": [_localized_gap(code) for code in sorted(gaps)],
        "findings": [
            {
                "code": "DELIVERY_COMPLETE_IS_NOT_RECEIPT_EVIDENCE",
                "text": {
                    "zh": "SAP交货完成标识与实际入库数量分别判断。",
                    "en": "The SAP delivery-complete flag and actual receipt quantity are assessed separately.",
                },
            }
        ],
        "next_actions": {
            "zh": ["优先跟进未入库或部分入库的计划行；无法确认的记录需先补齐凭证或单位证据。"],
            "en": ["Follow up not-received or partially received schedule lines first; complete document or unit evidence before acting on inconclusive rows."],
        },
    }
    output = {
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "schedule_line_count": len(records),
        "fully_received_count": counts["fully_received"],
        "partially_received_count": counts["partially_received"],
        "not_received_count": counts["not_received"],
        "inconclusive_count": counts["inconclusive"],
        "records": records,
        "business_report": report,
    }
    return {
        "rule_id": "mm_po_gr_status_v1",
        "status": "complete" if evidence_complete else "inconclusive",
        "business_status": business_status,
        "source_complete": source_complete,
        "evidence_complete": evidence_complete,
        "business_complete": evidence_complete,
        "evidence_gaps": sorted(gaps),
        "business_report": report,
        "workflow_output": output,
    }
