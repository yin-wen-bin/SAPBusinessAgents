from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


COMMON_LIMITATION_KEYWORDS = {
    "source_incomplete": [
        "source incomplete",
        "paging incomplete",
        "range limited",
        "范围限制",
        "分页未完成",
    ],
    "bank_settlement_not_proven": [
        "bank settlement",
        "bank receipt",
        "银行结算",
        "银行到账",
    ],
    "capability_gap": [
        "capability gap",
        "evidence unavailable",
        "inconclusive",
        "能力缺口",
        "证据不足",
        "无法确认",
    ],
}

AR_LIMITATION_KEYWORDS = {
    "historical_dunning_evidence": [
        "historical dunning",
        "dunning history",
        "last-dunning",
        "mhnd",
        "historical collection",
        "cutoff dunning",
        "历史催收",
        "截止日催收",
        "历史催款",
    ],
    **COMMON_LIMITATION_KEYWORDS,
}


def _spec(
    keys: list[str],
    facts: list[str],
    metrics: list[str],
    *,
    decimals: list[str] | None = None,
    decimal_metrics: list[str] | None = None,
    currencies: list[str] | None = None,
    units: list[str] | None = None,
    dates: list[str] | None = None,
    inputs: dict[str, str] | None = None,
    summary: bool = False,
) -> dict[str, Any]:
    return {
        "schemaVersion": "2.0",
        "comparisonMode": "business_semantic",
        "businessKeys": keys,
        "facts": list(dict.fromkeys([*facts, "business_status"])),
        "metrics": metrics,
        "decimalFields": decimals or [],
        "decimalMetricIds": decimal_metrics or [],
        "currencyFields": currencies or [],
        "unitFields": units or [],
        "dateFields": dates or [],
        "codeSetFields": [],
        "zeroPadFields": {},
        "booleanFields": [],
        "inputDefaults": inputs or {},
        "constantDefaults": {},
        "fieldAliases": {},
        "fieldExtractors": {},
        "currencyFromDecimal": {},
        "valueMappings": {},
        "limitationKeywords": COMMON_LIMITATION_KEYWORDS,
        "summaryRecord": summary,
        "currencyAndUnitPolicy": "compare_only_when_same_or_conversion_validated",
        "requiredLimitations": [],
        "businessStatusFromMetric": {},
        "limitationsFromMetrics": {},
        "blankValueKeywords": {},
        "blockingLimitations": [],
        "ignoredNoticeKeywords": [],
        "zeroFactWhenMetricZero": {},
        "recordScope": "",
        "metricDefinitions": {},
        "businessStatusDefinition": "",
        "businessStatusFromAnyPositiveMetric": {},
    }


SPECS: dict[str, dict[str, Any]] = {
    "ar-collection": _spec(
        ["company_code", "fiscal_year", "accounting_document", "accounting_document_item"],
        ["customer", "posting_date", "due_date", "as_of_status", "clearing_date", "clearing_document", "dunning_level", "last_dunning_date", "dunning_blocking_reason", "dunning_as_of_status"],
        ["open_items", "dunned_items", "historical_dunning_unknown"], decimals=["amount"], decimal_metrics=["open_items", "dunned_items", "historical_dunning_unknown"], currencies=["currency"],
        dates=["posting_date", "due_date", "clearing_date", "last_dunning_date"],
    ),
    "gr-ir-clearing": _spec(
        ["purchase_order", "purchase_order_item"],
        ["material", "receipt_rows", "invoice_rows"],
        ["receipt_rows", "invoice_rows", "gl_rows"],
        decimals=["receipt_rows", "invoice_rows", "receipt_quantity", "invoice_quantity"],
        decimal_metrics=["receipt_rows", "invoice_rows", "gl_rows"], units=["unit"],
    ),
    "month-end-closing": _spec(
        ["company_code", "fiscal_year", "period"], [], ["fi_rows"],
        decimal_metrics=["fi_rows"],
        inputs={"company_code": "company_code", "fiscal_year": "fiscal_year", "period": "period"},
        summary=True,
    ),
    "billing-block-diagnosis": _spec(
        ["sales_order", "sales_order_item"],
        ["billing_block_reason", "delivery_block_reason", "credit_status", "incompletion_status"],
        ["blocked_findings"],
    ),
    "billing-completeness-check": _spec(
        ["billing_document", "billing_document_item"],
        ["reference_document", "reference_document_item", "accounting_posting_status", "cancelled"],
        ["billing_items", "source_rows", "finding_count"],
    ),
    "delivered-not-billed": _spec(
        ["delivery_document", "delivery_document_item"],
        ["actual_goods_movement_date", "is_billed"], ["delivered_not_billed"],
        dates=["actual_goods_movement_date"],
    ),
    "delivery-delay-prediction": _spec(
        ["sales_order", "sales_order_item", "schedule_line"],
        ["due_date", "delivery_block_reason", "risk_score"], ["maximum_risk_score"],
        decimals=["risk_score"], decimal_metrics=["maximum_risk_score"], dates=["due_date"],
    ),
    "due-delivery-prioritization": _spec(
        ["sales_order", "sales_order_item", "schedule_line"],
        ["material", "plant", "due_date"], ["ranked_requirements"],
        decimals=["requested_quantity", "confirmed_quantity"], units=["unit"], dates=["due_date"],
    ),
    "returns-credit-anomaly": _spec(
        ["customer_return", "customer_return_item"],
        ["reference_document", "material_received", "refund_type"], ["finding_count"],
    ),
    "shortage-allocation-advisor": _spec(
        ["sales_order", "sales_order_item", "schedule_line"], ["material", "plant"],
        ["requested", "confirmed", "stock", "uncovered"],
        decimals=["requested_quantity", "confirmed_quantity"],
        decimal_metrics=["requested", "confirmed", "stock", "uncovered"], units=["unit"],
    ),
    "order-to-cash-anomaly-monitor": _spec(
        ["sales_order", "sales_order_item"],
        ["material", "item_billing_status", "item_delivery_status"], ["anomaly_count"],
    ),
    "billing-output-monitor": _spec(
        ["billing_document", "output_request"],
        ["output_status", "output_type", "transmission_medium"], ["output_rows", "failed_outputs"],
        inputs={"billing_document": "billing_document"},
    ),
    "billing-dispute-classification": _spec(
        ["billing_document", "billing_document_item"],
        ["dispute_case_count", "root_cause_codes"], ["case_count", "root_cause_counts"],
    ),
    "order-to-cash-status": _spec(
        ["sales_order", "sales_order_item"], ["material"],
        ["sales_orders", "sales_order_items", "delivery_items", "billing_items", "billing_headers", "accounting_items", "document_flow_rows", "pgi_items", "cleared_items", "bank_receipt_evidence"],
        decimals=["requested_quantity"],
        decimal_metrics=["sales_orders", "sales_order_items", "delivery_items", "billing_items", "billing_headers", "accounting_items", "document_flow_rows", "pgi_items", "cleared_items", "bank_receipt_evidence"],
        units=["unit"], inputs={"sales_order": "sales_order"},
    ),
    "procure-to-pay-status": _spec(
        ["purchase_order", "purchase_order_item"], ["material", "plant"],
        ["purchase_orders", "purchase_order_items", "material_document_items", "supplier_invoice_items", "accounting_items", "active_receipts", "cleared_items", "payment_evidence"],
        decimals=["order_quantity"], units=["unit"], inputs={"purchase_order": "purchase_order"},
    ),
    "inventory-health-balancing": _spec(
        ["material", "plant", "storage_location", "batch"],
        ["last_movement_date", "shelf_life_expiration_date"],
        ["unrestricted_stock", "stock_age_days", "expiry_candidates", "confirmed_transfer_quantity"],
        decimals=["unrestricted_stock", "safety_stock"], decimal_metrics=["unrestricted_stock", "stock_age_days", "confirmed_transfer_quantity"],
        units=["unit"], dates=["last_movement_date", "shelf_life_expiration_date"],
    ),
    "material-shortage-procurement-response": _spec(
        ["material", "plant", "requirement_id"],
        ["requirement_date", "mrp_element_type"],
        ["shortage_quantity", "pending_pr", "expedite_po", "valid_source_candidates"],
        decimals=["shortage_quantity"], decimal_metrics=["shortage_quantity"], units=["unit"], dates=["requirement_date"],
    ),
    "intelligent-sourcing-rfq": _spec(
        ["rfq", "rfq_item", "supplier"],
        ["quotation", "eligible", "score"], ["eligible_quotations", "ranked_quotations"],
        decimals=["net_price", "price_unit", "score"], currencies=["currency"], units=["unit"],
    ),
    "supplier-performance-risk": _spec(
        ["purchase_order", "purchase_order_item", "schedule_line"],
        ["delivery_date", "on_time_in_full"], ["due_schedule_lines", "on_time_in_full", "otif_percent"],
        decimals=["scheduled_quantity", "net_receipt_by_due"], decimal_metrics=["otif_percent"],
        units=["unit"], dates=["delivery_date"],
    ),
    "demand-forecast-planning": _spec(
        ["material", "plant", "requirement_date"], [], ["demand_rows", "planned_order_rows"],
        decimals=["demand_quantity", "planned_quantity"], units=["unit"], dates=["requirement_date"],
        inputs={"material": "material", "plant": "plant", "requirement_date": "date_from"},
        summary=True,
    ),
    "mrp-exception-analysis": _spec(
        ["material", "plant", "mrp_element"],
        ["requirement_date", "exception_message"], ["coverage_rows", "supply_demand_rows"],
        decimals=["quantity"], units=["unit"], dates=["requirement_date"],
    ),
    "production-order-monitoring": _spec(
        ["manufacturing_order", "operation"],
        ["work_center", "confirmed", "planned_start", "planned_end"],
        ["operation_rows", "unconfirmed_operations", "movement_rows"],
        dates=["planned_start", "planned_end"], inputs={"manufacturing_order": "manufacturing_order"},
    ),
    "production-scheduling-capacity": _spec(
        ["plant", "work_center", "capacity_date"], [],
        ["planned_rows", "operation_rows", "capacity_bucket_rows"],
        decimals=["required_capacity", "available_capacity"], units=["unit"], dates=["capacity_date"],
    ),
    "production-variance-analysis": _spec(
        ["manufacturing_order", "cost_element"], [],
        ["operation_rows", "movement_rows", "cost_rows"], decimals=["actual_amount"], currencies=["currency"],
        inputs={"manufacturing_order": "manufacturing_order"},
    ),
    "cost-center-expense-anomaly": _spec(
        ["company_code", "controlling_area", "cost_center", "fiscal_year", "period_from", "period_to"],
        [], ["actual_amount", "plan_amount", "variance_amount", "variance_pct", "currency"],
        decimal_metrics=["actual_amount", "plan_amount", "variance_amount", "variance_pct"],
        inputs={key: key for key in ("company_code", "controlling_area", "cost_center", "fiscal_year", "period_from", "period_to")}, summary=True,
    ),
    "co-month-end-allocation-settlement": _spec(
        ["company_code", "controlling_area", "fiscal_year", "period", "internal_order"],
        [], ["posting_rows", "allocation_cycle_rows", "settlement_rule_rows", "ready"],
        inputs={key: key for key in ("company_code", "controlling_area", "fiscal_year", "period", "internal_order")}, summary=True,
    ),
    "product-cost-variance": _spec(
        ["company_code", "manufacturing_order", "material", "fiscal_year", "period"],
        [], ["order_actual_amount", "standard_unit_price", "periodic_unit_price", "unit_price_variance"],
        decimal_metrics=["order_actual_amount", "standard_unit_price", "periodic_unit_price", "unit_price_variance"],
        inputs={key: key for key in ("company_code", "manufacturing_order", "material", "fiscal_year", "period")}, summary=True,
    ),
    "budget-rolling-forecast": _spec(
        ["company_code", "cost_center", "fiscal_year", "current_period"],
        [], ["actual_ytd", "annual_plan", "full_year_forecast", "forecast_variance_pct"],
        decimal_metrics=["actual_ytd", "annual_plan", "full_year_forecast", "forecast_variance_pct"],
        inputs={key: key for key in ("company_code", "cost_center", "fiscal_year", "current_period")}, summary=True,
    ),
    "internal-order-project-control": _spec(
        ["company_code", "object_type", "object_id", "fiscal_year"],
        [], ["actual_amount", "plan_amount", "budget_amount", "commitment_amount", "estimate_at_completion", "remaining_budget", "budget_consumption_pct"],
        decimal_metrics=["actual_amount", "plan_amount", "budget_amount", "commitment_amount", "estimate_at_completion", "remaining_budget", "budget_consumption_pct"],
        inputs={key: key for key in ("company_code", "object_type", "object_id", "fiscal_year")}, summary=True,
    ),
}

SPECS["ar-collection"]["inputDefaults"] = {"customer": "customer"}
SPECS["intelligent-sourcing-rfq"]["fieldAliases"] = {
    "net_price": ["quoted_unit_price"],
    "currency": ["quote_currency"],
    "price_unit": ["price_basis_qty"],
    "unit": ["quote_unit", "price_unit"],
}
SPECS["intelligent-sourcing-rfq"]["valueMappings"] = {
    "business_status": {
        "eligible_ranked": "normal",
    },
    "eligible": {
        "true": True,
        "false": False,
    },
}
SPECS["co-month-end-allocation-settlement"]["metricValueMappings"] = {
    "ready": {
        "not determined": None,
        "unknown": None,
        "unavailable": None,
    }
}
SPECS["ar-collection"]["constantDefaults"] = {
    "business_status": "capability_blocked",
    "dunning_blocking_reason": "",
}
SPECS["ar-collection"]["fieldAliases"] = {
    "company_code": ["Company Code", "公司代码"],
    "fiscal_year": ["Fiscal Year", "会计年度"],
    "accounting_document": ["Accounting Document", "会计凭证"],
    "accounting_document_item": ["Accounting Document Item", "Item", "行项目"],
    "customer": ["Customer", "客户"],
    "posting_date": ["Posting Date", "过账日期"],
    "due_date": ["Due Date", "Net Due Date", "net_due_date", "到期日"],
    "amount": ["Amount", "Transaction Amount", "transaction_amount", "Company-Code Amount", "company_code_amount", "金额"],
    "currency": ["Currency", "Transaction Currency", "transaction_currency", "Company-Code Currency", "company_code_currency", "币种"],
    "as_of_status": ["As-of Status", "截止日状态"],
    "clearing_date": ["Clearing Date", "Later Clearing Date", "later_clearing_date", "清账日期", "后续清账日"],
    "clearing_document": ["Clearing Document", "清账凭证"],
    "dunning_level": ["Dunning Level", "催款级别", "催收级别"],
    "last_dunning_date": ["Last Dunning Date", "最后催款日期", "最后催收日期"],
    "dunning_blocking_reason": ["Dunning Blocking Reason", "催款冻结原因", "催收冻结原因"],
    "dunning_as_of_status": ["Dunning As-of Status", "截止日催收状态", "截止日催款状态"],
    "open_items": ["Open Items", "Open Count", "open_count", "未清项目数"],
    "dunned_items": ["Dunned Items", "Dunned Count", "dunned_count", "Dunned By Cutoff", "dunned_by_cutoff", "截止日前催收项目数"],
    "historical_dunning_unknown": ["Historical Dunning Unknown", "Unknown Dunning Count", "unknown_dunning_count", "historical_dunning_unknown", "历史催收未知项目数"],
}
SPECS["ar-collection"]["fieldExtractors"] = {
    "as_of_status": {
        "source": ["later_clearing", "later_clearing_date", "subsequent_clearing", "clearing_date"],
        "contains": {"none": "open", "no subsequent": "open", "still uncleared": "open"},
        "default": "open_subsequently_cleared",
    },
    "clearing_date": {
        "source": ["later_clearing", "later_clearing_date", "subsequent_clearing"],
        "pattern": r"(\d{4}-\d{2}-\d{2})",
        "default": "",
    },
    "clearing_document": {
        "source": ["later_clearing", "subsequent_clearing"],
        "pattern": r"(?:doc|document)\s*(\d+)",
        "default": "",
    },
    "dunning_level": {
        "source": ["cutoff_dunning", "dunning_status"],
        "pattern": r"level\s*(\d+)",
        "default": "0",
    },
    "last_dunning_date": {
        "source": ["cutoff_dunning", "dunning_status"],
        "pattern": r"(\d{4}-\d{2}-\d{2})",
        "default": "",
    },
    "dunning_as_of_status": {
        "source": ["cutoff_dunning", "cutoff_dunning_status", "dunning_status"],
        "contains": {
            "historically unknown": "historical_status_unknown",
            "history unknown": "historical_status_unknown",
            "dunned by cutoff": "confirmed_before_cutoff",
            "no dunning recorded": "no_item_dunning_evidence",
            "no pre-cutoff dunning found": "no_item_dunning_evidence",
        },
        "default": "historical_status_unknown",
    },
}
SPECS["ar-collection"]["limitationKeywords"] = AR_LIMITATION_KEYWORDS
SPECS["ar-collection"]["requiredLimitations"] = ["historical_dunning_evidence"]
SPECS["gr-ir-clearing"]["constantDefaults"] = {"business_status": "attention"}
SPECS["gr-ir-clearing"]["fieldAliases"] = {
    "purchase_order": ["Purchase Order", "采购订单"],
    "purchase_order_item": ["Purchase Order Item", "采购订单项目", "Item"],
    "material": ["Material", "物料"],
    "receipt_rows": ["Receipt Rows", "GR Rows", "收货行数"],
    "invoice_rows": ["Invoice Rows", "IR Rows", "发票行数"],
    "receipt_quantity": ["Receipt Quantity", "GR Quantity", "gr_qty", "收货数量"],
    "invoice_quantity": ["Invoice Quantity", "IR Quantity", "invoice_qty", "发票数量"],
    "unit": ["Unit", "GR Unit", "gr_unit", "单位", "收货单位"],
    "gl_rows": ["G/L Rows", "GL Rows", "总账行数"],
}
SPECS["month-end-closing"]["constantDefaults"] = {"business_status": "capability_blocked"}
SPECS["month-end-closing"]["fieldAliases"] = {
    "company_code": ["Company Code", "公司代码"],
    "fiscal_year": ["Fiscal Year", "会计年度"],
    "period": ["Fiscal Period", "Period", "会计期间"],
    "fi_rows": ["FI Rows", "Accounting Rows", "accounting_posting_line_count", "FI行数", "会计行数"],
}
SPECS["month-end-closing"]["requiredLimitations"] = [
    "period_control_asset_depreciation_and_specialized_closing_checks"
]
SPECS["month-end-closing"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "period_control_asset_depreciation_and_specialized_closing_checks": [
        "period control",
        "period was closed",
        "close completion",
        "asset depreciation",
        "closing checklist",
        "specialized close checks",
        "期间控制",
        "资产折旧",
        "关账检查",
    ],
}

SPECS["billing-output-monitor"]["constantDefaults"] = {
    "business_status": "capability_blocked",
}
SPECS["billing-output-monitor"]["requiredLimitations"] = [
    "billing_output_status_evidence"
]
SPECS["billing-output-monitor"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "billing_output_status_evidence": [
        "billing output status",
        "structured output status",
        "output evidence",
        "nast",
        "输出状态证据",
        "结构化输出状态",
        "no output-detail table",
        "output_rows or failed_outputs cannot be calculated",
    ],
}
SPECS["billing-output-monitor"]["fieldAliases"] = {
    "billing_document": ["Billing Document", "开票凭证"],
    "output_request": ["Output Request", "输出请求"],
    "output_status": ["Output Status", "Processing Status", "输出状态"],
    "output_type": ["Output Type", "输出类型"],
    "transmission_medium": ["Transmission Medium", "传输媒介"],
    "output_rows": ["Output Rows", "输出行数"],
    "failed_outputs": ["Failed Outputs", "失败输出数"],
}
SPECS["billing-output-monitor"]["blankValueKeywords"] = {
    "output_request": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
    "output_status": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
    "output_type": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
    "transmission_medium": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
}

SPECS["billing-dispute-classification"]["constantDefaults"] = {
    "business_status": "capability_blocked",
}
SPECS["billing-dispute-classification"]["requiredLimitations"] = [
    "billing_dispute_case_evidence"
]
SPECS["billing-dispute-classification"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "billing_dispute_case_evidence": [
        "billing dispute case",
        "dispute case evidence",
        "structured dispute case",
        "争议案件证据",
        "结构化争议案件",
        "structured dispute-case",
        "dispute_case_count must not be reported",
    ],
}
SPECS["billing-dispute-classification"]["fieldAliases"] = {
    "billing_document": ["Billing Document", "开票凭证"],
    "billing_document_item": ["Billing Document Item", "开票凭证项目", "Item"],
    "dispute_case_count": ["Dispute Case Count", "争议案件数"],
    "root_cause_codes": ["Root Cause Codes", "根因码"],
    "case_count": ["Case Count", "案件数"],
    "root_cause_counts": ["Root Cause Counts", "根因统计"],
}
SPECS["billing-dispute-classification"]["blankValueKeywords"] = {
    "dispute_case_count": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
    "root_cause_codes": ["unknown", "unavailable", "undetermined", "无法确认", "不可用"],
}
SPECS["billing-dispute-classification"]["ignoredNoticeKeywords"] = [
    "bank settlement",
    "bank receipt",
    "银行结算",
    "银行到账",
]

SPECS["shortage-allocation-advisor"]["constantDefaults"] = {
    "business_status": "capability_blocked",
}

SPECS["demand-forecast-planning"]["zeroFactWhenMetricZero"] = {
    "planned_quantity": "planned_order_rows",
}

SPECS["material-shortage-procurement-response"]["recordScope"] = (
    "Return only authoritative MRP coverage or supply-demand requirement rows. "
    "Purchase requisitions, PO schedule lines, and source candidates are contextual "
    "evidence summarized by metrics, not comparison records."
)
SPECS["material-shortage-procurement-response"]["metricDefinitions"] = {
    "pending_pr": "Count exact material/plant PR items that are not deleted and are not in completed/released terminal processing states.",
    "expedite_po": "Count exact material/plant PO schedule lines with delivery before the as-of date and positive schedule quantity minus committed quantity.",
    "valid_source_candidates": "Count exact purchasing-organization/plant info-record rows returned by the complete source query.",
}
SPECS["material-shortage-procurement-response"]["businessStatusDefinition"] = (
    "Use attention when shortage_quantity, pending_pr, or expedite_po is positive; "
    "use normal only when all three are zero. Test-data qualification BLOCKED is a "
    "separate acceptance verdict and must not replace this business status."
)
SPECS["material-shortage-procurement-response"]["businessStatusFromAnyPositiveMetric"] = {
    "metrics": ["shortage_quantity", "pending_pr", "expedite_po"],
    "positive": "attention",
    "zero": "normal",
}
SPECS["material-shortage-procurement-response"]["valueMappings"] = {
    "mrp_element_type": {
        "materialcoverage": "material_coverage",
        "material_coverage": "material_coverage",
    }
}
SPECS["shortage-allocation-advisor"]["requiredLimitations"] = [
    "atp_availability_evidence"
]
SPECS["shortage-allocation-advisor"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "atp_availability_evidence": [
        "atp availability",
        "released atp",
        "availability evidence",
        "historical key date",
        "historical stock evidence",
        "not aligned to the historical",
        "atp 可用量",
        "atp 能力",
        "atp 证据",
        "历史库存证据",
        "历史关键日期",
    ],
}

SPECS["order-to-cash-anomaly-monitor"]["constantDefaults"] = {
    "business_status": "capability_blocked",
}
SPECS["order-to-cash-anomaly-monitor"]["fieldAliases"] = {
    "item_billing_status": ["order_billing_status", "Order Billing Status", "Billing Status"],
    "item_delivery_status": ["delivery_status", "Delivery Status"],
}
SPECS["order-to-cash-anomaly-monitor"]["blankValueKeywords"] = {
    "item_billing_status": ["blank", "empty", "none", "空白", "未设置"],
}
SPECS["order-to-cash-anomaly-monitor"]["ignoredNoticeKeywords"] = [
    "anomaly definition",
    "anomaly rule",
    "异常定义",
    "异常规则",
]
SPECS["order-to-cash-anomaly-monitor"]["requiredLimitations"] = [
    "billing_output_status_evidence",
    "billing_dispute_case_evidence",
]
SPECS["order-to-cash-anomaly-monitor"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "billing_output_status_evidence": SPECS["billing-output-monitor"]["limitationKeywords"]["billing_output_status_evidence"],
    "billing_dispute_case_evidence": SPECS["billing-dispute-classification"]["limitationKeywords"]["billing_dispute_case_evidence"],
}

SPECS["order-to-cash-status"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "o2c_ar_clearing_evidence": [
        "ar clearing evidence",
        "receivables clearing",
        "clearing evidence is missing",
        "应收清账证据",
        "清账证据缺失",
    ],
    "shared_document_amount_attribution": [
        "shared billing accounting document",
        "shared accounting document",
        "amount attribution",
        "金额归属",
        "共享会计凭证",
        "shared-document totals",
    ],
}
SPECS["order-to-cash-status"]["metricValueMappings"] = {
    "document_flow_rows": {"unconfirmed": "0", "未确认": "0"},
}

SPECS["procure-to-pay-status"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "p2p_supplier_invoice_evidence": [
        "supplier invoice evidence",
        "no supplier invoice",
        "supplier invoice branch is empty",
        "invoice not found",
        "供应商发票证据",
        "未找到供应商发票",
        "发票分支为空",
    ],
}

SPECS["billing-block-diagnosis"]["fieldAliases"] = {
    "sales_order": ["Sales Order", "销售订单"],
    "sales_order_item": ["Sales Order Item", "Item", "项目", "订单项目"],
    "billing_block_reason": ["Billing Block Reason", "Billing Block", "开票冻结原因", "项目开票冻结"],
    "delivery_block_reason": ["Delivery Block Reason", "Delivery Block", "交货冻结原因"],
    "credit_status": ["Credit Status", "Credit Check Status", "信用状态", "信用检查状态"],
    "incompletion_status": ["Incompletion Status", "Incompletion", "不完整状态"],
    "blocked_findings": ["Blocked Findings", "Block Findings", "冻结发现数"],
}
SPECS["billing-block-diagnosis"]["businessStatusFromMetric"] = {
    "metric": "blocked_findings",
    "zero": "normal",
    "nonzero": "attention",
}
SPECS["billing-block-diagnosis"]["constantDefaults"] = {
    "business_status": "capability_blocked",
}
SPECS["billing-block-diagnosis"]["requiredLimitations"] = [
    "sales_order_item_incompletion_evidence"
]
SPECS["billing-block-diagnosis"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "sales_order_item_incompletion_evidence": [
        "item-level incompletion",
        "dedicated item incompletion",
        "vbup",
        "项目级不完整",
        "订单项目不完整",
    ],
}
SPECS["billing-block-diagnosis"]["blankValueKeywords"] = {
    "billing_block_reason": ["none:", "blank", "未设置", "空白"],
    "delivery_block_reason": ["none:", "blank", "未设置", "空白"],
    "credit_status": ["blank", "no status code", "未返回", "空白"],
    "incompletion_status": ["not evidenced", "not exposed", "无法证明", "未提供"],
}
SPECS["billing-block-diagnosis"]["blockingLimitations"] = [
    "sales_order_item_incompletion_evidence"
]

SPECS["due-delivery-prioritization"]["requiredLimitations"] = [
    "current_stock_not_historical_atp"
]
SPECS["due-delivery-prioritization"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "current_stock_not_historical_atp": [
        "not historical stock",
        "not historical stock as of",
        "not atp",
        "not automatically allocatable",
        "current_stock_not_historical_atp",
        "当前库存",
        "不是历史库存",
        "不等同于 atp",
    ],
}

for _agent_id, _blocking_items in {
    "billing-output-monitor": ["billing_output_status_evidence"],
    "billing-dispute-classification": ["billing_dispute_case_evidence"],
    "shortage-allocation-advisor": ["atp_availability_evidence"],
    "order-to-cash-anomaly-monitor": [
        "billing_output_status_evidence",
        "billing_dispute_case_evidence",
    ],
    "production-variance-analysis": ["production_cost_evidence", "production_cost_relationship"],
    "demand-forecast-planning": ["pir_evidence", "sales_demand_period_evidence"],
    "mrp-exception-analysis": ["mrp_coverage_or_supply_demand_evidence"],
    "production-scheduling-capacity": ["complete_capacity_bucket_evidence"],
    "cost-center-expense-anomaly": ["plan_evidence_missing"],
    "budget-rolling-forecast": ["budget_evidence_missing"],
    "internal-order-project-control": ["master_evidence", "plan_evidence", "budget_evidence", "commitment_evidence", "control_object_not_found"],
    "product-cost-variance": ["standard_cost_evidence"],
    "co-month-end-allocation-settlement": ["allocation_cycle_evidence", "object_status_evidence", "settlement_rule_evidence"],
}.items():
    SPECS[_agent_id]["blockingLimitations"] = _blocking_items

for _agent_id, _required, _keywords in (
    (
        "production-variance-analysis",
        ["production_cost_evidence", "production_cost_relationship"],
        {
            "production_cost_evidence": ["production cost evidence", "cost items", "成本证据", "成本行项目"],
            "production_cost_relationship": ["production cost relationship", "fi orderid", "cost attribution", "成本关系", "成本归属"],
        },
    ),
    (
        "demand-forecast-planning",
        ["pir_evidence", "sales_demand_period_evidence"],
        {
            "pir_evidence": ["pir evidence", "planned independent requirements", "pbim", "pbed", "独立需求", "pir"],
            "sales_demand_period_evidence": ["sales demand period", "demand date", "period attribution", "销售需求期间", "需求日期"],
        },
    ),
    (
        "mrp-exception-analysis",
        ["mrp_coverage_or_supply_demand_evidence"],
        {"mrp_coverage_or_supply_demand_evidence": ["supply-demand", "supply demand", "mrp coverage", "供需", "mrp 覆盖"]},
    ),
    (
        "production-scheduling-capacity",
        ["complete_capacity_bucket_evidence"],
        {"complete_capacity_bucket_evidence": ["capacity bucket", "complete capacity", "产能桶", "完整产能"]},
    ),
):
    SPECS[_agent_id]["requiredLimitations"] = _required
    SPECS[_agent_id]["limitationKeywords"] = {**COMMON_LIMITATION_KEYWORDS, **_keywords}

SPECS["budget-rolling-forecast"]["fieldAliases"] = {
    "actual_ytd": ["net_amount", "actual amount", "actual ytd amount", "累计实际"],
    "annual_plan": ["annual budget", "annual plan amount", "全年计划", "年度预算"],
    "full_year_forecast": ["forecast", "forecast amount", "全年预测"],
    "forecast_variance_pct": ["forecast variance", "forecast variance percent", "预测差异率"],
}
SPECS["budget-rolling-forecast"]["ignoredNoticeKeywords"] = [
    "monthly-average extrapolation",
    "monthly average extrapolation",
    "forecast method",
    "月均外推",
    "预测方法",
]
for _agent_id, _required, _keywords in (
    ("cost-center-expense-anomaly", ["plan_evidence_missing"], {"plan_evidence_missing": ["plan evidence", "plan base missing", "planned expense", "计划证据", "计划基数"]}),
    ("budget-rolling-forecast", ["budget_evidence_missing"], {"budget_evidence_missing": ["annual plan missing", "budget evidence", "plan is zero", "年度计划缺失", "预算证据"]}),
    ("internal-order-project-control", ["master_evidence", "plan_evidence", "budget_evidence", "commitment_evidence", "control_object_not_found"], {
        "master_evidence": ["master_evidence"],
        "plan_evidence": ["plan_evidence", "plan evidence", "plan missing", "计划证据", "计划缺失"],
        "budget_evidence": ["budget_evidence", "budget evidence", "budget missing", "预算"],
        "commitment_evidence": ["commitment_evidence", "commitment evidence", "commitment missing", "承诺"],
        "control_object_not_found": ["control object", "object not found", "控制对象"],
    }),
    ("product-cost-variance", ["standard_cost_evidence"], {
        "standard_cost_evidence": ["standard_cost_evidence"],
    }),
    ("co-month-end-allocation-settlement", ["allocation_cycle_evidence", "object_status_evidence", "settlement_rule_evidence"], {
        "allocation_cycle_evidence": ["allocation_cycle_evidence"],
        "object_status_evidence": ["object_status_evidence"],
        "settlement_rule_evidence": ["settlement_rule_evidence"],
    }),
):
    SPECS[_agent_id]["requiredLimitations"] = _required
    SPECS[_agent_id]["limitationKeywords"] = {**COMMON_LIMITATION_KEYWORDS, **_keywords}

SPECS["budget-rolling-forecast"]["limitationKeywords"]["budget_evidence_missing"].extend(
    ["plan unavailable", "annual plan unavailable", "no plan rows", "计划不可用", "无计划行"]
)

for _agent_id, _metric_id, _zero_status, _nonzero_status in (
    ("billing-completeness-check", "finding_count", "normal", "attention"),
    ("delivered-not-billed", "delivered_not_billed", "normal", "attention"),
    ("delivery-delay-prediction", "maximum_risk_score", "normal", "attention"),
    ("due-delivery-prioritization", "ranked_requirements", "normal", "attention"),
    ("returns-credit-anomaly", "finding_count", "normal", "attention"),
    ("order-to-cash-status", "cleared_items", "partial", "complete_to_ar_clearing"),
):
    SPECS[_agent_id]["businessStatusFromMetric"] = {
        "metric": _metric_id,
        "zero": _zero_status,
        "nonzero": _nonzero_status,
    }

SPECS["billing-completeness-check"]["valueMappings"] = {
    "cancelled": {
        "no": False,
        "false": False,
        "否": False,
        "yes": True,
        "true": True,
        "是": True,
    },
}
SPECS["delivered-not-billed"]["valueMappings"] = {
    "is_billed": {
        "yes": True,
        "true": True,
        "是": True,
        "no": False,
        "false": False,
        "否": False,
    },
}
SPECS["delivery-delay-prediction"]["blankValueKeywords"] = {
    "delivery_block_reason": ["none", "blank", "空白", "未设置"],
}
SPECS["delivery-delay-prediction"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "schedule_line_delivery_evidence_discrepancy": [
        "schedule-line delivered quantity remains 0",
        "计划行的已交货数量仍为0",
    ],
}
SPECS["delivery-delay-prediction"]["ignoredNoticeKeywords"] = [
    "risk_score is a derived assessment",
    "risk_score为派生评估值",
]

MM_LIMITATION_KEYWORDS = {
    **COMMON_LIMITATION_KEYWORDS,
    "mrp_evidence": [
        "mrp evidence unavailable",
        "mrp coverage unavailable",
        "mrp query incomplete",
        "mrp timeout",
        "mrp 证据不可用",
        "mrp 证据不足",
        "mrp 超时",
    ],
    "pr_evidence": ["pr evidence unavailable", "pr query incomplete", "采购申请证据不足"],
    "po_schedule_evidence": ["po schedule evidence unavailable", "po schedule query incomplete", "计划行证据不足"],
    "source_evidence": ["source evidence unavailable", "source query incomplete", "货源证据不足"],
    "movement_evidence": ["movement evidence unavailable", "movement query incomplete", "移动证据不足"],
    "batch_expiry_evidence": ["batch expiry evidence unavailable", "batch expiry query incomplete", "批次效期证据不足"],
    "supplier_evidence": ["supplier evidence unavailable", "supplier query incomplete", "供应商证据不足"],
    "receipt_evidence": ["receipt evidence unavailable", "receipt query incomplete", "收货证据不足"],
    "low_sample_confidence": [
        "low sample",
        "fewer than five",
        "less than 5",
        "少于 5",
        "低样本",
        "样本不足",
    ],
}
for _agent_id in (
    "inventory-health-balancing",
    "material-shortage-procurement-response",
    "intelligent-sourcing-rfq",
    "supplier-performance-risk",
):
    SPECS[_agent_id]["limitationKeywords"] = MM_LIMITATION_KEYWORDS
SPECS["material-shortage-procurement-response"]["blockingLimitations"] = [
    "mrp_evidence"
]
SPECS["inventory-health-balancing"]["valueMappings"] = {
    "batch": {
        "(blank)": "_NO_BATCH",
        "blank": "_NO_BATCH",
    }
}
SPECS["inventory-health-balancing"]["constantDefaults"] = {
    "batch": "_NO_BATCH",
}
SPECS["supplier-performance-risk"]["booleanFields"] = ["on_time_in_full"]
SPECS["supplier-performance-risk"]["fieldExtractors"] = {
    "business_status": {
        "source": "business_status",
        "default": "attention",
        "always": True,
    }
}
SPECS["supplier-performance-risk"]["ignoredNoticeKeywords"] = [
    "source_complete=true",
]
SPECS["inventory-health-balancing"]["limitationKeywords"] = {
    **MM_LIMITATION_KEYWORDS,
    "historical_stock_balance_evidence": [
        "historical stock balance",
        "no key date",
        "current stock has no",
        "historical_stock_balance_evidence",
        "历史库存余额",
        "没有关键日期",
    ],
}
SPECS["inventory-health-balancing"]["blockingLimitations"] = [
    "historical_stock_balance_evidence"
]

SPECS["returns-credit-anomaly"]["limitationKeywords"] = {
    **COMMON_LIMITATION_KEYWORDS,
    "return_receipt_evidence": [
        "receipt field is blank",
        "receipt status unavailable",
        "return_receipt_evidence",
        "收货字段为空",
        "收货状态不可用",
    ],
    "return_refund_type_evidence": [
        "refund-type field is blank",
        "refund type unavailable",
        "return_refund_type_evidence",
        "退款类型字段为空",
        "退款类型不可用",
    ],
}
SPECS["returns-credit-anomaly"]["blankValueKeywords"] = {
    "reference_document": [
        "no follow-on",
        "not found",
        "billingdocumenttype",
        "blank",
        "未找到",
        "为空",
    ],
    "material_received": ["unknown", "unavailable", "未知", "不可用"],
    "refund_type": ["unknown", "unavailable", "未知", "不可用"],
}
SPECS["returns-credit-anomaly"]["blockingLimitations"] = [
    "return_receipt_evidence",
    "return_refund_type_evidence",
]

SPECS["mrp-exception-analysis"]["fieldAliases"] = {
    "quantity": ["open_quantity"],
    "unit": ["base_unit"],
}
SPECS["mrp-exception-analysis"]["fieldExtractors"] = {
    "mrp_element": {
        "source": "mrp_element_category",
        "contains": {"WB": "_STOCK"},
        "default": "",
    }
}
SPECS["mrp-exception-analysis"]["codeSetFields"] = ["exception_message"]
SPECS["mrp-exception-analysis"]["valueMappings"] = {
    "business_status": {
        "no_exception_detected": "attention",
        "supply_exception_detected": "attention",
    }
}
SPECS["mrp-exception-analysis"]["ignoredNoticeKeywords"] = [
    "source_complete=true",
]
SPECS["budget-rolling-forecast"]["zeroPadFields"] = {"current_period": 3}
SPECS["production-order-monitoring"]["booleanFields"] = ["confirmed"]
SPECS["production-order-monitoring"]["valueMappings"] = {
    "business_status": {"attention_required": "attention"}
}
SPECS["procure-to-pay-status"]["fieldAliases"] = {
    "unit": ["order_unit"],
}
SPECS["procure-to-pay-status"]["valueMappings"] = {
    "business_status": {
        "receipt_recorded_invoice_branch_empty_no_clearing_or_payment_evidence": "partial",
    }
}
SPECS["procure-to-pay-status"]["fieldExtractors"] = {
    "business_status": {
        "source": "business_status",
        "contains": {
            "partially received": "partial",
            "receipt recorded": "partial",
        },
        "default": "partial",
        "always": True,
    }
}
SPECS["procure-to-pay-status"]["limitationsFromMetrics"] = {
    "supplier_invoice_items": {
        "zero": "p2p_supplier_invoice_evidence",
    }
}
SPECS["internal-order-project-control"]["valueMappings"] = {
    "object_type": {"internal_order": "INTERNAL_ORDER"}
}
SPECS["production-variance-analysis"]["ignoredNoticeKeywords"] = [
    "empty cost table does not mean actual cost is zero",
    "空成本表不表示实际成本为零",
]
SPECS["mrp-exception-analysis"]["requiredLimitations"] = []
SPECS["mrp-exception-analysis"]["blockingLimitations"] = []
SPECS["returns-credit-anomaly"]["fieldExtractors"] = {
    "reference_document": {
        "source": "reference_document",
        "pattern": r"(\d{8,10}/\d+)",
        "default": "",
        "always": True,
    }
}


def migrate(root: Path) -> int:
    changed = 0
    found: set[str] = set()
    for path in sorted((root / "agents").glob("*/*/agent.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        slug = str(manifest.get("slug") or path.parent.name)
        if slug == "ap-payment":
            continue
        if slug not in SPECS:
            raise ValueError(f"missing v2 acceptance spec for {slug}")
        found.add(slug)
        execution = manifest.get("execution")
        if not isinstance(execution, dict):
            raise ValueError(f"{path} has no execution contract")
        if execution.get("acceptance") != SPECS[slug]:
            execution["acceptance"] = SPECS[slug]
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed += 1
    missing = set(SPECS) - found
    if missing:
        raise ValueError(f"acceptance specs do not map to manifests: {sorted(missing)!r}")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the remaining fixed Agents to acceptance schema v2.")
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    changed = migrate(Path(args.repository).resolve())
    print(json.dumps({"updated_agents": changed, "contract_count": len(SPECS)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
