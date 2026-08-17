import type { Locale } from "./types";

const STATUS_LABELS: Record<string, Record<Locale, string>> = {
  "Live-tested design": { zh: "已完成真机验证的设计", en: "Live-tested design" },
  "Runnable prototype": { zh: "可运行原型", en: "Runnable prototype" },
};

const TAG_LABELS_ZH: Record<string, string> = {
  "Accounts Payable": "应付账款",
  "Accounts Receivable": "应收账款",
  Aging: "账龄",
  Capacity: "产能",
  "Cash Application": "收款核销",
  "Closing Control": "关账控制",
  Collections: "催收",
  Confirmation: "生产确认",
  Consumption: "耗用",
  "Cross Module": "跨模块",
  "Demand Forecast": "需求预测",
  "Document Flow": "凭证流",
  Exception: "异常",
  "Exception Analysis": "异常分析",
  Execution: "执行",
  "Explainable AI": "可解释 AI",
  "Explainable Rules": "可解释规则",
  "GR/IR": "收货/发票收货",
  MRP: "物料需求计划",
  "Material Movement": "物料移动",
  "Month End": "月结",
  "Open Items": "未清项",
  "Order-to-Cash": "订单到收款",
  PIR: "计划独立需求",
  "Payment Risk": "付款风险",
  "Planned Order": "计划订单",
  Planning: "计划",
  "Procure-to-Pay": "采购到付款",
  "Production Order": "生产订单",
  "Purchase Order": "采购订单",
  "Read Only": "只读",
  "Read-only": "只读",
  Reconciliation: "对账",
  Rescheduling: "重排程",
  "Root Cause": "根因",
  "SAP SD": "SAP 销售与分销",
  "Sales Demand": "销售需求",
  Scheduling: "排程",
  Scrap: "报废",
  Shortage: "短缺",
  Status: "状态",
  Variance: "差异",
  "Work Center": "工作中心",
};

export const statusLabel = (status: string, locale: Locale) =>
  STATUS_LABELS[status]?.[locale] || status;

export const tagLabel = (tag: string, locale: Locale) =>
  locale === "zh" ? TAG_LABELS_ZH[tag] || tag : tag;
