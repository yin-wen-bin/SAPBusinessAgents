from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def text(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def string_input(
    zh: str,
    en: str,
    *,
    placeholder_zh: str,
    placeholder_en: str,
    pattern: str | None = None,
    maximum: int = 40,
    date_value: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "string",
        "title": text(zh, en),
        "description": text(
            f"请输入{zh}。",
            f"Enter the {en.lower()}.",
        ),
        "placeholder": text(placeholder_zh, placeholder_en),
        "minLength": 1,
        "maxLength": maximum,
    }
    if pattern:
        result["pattern"] = pattern
    if date_value:
        result["format"] = "date"
        result["pattern"] = "^\\d{4}-\\d{2}-\\d{2}$"
        result["maxLength"] = 10
    return result


DOC = r"^[0-9A-Za-z_-]+$"
DIGITS = r"^[0-9]+$"


def schema(
    properties: dict[str, dict[str, Any]],
    *,
    ranges: list[tuple[str, str, int]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if ranges:
        result["dateRangePairs"] = [
            {"from": start, "to": end, "maxDays": maximum}
            for start, end, maximum in ranges
        ]
    return result


def filt(field: str, value: str, *, op: str = "eq", kind: str = "string") -> dict[str, Any]:
    return {"field": field, "operator": op, "value": value, "value_type": kind}


def bind(field: str, source: str, source_field: str) -> dict[str, Any]:
    return {
        "field": field,
        "source_step_id": source,
        "source_field": source_field,
        "fanout": True,
        "fetch_all_for_binding": True,
    }


def query(
    step_id: str,
    service: str,
    entity: str,
    *,
    fields: list[str],
    filters: list[dict[str, Any]] | None = None,
    bindings: list[dict[str, Any]] | None = None,
    order_by: list[str] | None = None,
    rationale: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "step_id": step_id,
        "service_name": service,
        "odata_version": "2.0",
        "entity_set": entity,
        "http_method": "GET",
        "select_fields": fields,
        "rationale": rationale,
    }
    if filters:
        result["filters"] = filters
    if bindings:
        result["filter_from_previous"] = bindings
    if order_by:
        result["order_by"] = order_by
    return result


def sap_step(
    step_id: str,
    queries: list[dict[str, Any]],
    *,
    rationale: str,
    record_gap: bool = False,
) -> dict[str, Any]:
    first = queries[0]
    result: dict[str, Any] = {
        "id": step_id,
        "executor": "sap_read",
        "operation": "execute_plan",
        "readOnly": True,
        "request": {
            "plan": {
                "service_name": first["service_name"],
                "odata_version": first["odata_version"],
                "entity_set": first["entity_set"],
                "http_method": "GET",
                "plan_kind": "multi_step" if len(queries) > 1 else "direct",
                "step_id": first["step_id"] if len(queries) == 1 else None,
                "steps": queries if len(queries) > 1 else None,
                "select_fields": first.get("select_fields") if len(queries) == 1 else None,
                "filters": first.get("filters") if len(queries) == 1 else None,
                "filter_from_previous": first.get("filter_from_previous") if len(queries) == 1 else None,
                "order_by": first.get("order_by") if len(queries) == 1 else None,
                "rationale": rationale,
            }
        },
    }
    plan = result["request"]["plan"]
    for key in ["step_id", "steps", "select_fields", "filters", "filter_from_previous", "order_by"]:
        if plan.get(key) is None:
            plan.pop(key, None)
    if record_gap:
        result["failurePolicy"] = "record_gap"
    return result


def execution(
    agent_id: str,
    input_schema: dict[str, Any],
    sap_steps: list[dict[str, Any]],
    *,
    known_gaps: list[str] | None = None,
) -> dict[str, Any]:
    evidence = {}
    for step in sap_steps:
        plan = ((step.get("request") or {}).get("plan") or {})
        evidence_name = str(plan.get("step_id") or step["id"])
        evidence[evidence_name] = f"{{{{steps.{step['id']}.output}}}}"
    return {
        "mode": "deterministic",
        "timeoutSeconds": 300,
        "inputSchema": input_schema,
        "steps": sap_steps
        + [
            {
                "id": "evaluate_business_result",
                "executor": "rule",
                "operation": "evaluate_business_agent",
                "inputMapping": {
                    "agent_id": agent_id,
                    "run_input": "{{input}}",
                    "evidence": evidence,
                    "known_gaps": known_gaps or [],
                },
            }
        ],
    }


def exact_sales_order_plan(include_fi: bool = False) -> list[dict[str, Any]]:
    steps = [
        query("sales_orders", "API_SALES_ORDER_SRV", "A_SalesOrder", fields=["SalesOrder", "SalesOrganization", "RequestedDeliveryDate", "TotalBlockStatus", "TotalCreditCheckStatus", "HeaderBillingBlockReason", "DeliveryBlockReason", "OverallDeliveryStatus", "OverallOrdReltdBillgStatus"], filters=[filt("SalesOrder", "{{input.sales_order}}")], rationale="Load the requested sales order."),
        query("sales_order_items", "API_SALES_ORDER_SRV", "A_SalesOrderItem", fields=["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "ConfdDelivQtyInOrderQtyUnit", "DeliveryPriority", "ItemBillingBlockReason", "DeliveryStatus"], filters=[filt("SalesOrder", "{{input.sales_order}}")], rationale="Load sales-order items."),
        query("delivery_items", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", fields=["DeliveryDocument", "DeliveryDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "Plant", "Material", "ActualDeliveryQuantity", "GoodsMovementStatus", "DeliveryRelatedBillingStatus", "ItemBillingBlockReason"], filters=[filt("ReferenceSDDocument", "{{input.sales_order}}")], rationale="Load deliveries referencing the sales order."),
        query("delivery_headers", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryHeader", fields=["DeliveryDocument", "ActualGoodsMovementDate", "OverallGoodsMovementStatus", "OverallDelivReltdBillgStatus", "DeliveryBlockReason", "HeaderBillingBlockReason", "TotalCreditCheckStatus"], bindings=[bind("DeliveryDocument", "delivery_items", "DeliveryDocument")], rationale="Load delivery header status."),
        query("billing_items", "API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", fields=["BillingDocument", "BillingDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "SalesDocument", "SalesDocumentItem", "BillingQuantity", "NetAmount", "TransactionCurrency"], filters=[filt("SalesDocument", "{{input.sales_order}}")], rationale="Load billing items associated with the sales order."),
        query("billing_headers", "API_BILLING_DOCUMENT_SRV", "A_BillingDocument", fields=["BillingDocument", "BillingDocumentDate", "BillingDocumentIsCancelled", "AccountingPostingStatus", "OverallBillingStatus", "TotalNetAmount", "TransactionCurrency"], bindings=[bind("BillingDocument", "billing_items", "BillingDocument")], rationale="Load billing headers."),
    ]
    if include_fi:
        steps.append(query("accounting_items", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem", "SalesDocument", "BillingDocument", "Customer", "FinancialAccountType", "PostingDate", "NetDueDate", "IsCleared", "ClearingAccountingDocument", "ClearingDocFiscalYear", "AccountingDocumentType", "AmountInTransactionCurrency", "TransactionCurrency"], filters=[filt("SalesDocument", "{{input.sales_order}}")], rationale="Load receivable and clearing evidence."))
    return steps


def specs() -> dict[str, dict[str, Any]]:
    sales_order_input = schema({"sales_order": string_input("销售订单号", "Sales order", placeholder_zh="例如：5814", placeholder_en="Example: 5814", pattern=DIGITS, maximum=10)})
    billing_input = schema({"billing_document": string_input("开票凭证号", "Billing document", placeholder_zh="例如：90000025", placeholder_en="Example: 90000025", pattern=DIGITS, maximum=10)})
    organization_range = schema(
        {
            "sales_organization": string_input("销售组织", "Sales organization", placeholder_zh="例如：1710", placeholder_en="Example: 1710", pattern=DOC, maximum=4),
            "date_from": string_input("开始日期", "Start date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
            "date_to": string_input("结束日期", "End date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
        }, ranges=[("date_from", "date_to", 31)]
    )

    fi_fields = ["CompanyCode", "FiscalYear", "FiscalPeriod", "Ledger", "AccountingDocument", "AccountingDocumentItem", "Supplier", "Customer", "FinancialAccountType", "GLAccount", "OperationalGLAccount", "PurchasingDocument", "PurchasingDocumentItem", "PostingDate", "NetDueDate", "ClearingDate", "ClearingIsReversed", "IsOpenItemManaged", "DebitCreditCode", "IsCleared", "ClearingAccountingDocument", "ClearingDocFiscalYear", "PaymentMethod", "PaymentBlockingReason", "HouseBank", "HouseBankAccount", "AccountingDocumentType", "AmountInTransactionCurrency", "TransactionCurrency", "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "OrderID"]
    result: dict[str, dict[str, Any]] = {}

    ap_inputs = schema({
        "company_code": string_input("公司代码", "Company code", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=4),
        "supplier": string_input("供应商编号", "Supplier", placeholder_zh="例如：1000123", placeholder_en="Example: 1000123", pattern=DOC, maximum=10),
        "as_of": string_input("查询基准日", "As-of date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
    })
    ap_plan = [query("supplier_items", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=fi_fields, filters=[filt("CompanyCode", "{{input.company_code}}"), filt("Supplier", "{{input.supplier}}"), filt("FinancialAccountType", "K"), filt("PostingDate", "{{input.as_of}}", op="le", kind="date_end")], order_by=["FiscalYear", "AccountingDocument", "AccountingDocumentItem", "Ledger"], rationale="Load supplier open, cleared, due, and payment evidence through the cutoff.")]
    result["ap-payment"] = execution("ap-payment", ap_inputs, [sap_step("collect_ap_evidence", ap_plan, rationale="Collect supplier payment evidence.")], known_gaps=["bank_settlement_not_proven", "payment_run_and_bank_master_evidence"])

    ar_inputs = schema({
        "company_code": string_input("公司代码", "Company code", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=4),
        "customer": string_input("客户编号", "Customer", placeholder_zh="例如：17100001", placeholder_en="Example: 17100001", pattern=DOC, maximum=10),
        "as_of": string_input("查询基准日", "As-of date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
    })
    ar_plan = [
        query(
            "customer_items",
            "API_OPLACCTGDOCITEMCUBE_SRV",
            "A_OperationalAcctgDocItemCube",
            fields=[
                *fi_fields,
                "DunningArea",
                "DunningKey",
                "DunningLevel",
                "DunningBlockingReason",
                "LastDunningDate",
                "PaymentTerms",
            ],
            filters=[
                filt("CompanyCode", "{{input.company_code}}"),
                filt("Customer", "{{input.customer}}"),
                filt("FinancialAccountType", "D"),
                filt("PostingDate", "{{input.as_of}}", op="le", kind="date_end"),
            ],
            order_by=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"],
            rationale="Load customer line-item and item-level dunning evidence through the cutoff.",
        ),
        query(
            "customer_dunning",
            "API_BUSINESS_PARTNER",
            "A_CustomerDunning",
            fields=["Customer", "CompanyCode", "DunningArea", "DunningProcedure", "DunningLevel", "DunningBlock", "DunningRecipient", "LastDunnedOn", "LegDunningProcedureOn", "DunningClerk"],
            filters=[filt("CompanyCode", "{{input.company_code}}"), filt("Customer", "{{input.customer}}")],
            order_by=["Customer", "CompanyCode", "DunningArea"],
            rationale="Load the current customer dunning master as a separate, explicitly non-historical source.",
        ),
    ]
    result["ar-collection"] = execution(
        "ar-collection",
        ar_inputs,
        [sap_step("collect_ar_evidence", ar_plan, rationale="Collect customer receivable and dunning evidence.")],
    )

    grir_inputs = schema({
        "company_code": string_input("公司代码", "Company code", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=4),
        "gl_account": string_input("GR/IR 总账科目", "GR/IR G/L account", placeholder_zh="例如：0021100000", placeholder_en="Example: 0021100000", pattern=DIGITS, maximum=10),
        "date_from": string_input("开始日期", "Start date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
        "date_to": string_input("截止日期", "End date", placeholder_zh="YYYY-MM-DD", placeholder_en="YYYY-MM-DD", date_value=True),
    }, ranges=[("date_from", "date_to", 366)])
    grir_plan = [
        query("gl_items", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem", "GLAccount", "PurchasingDocument", "PurchasingDocumentItem", "PostingDate", "AmountInCompanyCodeCurrency", "CompanyCodeCurrency", "DebitCreditCode"], filters=[filt("CompanyCode", "{{input.company_code}}"), filt("GLAccount", "{{input.gl_account}}"), filt("PostingDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("PostingDate", "{{input.date_to}}", op="le", kind="date_end")], order_by=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"], rationale="Load scoped GR/IR G/L line items from the operational accounting cube with executable live stable keys."),
        query("purchase_order_items", "API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem", fields=["PurchaseOrder", "PurchaseOrderItem", "Material", "Plant", "OrderQuantity", "PurchaseOrderQuantityUnit", "OrderPriceUnit", "DocumentCurrency"], bindings=[bind("PurchaseOrder", "gl_items", "PurchasingDocument")], rationale="Load purchase-order items referenced by GR/IR lines."),
        query("material_documents", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", fields=["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "PurchaseOrder", "PurchaseOrderItem", "Material", "Plant", "QuantityInBaseUnit", "DebitCreditCode", "ReversedMaterialDocument"], bindings=[bind("PurchaseOrder", "gl_items", "PurchasingDocument")], rationale="Load receipt and reversal evidence."),
        query("supplier_invoice_items", "API_SUPPLIERINVOICE_PROCESS_SRV", "A_SuplrInvcItemPurOrdRef", fields=["SupplierInvoice", "FiscalYear", "SupplierInvoiceItem", "PurchaseOrder", "PurchaseOrderItem", "QuantityInPurchaseOrderUnit", "SupplierInvoiceItemAmount", "DocumentCurrency"], bindings=[bind("PurchaseOrder", "gl_items", "PurchasingDocument")], rationale="Load supplier invoice references."),
    ]
    result["gr-ir-clearing"] = execution("gr-ir-clearing", grir_inputs, [sap_step("collect_grir_evidence", grir_plan, rationale="Collect GR/IR evidence by G/L scope.")])

    close_inputs = schema({
        "company_code": string_input("公司代码", "Company code", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=4),
        "fiscal_year": string_input("会计年度", "Fiscal year", placeholder_zh="例如：2026", placeholder_en="Example: 2026", pattern=DIGITS, maximum=4),
        "period": string_input("会计期间", "Fiscal period", placeholder_zh="例如：08", placeholder_en="Example: 08", pattern=DIGITS, maximum=3),
    })
    close_plan = [query("fi_period_items", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=fi_fields, filters=[filt("CompanyCode", "{{input.company_code}}"), filt("FiscalYear", "{{input.fiscal_year}}"), filt("FiscalPeriod", "{{input.period}}")], order_by=["PostingDate", "CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem"], rationale="Load scoped FI period evidence without performing closing actions.")]
    result["month-end-closing"] = execution("month-end-closing", close_inputs, [sap_step("collect_month_end_evidence", close_plan, rationale="Collect available month-end evidence.")], known_gaps=["period_control_asset_depreciation_and_specialized_closing_checks"])

    result["billing-block-diagnosis"] = execution(
        "billing-block-diagnosis",
        sales_order_input,
        [sap_step("collect_billing_block_evidence", exact_sales_order_plan(), rationale="Collect sales-order and delivery block evidence.")],
        known_gaps=["sales_order_item_incompletion_evidence"],
    )

    billing_plan = [
        query("billing_headers", "API_BILLING_DOCUMENT_SRV", "A_BillingDocument", fields=["BillingDocument", "SalesOrganization", "BillingDocumentDate", "BillingDocumentIsCancelled", "AccountingPostingStatus", "OverallBillingStatus", "TotalNetAmount", "TransactionCurrency", "TaxAmount"], filters=[filt("BillingDocument", "{{input.billing_document}}")], rationale="Load the requested billing header."),
        query("billing_items", "API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", fields=["BillingDocument", "BillingDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "SalesDocument", "SalesDocumentItem", "BillingQuantity", "NetAmount", "TransactionCurrency", "TaxAmount", "Plant", "Material"], filters=[filt("BillingDocument", "{{input.billing_document}}")], rationale="Load billing items and source references."),
        query("source_sales_items", "API_SALES_ORDER_SRV", "A_SalesOrderItem", fields=["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "NetAmount", "TransactionCurrency"], bindings=[bind("SalesOrder", "billing_items", "SalesDocument"), bind("SalesOrderItem", "billing_items", "SalesDocumentItem")], rationale="Load source sales-order items."),
        query("source_delivery_items", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", fields=["DeliveryDocument", "DeliveryDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "ActualDeliveryQuantity", "Plant", "Material"], bindings=[bind("DeliveryDocument", "billing_items", "ReferenceSDDocument"), bind("DeliveryDocumentItem", "billing_items", "ReferenceSDDocumentItem")], rationale="Load source delivery items when billing is delivery-related."),
    ]
    result["billing-completeness-check"] = execution("billing-completeness-check", billing_input, [sap_step("collect_billing_completeness_evidence", billing_plan, rationale="Collect billing and source-document evidence.")])
    base_billing = billing_plan[:2]
    result["billing-output-monitor"] = execution("billing-output-monitor", billing_input, [sap_step("collect_billing_output_base", base_billing, rationale="Collect the base billing document.")], known_gaps=["billing_output_status_evidence"])
    dispute_plan = base_billing + [query("accounting_items", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=fi_fields, filters=[filt("BillingDocument", "{{input.billing_document}}")], rationale="Load accounting evidence for the billing document.")]
    result["billing-dispute-classification"] = execution("billing-dispute-classification", billing_input, [sap_step("collect_dispute_base_evidence", dispute_plan, rationale="Collect billing and FI evidence.")], known_gaps=["billing_dispute_case_evidence"])

    delivery_monitor_plan = [
        query("delivery_headers", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryHeader", fields=["DeliveryDocument", "SalesOrganization", "ActualGoodsMovementDate", "OverallGoodsMovementStatus", "OverallDelivReltdBillgStatus", "DeliveryBlockReason", "HeaderBillingBlockReason", "PlannedGoodsIssueDate"], filters=[filt("SalesOrganization", "{{input.sales_organization}}"), filt("ActualGoodsMovementDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("ActualGoodsMovementDate", "{{input.date_to}}", op="le", kind="date_end")], rationale="Load deliveries in the selected PGI period."),
        query("delivery_items", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", fields=["DeliveryDocument", "DeliveryDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "Plant", "Material", "ActualDeliveryQuantity", "GoodsMovementStatus", "DeliveryRelatedBillingStatus"], bindings=[bind("DeliveryDocument", "delivery_headers", "DeliveryDocument")], rationale="Load items for selected deliveries."),
        query("billing_items", "API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", fields=["BillingDocument", "BillingDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "BillingQuantity", "NetAmount", "TransactionCurrency"], bindings=[bind("ReferenceSDDocument", "delivery_items", "DeliveryDocument")], rationale="Load billing items referencing selected deliveries."),
    ]
    result["delivered-not-billed"] = execution("delivered-not-billed", organization_range, [sap_step("collect_delivered_not_billed", delivery_monitor_plan, rationale="Collect delivered-not-billed evidence.")])

    demand_plan = [
        query("sales_orders", "API_SALES_ORDER_SRV", "A_SalesOrder", fields=["SalesOrder", "SalesOrganization", "RequestedDeliveryDate", "TotalBlockStatus", "TotalCreditCheckStatus", "DeliveryBlockReason"], filters=[filt("SalesOrganization", "{{input.sales_organization}}"), filt("RequestedDeliveryDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("RequestedDeliveryDate", "{{input.date_to}}", op="le", kind="date_end")], rationale="Load sales orders in the requested-delivery range."),
        query("sales_order_items", "API_SALES_ORDER_SRV", "A_SalesOrderItem", fields=["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "RequestedQuantityUnit", "OrderQuantityUnit", "ConfdDelivQtyInOrderQtyUnit", "DeliveryPriority", "DeliveryStatus"], bindings=[bind("SalesOrder", "sales_orders", "SalesOrder")], rationale="Load items for selected sales orders."),
        query("schedule_lines", "API_SALES_ORDER_SRV", "A_SalesOrderScheduleLine", fields=["SalesOrder", "SalesOrderItem", "ScheduleLine", "RequestedDeliveryDate", "ConfirmedDeliveryDate", "ScheduleLineOrderQuantity", "ConfdOrderQtyByMatlAvailCheck", "DeliveredQtyInOrderQtyUnit", "DelivBlockReasonForSchedLine"], bindings=[bind("SalesOrder", "sales_order_items", "SalesOrder"), bind("SalesOrderItem", "sales_order_items", "SalesOrderItem")], rationale="Load schedule-line commitment evidence."),
        query("delivery_items", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", fields=["DeliveryDocument", "DeliveryDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "ActualDeliveryQuantity", "GoodsMovementStatus"], bindings=[bind("ReferenceSDDocument", "sales_orders", "SalesOrder")], rationale="Load deliveries that actually reference the selected sales orders; the rule joins item keys from the returned evidence."),
        query("delivery_headers", "API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryHeader", fields=["DeliveryDocument", "SalesOrganization", "PlannedGoodsIssueDate", "ActualGoodsMovementDate", "OverallGoodsMovementStatus", "DeliveryBlockReason", "TotalCreditCheckStatus"], bindings=[bind("DeliveryDocument", "delivery_items", "DeliveryDocument")], rationale="Load execution dates for deliveries linked by stable document references."),
    ]
    result["delivery-delay-prediction"] = execution("delivery-delay-prediction", organization_range, [sap_step("collect_delivery_delay_evidence", demand_plan, rationale="Collect deterministic delivery-risk evidence.")])

    due_inputs = schema({
        "sales_organization": organization_range["properties"]["sales_organization"],
        "plant": string_input("工厂", "Plant", placeholder_zh="例如：1710", placeholder_en="Example: 1710", pattern=DOC, maximum=4),
        "date_from": organization_range["properties"]["date_from"],
        "date_to": organization_range["properties"]["date_to"],
    }, ranges=[("date_from", "date_to", 31)])
    due_plan = demand_plan[:3] + [query("material_stock", "API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod", fields=["Material", "Plant", "StorageLocation", "MaterialBaseUnit", "MatlWrhsStkQtyInMatlBaseUnit", "InventoryStockType"], filters=[filt("Plant", "{{input.plant}}")], rationale="Load plant stock used as supporting prioritization evidence.")]
    result["due-delivery-prioritization"] = execution("due-delivery-prioritization", due_inputs, [sap_step("collect_due_priority_evidence", due_plan, rationale="Collect due-delivery priority evidence.")])

    anomaly_plan = exact_sales_order_plan(include_fi=True)
    anomaly_plan[0]["filters"] = [filt("SalesOrganization", "{{input.sales_organization}}"), filt("SalesOrderDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("SalesOrderDate", "{{input.date_to}}", op="le", kind="date_end")]
    anomaly_plan[1].pop("filters", None); anomaly_plan[1]["filter_from_previous"] = [bind("SalesOrder", "sales_orders", "SalesOrder")]
    anomaly_plan[2].pop("filters", None); anomaly_plan[2]["filter_from_previous"] = [bind("ReferenceSDDocument", "sales_orders", "SalesOrder")]
    anomaly_plan[4].pop("filters", None); anomaly_plan[4]["filter_from_previous"] = [bind("SalesDocument", "sales_orders", "SalesOrder")]
    anomaly_plan[6].pop("filters", None); anomaly_plan[6]["filter_from_previous"] = [bind("SalesDocument", "sales_orders", "SalesOrder")]
    anomaly_plan.append(query("accounting_items_by_billing", "API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", fields=["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem", "SalesDocument", "BillingDocument", "Customer", "FinancialAccountType", "PostingDate", "NetDueDate", "IsCleared", "ClearingAccountingDocument", "ClearingDocFiscalYear", "AccountingDocumentType", "AmountInTransactionCurrency", "TransactionCurrency"], bindings=[bind("BillingDocument", "billing_items", "BillingDocument")], rationale="Load customer-subledger FI items linked through the follow-on billing documents."))
    result["order-to-cash-anomaly-monitor"] = execution("order-to-cash-anomaly-monitor", organization_range, [sap_step("collect_o2c_anomaly_evidence", anomaly_plan, rationale="Collect bounded O2C anomaly evidence.")], known_gaps=["billing_output_status_evidence", "billing_dispute_case_evidence"])

    return_plan = [
        query("returns", "API_CUSTOMER_RETURN_SRV", "A_CustomerReturn", fields=["CustomerReturn", "SalesOrganization", "CustomerReturnDate", "ReferenceSDDocument", "OverallSDProcessStatus"], filters=[filt("SalesOrganization", "{{input.sales_organization}}"), filt("CustomerReturnDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("CustomerReturnDate", "{{input.date_to}}", op="le", kind="date_end")], rationale="Load customer returns in scope."),
        query("return_items", "API_CUSTOMER_RETURN_SRV", "A_CustomerReturnItem", fields=["CustomerReturn", "CustomerReturnItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "ReturnReason", "ReturnsMaterialHasBeenReceived", "ReturnsRefundType", "RequestedQuantity", "NetAmount"], bindings=[bind("CustomerReturn", "returns", "CustomerReturn")], rationale="Load customer-return items."),
        query("credit_requests", "API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequest", fields=["CreditMemoRequest", "SalesOrganization", "CreditMemoRequestDate", "ReferenceSDDocument", "TotalNetAmount", "TransactionCurrency"], filters=[filt("SalesOrganization", "{{input.sales_organization}}"), filt("CreditMemoRequestDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("CreditMemoRequestDate", "{{input.date_to}}", op="le", kind="date_end")], rationale="Load credit memo requests in scope."),
        query("credit_request_items", "API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequestItem", fields=["CreditMemoRequest", "CreditMemoRequestItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "RequestedQuantity", "NetAmount"], bindings=[bind("CreditMemoRequest", "credit_requests", "CreditMemoRequest")], rationale="Load credit memo request items."),
        query("billing_documents", "API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", fields=["BillingDocument", "BillingDocumentItem", "SalesDocument", "SalesDocumentItem", "ReferenceSDDocument", "ReferenceSDDocumentItem", "BillingQuantity", "NetAmount", "TransactionCurrency"], bindings=[bind("SalesDocument", "returns", "CustomerReturn")], rationale="Load follow-on billing whose sales-document source is the selected customer return."),
    ]
    result["returns-credit-anomaly"] = execution("returns-credit-anomaly", organization_range, [sap_step("collect_returns_credit_evidence", return_plan, rationale="Collect returns and credit evidence.")])

    shortage_inputs = schema({
        "plant": string_input("工厂", "Plant", placeholder_zh="例如：1710", placeholder_en="Example: 1710", pattern=DOC, maximum=4),
        "material": string_input("物料", "Material", placeholder_zh="例如：SG21", placeholder_en="Example: SG21", pattern=DOC, maximum=40),
        "date_from": organization_range["properties"]["date_from"],
        "date_to": organization_range["properties"]["date_to"],
    }, ranges=[("date_from", "date_to", 31)])
    shortage_plan = [
        query("sales_order_items", "API_SALES_ORDER_SRV", "A_SalesOrderItem", fields=["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "RequestedQuantityUnit", "ConfdDelivQtyInOrderQtyUnit", "DeliveryPriority"], filters=[filt("ProductionPlant", "{{input.plant}}"), filt("Material", "{{input.material}}")], rationale="Load demand for the selected material and plant."),
        query("schedule_lines", "API_SALES_ORDER_SRV", "A_SalesOrderScheduleLine", fields=["SalesOrder", "SalesOrderItem", "ScheduleLine", "RequestedDeliveryDate", "ConfirmedDeliveryDate", "ScheduleLineOrderQuantity", "ConfdOrderQtyByMatlAvailCheck", "DeliveredQtyInOrderQtyUnit"], bindings=[bind("SalesOrder", "sales_order_items", "SalesOrder"), bind("SalesOrderItem", "sales_order_items", "SalesOrderItem")], rationale="Load schedule-line demand and confirmations."),
        query("material_stock", "API_MATERIAL_STOCK_SRV", "A_MatlStkInAcctMod", fields=["Material", "Plant", "StorageLocation", "MaterialBaseUnit", "MatlWrhsStkQtyInMatlBaseUnit", "InventoryStockType"], filters=[filt("Plant", "{{input.plant}}"), filt("Material", "{{input.material}}")], rationale="Load the current stock snapshot."),
    ]
    result["shortage-allocation-advisor"] = execution("shortage-allocation-advisor", shortage_inputs, [sap_step("collect_shortage_evidence", shortage_plan, rationale="Collect demand and stock evidence.")], known_gaps=["atp_availability_evidence"])

    forecast_inputs = schema({
        "plant": shortage_inputs["properties"]["plant"],
        "material": shortage_inputs["properties"]["material"],
        "date_from": shortage_inputs["properties"]["date_from"],
        "date_to": shortage_inputs["properties"]["date_to"],
    }, ranges=[("date_from", "date_to", 366)])
    forecast_plan = [
        query("sales_demand", "API_SALES_ORDER_SRV", "A_SalesOrderItem", fields=["SalesOrder", "SalesOrderItem", "Material", "ProductionPlant", "RequestedQuantity", "RequestedQuantityUnit", "ConfdDelivQtyInOrderQtyUnit"], filters=[filt("ProductionPlant", "{{input.plant}}"), filt("Material", "{{input.material}}")], rationale="Load historical sales demand."),
        query("planned_orders", "API_PLANNED_ORDERS", "A_PlannedOrder", fields=["PlannedOrder", "Material", "ProductionPlant", "TotalQuantity", "PlndOrderPlannedStartDate", "PlndOrderPlannedEndDate", "PlannedOrderIsFirm", "PlannedOrderIsConvertible"], filters=[filt("ProductionPlant", "{{input.plant}}"), filt("Material", "{{input.material}}"), filt("PlndOrderPlannedStartDate", "{{input.date_from}}", op="ge", kind="date_start"), filt("PlndOrderPlannedStartDate", "{{input.date_to}}", op="le", kind="date_end")], rationale="Load planned orders in the comparison period."),
    ]
    result["demand-forecast-planning"] = execution("demand-forecast-planning", forecast_inputs, [sap_step("collect_forecast_evidence", forecast_plan, rationale="Collect demand and planned-order evidence.")], known_gaps=["pir_evidence"])

    mrp_inputs = schema({
        "plant": string_input("MRP 工厂", "MRP plant", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=4),
        "mrp_area": string_input("MRP 范围", "MRP area", placeholder_zh="例如：1010", placeholder_en="Example: 1010", pattern=DOC, maximum=10),
        "material": shortage_inputs["properties"]["material"],
        "shortage_profile": string_input("短缺参数文件", "Shortage profile", placeholder_zh="例如：SAP000000001", placeholder_en="Example: SAP000000001", pattern=DOC, maximum=20),
        "shortage_counter": string_input("短缺计数器", "Shortage profile counter", placeholder_zh="例如：001", placeholder_en="Example: 001", pattern=DIGITS, maximum=3),
    })
    mrp_master = [query("mrp_material", "API_MRP_MATERIALS_SRV_01", "A_MRPMaterial", fields=["Material", "MRPArea", "MRPPlant", "MRPController", "MRPType", "SafetyStockQuantity"], filters=[filt("Material", "{{input.material}}"), filt("MRPArea", "{{input.mrp_area}}")], rationale="Load MRP master evidence.")]
    coverage = [query("material_coverages", "API_MRP_MATERIALS_SRV_01", "MaterialCoverages", fields=["Material", "MaterialShortageProfile", "MaterialShortageProfileCount", "MRPArea", "MRPPlant", "MaterialShortageDuration", "DaysOfSupplyDuration", "MaterialShortageQuantity", "MaterialShortageStartDate", "MaterialShortageEndDate"], filters=[filt("Material", "{{input.material}}"), filt("MRPArea", "{{input.mrp_area}}"), filt("MRPPlant", "{{input.plant}}"), filt("MaterialShortageProfile", "{{input.shortage_profile}}"), filt("MaterialShortageProfileCount", "{{input.shortage_counter}}")], rationale="Load material shortage coverage.")]
    supply = [query("supply_demand_items", "API_MRP_MATERIALS_SRV_01", "SupplyDemandItems", fields=["Material", "MRPArea", "MRPPlant", "MaterialShortageProfile", "MaterialShortageProfileCount", "MRPElement", "MRPElementCategory", "MRPElementAvailyOrRqmtDate", "MRPElementOpenQuantity", "MRPAvailableQuantity", "MaterialBaseUnit", "ExceptionMessageNumber", "ExceptionMessageText", "ExceptionMessageNumber2", "ExceptionMessageText2"], filters=[filt("Material", "{{input.material}}"), filt("MRPArea", "{{input.mrp_area}}"), filt("MRPPlant", "{{input.plant}}"), filt("MaterialShortageProfile", "{{input.shortage_profile}}"), filt("MaterialShortageProfileCount", "{{input.shortage_counter}}")], rationale="Load MRP supply-demand items.")]
    result["mrp-exception-analysis"] = execution("mrp-exception-analysis", mrp_inputs, [sap_step("collect_mrp_master", mrp_master, rationale="Collect MRP master evidence."), sap_step("collect_mrp_coverage", coverage, rationale="Collect MRP coverage evidence.", record_gap=True), sap_step("collect_mrp_elements", supply, rationale="Collect supply-demand evidence.", record_gap=True)])

    prod_input = schema({"manufacturing_order": string_input("生产订单号", "Manufacturing order", placeholder_zh="例如：1000000", placeholder_en="Example: 1000000", pattern=DIGITS, maximum=12)})
    prod_plan = [
        query("production_order", "API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", fields=["ManufacturingOrder", "ProductionPlant", "Material", "ManufacturingOrderType", "MfgOrderPlannedStartDate", "MfgOrderPlannedEndDate", "TotalQuantity", "MfgOrderConfirmedYieldQty"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load production-order header."),
        query("production_order_items", "API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderItem_2", fields=["ManufacturingOrder", "ManufacturingOrderItem", "Material", "ProductionPlant", "MfgOrderItemPlannedTotalQty", "MfgOrderItemGoodsReceiptQty", "MfgOrderItemActualDeviationQty"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load production-order items."),
        query("production_statuses", "API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderStatus_2", fields=["ManufacturingOrder", "StatusCode", "StatusShortName", "StatusName"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load production-order statuses."),
        query("production_operations", "API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2", fields=["ManufacturingOrder", "ManufacturingOrderOperation", "WorkCenter", "OperationIsConfirmed", "OperationIsPartiallyConfirmed", "OpPlannedTotalQuantity", "OpTotalConfirmedYieldQty", "OpErlstSchedldExecStrtDte", "OpErlstSchedldExecEndDte"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load production operations, planned dates, and confirmations."),
        query("production_components", "API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderComponent_2", fields=["ManufacturingOrder", "Reservation", "ReservationItem", "Material", "Plant", "RequiredQuantity", "WithdrawnQuantity", "ConfirmedAvailableQuantity"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load production-order components."),
        query("material_documents", "API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", fields=["MaterialDocumentYear", "MaterialDocument", "MaterialDocumentItem", "ManufacturingOrder", "Material", "Plant", "QuantityInBaseUnit", "DebitCreditCode", "ReversedMaterialDocument"], filters=[filt("ManufacturingOrder", "{{input.manufacturing_order}}")], rationale="Load material issues, receipts, and reversals."),
    ]
    result["production-order-monitoring"] = execution("production-order-monitoring", prod_input, [sap_step("collect_production_execution", prod_plan, rationale="Collect production execution evidence.")])

    schedule_inputs = schema({
        "plant": shortage_inputs["properties"]["plant"],
        "work_center": string_input("工作中心", "Work center", placeholder_zh="例如：ASSEMBLY", placeholder_en="Example: ASSEMBLY", pattern=DOC, maximum=20),
        "date_from": shortage_inputs["properties"]["date_from"],
        "date_to": shortage_inputs["properties"]["date_to"],
    }, ranges=[("date_from", "date_to", 31)])
    schedule_plan = [
        query("planned_pipeline_operations", "API_WORK_CENTERS", "A_WorkCenterCapPplineOp", fields=["Plant", "MRPController", "WorkCenter", "CapacityInternalID", "CapacityRequirement", "Material", "OrderID", "Operation", "OperationLatestStartDate", "OperationLatestEndDate", "CapacityRequirementUnit", "RemainingCapReqExecutionDurn"], filters=[filt("Plant", "{{input.plant}}"), filt("WorkCenter", "{{input.work_center}}"), filt("OperationLatestStartDate", "{{input.date_from}}", op="ge", kind="date_compact"), filt("OperationLatestStartDate", "{{input.date_to}}", op="le", kind="date_compact")], rationale="Load exact work-center planned pipeline operations in the scheduling horizon."),
        query("work_centers", "API_WORK_CENTERS", "A_WorkCenters", fields=["WorkCenterInternalID", "WorkCenterTypeCode", "WorkCenter", "Plant", "CapacityInternalID", "ValidityStartDate", "ValidityEndDate"], filters=[filt("Plant", "{{input.plant}}"), filt("WorkCenter", "{{input.work_center}}")], rationale="Load work-center master data."),
        query("work_center_capacities", "API_WORK_CENTERS", "A_WorkCenterCapacity", fields=["CapacityInternalID", "Capacity", "CapacityCategoryCode", "CapacityNumberOfCapacities", "CapacityPlanUtilizationPercent", "CapacityStartTime", "CapacityEndTime", "Plant"], bindings=[bind("CapacityInternalID", "work_centers", "CapacityInternalID")], rationale="Load work-center capacity master data."),
    ]
    bucket_plan = [query("capacity_buckets", "API_WORK_CENTERS", "A_WorkCenterCapPerBucketSet", fields=["P_CapEvalStartDate", "P_CapEvalEndDate", "P_CapEvalBucketType", "Plant", "WorkCenter", "CapacityInternalID", "CapacityEvaluationTimePeriod", "WorkCenterAvailableCapacity", "WorkCenterCapRqmtInCapUnit", "WrkCtrRmngCapInCapUnit", "WorkCenterTotUtilznInTmePerd", "WorkCenterCapacityUnit"], filters=[filt("P_CapEvalStartDate", "{{input.date_from}}", kind="date_start"), filt("P_CapEvalEndDate", "{{input.date_to}}", kind="date_end"), filt("P_CapEvalBucketType", "D"), filt("Plant", "{{input.plant}}"), filt("WorkCenter", "{{input.work_center}}")], rationale="Load parameterized capacity buckets.")]
    result["production-scheduling-capacity"] = execution("production-scheduling-capacity", schedule_inputs, [sap_step("collect_scheduling_objects", schedule_plan, rationale="Collect orders, operations, and work-center evidence."), sap_step("collect_capacity_buckets", bucket_plan, rationale="Collect parameterized capacity buckets.", record_gap=True)])

    # production-variance-analysis 0.2.0 is maintained as a specialized
    # five-source workflow with a dedicated deterministic rule.  Do not let
    # this legacy bulk generator replace it with a generic evidence summary.

    return result


RELATIONSHIPS: list[tuple[str, tuple[str, str, str, str], tuple[str, str, str, str]]] = [
    ("sd-order-header-items-binding", ("API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder", "sales_order_id"), ("API_SALES_ORDER_SRV", "A_SalesOrderItem", "SalesOrder", "sales_order_id")),
    ("sd-order-item-schedule-order", ("API_SALES_ORDER_SRV", "A_SalesOrderItem", "SalesOrder", "sales_order_id"), ("API_SALES_ORDER_SRV", "A_SalesOrderScheduleLine", "SalesOrder", "sales_order_id")),
    ("sd-order-item-schedule-item", ("API_SALES_ORDER_SRV", "A_SalesOrderItem", "SalesOrderItem", "sales_order_item_id"), ("API_SALES_ORDER_SRV", "A_SalesOrderScheduleLine", "SalesOrderItem", "sales_order_item_id")),
    ("sd-delivery-header-items", ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryHeader", "DeliveryDocument", "delivery_document_id"), ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", "DeliveryDocument", "delivery_document_id")),
    ("sd-order-delivery-reference", ("API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder", "sales_order_id"), ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", "ReferenceSDDocument", "sales_order_id")),
    ("sd-order-billing-sales-document", ("API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder", "sales_order_id"), ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "SalesDocument", "sales_order_id")),
    ("sd-order-fi-sales-document", ("API_SALES_ORDER_SRV", "A_SalesOrder", "SalesOrder", "sales_order_id"), ("API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", "SalesDocument", "sales_order_id")),
    ("sd-billing-item-sales-order", ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "SalesDocument", "sales_order_id"), ("API_SALES_ORDER_SRV", "A_SalesOrderItem", "SalesOrder", "sales_order_id")),
    ("sd-billing-item-sales-item", ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "SalesDocumentItem", "sales_order_item_id"), ("API_SALES_ORDER_SRV", "A_SalesOrderItem", "SalesOrderItem", "sales_order_item_id")),
    ("sd-billing-ref-delivery", ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "ReferenceSDDocument", "sd_reference_document_id"), ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", "DeliveryDocument", "delivery_document_id")),
    ("sd-billing-ref-delivery-item", ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "ReferenceSDDocumentItem", "sd_reference_document_item_id"), ("API_OUTBOUND_DELIVERY_SRV", "A_OutbDeliveryItem", "DeliveryDocumentItem", "delivery_document_item_id")),
    ("sd-return-header-items", ("API_CUSTOMER_RETURN_SRV", "A_CustomerReturn", "CustomerReturn", "customer_return_id"), ("API_CUSTOMER_RETURN_SRV", "A_CustomerReturnItem", "CustomerReturn", "customer_return_id")),
    ("sd-credit-header-items", ("API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequest", "CreditMemoRequest", "credit_memo_request_id"), ("API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequestItem", "CreditMemoRequest", "credit_memo_request_id")),
    ("sd-credit-billing", ("API_CREDIT_MEMO_REQUEST_SRV", "A_CreditMemoRequest", "CreditMemoRequest", "credit_memo_request_id"), ("API_BILLING_DOCUMENT_SRV", "A_BillingDocumentItem", "ReferenceSDDocument", "sd_reference_document_id")),
    ("fi-gl-purchase-order", ("API_GLACCOUNTLINEITEM", "GLAccountLineItem", "PurchasingDocument", "purchase_order_id"), ("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem", "PurchaseOrder", "purchase_order_id")),
    ("fi-gl-material-document-po", ("API_GLACCOUNTLINEITEM", "GLAccountLineItem", "PurchasingDocument", "purchase_order_id"), ("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", "PurchaseOrder", "purchase_order_id")),
    ("fi-gl-supplier-invoice-po", ("API_GLACCOUNTLINEITEM", "GLAccountLineItem", "PurchasingDocument", "purchase_order_id"), ("API_SUPPLIERINVOICE_PROCESS_SRV", "A_SuplrInvcItemPurOrdRef", "PurchaseOrder", "purchase_order_id")),
    ("fi-operational-purchase-order", ("API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", "PurchasingDocument", "purchase_order_id"), ("API_PURCHASEORDER_PROCESS_SRV", "A_PurchaseOrderItem", "PurchaseOrder", "purchase_order_id")),
    ("fi-operational-material-document-po", ("API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", "PurchasingDocument", "purchase_order_id"), ("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", "PurchaseOrder", "purchase_order_id")),
    ("fi-operational-supplier-invoice-po", ("API_OPLACCTGDOCITEMCUBE_SRV", "A_OperationalAcctgDocItemCube", "PurchasingDocument", "purchase_order_id"), ("API_SUPPLIERINVOICE_PROCESS_SRV", "A_SuplrInvcItemPurOrdRef", "PurchaseOrder", "purchase_order_id")),
    ("pp-planned-order-capacity", ("API_PLANNED_ORDERS", "A_PlannedOrder", "PlannedOrder", "planned_order_id"), ("API_PLANNED_ORDERS", "A_PlannedOrderCapacity", "PlannedOrder", "planned_order_id")),
    ("pp-work-center-capacity", ("API_WORK_CENTERS", "A_WorkCenters", "CapacityInternalID", "capacity_internal_id"), ("API_WORK_CENTERS", "A_WorkCenterCapacity", "CapacityInternalID", "capacity_internal_id")),
    ("pp-order-header-item", ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "ManufacturingOrder", "manufacturing_order_id"), ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderItem_2", "ManufacturingOrder", "manufacturing_order_id")),
    ("pp-order-header-status", ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "ManufacturingOrder", "manufacturing_order_id"), ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderStatus_2", "ManufacturingOrder", "manufacturing_order_id")),
    ("pp-order-header-operation", ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "ManufacturingOrder", "manufacturing_order_id"), ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderOperation_2", "ManufacturingOrder", "manufacturing_order_id")),
    ("pp-order-header-component", ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "ManufacturingOrder", "manufacturing_order_id"), ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrderComponent_2", "ManufacturingOrder", "manufacturing_order_id")),
    ("pp-order-material-document", ("API_PRODUCTION_ORDER_2_SRV", "A_ProductionOrder_2", "ManufacturingOrder", "manufacturing_order_id"), ("API_MATERIAL_DOCUMENT_SRV", "A_MaterialDocumentItem", "ManufacturingOrder", "manufacturing_order_id")),
]


def update_relationship_catalog() -> None:
    path = ROOT / "config" / "business-relationships.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    semantics = payload.setdefault("field_semantics", [])
    relationships = payload.setdefault("relationships", [])
    semantics_by_key = {
        (item["service_name"], item["entity_set"], item["field"]): item
        for item in semantics
    }
    relationships_by_id = {item["id"]: item for item in relationships}
    for relation_id, source, target in RELATIONSHIPS:
        for service, entity, field, semantic in (source, target):
            key = (service, entity, field)
            existing_semantic = semantics_by_key.get(key)
            if existing_semantic is None:
                existing_semantic = {
                    "service_name": service,
                    "odata_version": "2.0",
                    "entity_set": entity,
                    "field": field,
                    "semantic": semantic,
                }
                semantics.append(existing_semantic)
                semantics_by_key[key] = existing_semantic
            else:
                existing_semantic.setdefault("odata_version", "2.0")
        relationship = relationships_by_id.get(relation_id)
        if relationship is None:
            relationship = {
                "id": relation_id,
                "modes": ["binding"],
                "source": {"service_name": source[0], "odata_version": "2.0", "entity_set": source[1], "field": source[2]},
                "target": {"service_name": target[0], "odata_version": "2.0", "entity_set": target[1], "field": target[2]},
            }
            relationships.append(relationship)
            relationships_by_id[relation_id] = relationship
        else:
            relationship["source"].setdefault("odata_version", "2.0")
            relationship["target"].setdefault("odata_version", "2.0")
    payload["description"] = "Approved GET-only business-key semantics and cross-entity joins for deterministic FI, MM, SD, and PP Agents."
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    try:
        from scripts.migrate_agent_page_contracts import migrate
    except ModuleNotFoundError:
        # Support both ``python -m scripts.apply_deterministic_agents`` and
        # direct execution from the repository root.
        from migrate_agent_page_contracts import migrate

    definitions = specs()
    for agent_id, definition in definitions.items():
        matches = list((ROOT / "agents").glob(f"*/{agent_id}/agent.json"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one manifest for {agent_id}, found {len(matches)}")
        path = matches[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schemaVersion"] = 2
        payload["status"] = "Live-tested deterministic prototype"
        payload["execution"] = definition
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        migrate(path)
    update_relationship_catalog()
    print(f"Updated {len(definitions)} deterministic Agent manifests.")


if __name__ == "__main__":
    main()
