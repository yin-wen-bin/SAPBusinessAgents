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
    for payload in _all_payloads(inputs):
        step_results = payload.get("step_results")
        if not isinstance(step_results, dict):
            data = payload.get("data")
            step_results = data.get("step_results") if isinstance(data, dict) else None
        if not isinstance(step_results, dict):
            continue
        for step_id, result in step_results.items():
            if wanted and step_id not in wanted:
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
) -> JsonObject:
    missing = sorted(set(gaps or []))
    source_complete = _source_complete(inputs)
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
    available = sum((_decimal(row.get("MatlWrhsStkQtyInMatlBaseUnit") or row.get("MaterialBaseUnit")) for row in stock), Decimal(0))
    requested = sum((_decimal(row.get("ScheduleLineOrderQuantity") or row.get("RequestedQuantity")) for row in schedules + items), Decimal(0))
    confirmed = sum((_decimal(row.get("ConfdOrderQtyByMatlAvailCheck") or row.get("ConfdDelivQtyInOrderQtyUnit")) for row in schedules + items), Decimal(0))
    shortage = max(requested - confirmed - available, Decimal(0))
    gaps = _gaps(inputs, "atp_availability_evidence")
    return _result(
        inputs,
        business_status="attention" if shortage else "partial",
        headline_zh=f"基于当前库存的未覆盖需求为 {shortage}",
        headline_en=f"Uncovered demand based on current stock is {shortage}",
        overview_zh="当前只能提供基于订单确认量和库存快照的建议；不能替代 SAP ATP 检查。",
        overview_en="The recommendation uses confirmed demand and a stock snapshot and does not replace SAP ATP.",
        stages=[_stage("demand", "订单需求", "Order demand", len(items) + len(schedules)), _stage("stock", "库存快照", "Stock snapshot", len(stock)), _stage("atp", "ATP 可用量", "ATP availability", 0, state="unknown")],
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
    gaps = _gaps(inputs, "billing_output_and_dispute_evidence")
    return _result(
        inputs,
        business_status="attention" if anomalies else "partial",
        headline_zh=f"当前证据中识别到 {anomalies} 项 O2C 异常",
        headline_en=f"Identified {anomalies} O2C anomaly item(s) in current evidence",
        overview_zh="结果覆盖订单、交货、开票和应收；发票输出与争议维度当前未覆盖。",
        overview_en="The result covers orders, deliveries, billing, and receivables; output and dispute dimensions are not covered.",
        stages=[_stage("orders", "销售订单", "Sales orders", len(orders)), _stage("deliveries", "交货", "Deliveries", len(deliveries)), _stage("billing", "开票", "Billing", len(billing)), _stage("accounting", "应收", "Receivables", len(accounting))],
        metrics=[{"id": "anomaly_count", "value": anomalies}],
        gaps=gaps,
        actions_zh=["按冻结、取消和未清应收分别分派责任人。"] if anomalies else [],
        actions_en=["Assign owners for blocks, cancellations, and open receivables."] if anomalies else [],
    )


def _known_capability_block(inputs: JsonObject, zh: str, en: str, gap: str, stage_zh: str, stage_en: str) -> JsonObject:
    billing = _rows(inputs, "billing_headers", "billing_items", "accounting_items")
    return _result(
        inputs,
        business_status="capability_blocked",
        headline_zh=zh,
        headline_en=en,
        overview_zh="基础 SAP 凭证已读取，但缺少作出该业务结论所必需的只读取证能力。",
        overview_en="Base SAP documents were read, but a required read-only evidence capability is unavailable.",
        stages=[_stage("base_document", "基础业务凭证", "Base business document", len(billing)), _stage("required_evidence", stage_zh, stage_en, 0, state="unknown")],
        gaps=_gaps(inputs, gap),
        actions_zh=["补齐并审批对应的只读 SAPSkillhub Skill 后重新运行。"],
        actions_en=["Add and approve the corresponding read-only SAPSkillhub Skill, then rerun."],
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


_EVALUATORS: dict[str, Callable[[JsonObject], JsonObject]] = {
    "ap-payment": _ap_payment,
    "ar-collection": _ar_collection,
    "gr-ir-clearing": _grir,
    "month-end-closing": _month_end,
    "billing-block-diagnosis": _billing_block,
    "billing-completeness-check": _billing_completeness,
    "billing-dispute-classification": lambda inputs: _known_capability_block(inputs, "缺少争议文本，无法进行争议分类", "Dispute text is missing, so classification cannot be completed", "billing_dispute_text_evidence", "争议案件或沟通文本", "Dispute case or communication text"),
    "billing-output-monitor": lambda inputs: _known_capability_block(inputs, "缺少发票输出状态，无法确认是否已发送", "Billing output status is missing, so delivery cannot be confirmed", "billing_output_status_evidence", "Output Management、VF31 或 SOST 状态", "Output Management, VF31, or SOST status"),
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
}
