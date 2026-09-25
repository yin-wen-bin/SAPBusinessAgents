import { useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus, localText, presentationCell, trialFailureText } from "../lib/agentDraft";

const PAGE_SIZE = 20;
const display = (value: any, locale: Locale) => typeof value === "boolean" ? (locale === "zh" ? value ? "是" : "否" : value ? "Yes" : "No") : value == null ? "—" : typeof value === "object" ? localText(value, locale) || "—" : String(value);

function initialPages(rows: any[]) {
  const pages: Record<number, any[]> = {};
  for (let offset = 0; offset < rows.length; offset += PAGE_SIZE) pages[offset / PAGE_SIZE] = rows.slice(offset, offset + PAGE_SIZE);
  return pages;
}

function ResultTable({ block, blockIndex, runId, apiBase, locale }: { block: any; blockIndex: number; runId: string; apiBase: string; locale: Locale }) {
  const rows = block.rows || [];
  const columns = block.columns || [];
  const count = block.total_rows ?? rows.length;
  const [page, setPage] = useState(0);
  const [pages, setPages] = useState<Record<number, any[]>>(() => initialPages(rows));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const current = pages[page] || [];
  const loaded = Math.min(count, Object.values(pages).reduce((total, items) => total + items.length, 0));
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const move = async (next: number) => {
    if (pages[next]) { setPage(next); return; }
    setLoading(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/runs/${encodeURIComponent(runId)}/presentation/blocks/${blockIndex}/rows?offset=${next * PAGE_SIZE}&limit=${PAGE_SIZE}`);
      if (!response.ok) throw new Error("page_failed");
      const value = await response.json();
      if (!Array.isArray(value.rows)) throw new Error("page_invalid");
      setPages((existing) => ({ ...existing, [next]: value.rows }));
      setPage(next);
    } catch {
      setError(tr("无法加载下一页业务结果，请重试或打开完整运行记录。", "Could not load the next business-result page. Retry or open the full run."));
    } finally { setLoading(false); }
  };
  return <><div className="run-table-scroll"><table className="run-business-table"><thead><tr>{columns.map((column: any, index: number) => <th key={index}>{localText(column.label || column.title || column, locale)}</th>)}</tr></thead><tbody>{current.map((row: any, index: number) => <tr key={index}>{columns.map((column: any, cell: number) => <td key={cell}>{display(presentationCell(row, cell, column.key || column.id), locale)}</td>)}</tr>)}</tbody></table></div>
    <p>{tr(`已加载 ${loaded} / ${count} 行业务结果。`, `${loaded} / ${count} business-result rows loaded.`)}</p>
    {error && <p className="agent-alert error" role="alert">{error}</p>}
    {count > PAGE_SIZE && <div className="agent-actions"><button type="button" disabled={loading || page === 0} onClick={() => void move(page - 1)}>{tr("上一页", "Previous")}</button><span>{page + 1} / {Math.ceil(count / PAGE_SIZE)}</span><button type="button" disabled={loading || (page + 1) * PAGE_SIZE >= count} onClick={() => void move(page + 1)}>{loading ? tr("正在加载", "Loading") : tr("下一页", "Next")}</button></div>}</>;
}

/** Displays only the existing public presentation, never restricted artifacts or technical raw output. */
export default function AgentDraftResult({ run, trial, locale, runPath, apiBase, feedbackSource, revision }: { run: any; trial?: any; locale: Locale; runPath: string; apiBase: string; feedbackSource?: { source_id: string; digest: string }; revision?: number }) {
  const result = run?.result || {};
  const rule = (result.rule_results || []).find((item: any) => item.business_report);
  const report = rule?.business_report || {};
  const presentation = result.presentation;
  const gaps = rule?.evidence_gaps || result.evidence_gaps || [];
  const blocks = presentation?.blocks || [];
  const failure = trialFailureText(run, locale);
  const businessAvailable = trial?.business_output_available ?? Boolean(rule?.business_report && blocks.length);
  const missingBusinessResult = ["completed", "inconclusive"].includes(String(run?.status)) && !businessAvailable;
  const sourceComplete = rule?.source_complete ?? result.completeness?.source_complete;
  const evidenceComplete = rule?.evidence_complete ?? result.completeness?.business_complete;
  const countMetric = (report.metrics || []).find((item: any) => ["record_count", "result_count", "count"].includes(String(item?.id || item?.key || "").toLowerCase()));
  const tableBlocks = blocks.filter((block: any) => block?.type === "table");
  const completeZeroResult = businessAvailable && sourceComplete === true && evidenceComplete === true && (
    (tableBlocks.length > 0 && tableBlocks.every((block: any) => Number(block.total_rows ?? block.rows?.length ?? 0) === 0))
    || Number(countMetric?.value) === 0
  );
  const artifacts = (result.artifacts || []).filter((item: any) => typeof item?.name === "string" && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(item.name));
  return <div className="draft-trial-result"><h3>{localText(presentation?.title || report.headline || report.summary || result.summary, locale) || (locale === "zh" ? "本次试运行结果" : "Trial result")}</h3><dl><dt>{locale === "zh" ? "业务状态" : "Business status"}</dt><dd>{draftStatus(rule?.business_status || result.business_status || (missingBusinessResult ? "unknown" : run.status), locale)}</dd><dt>{locale === "zh" ? "查询源完整" : "Source complete"}</dt><dd>{display(sourceComplete, locale)}</dd><dt>{locale === "zh" ? "业务证据完整" : "Evidence complete"}</dt><dd>{display(evidenceComplete, locale)}</dd></dl>
    {failure && <section className="agent-alert" role="alert"><h4>{locale === "zh" ? "试运行失败原因" : "Why the trial failed"}</h4><p>{failure}</p></section>}
    {feedbackSource && feedbackSource.source_id === run.run_id && Array.isArray(trial?.errors) && trial.errors.length > 0 && <section className="agent-alert"><h4>{locale === "zh" ? "可标注的试运行诊断" : "Annotatable trial diagnostics"}</h4><ul>{trial.errors.map((issue: any, index: number) => <li key={index} data-draft-ref={`/errors/${index}`} data-draft-annotation-kind="trial_issue" data-draft-source-id={feedbackSource.source_id} data-draft-source-digest={feedbackSource.digest} data-draft-revision={revision}>{localText(issue.message || issue.description, locale) || issue.code || String(issue)}</li>)}</ul></section>}
    {missingBusinessResult && <section className="agent-alert error" role="alert"><h4>{locale === "zh" ? "缺少业务结果" : "Business result missing"}</h4><p>{locale === "zh" ? "查询已执行，但该草稿没有生成业务结果。SAP读取完成不等于业务试运行通过；请补充确定性业务规则、业务报告和展示定义后重新试运行。" : "The query ran, but this draft did not generate a business result. Completing SAP reads does not pass a business trial; add a deterministic business rule, report, and presentation, then run the trial again."}</p></section>}
    {completeZeroResult && <section className="agent-alert" role="status"><h4>{locale === "zh" ? "完整零结果" : "Complete zero result"}</h4><p>{locale === "zh" ? "查询和业务证据均完整，本次条件下没有匹配业务记录。这与执行失败或证据不完整不同。" : "The query and business evidence are complete, and no business records match these conditions. This differs from a failed run or incomplete evidence."}</p></section>}
    {gaps.length > 0 && <section className="agent-alert"><h4>{locale === "zh" ? "证据缺口" : "Evidence gaps"}</h4><ul>{gaps.map((gap: any, index: number) => <li key={index}>{localText(gap.message || gap.description || gap.reason || gap, locale) || gap.code}</li>)}</ul></section>}
    {businessAvailable && blocks.map((block: any, index: number) => <section className={`presentation-block presentation-block-${block.type}`} key={`${run.run_id}:${index}`}>{block.title && <h4>{localText(block.title, locale)}</h4>}
      {block.type === "table" ? <ResultTable block={block} blockIndex={index} runId={run.run_id} apiBase={apiBase} locale={locale} /> : block.type === "metrics" ? <div className="presentation-metrics">{(block.metrics || block.items || []).map((metric: any, number: number) => <div className="presentation-metric" key={number}><span>{localText(metric.label, locale)}</span><strong>{display(metric.value, locale)}</strong></div>)}</div> : block.type === "key_value" ? <dl>{(block.entries || []).map((item: any, number: number) => <div key={number}><dt>{localText(item.label, locale)}</dt><dd>{display(item.value, locale)}</dd></div>)}</dl> : block.type === "bullet_list" ? <ul>{block.items.map((item: any, number: number) => <li key={number}>{localText(item.text || item, locale)}</li>)}</ul> : <p>{localText(block.text || block.content || block.body, locale)}</p>}
    </section>)}
    {businessAvailable && !blocks.length && (report.recommendations || []).length > 0 && <ul>{report.recommendations.map((item: any, index: number) => <li key={index}>{localText(item.text || item, locale)}</li>)}</ul>}
    <div className="agent-actions"><a href={`${runPath}?run=${encodeURIComponent(run.run_id)}`} target="_blank" rel="noreferrer">{locale === "zh" ? "查看完整运行记录与证据" : "Open full run and evidence"}</a>{artifacts.length > 0 && <details><summary>{locale === "zh" ? "下载结果" : "Download results"}</summary><ul>{artifacts.map((artifact: any) => <li key={artifact.name}><a href={`${apiBase}/api/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(artifact.name)}`} target="_blank" rel="noreferrer">{artifact.name}</a></li>)}</ul></details>}</div>
  </div>;
}
