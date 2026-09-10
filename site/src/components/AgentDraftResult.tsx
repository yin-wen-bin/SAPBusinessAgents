import { useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus, localText, presentationCell, trialFailureText } from "../lib/agentDraft";

const display = (value: any, locale: Locale) => typeof value === "boolean" ? (locale === "zh" ? value ? "是" : "否" : value ? "Yes" : "No") : value == null ? "—" : typeof value === "object" ? localText(value, locale) || "—" : String(value);

function ResultTable({ block, locale }: { block: any; locale: Locale }) {
  const [page, setPage] = useState(0);
  const rows = block.rows || [];
  const columns = block.columns || [];
  const count = block.total_rows ?? rows.length;
  return <><div className="run-table-scroll"><table className="run-business-table"><thead><tr>{columns.map((column: any, index: number) => <th key={index}>{localText(column.label || column.title || column, locale)}</th>)}</tr></thead><tbody>{rows.slice(page * 20, page * 20 + 20).map((row: any, index: number) => <tr key={index}>{columns.map((column: any, cell: number) => <td key={cell}>{display(presentationCell(row, cell, column.key || column.id), locale)}</td>)}</tr>)}</tbody></table></div><p>{locale === "zh" ? `已加载 ${rows.length} / ${count} 行；完整证据可在运行记录中查看。` : `${rows.length} / ${count} rows loaded; open the run for full evidence.`}</p>{rows.length > 20 && <div className="agent-actions"><button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>{locale === "zh" ? "上一页" : "Previous"}</button><span>{page + 1} / {Math.ceil(rows.length / 20)}</span><button type="button" disabled={(page + 1) * 20 >= rows.length} onClick={() => setPage(page + 1)}>{locale === "zh" ? "下一页" : "Next"}</button></div>}</>;
}

/** Displays only the existing public presentation, never restricted artifacts or technical raw output. */
export default function AgentDraftResult({ run, locale, runPath, apiBase }: { run: any; locale: Locale; runPath: string; apiBase: string }) {
  const result = run?.result || {};
  const rule = (result.rule_results || []).find((item: any) => item.business_report);
  const report = rule?.business_report || {};
  const presentation = result.presentation;
  const gaps = rule?.evidence_gaps || result.evidence_gaps || [];
  const blocks = presentation?.blocks || [];
  const failure = trialFailureText(run, locale);
  return <div className="draft-trial-result"><h3>{localText(presentation?.title || report.headline || report.summary || result.summary, locale) || (locale === "zh" ? "本次试运行结果" : "Trial result")}</h3><dl><dt>{locale === "zh" ? "业务状态" : "Business status"}</dt><dd>{draftStatus(rule?.business_status || result.business_status || run.status, locale)}</dd><dt>{locale === "zh" ? "查询源完整" : "Source complete"}</dt><dd>{display(rule?.source_complete ?? result.completeness?.source_complete, locale)}</dd><dt>{locale === "zh" ? "业务证据完整" : "Evidence complete"}</dt><dd>{display(rule?.evidence_complete ?? result.completeness?.business_complete, locale)}</dd></dl>
    {failure && <section className="agent-alert" role="alert"><h4>{locale === "zh" ? "试运行失败原因" : "Why the trial failed"}</h4><p>{failure}</p></section>}
    {gaps.length > 0 && <section className="agent-alert"><h4>{locale === "zh" ? "证据缺口" : "Evidence gaps"}</h4><ul>{gaps.map((gap: any, index: number) => <li key={index}>{localText(gap.message || gap.description || gap.reason || gap, locale) || gap.code}</li>)}</ul></section>}
    {blocks.map((block: any, index: number) => <section className={`presentation-block presentation-block-${block.type}`} key={index}>{block.title && <h4>{localText(block.title, locale)}</h4>}
      {block.type === "table" ? <ResultTable block={block} locale={locale} /> : block.type === "metrics" ? <div className="presentation-metrics">{(block.metrics || block.items || []).map((metric: any, number: number) => <div className="presentation-metric" key={number}><span>{localText(metric.label, locale)}</span><strong>{display(metric.value, locale)}</strong></div>)}</div> : block.type === "key_value" ? <dl>{(block.entries || []).map((item: any, number: number) => <div key={number}><dt>{localText(item.label, locale)}</dt><dd>{display(item.value, locale)}</dd></div>)}</dl> : block.type === "bullet_list" ? <ul>{block.items.map((item: any, number: number) => <li key={number}>{localText(item.text || item, locale)}</li>)}</ul> : <p>{localText(block.text || block.content || block.body, locale)}</p>}
    </section>)}
    {!blocks.length && (report.recommendations || []).length > 0 && <ul>{report.recommendations.map((item: any, index: number) => <li key={index}>{localText(item.text || item, locale)}</li>)}</ul>}
    <div className="agent-actions"><a href={`${runPath}?run=${encodeURIComponent(run.run_id)}`} target="_blank" rel="noreferrer">{locale === "zh" ? "查看完整运行记录、证据及下载" : "Open full run, evidence and downloads"}</a><details><summary>{locale === "zh" ? "下载结果" : "Download results"}</summary><a href={`${apiBase}/api/runs/${encodeURIComponent(run.run_id)}/artifacts/result.json`} target="_blank" rel="noreferrer">JSON</a></details></div>
  </div>;
}
