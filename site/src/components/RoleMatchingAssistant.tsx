import React, { useCallback, useEffect, useMemo, useState } from "react";
import type { Locale } from "../lib/types";

type Props = { apiBase: string; locale: Locale; workflowsPath: string };
type Session = {
  session_id: string;
  status: string;
  phase: string;
  current_revision: number;
  error?: { code?: string; message?: string } | null;
  queue_position?: number | null;
  turns?: Array<{ turn: number; kind: string; status: string; message?: string | null; result_revision?: number | null; rematch_mode?: string | null; decision?: Record<string, any> }>;
};
type Revision = {
  revision: number;
  catalog_digest: string;
  result: Record<string, any>;
};
type DocumentItem = { document_id: string; name: string; status: string; issue?: string | null; paths?: string[]; excluded?: boolean };

const text = {
  zh: {
    title: "运行岗位匹配助理", lead: "选择本机材料后，系统会提取正文并交给当前Codex Runtime理解；本地完整路径不会发送给Runtime。",
    paths: "文件或目录路径", pathHint: "每行一个Windows绝对路径或UNC路径。目录会递归扫描，不跟随链接。",
    consent: "我确认选定材料的正文会发送给当前Codex Runtime用于本次岗位匹配分析。",
    preflight: "检查材料", checking: "正在检查…", preflightReady: "材料预检通过", files: "个支持文件", preflightRequired: "请先检查材料。",
    start: "开始只读分析", starting: "正在创建会话…", resume: "当前会话", newSession: "开始新会话",
    progress: "分析进度", phases: { queued: "等待分析", scanning: "扫描材料", extracting: "提取正文", understanding: "理解岗位和流程", matching_agents: "匹配Agent", compiling_workflows: "编译工作流建议", reviewing: "生成分析报告", failed: "分析失败", cancelled: "已取消" },
    operations: "SAP日常操作", matches: "Agent匹配", workflows: "工作流建议", gaps: "Agent能力缺口", documents: "材料与解析问题",
    roles: "岗位", processes: "业务流程", noData: "本轮没有识别到相关内容。", source: "来源", coverage: "覆盖", confidence: "置信度", validation: "验收", createDraft: "创建工作流草稿",
    feedback: "补充材料或修正理解", feedbackHint: "说明需要补充、排除或修正的岗位、流程或操作。用户说明会作为上下文，不会伪装成文档证据。",
    addedPaths: "新增路径（可选，每行一个）", incremental: "增量匹配", full: "全量重新匹配", submitFeedback: "提交反馈并继续", cancel: "取消分析",
    downloads: "下载", report: "Markdown报告", error: "操作失败", revision: "修订", incomplete: "完整性", skipped: "跳过的非SAP操作", unsupported: "无法解析的材料",
    exclude: "在下一轮排除", noDocuments: "尚未取得材料清单。",
    timeline: "分析时间线", turn: "轮次", changes: "变化",
  },
  en: {
    title: "Run role-matching assistant", lead: "After you select local material, extracted text is sent to the current Codex Runtime for analysis; full local paths are not shared with the Runtime.",
    paths: "File or directory paths", pathHint: "One absolute Windows or UNC path per line. Directories are scanned recursively without following links.",
    consent: "I confirm that text from the selected material may be sent to the current Codex Runtime for this role-matching analysis.",
    preflight: "Check material", checking: "Checking…", preflightReady: "Material preflight passed", files: "supported files", preflightRequired: "Check the material first.",
    start: "Start read-only analysis", starting: "Creating session…", resume: "Current session", newSession: "Start new session",
    progress: "Analysis progress", phases: { queued: "Waiting", scanning: "Scanning material", extracting: "Extracting text", understanding: "Understanding roles and processes", matching_agents: "Matching agents", compiling_workflows: "Compiling workflow suggestions", reviewing: "Preparing report", failed: "Analysis failed", cancelled: "Cancelled" },
    operations: "SAP daily operations", matches: "Agent matches", workflows: "Workflow suggestions", gaps: "Agent capability gaps", documents: "Materials and parser issues",
    roles: "Roles", processes: "Processes", noData: "No relevant content was identified in this revision.", source: "Source", coverage: "Coverage", confidence: "Confidence", validation: "Acceptance", createDraft: "Create workflow draft",
    feedback: "Add material or correct the analysis", feedbackHint: "Explain the role, process, or operation to add, exclude, or correct. User context is not presented as document evidence.",
    addedPaths: "Additional paths (optional, one per line)", incremental: "Incremental rematch", full: "Full rematch", submitFeedback: "Submit feedback and continue", cancel: "Cancel analysis",
    downloads: "Downloads", report: "Markdown report", error: "Operation failed", revision: "Revision", incomplete: "Completeness", skipped: "Skipped non-SAP operations", unsupported: "Unparseable material",
    exclude: "Exclude in next revision", noDocuments: "No document inventory is available yet.",
    timeline: "Analysis timeline", turn: "Turn", changes: "Changes",
  },
} as const;

const lines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
const label = (value: any, locale: Locale) => typeof value === "string" ? value : value?.[locale] || value?.zh || value?.en || "—";

export default function RoleMatchingAssistant({ apiBase, locale, workflowsPath }: Props) {
  const copy = text[locale];
  const [paths, setPaths] = useState("");
  const [consent, setConsent] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [feedback, setFeedback] = useState("");
  const [addedPaths, setAddedPaths] = useState("");
  const [mode, setMode] = useState<"incremental" | "full">("incremental");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preflight, setPreflight] = useState<{ ready: boolean; supported_file_count: number; total_bytes: number; issues: any[]; blockers: any[] } | null>(null);

  const load = useCallback(async (sessionId: string) => {
    const response = await fetch(`${apiBase}/api/role-matching/sessions/${sessionId}`);
    if (!response.ok) throw new Error(await response.text());
    const current = await response.json() as Session;
    setSession(current);
    const documentResponse = await fetch(`${apiBase}/api/role-matching/sessions/${sessionId}/documents`);
    if (documentResponse.ok) {
      const items = await documentResponse.json() as DocumentItem[];
      setDocuments(items);
      setExcluded(items.filter((item) => item.excluded).map((item) => item.document_id));
    }
    if (current.current_revision > 0) {
      const result = await fetch(`${apiBase}/api/role-matching/sessions/${sessionId}/revisions/${current.current_revision}`);
      if (result.ok) setRevision(await result.json());
    }
  }, [apiBase]);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("session");
    if (id) load(id).catch((reason) => setError(String(reason)));
  }, [load]);

  useEffect(() => {
    if (!session || ["completed", "failed", "cancelled"].includes(session.status)) return;
    const source = new EventSource(`${apiBase}/api/role-matching/sessions/${session.session_id}/events`);
    const refresh = () => load(session.session_id).catch((reason) => setError(String(reason)));
    source.onmessage = refresh;
    ["session_queued", "scan_started", "extraction_completed", "understanding_started", "matching_started", "workflow_compilation_started", "analysis_completed", "analysis_failed", "session_cancelled"].forEach((name) => source.addEventListener(name, refresh));
    source.onerror = () => { source.close(); refresh(); };
    return () => source.close();
  }, [apiBase, load, session?.session_id, session?.status]);

  const start = async () => {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/role-matching/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths: lines(paths), locale, consentToRuntime: consent }) });
      if (!response.ok) throw new Error(await response.text());
      const created = await response.json() as Session;
      setSession(created); setRevision(null);
      const url = new URL(window.location.href); url.searchParams.set("session", created.session_id); history.replaceState({}, "", url);
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const checkMaterial = async () => {
    setBusy(true); setError(""); setPreflight(null);
    try {
      const response = await fetch(`${apiBase}/api/role-matching/preflight`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paths: lines(paths) }) });
      if (!response.ok) throw new Error(await response.text());
      setPreflight(await response.json());
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const submitFeedback = async () => {
    if (!session || !revision) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/role-matching/sessions/${session.session_id}/feedback`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ baseRevision: revision.revision, message: feedback, rematchMode: mode, addedPaths: lines(addedPaths), excludedDocumentIds: excluded }) });
      if (!response.ok) throw new Error(await response.text());
      setSession(await response.json()); setFeedback(""); setAddedPaths("");
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };

  const createDraft = async (suggestionId: string) => {
    if (!session || !revision) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiBase}/api/role-matching/sessions/${session.session_id}/workflow-suggestions/${suggestionId}/draft`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: revision.revision, expectedCatalogDigest: revision.catalog_digest }) });
      if (!response.ok) throw new Error(await response.text());
      const draft = await response.json();
      window.location.href = `${workflowsPath}?view=create&draft=${encodeURIComponent(draft.draft_id)}&step=review`;
    } catch (reason) { setError(String(reason)); setBusy(false); }
  };

  const result = revision?.result || {};
  const active = session && !["completed", "failed", "cancelled"].includes(session.status);
  const metrics = useMemo(() => ({ roles: result.roles?.length || 0, processes: result.processes?.length || 0, operations: result.operations?.length || 0, matches: result.agent_matches?.length || 0 }), [result]);

  return <section className="role-matching-assistant" aria-live="polite">
    <header><h2>{copy.title}</h2><p>{copy.lead}</p></header>
    {!session && <div className="role-card">
      <label><strong>{copy.paths}</strong><textarea rows={5} value={paths} onChange={(event) => { setPaths(event.target.value); setPreflight(null); }} placeholder={"D:\\BusinessDocs\\P2P\n\\\\server\\share\\SOP"} /></label>
      <p className="field-help">{copy.pathHint}</p>
      <button type="button" className="secondary" onClick={checkMaterial} disabled={busy || lines(paths).length === 0}>{busy ? copy.checking : copy.preflight}</button>
      {preflight && <div className={preflight.ready ? "role-preflight-ready" : "role-error"}><strong>{preflight.ready ? copy.preflightReady : copy.error}</strong><p>{preflight.supported_file_count} {copy.files} · {(preflight.total_bytes / 1024 / 1024).toFixed(2)} MB · {preflight.issues.length} issues</p></div>}
      <label className="role-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /> <span>{copy.consent}</span></label>
      <button type="button" onClick={start} disabled={busy || !consent || !preflight?.ready}>{busy ? copy.starting : copy.start}</button>
    </div>}
    {session && <>
      <div className="role-progress role-card"><div><strong>{copy.progress}</strong><code>{session.session_id}</code></div><div className="role-progress-track"><span className={active ? "active" : ""}></span></div><p>{(copy.phases as any)[session.phase] || session.phase}{session.queue_position ? ` · #${session.queue_position}` : ""}</p>{active && <button type="button" className="secondary" onClick={() => fetch(`${apiBase}/api/role-matching/sessions/${session.session_id}/cancel`, { method: "POST" }).then(() => load(session.session_id))}>{copy.cancel}</button>}</div>
      {session.error && <div className="role-error"><strong>{copy.error}</strong><p>{session.error.message || session.error.code}</p></div>}
      {revision && <>
        <section className="role-result"><h3>{copy.timeline}</h3><ol className="role-timeline">{(session.turns || []).map((turn) => <li key={turn.turn}><strong>{copy.turn} {turn.turn} · {turn.rematch_mode || turn.kind}</strong><span>{turn.status}{turn.result_revision ? ` · ${copy.revision} ${turn.result_revision}` : ""}</span>{turn.message && <p>{turn.message}</p>}{turn.decision?.change_summary && <small>{copy.changes}: {Object.entries(turn.decision.change_summary).map(([key, value]: any) => `${key} +${value.added}/-${value.removed}/~${value.changed}`).join(" · ")}</small>}</li>)}</ol></section>
        <div className="role-metrics"><div><strong>{metrics.roles}</strong><span>{copy.roles}</span></div><div><strong>{metrics.processes}</strong><span>{copy.processes}</span></div><div><strong>{metrics.operations}</strong><span>{copy.operations}</span></div><div><strong>{metrics.matches}</strong><span>{copy.matches}</span></div></div>
        <section className="role-result"><h3>{copy.roles} / {copy.processes}</h3><div className="role-grid">{[...(result.roles || []), ...(result.processes || [])].map((item: any, index: number) => <article key={item.role_id || item.process_id || index}><h4>{label(item.name || item.title, locale)}</h4><p>{label(item.description, locale)}</p><SourceRefs refs={item.evidence_refs} copy={copy} /></article>)}</div></section>
        <section className="role-result"><h3>{copy.operations}</h3>{(result.operations || []).length === 0 ? <p>{copy.noData}</p> : <div className="role-grid">{result.operations.map((item: any) => <article key={item.operation_id}><span className="role-kicker">{item.role || item.department || "SAP"}</span><h4>{label(item.name, locale)}</h4><p>{label(item.description, locale)}</p><small>{item.process || item.sap_system_or_module || ""}</small><SourceRefs refs={item.evidence_refs} copy={copy} /></article>)}</div>}</section>
        <section className="role-result"><h3>{copy.matches}</h3><div className="role-table"><table><thead><tr><th>Agent</th><th>{copy.coverage}</th><th>{copy.confidence}</th><th>{copy.validation}</th><th>{locale === "zh" ? "原因" : "Reason"}</th><th>{copy.source}</th></tr></thead><tbody>{(result.agent_matches || []).map((item: any, index: number) => <tr key={`${item.agent_id}-${index}`}><td><code>{item.agent_id}</code></td><td>{item.coverage}</td><td>{item.confidence}</td><td>{item.validation_verdict}{item.executable ? " · executable" : ""}</td><td>{label(item.reason, locale)}</td><td><SourceRefs refs={item.evidence_refs} copy={copy} /></td></tr>)}</tbody></table></div></section>
        <section className="role-result"><h3>{copy.workflows}</h3>{(result.workflow_suggestions || []).length === 0 ? <p>{copy.noData}</p> : <div className="role-grid">{result.workflow_suggestions.map((item: any) => <article key={item.suggestion_id}><h4>{label(item.title, locale)}</h4><p>{label(item.description, locale)}</p><p>{(item.stages || []).map((stage: any) => stage.agent_id).filter(Boolean).join(" → ")}</p><SourceRefs refs={item.evidence_refs} copy={copy} /><button type="button" onClick={() => createDraft(item.suggestion_id)} disabled={busy}>{copy.createDraft}</button></article>)}</div>}</section>
        <section className="role-result"><h3>{copy.gaps}</h3>{(result.agent_gaps || []).length === 0 ? <p>{copy.noData}</p> : <div className="role-grid">{result.agent_gaps.map((item: any) => <article key={item.gap_id}><h4>{label(item.required_capability, locale)}</h4><p>{label(item.business_impact, locale)}</p><p>{label(item.reason, locale)}</p><SourceRefs refs={item.evidence_refs} copy={copy} /></article>)}</div>}</section>
        <section className="role-result"><h3>{copy.incomplete}</h3><dl className="role-completeness">{Object.entries(result.completeness || {}).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value ? "✓" : "—"}</dd></div>)}</dl><p>{copy.skipped}: {result.non_sap_operation_count || 0} · {copy.unsupported}: {(result.document_issues || []).length}</p><p><a href={`${apiBase}/api/role-matching/sessions/${session.session_id}/revisions/${revision.revision}/report.md`}>{copy.report}</a> · <a href={`${apiBase}/api/role-matching/sessions/${session.session_id}/revisions/${revision.revision}/operations.csv`}>CSV</a> · <a href={`${apiBase}/api/role-matching/sessions/${session.session_id}/revisions/${revision.revision}/report.json`}>JSON</a></p></section>
        <section className="role-result"><h3>{copy.documents}</h3>{documents.length === 0 ? <p>{copy.noDocuments}</p> : <ul className="role-document-list">{documents.map((item) => <li key={item.document_id}><label><input type="checkbox" checked={excluded.includes(item.document_id)} onChange={(event) => setExcluded((current) => event.target.checked ? [...new Set([...current, item.document_id])] : current.filter((value) => value !== item.document_id))} /> <span><strong>{item.name}</strong> · {item.status}{item.issue ? ` · ${item.issue}` : ""}<small>{item.paths?.join(" · ")}</small></span></label></li>)}</ul>}</section>
        <section className="role-card"><h3>{copy.feedback}</h3><p>{copy.feedbackHint}</p><textarea rows={4} value={feedback} onChange={(event) => setFeedback(event.target.value)} /><label>{copy.addedPaths}<textarea rows={3} value={addedPaths} onChange={(event) => setAddedPaths(event.target.value)} /></label><div className="role-feedback-actions"><select value={mode} onChange={(event) => setMode(event.target.value as any)}><option value="incremental">{copy.incremental}</option><option value="full">{copy.full}</option></select><button type="button" onClick={submitFeedback} disabled={busy || !feedback.trim()}>{copy.submitFeedback}</button></div></section>
      </>}
    </>}
    {error && <div className="role-error"><strong>{copy.error}</strong><p>{error}</p></div>}
  </section>;
}

function SourceRefs({ refs, copy }: { refs: any[]; copy: any }) {
  if (!Array.isArray(refs) || refs.length === 0) return null;
  return <details><summary>{copy.source} ({refs.length})</summary><ul>{refs.map((ref, index) => <li key={index}><code>{ref.document_id} · {ref.chunk_id}</code> {formatLocator(ref.locator)}</li>)}</ul></details>;
}

function formatLocator(locator: any) {
  if (!locator || typeof locator !== "object") return "";
  return Object.entries(locator).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("–") : value}`).join(" · ");
}
