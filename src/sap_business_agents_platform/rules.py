from __future__ import annotations

from typing import Any

from .agent_rules import evaluate_business_agent


P2P_PAYMENT_DOCUMENT_TYPES = frozenset({"KZ", "ZP"})
O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES = frozenset({"DZ"})


def evaluate(operation: str, inputs: dict[str, Any]) -> dict[str, Any]:
    if operation == "assess_api_evidence":
        return assess_api_evidence(inputs)
    if operation == "evidence_summary":
        return evidence_summary(inputs)
    if operation == "extract_bounded_values":
        return extract_bounded_values(inputs)
    if operation == "evaluate_business_agent":
        return evaluate_business_agent(inputs)
    if operation == "evaluate_p2p_status":
        return evaluate_p2p_status(inputs)
    if operation == "evaluate_o2c_status":
        return evaluate_o2c_status(inputs)
    raise ValueError(f"Unknown deterministic rule operation: {operation}")


def assess_api_evidence(inputs: dict[str, Any]) -> dict[str, Any]:
    checks = inputs.get("checks")
    if not isinstance(checks, dict) or not checks:
        raise ValueError("assess_api_evidence requires named checks")
    needs_adt: dict[str, bool] = {}
    api_complete: dict[str, bool] = {}
    missing: list[str] = []
    for name, payload in checks.items():
        flags = _collect_source_complete(payload)
        ok_values = [value for value in _collect_values(payload, "ok") if isinstance(value, bool)]
        complete = bool(flags) and all(flags) and (not ok_values or all(ok_values))
        key = str(name)
        api_complete[key] = complete
        needs_adt[key] = not complete
        if not complete:
            missing.append(key)
    return {
        "rule_id": "api_evidence_gap_assessment_v1",
        "status": "complete" if not missing else "fallback_required",
        "api_complete": api_complete,
        "needs_adt": needs_adt,
        "missing_evidence": missing,
        "summary": {
            "zh": "标准 API 证据完整。" if not missing else "标准 API 存在证据缺口，将按白名单调用 ADT。",
            "en": "Standard API evidence is complete." if not missing else "Standard API evidence has gaps; allowlisted ADT fallbacks are required.",
        },
    }


def extract_bounded_values(inputs: dict[str, Any]) -> dict[str, Any]:
    """Extract one static column for a bounded downstream typed IN filter."""

    payload = inputs.get("payload")
    field = str(inputs.get("field") or "")
    maximum = inputs.get("max_values", 100)
    if not field or not field.replace("_", "").isalnum() or not field[0].isalpha():
        raise ValueError("extract_bounded_values requires one static field identifier")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
        raise ValueError("extract_bounded_values max_values must be between 1 and 100")
    values: list[Any] = []
    for group in _collect_values(payload, "rows"):
        if not isinstance(group, list):
            continue
        for row in group:
            if isinstance(row, dict) and row.get(field) not in {None, ""} and row[field] not in values:
                values.append(row[field])
    truncated = len(values) > maximum
    values = values[:maximum]
    flags = _collect_source_complete(payload)
    complete = bool(flags) and all(flags) and not truncated
    return {
        "rule_id": "extract_bounded_values_v1",
        "status": "complete" if complete else "inconclusive",
        "field": field,
        "values": values,
        "value_count": len(values),
        "has_values": bool(values),
        "source_complete": complete,
        "truncated": truncated,
    }


def evidence_summary(inputs: dict[str, Any]) -> dict[str, Any]:
    complete_flags = _collect_source_complete(inputs)
    case_ids = [str(value) for value in _collect_values(inputs, "case_id") if value]
    successful = sum(1 for value in _collect_values(inputs, "ok") if value is True)
    source_complete = bool(complete_flags) and all(complete_flags)
    if not complete_flags:
        reason = {
            "zh": "SAP 只读 Provider 没有返回数据范围完整性声明。",
            "en": "The SAP read Provider did not provide a source-completeness assertion.",
        }
    elif source_complete:
        reason = {
            "zh": "所有 SAP 只读证据来源都确认当前查询范围已完整返回。",
            "en": "Every SAP read evidence source reports source_complete=true.",
        }
    else:
        reason = {
            "zh": "至少一个 SAP 只读证据来源存在数量限制或数据范围不完整。",
            "en": "At least one SAP read evidence source is bounded or incomplete.",
        }
    return {
        "rule_id": "evidence_completeness",
        "status": "complete" if source_complete else "inconclusive",
        "score": 100 if source_complete else 0,
        "reason": reason,
        "evidence_refs": case_ids,
        "source_complete": source_complete,
        "successful_source_count": successful,
    }


def evaluate_p2p_status(inputs: dict[str, Any]) -> dict[str, Any]:
    steps = _step_results(inputs)
    order_rows = _step_rows(steps, "purchase_order")
    item_rows = _step_rows(steps, "purchase_order_items")
    material_rows = _step_rows(steps, "material_documents")
    invoice_rows = _step_rows(steps, "supplier_invoice_items")
    accounting_rows = _step_rows(steps, "accounting_items") + _step_rows(
        steps, "clearing_documents"
    )

    receipt_rows = [row for row in material_rows if _movement_kind(row) == "receipt"]
    reversal_rows = [row for row in material_rows if _movement_kind(row) == "reversal"]
    active_receipts = max(0, len(receipt_rows) - len(reversal_rows))
    cleared_rows = [
        row
        for row in accounting_rows
        if _is_true(row.get("IsCleared"))
        and bool(str(row.get("ClearingAccountingDocument") or "").strip())
    ]
    payment_rows = [
        row
        for row in accounting_rows
        if str(row.get("AccountingDocumentType") or "").upper()
        in P2P_PAYMENT_DOCUMENT_TYPES
        and bool(str(row.get("PaymentMethod") or "").strip())
        and bool(
            str(row.get("HouseBank") or row.get("HouseBankAccount") or "").strip()
        )
    ]

    stages = {
        "purchase_order": _presence_stage(order_rows),
        "items": _presence_stage(item_rows),
        "goods_receipt": {
            "state": "confirmed" if active_receipts > 0 else "not_confirmed",
            "receipt_evidence_count": len(receipt_rows),
            "reversal_evidence_count": len(reversal_rows),
            "net_active_receipt_count": active_receipts,
        },
        "supplier_invoice": _presence_stage(invoice_rows),
        "fi_clearing": {
            "state": "confirmed" if cleared_rows else "not_confirmed",
            "evidence_count": len(cleared_rows),
        },
        "payment": {
            "state": "confirmed" if payment_rows else "not_confirmed",
            "evidence_count": len(payment_rows),
            "required_document_types": sorted(P2P_PAYMENT_DOCUMENT_TYPES),
            "requires_payment_method_and_house_bank": True,
        },
    }
    source_complete = _source_complete(inputs)
    required = ["purchase_order", "items", "goods_receipt", "supplier_invoice"]
    core_confirmed = all(stages[name]["state"] == "confirmed" for name in required)
    business_complete = (
        core_confirmed
        and stages["fi_clearing"]["state"] == "confirmed"
        and stages["payment"]["state"] == "confirmed"
    )
    business_status = "complete" if business_complete else "partial"
    counts = {
        "purchase_orders": len(order_rows),
        "purchase_order_items": len(item_rows),
        "material_document_items": len(material_rows),
        "supplier_invoice_items": len(invoice_rows),
        "accounting_items": len(accounting_rows),
    }
    business_report = _p2p_business_report(
        stages=stages,
        counts=counts,
        receipt_count=len(receipt_rows),
        reversal_count=len(reversal_rows),
        active_receipt_count=active_receipts,
        clearing_count=len(cleared_rows),
        payment_count=len(payment_rows),
    )
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    company_code = _first_non_empty(order_rows + accounting_rows, "CompanyCode")
    supplier = _first_non_empty(order_rows + accounting_rows, "Supplier")
    return {
        "rule_id": "p2p_deterministic_status_v1",
        "status": "complete" if source_complete and business_complete else "inconclusive",
        "business_status": business_status,
        "score": round(
            100
            * sum(stage["state"] == "confirmed" for stage in stages.values())
            / len(stages)
        ),
        "source_complete": source_complete,
        "stages": stages,
        "counts": counts,
        "business_report": business_report,
        "reason": business_report["overview"],
        "summary": business_report["summary"],
        "evidence_refs": _case_ids(inputs),
        "workflow_output": {
            "purchase_order": str(run_input.get("purchase_order") or ""),
            "company_code": company_code,
            "supplier": supplier,
            "business_status": business_status,
            "source_complete": source_complete,
            "business_report": business_report,
        },
    }


def evaluate_o2c_status(inputs: dict[str, Any]) -> dict[str, Any]:
    steps = _step_results(inputs)
    order_rows = _step_rows(steps, "sales_order")
    item_rows = _step_rows(steps, "sales_order_items")
    delivery_rows = _step_rows(steps, "delivery_items")
    delivery_header_rows = _step_rows(steps, "delivery_headers")
    billing_rows = _rows_for_prefixes(steps, ("billing_items",))
    billing_header_rows = _rows_for_prefixes(steps, ("billing_headers",))
    accounting_rows = _rows_for_prefixes(
        steps,
        ("accounting_items", "accounting_by_", "clearing_documents"),
    )

    pgi_rows = [
        row
        for row in delivery_rows + delivery_header_rows
        if _completed_status(row.get("GoodsMovementStatus"))
        or _completed_status(row.get("OverallGoodsMovementStatus"))
        or bool(str(row.get("ActualGoodsMovementDate") or "").strip())
    ]
    cleared_rows = [
        row
        for row in accounting_rows
        if _is_true(row.get("IsCleared"))
        and bool(str(row.get("ClearingAccountingDocument") or "").strip())
    ]
    bank_rows = [
        row
        for row in accounting_rows
        if str(row.get("AccountingDocumentType") or "").upper()
        in O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES
        and bool(
            str(row.get("HouseBank") or row.get("HouseBankAccount") or "").strip()
        )
    ]
    stages = {
        "sales_order": _presence_stage(order_rows),
        "items": _presence_stage(item_rows),
        "delivery": _presence_stage(delivery_rows),
        "pgi": {"state": "confirmed" if pgi_rows else "not_confirmed", "evidence_count": len(pgi_rows)},
        "billing": {
            "state": "confirmed" if billing_rows else "not_confirmed",
            "item_evidence_count": len(billing_rows),
            "header_evidence_count": len(billing_header_rows),
        },
        "ar_clearing": {
            "state": "confirmed" if cleared_rows else "not_confirmed",
            "evidence_count": len(cleared_rows),
        },
        "bank_receipt": {
            "state": "confirmed" if bank_rows else "unknown",
            "evidence_count": len(bank_rows),
            "required_document_types": sorted(O2C_CUSTOMER_PAYMENT_DOCUMENT_TYPES),
            "requires_house_bank": True,
        },
    }
    source_complete = _source_complete(inputs)
    process_stages = ("sales_order", "items", "delivery", "pgi", "billing", "ar_clearing")
    process_complete = all(stages[name]["state"] == "confirmed" for name in process_stages)
    business_status = "complete_to_ar_clearing" if process_complete else "partial"
    counts = {
        "sales_orders": len(order_rows),
        "sales_order_items": len(item_rows),
        "delivery_items": len(delivery_rows),
        "billing_items": len(billing_rows),
        "billing_headers": len(billing_header_rows),
        "accounting_items": len(accounting_rows),
    }
    business_report = _o2c_business_report(
        stages=stages,
        counts=counts,
        pgi_count=len(pgi_rows),
        clearing_count=len(cleared_rows),
        bank_count=len(bank_rows),
    )
    run_input = inputs.get("run_input") if isinstance(inputs.get("run_input"), dict) else {}
    company_code = _first_non_empty(accounting_rows, "CompanyCode")
    customer = _first_non_empty(accounting_rows, "Customer") or _first_non_empty(
        order_rows, "SoldToParty", "Customer"
    )
    return {
        "rule_id": "o2c_deterministic_status_v1",
        "status": "complete" if source_complete and process_complete else "inconclusive",
        "business_status": business_status,
        "score": round(
            100
            * sum(stages[name]["state"] == "confirmed" for name in process_stages)
            / len(process_stages)
        ),
        "source_complete": source_complete,
        "stages": stages,
        "counts": counts,
        "business_report": business_report,
        "reason": business_report["overview"],
        "summary": business_report["summary"],
        "evidence_refs": _case_ids(inputs),
        "workflow_output": {
            "sales_order": str(run_input.get("sales_order") or ""),
            "company_code": company_code,
            "customer": customer,
            "business_status": business_status,
            "source_complete": source_complete,
            "business_report": business_report,
        },
    }


def _p2p_business_report(
    *,
    stages: dict[str, dict[str, Any]],
    counts: dict[str, int],
    receipt_count: int,
    reversal_count: int,
    active_receipt_count: int,
    clearing_count: int,
    payment_count: int,
) -> dict[str, Any]:
    first_gap = next(
        (
            stage_id
            for stage_id in (
                "purchase_order",
                "items",
                "goods_receipt",
                "supplier_invoice",
                "fi_clearing",
                "payment",
            )
            if stages[stage_id]["state"] != "confirmed"
        ),
        None,
    )
    headlines = {
        None: {
            "zh": "采购、收货、发票和付款证据均已确认",
            "en": "Procurement, receipt, invoice, and payment evidence are confirmed",
        },
        "purchase_order": {
            "zh": "未找到这张采购订单",
            "en": "The purchase order was not found",
        },
        "items": {
            "zh": "已找到采购订单，但未找到订单项目",
            "en": "The purchase order was found, but no items were returned",
        },
        "goods_receipt": {
            "zh": "采购订单已创建，尚未找到有效收货记录",
            "en": "The purchase order exists, but no active goods receipt was found",
        },
        "supplier_invoice": {
            "zh": "已找到采购订单和收货记录，尚未找到供应商发票",
            "en": "The purchase order and receipt were found, but no supplier invoice was found",
        },
        "fi_clearing": {
            "zh": "已找到供应商发票，尚未确认财务清账",
            "en": "The supplier invoice was found, but FI clearing is not confirmed",
        },
        "payment": {
            "zh": "已确认财务清账，尚未确认付款",
            "en": "FI clearing is confirmed, but payment is not confirmed",
        },
    }
    overviews = {
        None: {
            "zh": "系统在当前只读查询范围内找到了完整的采购到付款证据链。",
            "en": "The complete procure-to-pay evidence chain was found within the current read-only query scope.",
        },
        "purchase_order": {
            "zh": "当前查询范围内没有返回该采购订单，请先核对订单号和访问权限。",
            "en": "The order was not returned in the current query scope. Check the order number and access rights.",
        },
        "items": {
            "zh": "订单抬头已经返回，但没有可用于继续核验的采购订单项目。",
            "en": "The order header was returned, but no purchase-order items were available for downstream checks.",
        },
        "goods_receipt": {
            "zh": "系统没有找到未被冲销的收货记录，因此不能确认货物已经入库。",
            "en": "No non-reversed goods-receipt record was found, so receipt cannot be confirmed.",
        },
        "supplier_invoice": {
            "zh": "系统确认订单已产生收货记录，但没有找到引用该订单的供应商发票，因此无法继续确认清账和付款。",
            "en": "Receipt evidence exists, but no supplier invoice referencing the order was found, so clearing and payment cannot be confirmed.",
        },
        "fi_clearing": {
            "zh": "发票证据已经返回，但没有找到满足条件的清账记录。付款状态仍不能确认。",
            "en": "Invoice evidence was returned, but no qualifying clearing record was found. Payment remains unconfirmed.",
        },
        "payment": {
            "zh": "清账证据已经返回，但没有同时满足付款凭证类型、付款方式和开户行条件的付款证据。",
            "en": "Clearing evidence was returned, but no payment evidence met the document-type, payment-method, and house-bank requirements.",
        },
    }
    stage_rows = [
        _business_stage(
            "purchase_order",
            "采购订单",
            "Purchase order",
            stages["purchase_order"]["state"],
            f"找到 {counts['purchase_orders']} 张采购订单。" if counts["purchase_orders"] else "未找到采购订单。",
            f"Found {counts['purchase_orders']} purchase order(s)." if counts["purchase_orders"] else "No purchase order was found.",
        ),
        _business_stage(
            "items",
            "订单项目",
            "Order items",
            stages["items"]["state"],
            f"找到 {counts['purchase_order_items']} 个采购订单项目。" if counts["purchase_order_items"] else "未找到采购订单项目。",
            f"Found {counts['purchase_order_items']} purchase-order item(s)." if counts["purchase_order_items"] else "No purchase-order items were found.",
        ),
        _business_stage(
            "goods_receipt",
            "收货",
            "Goods receipt",
            stages["goods_receipt"]["state"],
            (
                f"找到 {active_receipt_count} 条当前有效的收货记录，另有 {reversal_count} 条冲销记录。"
                "这里表示存在收货记录，不代表采购数量已经全部收齐。"
                if active_receipt_count
                else f"查询了 {receipt_count} 条收货和 {reversal_count} 条冲销记录，未确认当前有效收货。"
            ),
            (
                f"Found {active_receipt_count} active receipt record(s) and {reversal_count} reversal record(s). "
                "This confirms receipt evidence exists; it does not prove the full ordered quantity was received."
                if active_receipt_count
                else f"Checked {receipt_count} receipt and {reversal_count} reversal record(s); no active receipt was confirmed."
            ),
        ),
        _business_stage(
            "supplier_invoice",
            "供应商发票",
            "Supplier invoice",
            stages["supplier_invoice"]["state"],
            f"找到 {counts['supplier_invoice_items']} 条关联发票项目。" if counts["supplier_invoice_items"] else "未找到引用该采购订单的供应商发票项目。",
            f"Found {counts['supplier_invoice_items']} linked supplier-invoice item(s)." if counts["supplier_invoice_items"] else "No supplier-invoice item referencing the purchase order was found.",
        ),
        _business_stage(
            "fi_clearing",
            "财务清账",
            "FI clearing",
            stages["fi_clearing"]["state"],
            (
                f"找到 {clearing_count} 条已清账证据。"
                if clearing_count
                else f"找到 {counts['accounting_items']} 条财务记录，但未找到满足条件的清账证据。"
            ),
            (
                f"Found {clearing_count} qualifying cleared record(s)."
                if clearing_count
                else f"Found {counts['accounting_items']} FI record(s), but no qualifying clearing evidence."
            ),
        ),
        _business_stage(
            "payment",
            "付款",
            "Payment",
            stages["payment"]["state"],
            (
                f"找到 {payment_count} 条同时具备付款凭证类型、付款方式和开户行的付款证据。"
                if payment_count
                else "未找到同时具备付款凭证类型、付款方式和开户行的付款证据。清账编号本身不能证明已经付款。"
            ),
            (
                f"Found {payment_count} payment record(s) with a qualifying document type, payment method, and house bank."
                if payment_count
                else "No payment record had a qualifying document type, payment method, and house bank. A clearing reference alone does not prove payment."
            ),
        ),
    ]
    actions_zh: list[str] = []
    actions_en: list[str] = []
    if first_gap in {"purchase_order", "items"}:
        actions_zh.append("核对采购订单号、订单是否已保存，以及当前用户是否有权读取该采购组织的数据。")
        actions_en.append("Check the purchase-order number, whether the order was saved, and access to the purchasing-organization data.")
    elif first_gap == "goods_receipt":
        actions_zh.append("确认仓库是否已经过账收货；如已收货，核对物料凭证是否引用该采购订单项目。")
        actions_en.append("Confirm whether goods receipt was posted and whether the material document references the purchase-order item.")
    elif first_gap == "supplier_invoice":
        actions_zh.extend(
            [
                "确认供应商发票是否已通过 MIRO 正式过账，而不是仍处于暂存或待处理状态。",
                "如业务上已经收到发票，核对发票项目是否正确引用这张采购订单。",
            ]
        )
        actions_en.extend(
            [
                "Confirm whether the supplier invoice was posted in MIRO rather than remaining parked or pending.",
                "If the invoice is expected, verify that its item correctly references this purchase order.",
            ]
        )
    elif first_gap == "fi_clearing":
        actions_zh.append("请应付会计检查供应商未清项、付款冻结、到期日以及付款运行状态。")
        actions_en.append("Ask Accounts Payable to review the supplier open item, payment block, due date, and payment-run status.")
    elif first_gap == "payment":
        actions_zh.append("核对付款运行是否生成了允许的付款凭证类型，并确认付款方式和开户行信息已经写入凭证。")
        actions_en.append("Check whether the payment run created an allowed payment document type with payment method and house-bank information.")
    else:
        actions_zh.append("如需审计，可下载业务报告和阶段明细留档。")
        actions_en.append("For audit purposes, download the business report and stage details.")
    headline = headlines[first_gap]
    overview = overviews[first_gap]
    return {
        "tone": "success" if first_gap is None else "warning",
        "headline": headline,
        "overview": overview,
        "summary": {
            "zh": f"{headline['zh']}。{overview['zh']}",
            "en": f"{headline['en']}. {overview['en']}",
        },
        "stages": stage_rows,
        "next_actions": {"zh": actions_zh, "en": actions_en},
    }


def _o2c_business_report(
    *,
    stages: dict[str, dict[str, Any]],
    counts: dict[str, int],
    pgi_count: int,
    clearing_count: int,
    bank_count: int,
) -> dict[str, Any]:
    process_order = ("sales_order", "items", "delivery", "pgi", "billing", "ar_clearing")
    first_gap = next(
        (stage_id for stage_id in process_order if stages[stage_id]["state"] != "confirmed"),
        None,
    )
    if first_gap is None and stages["bank_receipt"]["state"] != "confirmed":
        headline = {
            "zh": "订单已完成至应收清账，银行到账仍需单独确认",
            "en": "The order is complete through AR clearing; bank receipt still needs confirmation",
        }
        overview = {
            "zh": "销售订单、交货、发货过账、开票和应收清账均已找到证据；财务清账本身不能证明款项已经到达银行。",
            "en": "Evidence was found for order, delivery, PGI, billing, and AR clearing; FI clearing alone does not prove bank receipt.",
        }
        tone = "info"
    elif first_gap is None:
        headline = {
            "zh": "订单、交付、开票、清账和银行到账均已确认",
            "en": "Order, fulfillment, billing, clearing, and bank receipt are confirmed",
        }
        overview = {
            "zh": "系统在当前只读查询范围内找到了完整的订单到收款证据链。",
            "en": "The complete order-to-cash evidence chain was found within the current read-only query scope.",
        }
        tone = "success"
    else:
        gap_copy = {
            "sales_order": ("未找到这张销售订单", "The sales order was not found"),
            "items": ("已找到销售订单，但未找到订单项目", "The sales order was found, but no items were returned"),
            "delivery": ("销售订单已创建，尚未找到交货单", "The sales order exists, but no delivery was found"),
            "pgi": ("已找到交货单，尚未确认发货过账", "The delivery was found, but PGI is not confirmed"),
            "billing": ("交货已经发生，尚未找到开票凭证", "Delivery evidence exists, but no billing document was found"),
            "ar_clearing": ("已找到开票凭证，尚未确认应收清账", "The billing document was found, but AR clearing is not confirmed"),
        }
        zh, en = gap_copy[first_gap]
        headline = {"zh": zh, "en": en}
        overview = {
            "zh": "流程在上述阶段之后缺少可确认的业务证据，请根据阶段明细继续处理。",
            "en": "Confirmable business evidence is missing after this stage. Use the stage details to continue processing.",
        }
        tone = "warning"
    stage_rows = [
        _business_stage("sales_order", "销售订单", "Sales order", stages["sales_order"]["state"], f"找到 {counts['sales_orders']} 张销售订单。" if counts["sales_orders"] else "未找到销售订单。", f"Found {counts['sales_orders']} sales order(s)." if counts["sales_orders"] else "No sales order was found."),
        _business_stage("items", "订单项目", "Order items", stages["items"]["state"], f"找到 {counts['sales_order_items']} 个销售订单项目。" if counts["sales_order_items"] else "未找到销售订单项目。", f"Found {counts['sales_order_items']} sales-order item(s)." if counts["sales_order_items"] else "No sales-order items were found."),
        _business_stage("delivery", "交货", "Delivery", stages["delivery"]["state"], f"找到 {counts['delivery_items']} 条交货项目记录。" if counts["delivery_items"] else "未找到关联交货项目。", f"Found {counts['delivery_items']} delivery item record(s)." if counts["delivery_items"] else "No linked delivery item was found."),
        _business_stage("pgi", "发货过账", "Post goods issue", stages["pgi"]["state"], f"找到 {pgi_count} 条已完成发货过账的证据。" if pgi_count else "未找到已完成发货过账的证据。", f"Found {pgi_count} completed PGI record(s)." if pgi_count else "No completed PGI evidence was found."),
        _business_stage("billing", "开票", "Billing", stages["billing"]["state"], f"找到 {counts['billing_items']} 条开票项目和 {counts['billing_headers']} 张开票抬头。" if counts["billing_items"] else "未找到关联开票凭证。", f"Found {counts['billing_items']} billing item(s) and {counts['billing_headers']} billing header(s)." if counts["billing_items"] else "No linked billing document was found."),
        _business_stage("ar_clearing", "应收清账", "AR clearing", stages["ar_clearing"]["state"], f"找到 {clearing_count} 条应收清账证据。" if clearing_count else "未找到应收清账证据。", f"Found {clearing_count} AR-clearing record(s)." if clearing_count else "No AR-clearing evidence was found."),
        _business_stage("bank_receipt", "银行到账", "Bank receipt", stages["bank_receipt"]["state"], f"找到 {bank_count} 条带开户行信息的客户收款证据。" if bank_count else "尚未找到带开户行信息的客户收款证据；FI 清账不能单独证明银行到账。", f"Found {bank_count} customer-payment record(s) with house-bank information." if bank_count else "No customer-payment evidence with house-bank information was found; FI clearing alone does not prove bank receipt."),
    ]
    actions_zh = ["根据第一个未确认阶段，核对对应业务凭证是否已经正式过账并正确引用上游单据。"]
    actions_en = ["For the first unconfirmed stage, verify that the business document was posted and correctly references its upstream document."]
    if first_gap is None and not bank_count:
        actions_zh = ["如需确认实际到账，请继续核对银行对账单或带开户行信息的客户收款凭证。"]
        actions_en = ["To confirm actual receipt, check the bank statement or a customer-payment document containing house-bank information."]
    elif first_gap is None:
        actions_zh = ["如需审计，可下载业务报告和阶段明细留档。"]
        actions_en = ["For audit purposes, download the business report and stage details."]
    return {
        "tone": tone,
        "headline": headline,
        "overview": overview,
        "summary": {
            "zh": f"{headline['zh']}。{overview['zh']}",
            "en": f"{headline['en']}. {overview['en']}",
        },
        "stages": stage_rows,
        "next_actions": {"zh": actions_zh, "en": actions_en},
    }


def _business_stage(
    stage_id: str,
    label_zh: str,
    label_en: str,
    state: str,
    detail_zh: str,
    detail_en: str,
) -> dict[str, Any]:
    state_labels = {
        "confirmed": {"zh": "已确认", "en": "Confirmed"},
        "not_found": {"zh": "未找到", "en": "Not found"},
        "not_confirmed": {"zh": "未确认", "en": "Not confirmed"},
        "unknown": {"zh": "尚不明确", "en": "Unknown"},
    }
    return {
        "id": stage_id,
        "label": {"zh": label_zh, "en": label_en},
        "state": state,
        "state_label": state_labels.get(state, {"zh": state, "en": state}),
        "detail": {"zh": detail_zh, "en": detail_en},
    }


def _step_results(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        step_results = value.get("step_results")
        if isinstance(step_results, dict):
            return {
                str(key): child
                for key, child in step_results.items()
                if isinstance(child, dict)
            }
        for child in value.values():
            found = _step_results(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _step_results(child)
            if found:
                return found
    return {}


def _step_rows(steps: dict[str, dict[str, Any]], step_id: str) -> list[dict[str, Any]]:
    step = steps.get(step_id) or {}
    return [dict(row) for row in (step.get("results") or []) if isinstance(row, dict)]


def _rows_for_prefixes(
    steps: dict[str, dict[str, Any]], prefixes: tuple[str, ...]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step_id, step in steps.items():
        if any(step_id.startswith(prefix) for prefix in prefixes):
            rows.extend(
                dict(row) for row in (step.get("results") or []) if isinstance(row, dict)
            )
    return rows


def _presence_stage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"state": "confirmed" if rows else "not_found", "evidence_count": len(rows)}


def _first_non_empty(rows: list[dict[str, Any]], *fields: str) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def _movement_kind(row: dict[str, Any]) -> str:
    if _is_true(row.get("GoodsMovementIsCancelled")) or bool(
        str(row.get("ReversedMaterialDocument") or "").strip()
    ):
        return "reversal"
    movement = str(row.get("GoodsMovementType") or "").strip()
    if movement in {"102", "106", "122", "124", "162"}:
        return "reversal"
    if movement in {"101", "103", "105", "107", "109", "121", "123", "161"}:
        return "receipt"
    return "other"


def _completed_status(value: Any) -> bool:
    return str(value or "").strip().upper() in {"C", "COMPLETE", "COMPLETED"}


def _is_true(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"true", "x", "1", "yes"}


def _source_complete(value: Any) -> bool:
    flags = _collect_source_complete(value)
    return bool(flags) and all(flags)


def _case_ids(value: Any) -> list[str]:
    return list(dict.fromkeys(str(item) for item in _collect_values(value, "case_id") if item))


def _collect_source_complete(value: Any) -> list[bool]:
    flags: list[bool] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_complete" and isinstance(child, bool):
                flags.append(child)
            else:
                flags.extend(_collect_source_complete(child))
    elif isinstance(value, list):
        for child in value:
            flags.extend(_collect_source_complete(child))
    return flags


def _collect_values(value: Any, target_key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == target_key:
                values.append(child)
            values.extend(_collect_values(child, target_key))
    elif isinstance(value, list):
        for child in value:
            values.extend(_collect_values(child, target_key))
    return values
