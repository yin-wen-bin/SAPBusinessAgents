import React from "react";
import type { Locale } from "../lib/types";

type Diagnostic = {
  failure_code?: string;
  budget_seconds?: number;
  elapsed_ms?: number;
  validation_issues?: Array<{ path?: string; constraint?: string }>;
};

export default function RoleConsolidationStatus({ locale, evaluation, diagnostics, issues }: {
  locale: Locale;
  evaluation: Record<string, unknown>;
  diagnostics?: Diagnostic[];
  issues?: Array<{ code?: string; operation_id?: string; suggestion_id?: string }>;
}) {
  if (!evaluation.agent_catalog_complete || evaluation.consolidation_complete !== false) return null;
  const zh = locale === "zh";
  return <section className="role-error role-consolidation-diagnostic" aria-label={zh ? "汇总未完成" : "Consolidation incomplete"}>
    <h3>{zh ? "目录匹配完整，但汇总未完成" : "Catalog matching complete; consolidation incomplete"}</h3>
    <p role="status">{zh
      ? "流程已结束不代表完整分析。已核对的匹配结果可供查看，请先处理未解决操作或以下诊断。"
      : "A completed session is not a complete analysis. Checked matches remain viewable; resolve outstanding operations or diagnostics below."}</p>
    {Array.isArray(issues) && issues.length > 0 && <ul>{issues.map((item, index) => <li key={index}>
      <code>{item.code}</code>{item.operation_id && <> · <code>{item.operation_id}</code></>}
      {item.suggestion_id && <> · <code>{item.suggestion_id}</code></>}
    </li>)}</ul>}
    {Array.isArray(diagnostics) && diagnostics.length > 0 && <details>
      <summary>{zh ? "失败诊断" : "Failure diagnostics"}</summary>
      <ul>{diagnostics.slice(0, 5).map((item, index) => <li key={index}>
        <code>{item.failure_code}</code>
        {Number.isFinite(item.budget_seconds) && <p>{zh ? "阶段预算" : "Stage budget"}: {item.budget_seconds} s</p>}
        {Number.isFinite(item.elapsed_ms) && <p>{zh ? "实际耗时" : "Elapsed"}: {Math.round((item.elapsed_ms || 0) / 1000)} s</p>}
        {Array.isArray(item.validation_issues) && <ul>{item.validation_issues.slice(0, 50).map((issue, number) =>
          <li key={number}><code>{issue.path}</code> · {issue.constraint}</li>)}</ul>}
      </li>)}</ul>
    </details>}
  </section>;
}
