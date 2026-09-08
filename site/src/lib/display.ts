import type { AgentValidation, Locale } from "./types";

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

export const validationLabel = (value: string, locale: Locale): string => {
  const labels: Record<string, [string, string]> = {
    PASS: ["验收通过", "Acceptance passed"], PARTIAL: ["部分通过", "Partially passed"],
    BLOCKED: ["因证据或验收缺口受阻", "Blocked by evidence or acceptance gaps"],
    NOT_TESTED: ["尚未验收", "Not yet tested"], FAIL: ["验收未通过", "Acceptance failed"],
    MATCH: ["对比一致", "Match"], MISMATCH: ["对比不一致", "Mismatch"],
    complete: ["完整", "Complete"], partial: ["部分完整", "Partial"], bounded: ["限定范围", "Bounded"],
    true: ["是", "Yes"], false: ["否", "No"],
  };
  return labels[value]?.[locale === "zh" ? 0 : 1] ?? value;
};

export const statusLabel = (status: string, locale: Locale, validation?: AgentValidation) => {
  if (validation?.verdict === "PASS" && validation.documentationReuse) return locale === "zh" ? "复用原版本验收" : "Original acceptance reused";
  if (validation?.verdict === "PASS" && validation.acceptanceMode === "three_stage") {
    return locale === "zh" ? "三级验收通过" : "Three-stage acceptance passed";
  }
  if (validation) return validationLabel(validation.verdict, locale);
  if (status === "Beta") return locale === "zh" ? "试用（平台助理）" : "Beta (platform assistant)";
  return STATUS_LABELS[status]?.[locale] || (locale === "zh" ? "状态待复核" : "Status needs review");
};

export const tagLabel = (tag: string, locale: Locale) =>
  locale === "zh" ? TAG_LABELS_ZH[tag] || tag : tag;
