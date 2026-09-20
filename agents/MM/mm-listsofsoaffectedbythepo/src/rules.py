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



def _business_report(inputs):
    candidates = inputs.get("candidate_sales_orders")
    candidates = candidates if isinstance(candidates, list) else []
    order_ids = sorted(set(str(value).strip() for value in candidates if str(value).strip()))
    order_set = set(order_ids)
    evidence = inputs.get("evidence_summary") or {}
    complete = evidence.get("source_complete") is True and inputs.get("candidate_source_complete") is True

    item_keys = set()
    for row in _rows(inputs.get("sales_order_items")):
        order = str(row.get("SalesOrder") or "").strip()
        item = str(row.get("SalesOrderItem") or "").strip()
        if order in order_set and item:
            item_keys.add((order, item))

    schedule_keys = set()
    for row in _rows(inputs.get("schedule_lines")):
        order = str(row.get("SalesOrder") or "").strip()
        item = str(row.get("SalesOrderItem") or "").strip()
        schedule = str(row.get("ScheduleLine") or "").strip()
        if (order, item) in item_keys and schedule:
            schedule_keys.add((order, item, schedule))

    records = []
    for order in order_ids:
        item_count = sum(1 for key in item_keys if key[0] == order)
        schedule_count = sum(1 for key in schedule_keys if key[0] == order)
        records.append({
            "sales_order": order,
            "verified_item_count": item_count,
            "schedule_line_count": schedule_count,
            "assessment": (
                {"zh": "潜在暴露，待复核", "en": "Potential exposure; review needed"}
                if complete and item_count else
                {"zh": "证据不足，无法确认", "en": "Evidence incomplete; inconclusive"}
            ),
        })

    if not complete:
        headline = {"zh": "候选订单证据尚不完整", "en": "Candidate-order evidence is incomplete"}
        overview = {
            "zh": "查询源或候选提取未完整返回；以下清单仅供核对，不能据此排除其他订单或认定延期影响。",
            "en": "A source or candidate extraction is incomplete. Review the returned list, but do not rule out other orders or confirm a delay impact.",
        }
    elif order_ids:
        headline = {"zh": "发现待复核的潜在暴露销售订单", "en": "Sales orders requiring exposure review"}
        overview = {
            "zh": "在本次采购订单、物料和工厂查询范围内，发现 " + str(len(order_ids)) + " 个候选销售订单。MRP 与订单证据只支持潜在关联，未证明采购订单延期导致交期或数量受到影响。",
            "en": "Within this purchase-order, material, and plant scope, " + str(len(order_ids)) + " sales orders are candidates. MRP and order evidence supports potential association, not a proven delivery or quantity impact caused by the delay.",
        }
    else:
        headline = {"zh": "本次范围未发现候选销售订单", "en": "No candidate sales orders found in this scope"}
        overview = {
            "zh": "本次查询范围内没有返回符合条件的候选销售订单；这不等于证明其他范围不存在影响。",
            "en": "No matching candidate sales orders were returned within this query scope; this does not rule out impacts outside that scope.",
        }

    report = {
        "headline": headline,
        "overview": overview,
        "metrics": [
            {"id": "candidate_sales_order_count", "label": {"zh": "待复核候选销售订单", "en": "Candidate sales orders for review"}, "value": len(order_ids)},
            {"id": "verified_item_count", "label": {"zh": "已匹配销售订单项目", "en": "Matched sales-order items"}, "value": len(item_keys)},
            {"id": "schedule_line_count", "label": {"zh": "已匹配计划行", "en": "Matched schedule lines"}, "value": len(schedule_keys)},
        ],
        "record_columns": [
            {"key": "sales_order", "label": {"zh": "销售订单", "en": "Sales order"}},
            {"key": "verified_item_count", "label": {"zh": "匹配项目数", "en": "Matched items"}, "format": "integer"},
            {"key": "schedule_line_count", "label": {"zh": "匹配计划行数", "en": "Matched schedule lines"}, "format": "integer"},
            {"key": "assessment", "label": {"zh": "结论", "en": "Assessment"}},
        ],
        "records": records,
        "findings": [{
            "code": "POTENTIAL_EXPOSURE_ONLY",
            "detail": {
                "zh": "候选订单仅表示相同物料和工厂范围内的潜在暴露，尚无 ATP/pegging 证据证明由该采购订单延期造成实际短缺或交期变化。",
                "en": "Candidates indicate potential exposure within the material and plant scope. ATP/pegging evidence has not established an actual shortage or delivery change caused by this delayed purchase order.",
            },
        }],
        "next_actions": {
            "zh": ["请结合 ATP/pegging 及最新供需证据，逐订单核对交期和数量。"],
            "en": ["Review delivery dates and quantities per order against ATP/pegging and current supply-demand evidence."],
        },
    }
    return {
        "rule_id": "po_sales_impact_candidate_report_v1",
        "status": "complete" if complete else "inconclusive",
        "summary": headline,
        "business_report": report,
        "workflow_output": {"business_report": report},
    }

def evaluate(inputs):
    if inputs.get("mode") == "business_report":
        return _business_report(inputs)
    field = str(inputs.get("field") or "").strip()
    maximum = inputs.get("max_values", 100)
    maximum = maximum if isinstance(maximum, int) and not isinstance(maximum, bool) and 1 <= maximum <= 100 else 100
    values = sorted(set(
        str(row.get(field)).strip()
        for row in _rows(inputs.get("payload"))
        if field and row.get(field) not in (None, "")
    ))
    truncated = len(values) > maximum
    values = values[:maximum]
    flags = _flags(inputs.get("payload"))
    source_complete = bool(flags) and all(flags) and not truncated
    has_values = bool(values)
    return {
        "rule_id": "extract_sap_evidence_values_v1",
        "status": "complete" if source_complete else "inconclusive",
        "field": field,
        "values": values,
        "value_count": len(values),
        "has_values": has_values,
        "source_complete": source_complete,
        "truncated": truncated,
        "can_query": source_complete and has_values,
    }
