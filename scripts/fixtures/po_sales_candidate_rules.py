"""Deterministic, evidence-only example for PO/SO candidate screening."""


def _rows(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "results" and isinstance(child, list):
                found.extend(item for item in child if isinstance(item, dict))
            found.extend(_rows(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_rows(child))
    return found


def _flags(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_complete" and isinstance(child, bool):
                found.append(child)
            found.extend(_flags(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_flags(child))
    return found


def _complete(value):
    flags = _flags(value)
    return bool(flags) and all(flags)


def _text(row, field):
    return str(row.get(field) or "").strip()


def _business_report(inputs):
    pairs = set()
    gaps = set()
    po_rows = _rows(inputs.get("purchase_order_items"))
    for row in po_rows:
        material = _text(row, "Material")
        plant = _text(row, "Plant")
        if material and plant:
            pairs.add((material, plant))
        else:
            gaps.add("purchase_order_scope_incomplete")
    if not _complete(inputs.get("purchase_order_items")):
        gaps.add("purchase_order_source_incomplete")
    triples = set()
    if pairs:
        if not _complete(inputs.get("mrp_candidates")):
            gaps.add("mrp_source_incomplete")
        for row in _rows(inputs.get("mrp_candidates")):
            pair = (_text(row, "Material"), _text(row, "MRPPlant"))
            order = _text(row, "MRPElement")
            if pair in pairs and _text(row, "MRPElementCategory") == "VC":
                if order:
                    triples.add((order, pair[0], pair[1]))
                else:
                    gaps.add("mrp_order_reference_missing")
    item_keys = set()
    item_scopes = {}
    if triples:
        if not _complete(inputs.get("sales_order_items")) or inputs.get("candidate_source_complete") is not True:
            gaps.add("sales_order_source_incomplete")
        for row in _rows(inputs.get("sales_order_items")):
            order = _text(row, "SalesOrder")
            item = _text(row, "SalesOrderItem")
            triple = (order, _text(row, "Material"), _text(row, "ProductionPlant"))
            if order in set(value[0] for value in triples) and (not item or not triple[1] or not triple[2]):
                gaps.add("sales_order_association_unverified")
            if triple in triples and item:
                key = (order, item)
                if key in item_scopes and item_scopes[key] != triple:
                    gaps.add("conflicting_sales_order_item")
                item_scopes[key] = triple
                item_keys.add(key)
    schedule_keys = set()
    if item_keys:
        if not _complete(inputs.get("schedule_lines")):
            gaps.add("schedule_line_source_incomplete")
        for row in _rows(inputs.get("schedule_lines")):
            key = (_text(row, "SalesOrder"), _text(row, "SalesOrderItem"))
            schedule = _text(row, "ScheduleLine")
            if key in item_keys:
                if schedule:
                    schedule_keys.add((key[0], key[1], schedule))
                else:
                    gaps.add("schedule_line_key_missing")
    complete = not gaps
    source_complete = not any(code.endswith("source_incomplete") for code in gaps)
    orders = sorted(set(key[0] for key in item_keys))
    records = []
    for order in orders:
        records.append({
            "sales_order": order,
            "verified_item_count": sum(1 for key in item_keys if key[0] == order),
            "schedule_line_count": sum(1 for key in schedule_keys if key[0] == order),
            "assessment": "candidate_for_review" if complete else "inconclusive",
        })
    business_status = "inconclusive" if not complete else "attention" if records else "normal"
    headline = {"zh": "采购订单相关销售订单候选筛查", "en": "PO-related sales-order candidate screening"}
    metrics = [
        {"id": "candidate_sales_order_count", "label": {"zh": "候选销售订单数", "en": "Candidate sales orders"}, "value": len(orders)},
        {"id": "verified_item_count", "label": {"zh": "已核实项目数", "en": "Verified items"}, "value": len(item_keys)},
        {"id": "schedule_line_count", "label": {"zh": "已核实计划行数", "en": "Verified schedule lines"}, "value": len(schedule_keys)},
    ]
    report = {
        "headline": headline,
        "overview": {
            "zh": "按采购订单的实际物料与工厂配对、销售订单类MRP需求及订单项目筛查候选。数量只统计已返回并核实的记录；证据不完整时不能视为全部候选。未计算采购延期导致的实际短缺或交期变化。",
            "en": "Screen candidates using actual PO material/plant pairs, sales-order MRP demand and verified order items. Counts describe returned verified records and are not exhaustive when evidence is incomplete. Actual shortage or delivery changes caused by a PO delay are not calculated.",
        },
        "records": records,
        "record_columns": [
            {"key": "sales_order", "label": {"zh": "销售订单", "en": "Sales order"}},
            {"key": "verified_item_count", "label": {"zh": "已核实项目数", "en": "Verified items"}, "format": "integer"},
            {"key": "schedule_line_count", "label": {"zh": "已核实计划行数", "en": "Verified schedule lines"}, "format": "integer"},
            {"key": "assessment", "label": {"zh": "业务评估代码", "en": "Business assessment"}},
        ],
        "metrics": metrics,
        "missing_evidence": sorted(gaps),
        "findings": [{"code": "POTENTIAL_EXPOSURE_ONLY", "detail": {
            "zh": "仅列出潜在关联候选，不证明指定采购订单延期导致短缺或交期推迟；实际影响需要独立ATP/pegging及供需分配分析。",
            "en": "Candidates show potential association only, not shortages or delivery delays caused by this PO. Actual impact requires independent ATP/pegging and supply-demand allocation analysis.",
        }}],
        "next_actions": {"zh": ["结合最新供需和ATP/pegging证据复核候选订单。"], "en": ["Review candidates using current supply-demand and ATP/pegging evidence."]},
    }
    output = {
        "records": records, "business_report": report,
        "candidate_sales_orders": orders,
        "candidate_sales_order_count": len(orders),
        "verified_item_count": len(item_keys), "schedule_line_count": len(schedule_keys),
        "business_status": business_status, "source_complete": source_complete,
        "evidence_complete": complete, "business_complete": complete,
        "evidence_gap_codes": sorted(gaps),
    }
    return {"rule_id": "po_sales_candidate_contract_v1", "status": "complete" if complete else "inconclusive",
            "business_status": business_status, "summary": headline, "source_complete": source_complete,
            "evidence_complete": complete, "business_complete": complete, "missing_evidence": sorted(gaps),
            "business_report": report, "workflow_output": output}


def evaluate(inputs):
    if inputs.get("mode") == "business_report":
        return _business_report(inputs)
    field = str(inputs.get("field") or "").strip()
    maximum = inputs.get("max_values", 100)
    maximum = maximum if isinstance(maximum, int) and not isinstance(maximum, bool) and 1 <= maximum <= 100 else 100
    values = sorted(set(str(row.get(field)).strip() for row in _rows(inputs.get("payload")) if field and row.get(field) not in (None, "")))
    truncated = len(values) > maximum
    values = values[:maximum]
    complete = _complete(inputs.get("payload")) and not truncated
    return {"rule_id": "extract_sap_evidence_values_v1", "status": "complete" if complete else "inconclusive",
            "field": field, "values": values, "value_count": len(values), "has_values": bool(values),
            "source_complete": complete, "truncated": truncated, "can_query": complete and bool(values)}
