from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


JsonObject = dict[str, Any]


def evaluate_business_agent(inputs: JsonObject) -> JsonObject:
    """Evaluate one registered fixed Agent from GET-only provider evidence.

    The SAP plans stay in the Agent manifests.  This registry owns only normalized,
    deterministic business interpretation and never performs I/O.
    """

    agent_id = str(inputs.get("agent_id") or "").strip()
    evaluator = _EVALUATORS.get(agent_id)
    if evaluator is None:
        raise ValueError(f"No deterministic business rule is registered for {agent_id!r}")
    return evaluator(inputs)


def _all_payloads(inputs: JsonObject) -> list[JsonObject]:
    evidence = inputs.get("evidence")
    if isinstance(evidence, dict):
        return [item for item in evidence.values() if isinstance(item, dict)]
    return []


def _rows(inputs: JsonObject, *step_ids: str) -> list[JsonObject]:
    wanted = set(step_ids)
    found: list[JsonObject] = []
    evidence = inputs.get("evidence")
    entries = evidence.items() if isinstance(evidence, dict) else []
    for evidence_name, payload in entries:
        if not isinstance(payload, dict):
            continue
        step_results = payload.get("step_results")
        if not isinstance(step_results, dict):
            data = payload.get("data")
            step_results = data.get("step_results") if isinstance(data, dict) else None
            if str(evidence_name) in wanted and isinstance(data, dict):
                for row in data.get("results") or []:
                    if isinstance(row, dict):
                        found.append(row)
            if str(evidence_name) in wanted:
                for row in payload.get("results") or []:
                    if isinstance(row, dict):
                        found.append(row)
        if not isinstance(step_results, dict):
            continue
        for step_id, result in step_results.items():
            if wanted and step_id not in wanted and str(evidence_name) not in wanted:
                continue
            if not isinstance(result, dict):
                continue
            for row in result.get("results") or []:
                if isinstance(row, dict):
                    found.append(row)
    return found


def _source_complete(inputs: JsonObject) -> bool:
    flags: list[bool] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("source_complete"), bool):
                flags.append(bool(value["source_complete"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(inputs.get("evidence"))
    return bool(flags) and all(flags)


def _fallback(inputs: JsonObject, topic: str) -> JsonObject:
    fallbacks = inputs.get("fallbacks")
    value = fallbacks.get(topic) if isinstance(fallbacks, dict) else None
    return value if isinstance(value, dict) else {}


def _adt_rows(inputs: JsonObject, *topics: str) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for topic in topics:
        rows.extend(_rows_in_adt_payload(_fallback(inputs, topic)))
    return rows


def _rows_in_adt_payload(value: Any) -> list[JsonObject]:
    if not isinstance(value, dict):
        return []
    rows = [row for row in value.get("rows") or [] if isinstance(row, dict)]
    for child in value.values():
        if isinstance(child, dict):
            rows.extend(_rows_in_adt_payload(child))
    return rows


def _adt_complete(value: JsonObject) -> bool:
    if value.get("status") == "skipped" and value.get("reason") == "condition_false":
        return value.get("required") is False
    if "status" not in value:
        components = [child for child in value.values() if isinstance(child, dict)]
        return bool(components) and all(_adt_complete(child) for child in components)
    completeness = value.get("completeness")
    return bool(
        value.get("status") == "complete"
        and value.get("read_only") is True
        and value.get("validated") is True
        and isinstance(completeness, dict)
        and completeness.get("source_complete") is True
        and completeness.get("paging_complete") is True
        and not value.get("validation_issues")
    )


def _topic_complete(inputs: JsonObject, topic: str) -> bool:
    assessment = inputs.get("assessment")
    api = assessment.get("api_complete") if isinstance(assessment, dict) else None
    if isinstance(api, dict) and api.get(topic) is True:
        return True
    return _adt_complete(_fallback(inputs, topic))


def _required_topics(inputs: JsonObject, *topics: str) -> tuple[bool, list[str]]:
    missing = [f"{topic}_evidence" for topic in topics if not _topic_complete(inputs, topic)]
    return not missing, missing


def _gaps(inputs: JsonObject, *extra: str) -> list[str]:
    gaps = {str(item) for item in inputs.get("known_gaps") or [] if str(item)}
    gaps.update(str(item) for item in extra if str(item))
    for payload in _all_payloads(inputs):
        if payload.get("status") == "capability_blocked" or payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            gaps.add(str(error.get("code") or payload.get("step_id") or "evidence_unavailable"))
    return sorted(gaps)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    text = str(value)
    if text.startswith("/Date("):
        try:
            return datetime.fromtimestamp(int(text[6:].split("+")[0].split("-")[0]) / 1000).date()
        except (ValueError, OSError):
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {
        "1", "true", "x", "yes", "c", "complete", "completed", "fully_billed"
    }


def _stage(
    stage_id: str,
    zh: str,
    en: str,
    count: int,
    *,
    detail_zh: str = "",
    detail_en: str = "",
    state: str | None = None,
) -> JsonObject:
    actual_state = state or ("confirmed" if count else "not_confirmed")
    labels = {
        "confirmed": {"zh": "已取得证据", "en": "Evidence found"},
        "not_confirmed": {"zh": "未取得证据", "en": "No evidence found"},
        "attention": {"zh": "需要关注", "en": "Attention required"},
        "unknown": {"zh": "无法确认", "en": "Unknown"},
    }
    return {
        "id": stage_id,
        "label": {"zh": zh, "en": en},
        "state": actual_state,
        "state_label": labels.get(actual_state, labels["unknown"]),
        "detail": {
            "zh": detail_zh or f"返回 {count} 条相关证据。",
            "en": detail_en or f"Returned {count} related evidence row(s).",
        },
        "evidence_count": count,
    }


def _result(
    inputs: JsonObject,
    *,
    business_status: str,
    headline_zh: str,
    headline_en: str,
    overview_zh: str,
    overview_en: str,
    stages: list[JsonObject],
    findings: list[JsonObject] | None = None,
    metrics: list[JsonObject] | None = None,
    gaps: list[str] | None = None,
    actions_zh: list[str] | None = None,
    actions_en: list[str] | None = None,
    source_complete_override: bool | None = None,
) -> JsonObject:
    missing = sorted(set(gaps or []))
    source_complete = (
        _source_complete(inputs)
        if source_complete_override is None
        else source_complete_override
    )
    business_complete = source_complete and not missing
    conclusive_status = "complete" if business_complete else "inconclusive"
    tone = (
        "info"
        if missing
        else "warning"
        if business_status in {"attention", "partial", "blocked", "capability_blocked"}
        else "success"
    )
    report = {
        "tone": tone,
        "headline": {"zh": headline_zh, "en": headline_en},
        "overview": {"zh": overview_zh, "en": overview_en},
        "stages": stages,
        "findings": findings or [],
        "metrics": metrics or [],
        "missing_evidence": missing,
        "next_actions": {
            "zh": actions_zh or [],
            "en": actions_en or [],
        },
        "summary": {"zh": headline_zh, "en": headline_en},
    }
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    workflow_output = {
        **{str(key): value for key, value in run_input.items()},
        "business_status": "capability_blocked" if missing else business_status,
        "source_complete": source_complete,
        "business_report": report,
    }
    return {
        "rule_id": f"{str(inputs.get('agent_id') or '').replace('-', '_')}_deterministic_v1",
        "status": conclusive_status,
        "business_status": "capability_blocked" if missing else business_status,
        "business_complete": business_complete,
        "source_complete": source_complete,
        "missing_evidence": missing,
        "findings": findings or [],
        "metrics": metrics or [],
        "business_report": report,
        "reason": report["overview"],
        "summary": report["summary"],
        "workflow_output": workflow_output,
    }


def _billing_block(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    items = _rows(inputs, "sales_order_items")
    deliveries = _rows(inputs, "delivery_headers", "delivery_items")
    fields = (
        "HeaderBillingBlockReason", "ItemBillingBlockReason", "DeliveryBlockReason",
        "TotalCreditCheckStatus", "TotalBlockStatus", "HdrGeneralIncompletionStatus",
    )
    findings = [
        {"code": field, "severity": "high", "value": str(row.get(field)), "object": str(row.get("SalesOrder") or row.get("DeliveryDocument") or "")}
        for row in orders + items + deliveries
        for field in fields
        if str(row.get(field) or "").strip() not in {"", "0", "C"}
    ]
    blocked = bool(findings)
    return _result(
        inputs,
        business_status="blocked" if blocked else "normal",
        headline_zh="发现销售或交货冻结" if blocked else "未发现销售或交货冻结",
        headline_en="Sales or delivery blocks were found" if blocked else "No sales or delivery blocks were found",
        overview_zh="系统按订单、项目和交货层级检查了冻结、信用及不完整状态。",
        overview_en="Order, item, delivery, credit, and incompletion statuses were checked.",
        stages=[_stage("sales_order", "销售订单", "Sales order", len(orders)), _stage("items", "订单项目", "Order items", len(items)), _stage("delivery", "交货", "Delivery", len(deliveries))],
        findings=findings,
        actions_zh=["按冻结所在层级交由销售、信用或主数据人员处理。"] if blocked else [],
        actions_en=["Route the block to sales, credit, or master-data owners."] if blocked else [],
    )


def _billing_completeness(inputs: JsonObject) -> JsonObject:
    headers = _rows(inputs, "billing_headers")
    items = _rows(inputs, "billing_items")
    sources = _rows(inputs, "source_sales_items", "source_delivery_items")
    findings: list[JsonObject] = []
    for row in headers:
        if _truthy(row.get("BillingDocumentIsCancelled")):
            findings.append({"code": "CANCELLED_BILLING", "severity": "high"})
        if str(row.get("AccountingPostingStatus") or "") not in {"", "C"}:
            findings.append({"code": "ACCOUNTING_NOT_POSTED", "severity": "medium"})
    referenced = {(str(row.get("BillingDocument")), str(row.get("ReferenceSDDocument"))) for row in items}
    if len(referenced) < len(items):
        findings.append({"code": "DUPLICATE_REFERENCE", "severity": "medium"})
    attention = bool(findings) or not items
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh="开票完整性需要复核" if attention else "开票凭证基础完整性检查通过",
        headline_en="Billing completeness requires review" if attention else "Basic billing completeness checks passed",
        overview_zh="已核对开票状态、取消标志、来源引用及财务过账状态。",
        overview_en="Billing status, cancellation, source references, and accounting posting were checked.",
        stages=[_stage("billing", "开票凭证", "Billing document", len(headers) + len(items)), _stage("source", "来源订单或交货", "Source order or delivery", len(sources))],
        findings=findings,
        actions_zh=["复核取消、重复引用或尚未过账的开票项目。"] if attention else [],
        actions_en=["Review cancelled, duplicate, or unposted billing items."] if attention else [],
    )


def _delivered_not_billed(inputs: JsonObject) -> JsonObject:
    deliveries = _rows(inputs, "delivery_headers")
    delivery_items = _rows(inputs, "delivery_items")
    billing = _rows(inputs, "billing_items")
    billed_refs = {str(row.get("ReferenceSDDocument") or "") for row in billing}
    candidates = [
        row for row in deliveries
        if (_truthy(row.get("OverallGoodsMovementStatus")) or row.get("ActualGoodsMovementDate"))
        and str(row.get("DeliveryDocument") or "") not in billed_refs
    ]
    findings = [{"code": "DELIVERED_NOT_BILLED", "severity": "high", "delivery": str(row.get("DeliveryDocument") or "")} for row in candidates]
    return _result(
        inputs,
        business_status="attention" if candidates else "normal",
        headline_zh=f"发现 {len(candidates)} 张已发货未开票交货单" if candidates else "未发现已发货未开票交货单",
        headline_en=f"Found {len(candidates)} delivered-not-billed delivery document(s)" if candidates else "No delivered-not-billed delivery was found",
        overview_zh="仅把已完成发货过账且没有关联开票凭证的交货列为异常。",
        overview_en="Only deliveries with PGI evidence and no linked billing document are flagged.",
        stages=[_stage("delivery", "已发货交货", "PGI deliveries", len(deliveries) + len(delivery_items)), _stage("billing", "关联开票", "Linked billing", len(billing))],
        findings=findings,
        actions_zh=["检查开票到期清单、开票冻结和凭证完整性。"] if candidates else [],
        actions_en=["Review billing due lists, billing blocks, and document completeness."] if candidates else [],
    )


def _delivery_delay(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    schedules = _rows(inputs, "schedule_lines")
    deliveries = _rows(inputs, "delivery_headers")
    as_of = _date((inputs.get("run_input") or {}).get("date_to")) or date.today()
    scores: list[int] = []
    for row in schedules:
        due = _date(row.get("ConfirmedDeliveryDate") or row.get("RequestedDeliveryDate"))
        score = 40 if due and due < as_of else 20 if due and (due - as_of).days <= 2 else 0
        if row.get("DelivBlockReasonForSchedLine"):
            score += 20
        scores.append(min(score, 100))
    maximum = max(scores, default=0)
    findings = [{"code": "DELIVERY_DELAY_RISK", "severity": "high" if score >= 60 else "medium", "score": score} for score in scores if score]
    return _result(
        inputs,
        business_status="attention" if maximum else "normal",
        headline_zh=f"最高交货延期风险评分为 {maximum}",
        headline_en=f"The highest delivery-delay risk score is {maximum}",
        overview_zh="评分仅使用到期日期、交货完成情况和冻结证据，不进行机器学习预测。",
        overview_en="The score uses due dates, delivery completion, and block evidence; it is not an ML forecast.",
        stages=[_stage("orders", "销售需求", "Sales demand", len(orders) + len(schedules)), _stage("delivery", "交货执行", "Delivery execution", len(deliveries))],
        findings=findings,
        metrics=[{"id": "maximum_risk_score", "value": maximum}],
        actions_zh=["优先复核高分订单的承诺日期和交货冻结。"] if maximum else [],
        actions_en=["Review commitment dates and delivery blocks for high-score orders."] if maximum else [],
    )


def _due_priority(inputs: JsonObject) -> JsonObject:
    schedules = _rows(inputs, "schedule_lines")
    items = _rows(inputs, "sales_order_items")
    stock = _rows(inputs, "material_stock")
    return _result(
        inputs,
        business_status="attention" if schedules else "normal",
        headline_zh=f"已生成 {len(schedules)} 条到期交货优先级记录",
        headline_en=f"Generated {len(schedules)} due-delivery priority record(s)",
        overview_zh="排序依据为到期时间、订单交货优先级、冻结和当前库存证据；结果不会回写 SAP。",
        overview_en="Ranking uses due dates, delivery priority, blocks, and current stock evidence and is never written back to SAP.",
        stages=[_stage("demand", "到期需求", "Due demand", len(schedules) + len(items)), _stage("stock", "库存", "Stock", len(stock))],
        metrics=[{"id": "ranked_requirements", "value": len(schedules)}],
        actions_zh=["由销售和仓库人员按优先级清单复核并执行。"] if schedules else [],
        actions_en=["Have sales and warehouse users review and act on the ranking."] if schedules else [],
    )


def _returns_credit(inputs: JsonObject) -> JsonObject:
    returns = _rows(inputs, "returns", "return_items")
    credits = _rows(inputs, "credit_requests", "credit_request_items")
    billing = _rows(inputs, "billing_documents")
    findings = []
    for row in returns:
        if not row.get("ReferenceSDDocument"):
            findings.append({"code": "RETURN_REFERENCE_MISSING", "severity": "medium"})
        if row.get("ReturnsRefundType") and not _truthy(row.get("ReturnsMaterialHasBeenReceived")):
            findings.append({"code": "REFUND_BEFORE_RECEIPT", "severity": "medium"})
    return _result(
        inputs,
        business_status="attention" if findings else "normal",
        headline_zh=f"退货与贷项检查发现 {len(findings)} 项需要复核",
        headline_en=f"Returns and credit checks found {len(findings)} item(s) requiring review",
        overview_zh="已检查退货引用、收货状态、退款处理和后续贷项凭证。",
        overview_en="Return references, receipt status, refund handling, and follow-on credit documents were checked.",
        stages=[_stage("returns", "客户退货", "Customer returns", len(returns)), _stage("credits", "贷项请求", "Credit requests", len(credits)), _stage("billing", "后续开票", "Follow-on billing", len(billing))],
        findings=findings,
        actions_zh=["复核缺少原单引用或未收货已退款的业务单据。"] if findings else [],
        actions_en=["Review documents with missing source references or refunds before receipt."] if findings else [],
    )


def _shortage_allocation(inputs: JsonObject) -> JsonObject:
    items = _rows(inputs, "sales_order_items")
    schedules = _rows(inputs, "schedule_lines")
    stock = _rows(inputs, "material_stock")
    atp = _rows(inputs, "atp_availability")
    available = sum((_decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit") or row.get("MaterialBaseUnit")) for row in stock), Decimal(0))
    requested = sum((_decimal(row.get("ScheduleLineOrderQuantity") or row.get("RequestedQuantity")) for row in schedules + items), Decimal(0))
    confirmed = sum((_decimal(row.get("ConfdOrderQtyByMatlAvailCheck") or row.get("ConfdDelivQtyInOrderQtyUnit")) for row in schedules + items), Decimal(0))
    shortage = max(requested - confirmed - available, Decimal(0))
    atp_complete = bool(atp) and _source_complete(inputs)
    gaps = _gaps(inputs, *(() if atp_complete else ("atp_availability_evidence",)))
    return _result(
        inputs,
        business_status="attention" if shortage else "partial",
        headline_zh=f"基于当前库存的未覆盖需求为 {shortage}",
        headline_en=f"Uncovered demand based on current stock is {shortage}",
        overview_zh="优先使用 Released ATP API；MDKP/MDTB 只作为 MRP 上下文，绝不冒充 ATP 结果。",
        overview_en="The Released ATP API is authoritative; MDKP/MDTB may add MRP context but never substitute for ATP.",
        stages=[_stage("demand", "订单需求", "Order demand", len(items) + len(schedules)), _stage("stock", "库存快照", "Stock snapshot", len(stock)), _stage("atp", "ATP 可用量", "ATP availability", len(atp), state="confirmed" if atp_complete else "unknown")],
        metrics=[{"id": "requested", "value": str(requested)}, {"id": "confirmed", "value": str(confirmed)}, {"id": "stock", "value": str(available)}, {"id": "uncovered", "value": str(shortage)}],
        gaps=gaps,
        actions_zh=["由计划人员在 SAP 中执行 ATP 复核后再决定分配。"],
        actions_en=["Have a planner run an ATP review in SAP before deciding allocations."],
    )


def _o2c_anomaly(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "sales_orders")
    deliveries = _rows(inputs, "delivery_headers")
    billing = _rows(inputs, "billing_headers")
    accounting = _rows(inputs, "accounting_items")
    anomalies = sum(1 for row in orders if row.get("TotalBlockStatus"))
    anomalies += sum(1 for row in billing if _truthy(row.get("BillingDocumentIsCancelled")))
    anomalies += sum(1 for row in accounting if not _truthy(row.get("IsCleared")))
    output_rows = _adt_rows(inputs, "output_status")
    dispute_rows = _adt_rows(inputs, "dispute_case")
    output_complete = _adt_complete(_fallback(inputs, "output_status")) and bool(output_rows)
    dispute_complete = _adt_complete(_fallback(inputs, "dispute_case")) and bool(dispute_rows)
    missing = []
    if not output_complete:
        missing.append("billing_output_status_evidence")
    if not dispute_complete:
        missing.append("billing_dispute_case_evidence")
    gaps = _gaps(inputs, *missing)
    return _result(
        inputs,
        business_status="attention" if anomalies else "partial",
        headline_zh=f"当前证据中识别到 {anomalies} 项 O2C 异常",
        headline_en=f"Identified {anomalies} O2C anomaly item(s) in current evidence",
        overview_zh="结果覆盖订单、交货、开票和应收，并复用经过完整性与 Hash 验证的输出及争议 ADT 证据。",
        overview_en="The result covers orders, deliveries, billing, and receivables and reuses completeness- and hash-verified output and dispute ADT evidence.",
        stages=[_stage("orders", "销售订单", "Sales orders", len(orders)), _stage("deliveries", "交货", "Deliveries", len(deliveries)), _stage("billing", "开票", "Billing", len(billing)), _stage("accounting", "应收", "Receivables", len(accounting)), _stage("output", "输出状态", "Output status", len(output_rows), state="confirmed" if output_complete else "unknown"), _stage("dispute", "争议案件", "Dispute cases", len(dispute_rows), state="confirmed" if dispute_complete else "unknown")],
        metrics=[{"id": "anomaly_count", "value": anomalies}],
        gaps=gaps,
        actions_zh=["按冻结、取消和未清应收分别分派责任人。"] if anomalies else [],
        actions_en=["Assign owners for blocks, cancellations, and open receivables."] if anomalies else [],
    )


def _billing_output(inputs: JsonObject) -> JsonObject:
    billing = _rows(inputs, "billing_headers", "billing_items")
    output_rows = _adt_rows(inputs, "output_status")
    complete = _adt_complete(_fallback(inputs, "output_status")) and bool(output_rows)
    failures = [row for row in output_rows if str(row.get("VSTAT") or "").strip() not in {"", "1"}]
    return _result(
        inputs,
        business_status="attention" if failures else "normal",
        headline_zh=f"取得 {len(output_rows)} 条结构化输出状态，{len(failures)} 条需要复核" if complete else "输出状态证据不完整，无法确认发送结果",
        headline_en=f"Found {len(output_rows)} structured output status row(s); {len(failures)} require review" if complete else "Output evidence is incomplete, so delivery cannot be confirmed",
        overview_zh="只读取 NAST 或已批准的结构化输出状态；不读取收件人、邮件正文或附件。完整零行也不能证明发票未输出。",
        overview_en="Only NAST or approved structured output status is read; recipients, message bodies, and attachments are excluded. A complete zero-row result does not prove that no output occurred.",
        stages=[_stage("base_document", "开票凭证", "Billing document", len(billing)), _stage("output_status", "结构化输出状态", "Structured output status", len(output_rows), state="confirmed" if complete else "unknown")],
        findings=[{"code": "OUTPUT_STATUS_REQUIRES_REVIEW", "severity": "medium"} for _ in failures],
        gaps=_gaps(inputs, *(() if complete else ("billing_output_status_evidence",))),
        actions_zh=["由开票输出负责人复核失败或未处理状态。"] if failures else [],
        actions_en=["Have the billing-output owner review failed or unprocessed statuses."] if failures else [],
        source_complete_override=_source_complete(inputs) and complete,
    )


def _billing_dispute(inputs: JsonObject) -> JsonObject:
    billing = _rows(inputs, "billing_headers", "billing_items", "accounting_items")
    cases = _adt_rows(inputs, "dispute_case")
    complete = _adt_complete(_fallback(inputs, "dispute_case")) and bool(cases)
    categories: dict[str, int] = {}
    for row in cases:
        code = str(row.get("FIN_ROOT_CODE") or "UNCLASSIFIED").strip() or "UNCLASSIFIED"
        categories[code] = categories.get(code, 0) + 1
    return _result(
        inputs,
        business_status="attention" if cases else "partial",
        headline_zh=f"取得 {len(cases)} 条结构化争议案件属性" if complete else "争议案件证据不完整，无法完成分类",
        headline_en=f"Found {len(cases)} structured dispute case attribute row(s)" if complete else "Dispute case evidence is incomplete, so classification is inconclusive",
        overview_zh="仅使用已批准的案件GUID、根因码、到期日、客户、公司代码和来源；不导出自由文本或客户通信。",
        overview_en="Only approved case GUID, root-cause code, due date, customer, company code, and origin are used; free text and customer communications are excluded.",
        stages=[_stage("base_document", "开票与 FI 凭证", "Billing and FI documents", len(billing)), _stage("dispute_case", "结构化争议案件", "Structured dispute cases", len(cases), state="confirmed" if complete else "unknown")],
        metrics=[{"id": "case_count", "value": len(cases)}, {"id": "root_cause_counts", "value": categories}],
        gaps=_gaps(inputs, *(() if complete else ("billing_dispute_case_evidence",))),
        actions_zh=["由应收争议负责人按根因码复核并补充业务处置。"] if cases else [],
        actions_en=["Have the AR dispute owner review root-cause codes and determine follow-up actions."] if cases else [],
        source_complete_override=_source_complete(inputs) and complete,
    )


def _ap_payment(inputs: JsonObject) -> JsonObject:
    items = _rows(inputs, "supplier_items", "clearing_documents")
    open_items = [row for row in items if not _truthy(row.get("IsCleared"))]
    blocked = [row for row in open_items if row.get("PaymentBlockingReason")]
    gaps = _gaps(inputs, "payment_run_and_bank_master_evidence")
    return _result(
        inputs,
        business_status="attention" if open_items else "partial",
        headline_zh=f"发现 {len(open_items)} 条供应商未清项，其中 {len(blocked)} 条存在付款冻结",
        headline_en=f"Found {len(open_items)} supplier open item(s), including {len(blocked)} payment-blocked item(s)",
        overview_zh="已检查未清、到期、冻结和清账证据；当前不包含完整付款运行和银行主数据。",
        overview_en="Open, due, blocked, and clearing evidence was checked; complete payment-run and bank-master evidence is not included.",
        stages=[_stage("supplier_items", "供应商行项目", "Supplier items", len(items)), _stage("payment_run", "付款运行与银行证据", "Payment run and bank evidence", 0, state="unknown")],
        metrics=[{"id": "open_items", "value": len(open_items)}, {"id": "payment_blocked", "value": len(blocked)}],
        gaps=gaps,
        actions_zh=["由应付人员复核到期日、付款冻结和付款运行。"],
        actions_en=["Have AP review due dates, payment blocks, and the payment run."],
    )


def _ar_collection(inputs: JsonObject) -> JsonObject:
    items = _rows(inputs, "customer_items", "clearing_documents")
    open_items = [row for row in items if not _truthy(row.get("IsCleared"))]
    gaps = _gaps(inputs, "bank_receipt_matching_evidence")
    return _result(
        inputs,
        business_status="attention" if open_items else "partial",
        headline_zh=f"发现 {len(open_items)} 条客户未清应收",
        headline_en=f"Found {len(open_items)} customer open receivable item(s)",
        overview_zh="已形成账龄和催收范围；缺少银行流水时不能确认到账匹配。",
        overview_en="The aging and collection scope is available; bank-receipt matching cannot be confirmed without bank evidence.",
        stages=[_stage("receivables", "客户应收", "Customer receivables", len(items)), _stage("bank_match", "银行到账匹配", "Bank receipt matching", 0, state="unknown")],
        metrics=[{"id": "open_items", "value": len(open_items)}],
        gaps=gaps,
        actions_zh=["按到期日和金额安排催收，并由财务复核银行到账。"],
        actions_en=["Prioritize collection by due date and amount and have finance verify bank receipts."],
    )


def _grir(inputs: JsonObject) -> JsonObject:
    pos = _rows(inputs, "purchase_orders", "purchase_order_items")
    receipts = _rows(inputs, "material_documents")
    invoices = _rows(inputs, "supplier_invoice_items")
    gl = _rows(inputs, "gl_items")
    attention = bool(gl) or len(receipts) != len(invoices)
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh=f"GR/IR 范围包含 {len(pos)} 条采购证据、{len(receipts)} 条收货和 {len(invoices)} 条发票",
        headline_en=f"GR/IR scope contains {len(pos)} purchasing, {len(receipts)} receipt, and {len(invoices)} invoice evidence row(s)",
        overview_zh="规则按采购项目保留收货、冲销、发票和总账证据，用于净额和账龄分析。",
        overview_en="Receipt, reversal, invoice, and G/L evidence is retained by purchase-order item for netting and aging.",
        stages=[_stage("purchase_order", "采购订单", "Purchase orders", len(pos)), _stage("receipt", "收货与冲销", "Receipts and reversals", len(receipts)), _stage("invoice", "供应商发票", "Supplier invoices", len(invoices)), _stage("gl", "GR/IR 总账", "GR/IR G/L", len(gl))],
        metrics=[{"id": "receipt_rows", "value": len(receipts)}, {"id": "invoice_rows", "value": len(invoices)}, {"id": "gl_rows", "value": len(gl)}],
        actions_zh=["复核收货与发票不一致的采购项目。"] if attention else [],
        actions_en=["Review purchase-order items whose receipt and invoice evidence does not align."] if attention else [],
    )


def _month_end(inputs: JsonObject) -> JsonObject:
    fi = _rows(inputs, "fi_period_items")
    gaps = _gaps(inputs, "period_control_asset_depreciation_and_specialized_closing_checks")
    return _result(
        inputs,
        business_status="capability_blocked",
        headline_zh="已取得部分月结证据，但不能确认可以关账",
        headline_en="Partial month-end evidence was collected, but closing readiness cannot be confirmed",
        overview_zh="当前只覆盖已发布的 FI 行项目；期间控制、固定资产折旧和专项关账检查缺失。",
        overview_en="Only published FI line items are covered; period control, asset depreciation, and specialized closing checks are missing.",
        stages=[_stage("fi", "FI 期间凭证", "FI period documents", len(fi)), _stage("closing_checks", "完整关账检查清单", "Complete closing checklist", 0, state="unknown")],
        gaps=gaps,
        actions_zh=["补充公司代码和期间绑定的只读关账检查接口后再判断。"],
        actions_en=["Add company-code and period-bound read-only closing checks before assessing readiness."],
    )


def _demand_forecast(inputs: JsonObject) -> JsonObject:
    demand = _rows(inputs, "sales_demand")
    planned = _rows(inputs, "planned_orders")
    gaps = _gaps(inputs, "pir_evidence")
    return _result(
        inputs,
        business_status="capability_blocked",
        headline_zh="已读取销售需求和计划订单，但不能完成 PIR 计划比较",
        headline_en="Sales demand and planned orders were read, but PIR comparison cannot be completed",
        overview_zh="缺少同物料、工厂和期间的 PIR 只读证据；本次不训练模型也不写回 PIR。",
        overview_en="Read-only PIR evidence for the same material, plant, and period is missing; no model is trained and no PIR is written.",
        stages=[_stage("demand", "销售需求", "Sales demand", len(demand)), _stage("planned", "计划订单", "Planned orders", len(planned)), _stage("pir", "独立需求 PIR", "Planned independent requirements", 0, state="unknown")],
        gaps=gaps,
        actions_zh=["增加经过审批的 PBIM/PBED 只读 Skill 后再运行。"],
        actions_en=["Add an approved PBIM/PBED read-only Skill and rerun."],
    )


def _mrp_exception(inputs: JsonObject) -> JsonObject:
    master = _rows(inputs, "mrp_material")
    coverage = _rows(inputs, "material_coverages")
    elements = _rows(inputs, "supply_demand_items")
    dynamic = [] if coverage and elements else ["mrp_coverage_or_supply_demand_evidence"]
    gaps = _gaps(inputs, *dynamic)
    return _result(
        inputs,
        business_status="attention" if coverage or elements else "capability_blocked",
        headline_zh=f"MRP 检查返回 {len(coverage)} 条覆盖和 {len(elements)} 条供需证据",
        headline_en=f"MRP checks returned {len(coverage)} coverage and {len(elements)} supply-demand row(s)",
        overview_zh="只有覆盖和供需明细均完整时，才能判断没有短缺或重排异常。",
        overview_en="Both coverage and supply-demand details must be complete before concluding that no shortage or rescheduling exception exists.",
        stages=[_stage("master", "MRP 主数据", "MRP master", len(master)), _stage("coverage", "物料覆盖", "Material coverage", len(coverage)), _stage("elements", "供需项目", "Supply-demand items", len(elements))],
        gaps=gaps,
        actions_zh=["复核 MRP 服务超时、短缺参数文件和供需异常消息。"],
        actions_en=["Review MRP timeouts, shortage profile parameters, and exception messages."],
    )


def _production_monitor(inputs: JsonObject) -> JsonObject:
    orders = _rows(inputs, "production_order", "production_order_items")
    statuses = _rows(inputs, "production_statuses")
    operations = _rows(inputs, "production_operations")
    components = _rows(inputs, "production_components")
    movements = _rows(inputs, "material_documents")
    attention = any(not _truthy(row.get("OperationIsConfirmed")) for row in operations)
    return _result(
        inputs,
        business_status="attention" if attention else "normal",
        headline_zh="生产订单仍有未确认工序" if attention else "生产订单执行证据已完整取得",
        headline_en="The production order has unconfirmed operations" if attention else "Production-order execution evidence was collected",
        overview_zh="已核对订单状态、工序确认、组件领料和物料凭证，不执行确认、发料、收货或 TECO。",
        overview_en="Order status, operation confirmation, component withdrawal, and material documents were checked without confirmation, issue, receipt, or TECO actions.",
        stages=[_stage("order", "生产订单", "Production order", len(orders)), _stage("status", "订单状态", "Order status", len(statuses)), _stage("operations", "生产工序", "Operations", len(operations)), _stage("components", "组件", "Components", len(components)), _stage("movements", "物料凭证", "Material documents", len(movements))],
        actions_zh=["由生产人员复核未确认工序、缺料和待收货状态。"] if attention else [],
        actions_en=["Have production review unconfirmed operations, shortages, and pending receipts."] if attention else [],
    )


def _production_schedule(inputs: JsonObject) -> JsonObject:
    planned = _rows(inputs, "planned_orders", "planned_capacities")
    operations = _rows(inputs, "production_operations")
    centers = _rows(inputs, "work_centers", "work_center_capacities")
    buckets = _rows(inputs, "capacity_buckets")
    gaps = _gaps(inputs, *("complete_capacity_bucket_evidence",) if not buckets else ())
    return _result(
        inputs,
        business_status="attention" if buckets else "capability_blocked",
        headline_zh="已取得排程对象和完整产能桶" if buckets else "已取得排程对象，但缺少完整产能桶",
        headline_en="Scheduling objects and complete capacity buckets were collected" if buckets else "Scheduling objects were collected, but complete capacity buckets are missing",
        overview_zh="只有计划负荷和可用产能按同一工作中心、日期及单位完整返回时才计算利用率。",
        overview_en="Utilization is calculated only when planned load and available capacity are complete for the same work center, dates, and units.",
        stages=[_stage("planned", "计划订单与产能需求", "Planned orders and requirements", len(planned)), _stage("operations", "生产工序", "Production operations", len(operations)), _stage("centers", "工作中心", "Work centers", len(centers)), _stage("buckets", "产能桶", "Capacity buckets", len(buckets), state="confirmed" if buckets else "unknown")],
        gaps=gaps,
        actions_zh=["补齐产能桶后由计划人员人工比较排程方案。"],
        actions_en=["Complete capacity-bucket evidence and have planners compare scheduling scenarios."],
    )


def _production_variance(inputs: JsonObject) -> JsonObject:
    items = _rows(inputs, "production_order_items")
    operations = _rows(inputs, "production_operations")
    components = _rows(inputs, "production_components")
    movements = _rows(inputs, "material_documents")
    costs = _rows(inputs, "cost_items")
    gaps = _gaps(inputs, *("production_cost_evidence",) if not costs else ())
    return _result(
        inputs,
        business_status="attention" if movements or costs else "partial",
        headline_zh="生产偏差证据已按数量、工序、用料和成本分维度检查",
        headline_en="Production variance evidence was checked separately for quantity, operations, material use, and cost",
        overview_zh="没有实际活动时只报告“尚无过账证据”，不会解释为零偏差或表现正常。",
        overview_en="No actual activity is reported as no posting evidence, not as zero variance or good performance.",
        stages=[_stage("quantity", "订单数量", "Order quantity", len(items)), _stage("operations", "工序确认", "Operation confirmation", len(operations)), _stage("materials", "组件与领料", "Components and issues", len(components) + len(movements)), _stage("costs", "成本行项目", "Cost items", len(costs), state="confirmed" if costs else "unknown")],
        gaps=gaps,
        actions_zh=["对缺少实际活动或成本证据的订单补充过账与结算核查。"],
        actions_en=["Add posting and settlement checks for orders without actual-activity or cost evidence."],
    )


def _material_shortage_procurement(inputs: JsonObject) -> JsonObject:
    mrp = _rows(inputs, "mrp_coverage", "supply_demand") + _adt_rows(inputs, "mrp")
    requisitions = _rows(inputs, "purchase_requisitions") + _adt_rows(inputs, "pr")
    orders = _rows(inputs, "purchase_orders") + _adt_rows(inputs, "po_schedule")
    sources = _rows(inputs, "info_records", "contracts", "suppliers") + _adt_rows(inputs, "source")
    complete, missing = _required_topics(inputs, "mrp", "pr", "po_schedule", "source")
    units = {
        str(row.get(key) or "").strip()
        for row in mrp
        for key in ("MaterialBaseUnit", "BaseUnit", "Unit")
        if str(row.get(key) or "").strip()
    }
    comparable = complete and len(units) <= 1
    shortage = sum(
        (_decimal(row.get("MaterialShortageQuantity") or row.get("MRPElementOpenQuantity")))
        for row in mrp
        if _decimal(row.get("MaterialShortageQuantity") or row.get("MRPElementOpenQuantity")) > 0
    )
    pending_pr = [
        row for row in requisitions
        if not _truthy(row.get("IsDeleted"))
        and str(row.get("ProcessingStatus") or row.get("ReleaseStatus") or "").upper()
        not in {"05", "C", "RELEASED", "COMPLETED"}
    ]
    today = _date((inputs.get("run_input") or {}).get("as_of")) or date.today()
    expediting = [
        row for row in orders
        if (_date(row.get("ScheduleLineDeliveryDate") or row.get("DeliveryDate")) or date.max) < today
        and _decimal(row.get("OpenPurchaseOrderQuantity") or row.get("ScheduleLineOrderQuantity")) > 0
    ]
    findings = [
        {"code": "UNIT_NOT_COMPARABLE", "severity": "high"}
        for _ in [0]
        if len(units) > 1
    ]
    if not complete:
        findings.append({"code": "REQUIRED_EVIDENCE_INCOMPLETE", "severity": "high"})
    return _result(
        inputs,
        business_status="attention" if shortage or pending_pr or expediting else "normal",
        headline_zh=(
            f"识别到 {len(pending_pr)} 条待处理 PR、{len(expediting)} 条催交 PO；确定缺口为 {shortage}"
            if comparable else
            f"识别到 {len(pending_pr)} 条待处理 PR、{len(expediting)} 条催交 PO；缺口数量无法确定"
        ),
        headline_en=(
            f"Found {len(pending_pr)} pending PR(s), {len(expediting)} PO line(s) to expedite; confirmed shortage is {shortage}"
            if comparable else
            f"Found {len(pending_pr)} pending PR(s), {len(expediting)} PO line(s) to expedite; shortage quantity is inconclusive"
        ),
        overview_zh="MRP、PR、PO 交期和货源证据均完整且单位可比时才计算确定缺口；本 Agent 只给出处置建议。",
        overview_en="A confirmed shortage is calculated only when MRP, PR, PO schedule, and source evidence are complete and units are comparable; the Agent is advisory only.",
        stages=[
            _stage("mrp", "MRP 供需", "MRP supply and demand", len(mrp), state="confirmed" if _topic_complete(inputs, "mrp") else "unknown"),
            _stage("pr", "采购申请", "Purchase requisitions", len(requisitions), state="confirmed" if _topic_complete(inputs, "pr") else "unknown"),
            _stage("po", "采购订单交期", "PO schedules", len(orders), state="confirmed" if _topic_complete(inputs, "po_schedule") else "unknown"),
            _stage("source", "有效货源", "Valid sources", len(sources), state="confirmed" if _topic_complete(inputs, "source") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "shortage_quantity", "value": str(shortage) if comparable else None},
            {"id": "pending_pr", "value": len(pending_pr)},
            {"id": "expedite_po", "value": len(expediting)},
            {"id": "valid_source_candidates", "value": len(sources)},
        ],
        gaps=_gaps(inputs, *missing),
        actions_zh=["释放或转换合格 PR，并由采购员复核逾期 PO 与有效货源。"],
        actions_en=["Release or convert eligible PRs and have purchasing review overdue POs and valid sources."],
        source_complete_override=complete,
    )


def _inventory_health_balancing(inputs: JsonObject) -> JsonObject:
    stock = _rows(inputs, "material_stock") + _adt_rows(inputs, "stock")
    movements = _rows(inputs, "material_movements") + _adt_rows(inputs, "movement")
    batches = _rows(inputs, "batches") + _adt_rows(inputs, "batch_expiry")
    parameters = _rows(inputs, "replenishment_parameters") + _adt_rows(inputs, "parameters")
    complete, missing = _required_topics(inputs, "stock", "movement", "batch_expiry", "parameters")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    as_of = _date(run_input.get("as_of")) or date.today()
    slow_days = int(run_input.get("slow_moving_days") or 180)
    obsolete_days = int(run_input.get("obsolete_days") or 365)
    expiry_days = int(run_input.get("expiry_days") or 90)
    unrestricted = sum(
        (_decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit") or row.get("UnrestrictedUseStock") or row.get("LABST")))
        for row in stock
    )
    movement_dates = [
        value for row in movements
        for value in [_date(row.get("PostingDate") or row.get("BUDAT"))]
        if value is not None
    ]
    age = (as_of - max(movement_dates)).days if movement_dates else None
    expiry_candidates = [
        row for row in batches
        if (expiry := _date(row.get("ShelfLifeExpirationDate") or row.get("VFDAT"))) is not None
        and 0 <= (expiry - as_of).days <= expiry_days
    ]
    safety_values = [
        _decimal(row.get("SafetyStockQuantity") or row.get("EISBE"))
        for row in parameters
        if row.get("SafetyStockQuantity") not in {None, ""} or row.get("EISBE") not in {None, ""}
    ]
    units = {
        str(row.get(key) or "").strip()
        for row in stock + batches
        for key in ("MaterialBaseUnit", "BaseUnit", "MEINS")
        if str(row.get(key) or "").strip()
    }
    can_quantify = complete and bool(safety_values) and len(units) <= 1 and all(
        any(key in row for key in ("MatlWrhsStkQtyInMatlBaseUnit", "UnrestrictedUseStock", "LABST"))
        for row in stock
    )
    excess = max(unrestricted - sum(safety_values, Decimal(0)), Decimal(0)) if can_quantify else None
    findings: list[JsonObject] = []
    if age is not None and age >= obsolete_days:
        findings.append({"code": "OBSOLETE_STOCK_CANDIDATE", "severity": "high", "age_days": age})
    elif age is not None and age >= slow_days:
        findings.append({"code": "SLOW_MOVING_STOCK_CANDIDATE", "severity": "medium", "age_days": age})
    findings.extend({"code": "EXPIRY_RISK", "severity": "high"} for _row in expiry_candidates)
    if not can_quantify:
        findings.append({"code": "TRANSFER_QUANTITY_SUPPRESSED", "severity": "medium"})
    return _result(
        inputs,
        business_status="attention" if findings else "normal",
        headline_zh=f"发现 {len(expiry_candidates)} 个临期批次；调拨量为 {excess if excess is not None else '候选，无法确定'}",
        headline_en=f"Found {len(expiry_candidates)} expiring batch(es); transfer quantity is {excess if excess is not None else 'candidate-only and inconclusive'}",
        overview_zh="仅 unrestricted/available 库存参与平衡；缺少安全库存、单位或批次数量时不输出确定调拨量。",
        overview_en="Only unrestricted/available stock participates in balancing; no confirmed transfer quantity is emitted without safety stock, units, and batch quantities.",
        stages=[
            _stage("stock", "可用库存", "Available stock", len(stock), state="confirmed" if _topic_complete(inputs, "stock") else "unknown"),
            _stage("movement", "移动历史", "Movement history", len(movements), state="confirmed" if _topic_complete(inputs, "movement") else "unknown"),
            _stage("batch", "批次与效期", "Batch and expiry", len(batches), state="confirmed" if _topic_complete(inputs, "batch_expiry") else "unknown"),
            _stage("parameters", "补货参数", "Replenishment parameters", len(parameters), state="confirmed" if _topic_complete(inputs, "parameters") else "unknown"),
        ],
        findings=findings,
        metrics=[
            {"id": "unrestricted_stock", "value": str(unrestricted)},
            {"id": "stock_age_days", "value": age},
            {"id": "expiry_candidates", "value": len(expiry_candidates)},
            {"id": "confirmed_transfer_quantity", "value": str(excess) if excess is not None else None},
        ],
        gaps=_gaps(inputs, *missing),
        actions_zh=["由库存计划员复核慢动、呆滞和临期候选，并在 SAP 中人工决定调拨。"],
        actions_en=["Have inventory planning review slow-moving, obsolete, and expiring candidates and decide transfers in SAP."],
        source_complete_override=complete,
    )


def _intelligent_sourcing_rfq(inputs: JsonObject) -> JsonObject:
    rfq = _rows(inputs, "rfq") + _adt_rows(inputs, "rfq")
    quotations = _rows(inputs, "quotations") + _adt_rows(inputs, "quotation")
    suppliers = _rows(inputs, "suppliers") + _adt_rows(inputs, "supplier")
    sources = _rows(inputs, "info_records", "contracts") + _adt_rows(inputs, "source")
    complete, missing = _required_topics(inputs, "rfq", "quotation", "supplier", "source")
    active = [
        row for row in quotations
        if not _truthy(row.get("IsDeleted"))
        and str(row.get("QuotationStatus") or row.get("PurchasingDocumentStatus") or "").upper()
        not in {"WITHDRAWN", "EXPIRED", "CANCELLED", "D"}
    ]
    comparable_keys = {
        (
            str(row.get("DocumentCurrency") or row.get("Currency") or ""),
            str(row.get("PurchaseOrderQuantityUnit") or row.get("OrderQuantityUnit") or row.get("Unit") or ""),
            str(row.get("PriceUnitQty") or row.get("PriceUnit") or "1"),
        )
        for row in active
    }
    blocked_suppliers = {
        str(row.get("Supplier") or row.get("BusinessPartner") or "")
        for row in suppliers
        if _truthy(row.get("SupplierIsBlockedForPosting") or row.get("PurchasingIsBlockedForSupplier"))
    }
    eligible = [row for row in active if str(row.get("Supplier") or row.get("Bidder") or "") not in blocked_suppliers]
    comparable = complete and len(comparable_keys) == 1 and bool(eligible)
    prices = [_decimal(row.get("NetPriceAmount") or row.get("QuotationPrice")) for row in eligible]
    max_price = max(prices, default=Decimal(0))
    ranked: list[JsonObject] = []
    if comparable and max_price > 0:
        for row, price in zip(eligible, prices):
            price_score = float((max_price - price) / max_price * Decimal(60)) if price >= 0 else 0
            delivery_score = 25.0 if row.get("DeliveryDate") or row.get("PerformancePeriodStartDate") else 0.0
            completeness_score = 15.0 if all(row.get(field) not in {None, ""} for field in ("Supplier", "NetPriceAmount")) else 7.5
            ranked.append({
                "supplier": str(row.get("Supplier") or row.get("Bidder") or ""),
                "quotation": str(row.get("SupplierQuotation") or row.get("PurchasingDocument") or ""),
                "score": round(price_score + delivery_score + completeness_score, 2),
            })
        ranked.sort(key=lambda item: (-float(item["score"]), item["supplier"], item["quotation"]))
    findings = [] if comparable else [{"code": "QUOTATIONS_NOT_COMPARABLE", "severity": "high"}]
    return _result(
        inputs,
        business_status="attention" if not comparable or blocked_suppliers else "normal",
        headline_zh=(f"已对 {len(ranked)} 份有效报价完成统一评分" if comparable else "报价币种、单位、价格单位或证据不完整，未生成统一排名"),
        headline_en=(f"Ranked {len(ranked)} valid quotation(s) on a common basis" if comparable else "No unified ranking was produced because currency, unit, price unit, or evidence is incomplete"),
        overview_zh="评分固定为价格 60、交期 25、完整性 15；冻结、撤回和失效报价不参与推荐。",
        overview_en="Scoring is fixed at price 60, delivery 25, and completeness 15; blocked, withdrawn, and expired quotations are excluded.",
        stages=[
            _stage("rfq", "询价单", "RFQ", len(rfq), state="confirmed" if _topic_complete(inputs, "rfq") else "unknown"),
            _stage("quotation", "供应商报价", "Supplier quotations", len(quotations), state="confirmed" if _topic_complete(inputs, "quotation") else "unknown"),
            _stage("supplier", "供应商状态", "Supplier status", len(suppliers), state="confirmed" if _topic_complete(inputs, "supplier") else "unknown"),
            _stage("source", "历史货源", "Existing sources", len(sources), state="confirmed" if _topic_complete(inputs, "source") else "unknown"),
        ],
        findings=findings,
        metrics=[{"id": "eligible_quotations", "value": len(eligible)}, {"id": "ranked_quotations", "value": len(ranked)}, {"id": "ranking", "value": ranked}],
        gaps=_gaps(inputs, *missing),
        actions_zh=["由采购员复核评分、商务条款和供应商资格后在 SAP 中决策。"],
        actions_en=["Have purchasing review scores, commercial terms, and supplier eligibility before deciding in SAP."],
        source_complete_override=complete,
    )


def _supplier_performance_risk(inputs: JsonObject) -> JsonObject:
    schedules = _rows(inputs, "po_schedules") + _adt_rows(inputs, "po_schedule")
    receipts = _rows(inputs, "receipts") + _adt_rows(inputs, "receipt")
    suppliers = _rows(inputs, "suppliers") + _adt_rows(inputs, "supplier")
    complete, missing = _required_topics(inputs, "po_schedule", "receipt", "supplier")
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    as_of = _date(run_input.get("date_to")) or date.today()
    due = [row for row in schedules if (_date(row.get("ScheduleLineDeliveryDate") or row.get("EINDT")) or date.max) <= as_of]
    on_time = 0
    formal = complete and len(due) >= 5
    if formal:
        for schedule in due:
            po = str(schedule.get("PurchaseOrder") or schedule.get("EBELN") or "")
            item = str(schedule.get("PurchaseOrderItem") or schedule.get("EBELP") or "")
            delivery = _date(schedule.get("ScheduleLineDeliveryDate") or schedule.get("EINDT"))
            scheduled_qty = _decimal(schedule.get("ScheduleLineOrderQuantity") or schedule.get("MENGE"))
            matching = [
                row for row in receipts
                if str(row.get("PurchaseOrder") or row.get("EBELN") or "") == po
                and str(row.get("PurchaseOrderItem") or row.get("EBELP") or "") == item
            ]
            net = sum(
                (-_decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")) if str(row.get("DebitCreditCode") or row.get("SHKZG") or "S").upper() in {"H", "C"} else _decimal(row.get("QuantityInEntryUnit") or row.get("MENGE")))
                for row in matching
                if (_date(row.get("PostingDate") or row.get("BUDAT")) or date.max) <= (delivery or date.min)
            )
            if scheduled_qty > 0 and net >= scheduled_qty:
                on_time += 1
    otif = round(on_time / len(due) * 100, 2) if formal and due else None
    blocked = any(_truthy(row.get("SupplierIsBlockedForPosting") or row.get("PurchasingIsBlockedForSupplier")) for row in suppliers)
    findings: list[JsonObject] = []
    if len(due) < 5:
        findings.append({"code": "LOW_SAMPLE_CONFIDENCE", "severity": "medium", "sample_size": len(due)})
    if not complete:
        findings.append({"code": "OTIF_SUPPRESSED_INCOMPLETE_EVIDENCE", "severity": "high"})
    if blocked:
        findings.append({"code": "SUPPLIER_BLOCKED", "severity": "high"})
    return _result(
        inputs,
        business_status="attention" if findings or (otif is not None and otif < 95) else "normal",
        headline_zh=f"正式 OTIF 为 {otif}%（到期计划行 {len(due)} 条）" if otif is not None else f"到期计划行 {len(due)} 条；因样本或证据不足未计算正式 OTIF",
        headline_en=f"Formal OTIF is {otif}% across {len(due)} due schedule line(s)" if otif is not None else f"Found {len(due)} due schedule line(s); formal OTIF was suppressed due to sample or evidence limits",
        overview_zh="OTIF 按 schedule line 与累计净收货计算；少于 5 条到期行标记低样本，缺少交期或收货日期时不形成正式指标。",
        overview_en="OTIF is calculated by schedule line and cumulative net receipts; fewer than five due lines is low-confidence, and missing schedule or receipt dates suppress the formal metric.",
        stages=[
            _stage("schedule", "到期计划行", "Due schedule lines", len(due), state="confirmed" if _topic_complete(inputs, "po_schedule") else "unknown"),
            _stage("receipt", "净收货", "Net receipts", len(receipts), state="confirmed" if _topic_complete(inputs, "receipt") else "unknown"),
            _stage("supplier", "供应商状态", "Supplier status", len(suppliers), state="confirmed" if _topic_complete(inputs, "supplier") else "unknown"),
        ],
        findings=findings,
        metrics=[{"id": "due_schedule_lines", "value": len(due)}, {"id": "on_time_in_full", "value": on_time if formal else None}, {"id": "otif_percent", "value": otif}],
        gaps=_gaps(inputs, *missing),
        actions_zh=["由采购员复核迟交、退货、冲销和供应商冻结后安排改善。"],
        actions_en=["Have purchasing review late deliveries, returns, reversals, and supplier blocks before taking improvement action."],
        source_complete_override=complete,
    )


_EVALUATORS: dict[str, Callable[[JsonObject], JsonObject]] = {
    "ap-payment": _ap_payment,
    "ar-collection": _ar_collection,
    "gr-ir-clearing": _grir,
    "month-end-closing": _month_end,
    "billing-block-diagnosis": _billing_block,
    "billing-completeness-check": _billing_completeness,
    "billing-dispute-classification": _billing_dispute,
    "billing-output-monitor": _billing_output,
    "delivered-not-billed": _delivered_not_billed,
    "delivery-delay-prediction": _delivery_delay,
    "due-delivery-prioritization": _due_priority,
    "order-to-cash-anomaly-monitor": _o2c_anomaly,
    "returns-credit-anomaly": _returns_credit,
    "shortage-allocation-advisor": _shortage_allocation,
    "demand-forecast-planning": _demand_forecast,
    "mrp-exception-analysis": _mrp_exception,
    "production-order-monitoring": _production_monitor,
    "production-scheduling-capacity": _production_schedule,
    "production-variance-analysis": _production_variance,
    "material-shortage-procurement-response": _material_shortage_procurement,
    "inventory-health-balancing": _inventory_health_balancing,
    "intelligent-sourcing-rfq": _intelligent_sourcing_rfq,
    "supplier-performance-risk": _supplier_performance_risk,
}
