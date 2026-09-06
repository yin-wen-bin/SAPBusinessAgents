import { useEffect, useMemo, useState } from "react";

type Locale = "zh" | "en";
type Props = { apiBase: string; locale: Locale; runPath: string };

const text = {
  zh: {
    eyebrow: "固定 Agent 全生命周期管理", heading: "Agent 管理中心",
    lead: "创建、修改、验证、发布、启停和回滚确定性固定 Agent。岗位匹配助理等平台助理不在这里管理。",
    active: "使用中的 Agent", inactive: "已停用 Agent", drafts: "未发布草稿", create: "创建 Agent",
    loading: "正在读取 Agent 目录…", empty: "当前没有符合条件的 Agent。", view: "管理",
    version: "版本", validation: "验收", dependencies: "工作流引用", createVersion: "创建新版本",
    deactivate: "停用", activate: "重新启用", rollback: "回滚", delete: "永久删除",
    deleteBlocked: "当前不能永久删除", back: "返回目录", source: "创建方式", blank: "从空白模板",
    clone: "复制现有 Agent", free: "从成功自由查询", gap: "从工作流缺口", agentId: "Agent 技术 ID",
    module: "模块", sourceAgent: "源 Agent", runId: "自由查询运行编号", workflowDraft: "工作流草稿编号",
    gapId: "能力缺口编号", start: "生成草稿", compose: "定义与修改", review: "检查 Diff",
    validate: "验证", publish: "发布与启用", save: "保存新修订", feedback: "告诉 Codex 需要如何修改",
    sendFeedback: "提交修改要求", manifest: "Agent 结构化定义", readme: "业务说明", rules: "受控规则代码",
    diff: "本轮变更", noDiff: "当前修订没有变更。", staticCheck: "运行自动检查", liveCheck: "GET-only 真机验证",
    autoDiscover: "留空时使用 Agent 验收默认样本", input: "验证输入（JSON）", report: "验证报告",
    targetVersion: "目标版本", publishInactive: "发布为未启用版本", publishActive: "发布并启用",
    gitNote: "发布会自动创建本地 Git 分支和提交，不会推送远端。", passRequired: "只有 PASS 版本才能启用。",
    confirmId: "输入完整 Agent ID 确认永久删除", reason: "原因（可选）", patch: "补丁版本",
    minor: "次版本", major: "主版本", openRun: "查看验证运行", refresh: "刷新",
    agent: "Agent", state: "状态", actions: "操作", revision: "修订", updatedAt: "更新时间",
    draftSource: "创建来源", deleteDraft: "删除草稿", cancel: "取消",
    deleteDraftWarning: "此操作会永久删除草稿包、修订和对话，无法恢复。历史验证运行将继续保留。",
    confirmDraftId: "输入完整 Agent ID 确认删除草稿", draftDeleted: "未发布草稿已删除。",
    statusLabels: { draft: "编辑中", invalid: "检查未通过", validated: "已验证", validating: "正在真机验证", needs_review: "需要复核", published: "已发布", cancelled: "已取消", active: "使用中", inactive: "已停用", PASS: "通过", BLOCKED: "阻塞", NOT_TESTED: "未测试", INCONCLUSIVE: "证据不足", FAIL: "失败" },
    sourceLabels: { blank: "空白模板", clone: "复制现有 Agent", free_query: "成功自由查询", workflow_gap: "工作流能力缺口" },
  },
  en: {
    eyebrow: "Deterministic Agent lifecycle", heading: "Agent management center",
    lead: "Create, revise, validate, publish, activate, deactivate and roll back deterministic fixed Agents. Platform assistants are excluded.",
    active: "Active Agents", inactive: "Inactive Agents", drafts: "Unpublished drafts", create: "Create Agent",
    loading: "Loading Agent catalog…", empty: "No Agents match this view.", view: "Manage",
    version: "Version", validation: "Acceptance", dependencies: "Workflow references", createVersion: "Create new version",
    deactivate: "Deactivate", activate: "Activate", rollback: "Roll back", delete: "Delete permanently",
    deleteBlocked: "Permanent deletion is unavailable", back: "Back to catalog", source: "Creation source", blank: "Blank template",
    clone: "Clone existing Agent", free: "Successful free query", gap: "Workflow capability gap", agentId: "Agent technical ID",
    module: "Module", sourceAgent: "Source Agent", runId: "Free-query run ID", workflowDraft: "Workflow draft ID",
    gapId: "Capability gap ID", start: "Generate draft", compose: "Define and revise", review: "Review Diff",
    validate: "Validate", publish: "Publish and activate", save: "Save revision", feedback: "Tell Codex what to change",
    sendFeedback: "Submit revision request", manifest: "Structured Agent definition", readme: "Business README", rules: "Managed rule code",
    diff: "Revision changes", noDiff: "This revision has no changes.", staticCheck: "Run automatic checks", liveCheck: "GET-only live validation",
    autoDiscover: "Use Agent acceptance defaults when input is empty", input: "Validation input (JSON)", report: "Validation report",
    targetVersion: "Target version", publishInactive: "Publish inactive", publishActive: "Publish and activate",
    gitNote: "Publication creates and commits a local Git branch. It never pushes.", passRequired: "Only PASS versions can be activated.",
    confirmId: "Enter the complete Agent ID to confirm permanent deletion", reason: "Reason (optional)", patch: "Patch",
    minor: "Minor", major: "Major", openRun: "Open validation run", refresh: "Refresh",
    agent: "Agent", state: "Status", actions: "Actions", revision: "Revision", updatedAt: "Updated",
    draftSource: "Source", deleteDraft: "Delete draft", cancel: "Cancel",
    deleteDraftWarning: "This permanently deletes the draft package, revisions and conversation. Validation runs remain available.",
    confirmDraftId: "Enter the complete Agent ID to confirm draft deletion", draftDeleted: "The unpublished draft was deleted.",
    statusLabels: { draft: "Editing", invalid: "Checks failed", validated: "Validated", validating: "Live validation running", needs_review: "Needs review", published: "Published", cancelled: "Cancelled", active: "Active", inactive: "Inactive", PASS: "Passed", BLOCKED: "Blocked", NOT_TESTED: "Not tested", INCONCLUSIVE: "Inconclusive", FAIL: "Failed" },
    sourceLabels: { blank: "Blank template", clone: "Existing Agent copy", free_query: "Successful free query", workflow_gap: "Workflow capability gap" },
  },
};

function localized(value: any, locale: Locale): string {
  if (value && typeof value === "object") return String(value[locale] || value.zh || value.en || "");
  return String(value || "");
}

function mappedLabel(values: Record<string, string>, value: unknown): string {
  const key = String(value || "");
  return values[key] || key || "—";
}

function formattedDate(value: unknown, locale: Locale): string {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

async function request(url: string, init?: RequestInit) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(init?.headers || {}) }, ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(detail?.message || detail || body?.message || `HTTP ${response.status}`);
  }
  return body;
}

export default function AgentManagementCenter({ apiBase, locale, runPath }: Props) {
  const t = text[locale];
  const [catalog, setCatalog] = useState<any[]>([]);
  const [drafts, setDrafts] = useState<any[]>([]);
  const [tab, setTab] = useState<"active" | "inactive" | "drafts">("active");
  const [selected, setSelected] = useState<any>(null);
  const [draft, setDraft] = useState<any>(null);
  const [step, setStep] = useState<"compose" | "review" | "validate" | "publish">("compose");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<any>({ source: "blank", agentId: "", module: "Common", sourceAgentId: "", runId: "", workflowDraftId: "", gapId: "" });
  const [manifestText, setManifestText] = useState("");
  const [readme, setReadme] = useState("");
  const [rules, setRules] = useState("");
  const [feedback, setFeedback] = useState("");
  const [validationInput, setValidationInput] = useState("{}");
  const [validationReport, setValidationReport] = useState<any>(null);
  const [bump, setBump] = useState<"patch" | "minor" | "major">("patch");
  const [targetVersion, setTargetVersion] = useState("");
  const [confirmId, setConfirmId] = useState("");
  const [reason, setReason] = useState("");
  const [deleteCandidate, setDeleteCandidate] = useState<any>(null);
  const [draftConfirmId, setDraftConfirmId] = useState("");

  const load = async () => {
    setError("");
    try {
      const [agents, openDrafts] = await Promise.all([
        request(`${apiBase}/api/agents/catalog?state=all`),
        request(`${apiBase}/api/authoring/agents?state=unpublished`),
      ]);
      setCatalog(agents); setDrafts(openDrafts);
      const requestedAgent = new URLSearchParams(window.location.search).get("agent");
      if (requestedAgent) setSelected(agents.find((item: any) => item.id === requestedAgent) || null);
    } catch (e: any) { setError(e.message); }
  };

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const draftId = params.get("draft");
    if (draftId) void openDraft(draftId, (params.get("step") as any) || "compose");
  }, []);

  const list = useMemo(() => {
    if (tab === "drafts") {
      return [...drafts].sort((left, right) =>
        String(right.updated_at || "").localeCompare(String(left.updated_at || ""))
      );
    }
    return catalog
      .filter((item) => item.lifecycle?.state === tab)
      .sort((left, right) => {
        const moduleOrder = String(left.module || "").localeCompare(String(right.module || ""));
        return moduleOrder || localized(left.title, locale).localeCompare(localized(right.title, locale), locale);
      });
  }, [tab, catalog, drafts, locale]);

  const openDraft = async (draftId: string, requestedStep: any = "compose") => {
    setBusy(true); setError("");
    try {
      const value = await request(`${apiBase}/api/authoring/agents/${encodeURIComponent(draftId)}`);
      setDraft(value); setSelected(null); setCreateOpen(false); setStep(requestedStep);
      setManifestText(JSON.stringify(value.package.manifest, null, 2));
      setReadme(value.package.readme || ""); setRules(value.package.rules || "");
      setTargetVersion(value.target_version || value.package.manifest.version || "0.1.0");
      setValidationReport(value.validation || null);
      history.replaceState({}, "", `${window.location.pathname}?draft=${encodeURIComponent(draftId)}&step=${requestedStep}`);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const createDraft = async () => {
    setBusy(true); setError("");
    try {
      const payload: any = { source: form.source, locale };
      for (const key of ["agentId", "module", "sourceAgentId", "runId", "workflowDraftId", "gapId"]) if (form[key]) payload[key] = form[key];
      const value = await request(`${apiBase}/api/authoring/agents`, { method: "POST", body: JSON.stringify(payload) });
      await openDraft(value.draft_id);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const createVersion = async () => {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const value = await request(`${apiBase}/api/agents/${encodeURIComponent(selected.id)}/versions/draft`, {
        method: "POST", body: JSON.stringify({ bump, expectedVersion: selected.version, expectedAgentHash: selected.digest }),
      });
      await openDraft(value.draft_id || value.draft?.draft_id);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const saveRevision = async () => {
    if (!draft) return;
    setBusy(true); setError("");
    try {
      const manifest = JSON.parse(manifestText);
      const value = await request(`${apiBase}/api/authoring/agents/${encodeURIComponent(draft.draft_id)}`, {
        method: "PUT", body: JSON.stringify({ expectedRevision: draft.revision, manifest, readme, rules }),
      });
      await openDraft(value.draft_id, "review"); setNotice(locale === "zh" ? "新修订已保存，请检查 Diff。" : "Revision saved. Review the Diff.");
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const submitFeedback = async () => {
    if (!draft || !feedback.trim()) return;
    setBusy(true); setError("");
    try {
      const value = await request(`${apiBase}/api/authoring/agents/${encodeURIComponent(draft.draft_id)}/feedback`, {
        method: "POST", body: JSON.stringify({ baseTurn: (draft.conversation || []).length, baseRevision: draft.revision, feedback, locale }),
      });
      setFeedback(""); await openDraft(value.draft_id, "review");
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const runStatic = async () => {
    if (!draft) return; setBusy(true); setError("");
    try { const value = await request(`${apiBase}/api/authoring/agents/${draft.draft_id}/validate`, { method: "POST" }); await openDraft(value.draft_id, "validate"); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const runLive = async () => {
    if (!draft) return; setBusy(true); setError("");
    try {
      const input = JSON.parse(validationInput || "{}");
      const report = await request(`${apiBase}/api/authoring/agents/${draft.draft_id}/live-validate`, { method: "POST", body: JSON.stringify({ input, autoDiscover: Object.keys(input).length === 0 }) });
      setValidationReport(report); setNotice(locale === "zh" ? "真机验证已启动。完成后请刷新验证报告。" : "Live validation started. Refresh the report when it completes.");
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const refreshReport = async () => {
    if (!draft) return; setBusy(true);
    try { const report = await request(`${apiBase}/api/authoring/agents/${draft.draft_id}/validation-report`); setValidationReport(report); await openDraft(draft.draft_id, "validate"); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const publish = async (activate: boolean) => {
    if (!draft) return; setBusy(true); setError("");
    try {
      const result = await request(`${apiBase}/api/authoring/agents/${draft.draft_id}/publish`, { method: "POST", body: JSON.stringify({ expectedRevision: draft.revision, targetVersion, activate, validationReportDigest: validationReport?.report_digest || null }) });
      setNotice(`${result.branch} · ${result.commit_sha} · ${locale === "zh" ? "未推送" : "not pushed"}`); setDraft(null); history.replaceState({}, "", window.location.pathname); await load();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const lifecycleAction = async (action: string, version?: string) => {
    if (!selected) return; setBusy(true); setError("");
    try {
      const body: any = { expectedVersion: selected.version, expectedAgentHash: selected.digest, reason };
      if (version) body.version = version;
      if (action === "delete") body.confirmAgentId = confirmId;
      const result = await request(`${apiBase}/api/agents/${encodeURIComponent(selected.id)}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST", body: JSON.stringify(body) });
      setNotice(`${result.branch || ""} ${result.commit_sha || ""}`); setSelected(null); await load();
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  const deleteDraft = async (item: any) => {
    setBusy(true); setError("");
    try {
      await request(`${apiBase}/api/authoring/agents/${encodeURIComponent(item.draft_id)}`, {
        method: "DELETE",
        body: JSON.stringify({ expectedRevision: item.revision, confirmAgentId: draftConfirmId }),
      });
      setDeleteCandidate(null); setDraftConfirmId("");
      await load(); setNotice(t.draftDeleted);
    } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  };

  if (draft) return <main className="agent-management"><header><button onClick={() => { setDraft(null); history.replaceState({}, "", window.location.pathname); }}>{t.back}</button><p className="eyebrow">{t.eyebrow}</p><h1>{localized(draft.package.manifest.title, locale)}</h1><p><code>{draft.agent_id}</code> · {t.version} {draft.package.manifest.version}</p></header>
    <nav className="agent-steps">{(["compose", "review", "validate", "publish"] as const).map((name, index) => <button className={step === name ? "active" : ""} onClick={() => setStep(name)}>{index + 1}. {t[name]}</button>)}</nav>
    {error && <p className="agent-alert error">{error}</p>}{notice && <p className="agent-alert" aria-live="polite">{notice}</p>}
    {step === "compose" && <section className="agent-panel"><label>{t.manifest}<textarea rows={22} value={manifestText} onChange={(e) => setManifestText(e.target.value)} /></label><label>{t.readme}<textarea rows={8} value={readme} onChange={(e) => setReadme(e.target.value)} /></label><label>{t.rules}<textarea rows={12} value={rules} onChange={(e) => setRules(e.target.value)} /></label><div className="agent-actions"><button disabled={busy} onClick={saveRevision}>{t.save}</button></div><label>{t.feedback}<textarea rows={4} value={feedback} onChange={(e) => setFeedback(e.target.value)} /></label><button disabled={busy || !feedback.trim()} onClick={submitFeedback}>{t.sendFeedback}</button></section>}
    {step === "review" && <section className="agent-panel"><h2>{t.diff}</h2>{(draft.diff || []).length ? <table><thead><tr><th>Path</th><th>Change</th></tr></thead><tbody>{draft.diff.map((item: any, i: number) => <tr key={i}><td><code>{item.path}</code></td><td>{item.change}</td></tr>)}</tbody></table> : <p>{t.noDiff}</p>}<button onClick={() => setStep("validate")}>{t.validate}</button></section>}
    {step === "validate" && <section className="agent-panel"><p>{t.passRequired}</p><button disabled={busy} onClick={runStatic}>{t.staticCheck}</button><label>{t.input}<textarea rows={8} value={validationInput} onChange={(e) => setValidationInput(e.target.value)} /></label><p>{t.autoDiscover}</p><div className="agent-actions"><button disabled={busy} onClick={runLive}>{t.liveCheck}</button><button disabled={busy} onClick={refreshReport}>{t.refresh}</button></div>{validationReport && <><h2>{t.report}</h2><dl><dt>Status</dt><dd>{validationReport.status || validationReport.verdict}</dd><dt>Verdict</dt><dd>{validationReport.verdict}</dd><dt>Source complete</dt><dd>{String(validationReport.source_complete ?? "-")}</dd><dt>Evidence complete</dt><dd>{String(validationReport.evidence_complete ?? "-")}</dd></dl>{validationReport.run_id && <a href={`${runPath}?run=${encodeURIComponent(validationReport.run_id)}`}>{t.openRun}</a>}</>}</section>}
    {step === "publish" && <section className="agent-panel"><p>{t.gitNote}</p><label>{t.targetVersion}<input value={targetVersion} onChange={(e) => setTargetVersion(e.target.value)} /></label><div className="agent-actions"><button disabled={busy || validationReport?.verdict !== "PASS"} onClick={() => publish(false)}>{t.publishInactive}</button><button disabled={busy || validationReport?.verdict !== "PASS"} onClick={() => publish(true)}>{t.publishActive}</button></div></section>}
  </main>;

  if (selected) return <main className="agent-management"><button onClick={() => setSelected(null)}>{t.back}</button><p className="eyebrow">{t.eyebrow}</p><h1>{localized(selected.title, locale)}</h1><p>{localized(selected.summary, locale)}</p><dl><dt>ID</dt><dd><code>{selected.id}</code></dd><dt>{t.version}</dt><dd>{selected.version}</dd><dt>{t.validation}</dt><dd>{selected.validation?.verdict || "-"}</dd><dt>{t.dependencies}</dt><dd>{selected.workflow_dependencies?.length || 0}</dd></dl>
    {error && <p className="agent-alert error">{error}</p>}{notice && <p className="agent-alert">{notice}</p>}
    <section className="agent-panel"><h2>{t.createVersion}</h2><select value={bump} onChange={(e) => setBump(e.target.value as any)}><option value="patch">{t.patch}</option><option value="minor">{t.minor}</option><option value="major">{t.major}</option></select><button disabled={busy || !selected.management?.can_create_version} onClick={createVersion}>{t.createVersion}</button></section>
    <section className="agent-panel"><label>{t.reason}<input value={reason} onChange={(e) => setReason(e.target.value)} /></label><div className="agent-actions">{selected.lifecycle?.state === "active" ? <button disabled={busy} onClick={() => lifecycleAction("deactivate")}>{t.deactivate}</button> : <button disabled={busy} onClick={() => lifecycleAction("activate")}>{t.activate}</button>}</div></section>
    <section className="agent-panel danger"><h2>{t.delete}</h2>{(selected.management?.delete_blockers || []).length > 0 && <ul>{selected.management.delete_blockers.map((item: string) => <li>{item}</li>)}</ul>}<label>{t.confirmId}<input value={confirmId} onChange={(e) => setConfirmId(e.target.value)} /></label><button disabled={busy || !selected.management?.can_delete || confirmId !== selected.id} onClick={() => lifecycleAction("delete")}>{t.delete}</button></section>
  </main>;

  return <main className="agent-management"><header><p className="eyebrow">{t.eyebrow}</p><h1>{t.heading}</h1><p>{t.lead}</p><button onClick={() => setCreateOpen(!createOpen)}>{t.create}</button></header>{error && <p className="agent-alert error" aria-live="assertive">{error}</p>}{notice && <p className="agent-alert" aria-live="polite">{notice}</p>}
    {createOpen && <section className="agent-panel"><label>{t.source}<select value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}><option value="blank">{t.blank}</option><option value="clone">{t.clone}</option><option value="free_query">{t.free}</option><option value="workflow_gap">{t.gap}</option></select></label>{form.source === "blank" && <><label>{t.agentId}<input value={form.agentId} onChange={(e) => setForm({ ...form, agentId: e.target.value })} /></label><label>{t.module}<select value={form.module} onChange={(e) => setForm({ ...form, module: e.target.value })}>{["Common", "FI", "CO", "MM", "SD", "PP"].map((m) => <option>{m}</option>)}</select></label></>}{form.source === "clone" && <label>{t.sourceAgent}<select value={form.sourceAgentId} onChange={(e) => setForm({ ...form, sourceAgentId: e.target.value })}><option value=""></option>{catalog.map((item) => <option value={item.id}>{localized(item.title, locale)}</option>)}</select></label>}{["free_query", "workflow_gap"].includes(form.source) && <label>{t.runId}<input value={form.runId} onChange={(e) => setForm({ ...form, runId: e.target.value })} /></label>}{form.source === "workflow_gap" && <><label>{t.workflowDraft}<input value={form.workflowDraftId} onChange={(e) => setForm({ ...form, workflowDraftId: e.target.value })} /></label><label>{t.gapId}<input value={form.gapId} onChange={(e) => setForm({ ...form, gapId: e.target.value })} /></label></>}<button disabled={busy} onClick={createDraft}>{t.start}</button></section>}
    <nav className="agent-tabs"><button className={tab === "active" ? "active" : ""} onClick={() => { setTab("active"); setDeleteCandidate(null); }}>{t.active}</button><button className={tab === "inactive" ? "active" : ""} onClick={() => { setTab("inactive"); setDeleteCandidate(null); }}>{t.inactive}</button><button className={tab === "drafts" ? "active" : ""} onClick={() => setTab("drafts")}>{t.drafts}</button></nav>
    {list.length === 0 ? <p>{t.empty}</p> : tab === "drafts" ? <section className="agent-management-list agent-management-list-drafts" aria-label={t.drafts}>
      <div className="agent-list-header" aria-hidden="true"><span>{t.module}</span><span>{t.agent}</span><span>{t.draftSource}</span><span>{t.targetVersion}</span><span>{t.state}</span><span>{t.updatedAt}</span><span>{t.actions}</span></div>
      {list.map((item: any) => <article className="agent-list-item" key={item.draft_id}>
        <div className="agent-list-row">
          <div data-label={t.module}>{item.module || "—"}</div>
          <div className="agent-list-identity" data-label={t.agent}><strong>{localized(item.title, locale) || item.agent_id}</strong><code>{item.agent_id}</code></div>
          <div data-label={t.draftSource}>{mappedLabel(t.sourceLabels, item.source_type)}</div>
          <div data-label={t.targetVersion}>{item.target_version || "—"}</div>
          <div data-label={t.state}><span className="agent-list-status">{mappedLabel(t.statusLabels, item.status)}</span><small>{t.revision} {item.revision}</small></div>
          <div data-label={t.updatedAt}>{formattedDate(item.updated_at, locale)}</div>
          <div className="agent-list-actions" data-label={t.actions}><button onClick={() => openDraft(item.draft_id)}>{t.view}</button><button className="agent-danger-action" disabled={busy || !item.management?.can_delete} title={(item.management?.delete_blockers || []).join(", ")} onClick={() => { setDeleteCandidate(item); setDraftConfirmId(""); setError(""); }}>{t.deleteDraft}</button></div>
        </div>
        {deleteCandidate?.draft_id === item.draft_id && <div className="agent-draft-delete-confirm"><p>{t.deleteDraftWarning}</p><label>{t.confirmDraftId}<input value={draftConfirmId} onChange={(event) => setDraftConfirmId(event.target.value)} autoComplete="off" /></label><div className="agent-actions"><button className="agent-secondary-action" disabled={busy} onClick={() => { setDeleteCandidate(null); setDraftConfirmId(""); }}>{t.cancel}</button><button className="agent-danger-action solid" disabled={busy || draftConfirmId !== item.agent_id} onClick={() => deleteDraft(item)}>{t.deleteDraft}</button></div></div>}
      </article>)}
    </section> : <section className="agent-management-list agent-management-list-catalog" aria-label={tab === "active" ? t.active : t.inactive}>
      <div className="agent-list-header" aria-hidden="true"><span>{t.module}</span><span>{t.agent}</span><span>{t.version}</span><span>{t.state}</span><span>{t.actions}</span></div>
      {list.map((item: any) => <article className="agent-list-item" key={item.id}><div className="agent-list-row">
        <div data-label={t.module}>{item.module || "—"}</div>
        <div className="agent-list-identity" data-label={t.agent}><strong>{localized(item.title, locale)}</strong><span>{localized(item.summary, locale)}</span><code>{item.id}</code></div>
        <div data-label={t.version}>{item.version || "—"}</div>
        <div data-label={t.state}><span className="agent-list-status">{mappedLabel(t.statusLabels, item.lifecycle?.state)}</span><small>{t.validation}: {mappedLabel(t.statusLabels, item.validation?.verdict)}</small></div>
        <div className="agent-list-actions" data-label={t.actions}><button onClick={() => setSelected(item)}>{t.view}</button></div>
      </div></article>)}
    </section>}
  </main>;
}
