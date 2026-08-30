import React, { useCallback, useEffect, useMemo, useState } from "react";
import WorkflowBuilder from "./WorkflowBuilder";
import type {
  AgentDefinition,
  ExecutionInputProperty,
  Locale,
  LocalizedText,
  WorkflowDefinition,
} from "../lib/types";

type CenterProps = { apiBase: string; locale: Locale; runPath: string; askPath: string };
type CenterView = "published" | "create";
type Publication = {
  validation_status: string;
  validation_run_id?: string | null;
  validated_at?: string | null;
  evidence_gap_codes: string[];
  acknowledgement_recorded: boolean;
};
type Lifecycle = {
  state: "active" | "inactive";
  current_version: string;
  workflow_hash: string;
  version_count: number;
  deactivated_at?: string | null;
  deactivation_reason?: string | null;
  business_run_count: number;
};
type WorkflowManagement = {
  can_create_version: boolean;
  can_deactivate: boolean;
  can_activate: boolean;
  can_delete: boolean;
  blockers: string[];
};
type CatalogItem = {
  id: string;
  version: string;
  title: LocalizedText;
  description: LocalizedText;
  read_only: boolean;
  node_count: number;
  publication: Publication;
  lifecycle: Lifecycle;
  management: WorkflowManagement;
};
type VersionSummary = {
  version: string;
  current: boolean;
  workflow_hash: string;
  validation_status: string;
  validation_run_id?: string | null;
  validated_at?: string | null;
};
type WorkflowDetail = WorkflowDefinition & {
  status?: string;
  validation?: Record<string, unknown>;
};

const copy = {
  zh: {
    published: "已发布工作流",
    create: "创建工作流",
    heading: "我的工作流",
    lead: "查看、检查并运行已经完成真机验证和发布的确定性工作流。",
    search: "搜索工作流",
    searchPlaceholder: "按名称、说明或工作流标识搜索",
    loading: "正在读取已发布工作流…",
    empty: "还没有已发布工作流。",
    noMatches: "没有符合当前搜索条件的工作流。",
    loadFailed: "无法读取已发布工作流，请确认本地API已经启动。",
    detailFailed: "无法读取工作流详情。",
    view: "查看详情",
    run: "运行工作流",
    back: "返回工作流列表",
    version: "版本",
    nodes: "业务步骤",
    readOnly: "严格只读",
    validatedPass: "已验证通过",
    validatedWithGaps: "带完整性缺口发布",
    validationUnknown: "发布验证记录不可用",
    validation: "发布验证",
    validationRun: "验证运行",
    validatedAt: "验证时间",
    acknowledged: "缺口已由用户确认",
    gaps: "已确认的完整性缺口",
    noGaps: "没有记录完整性缺口。",
    purpose: "工作流用途",
    sequence: "执行步骤",
    condition: "执行条件",
    always: "始终执行",
    conditional: "仅在上游结果非空时执行",
    skip: "跳过时返回明确的不确定结果",
    inputs: "需要填写的信息",
    outputs: "最终业务输出",
    required: "必填",
    optional: "选填",
    executeTitle: "开始只读运行",
    executeHelp: "工作流只调用已固定的只读Agent、API、Skill和确定性规则。",
    submit: "开始运行",
    submitting: "正在创建运行…",
    inputInvalid: "请检查输入内容。",
    arrayHint: "每行一个，也可使用逗号或分号分隔",
    emptyItem: "不能包含空白项目。",
    unsupportedInput: "当前页面暂不支持此输入类型。",
    activeWorkflows: "使用中的工作流",
    inactiveWorkflows: "已停用工作流",
    inactive: "已停用",
    inactiveHelp: "该工作流不会接受新任务，但历史运行和版本仍然保留。",
    manage: "工作流管理",
    createVersion: "创建新版本",
    deactivate: "停用工作流",
    activate: "重新启用",
    delete: "永久删除",
    versionHistory: "版本历史",
    currentVersion: "当前版本",
    archivedVersion: "历史版本",
    viewVersion: "查看版本",
    bumpPatch: "补丁版本",
    bumpMinor: "次版本",
    bumpMajor: "主版本",
    reason: "操作原因（选填）",
    cancel: "取消",
    confirm: "确认",
    deleteConfirm: "请输入完整工作流ID以确认永久删除",
    deleteWarning: "永久删除会从当前正式目录移除全部版本，但不会改写Git历史。",
    gitPending: "操作已完成。请提交当前Git分支上的修改：",
    managementFailed: "工作流管理操作失败。",
    blockers: "当前不能永久删除",
    noInactive: "还没有已停用工作流。",
    businessRuns: "正式业务运行次数",
    workflowMustBeInactive: "必须先停用工作流",
    workflowHasBusinessRuns: "已经存在正式业务运行",
    workflowHasOpenVersionDrafts: "存在尚未完成的新版本草稿",
    workflowIsReferenced: "被其他正式定义引用",
    closeVersion: "关闭版本详情",
  },
  en: {
    published: "Published workflows",
    create: "Create workflow",
    heading: "My workflows",
    lead: "View, inspect, and run deterministic workflows that completed live validation and publication.",
    search: "Search workflows",
    searchPlaceholder: "Search by name, description, or workflow ID",
    loading: "Loading published workflows…",
    empty: "No workflow has been published yet.",
    noMatches: "No workflow matches the current search.",
    loadFailed: "Published workflows could not be loaded. Confirm that the local API is running.",
    detailFailed: "Workflow details could not be loaded.",
    view: "View details",
    run: "Run workflow",
    back: "Back to workflows",
    version: "Version",
    nodes: "Business steps",
    readOnly: "Strictly read-only",
    validatedPass: "Validation passed",
    validatedWithGaps: "Published with completeness gaps",
    validationUnknown: "Publication validation unavailable",
    validation: "Publication validation",
    validationRun: "Validation run",
    validatedAt: "Validated at",
    acknowledged: "Gaps acknowledged by the user",
    gaps: "Acknowledged completeness gaps",
    noGaps: "No completeness gap was recorded.",
    purpose: "Workflow purpose",
    sequence: "Execution steps",
    condition: "Execution condition",
    always: "Always executed",
    conditional: "Executed only when the upstream result is non-empty",
    skip: "Returns an explicit inconclusive result when skipped",
    inputs: "Required information",
    outputs: "Final business outputs",
    required: "Required",
    optional: "Optional",
    executeTitle: "Start a read-only run",
    executeHelp: "The workflow only calls pinned read-only Agents, APIs, Skills, and deterministic rules.",
    submit: "Start workflow",
    submitting: "Creating run…",
    inputInvalid: "Check the workflow input.",
    arrayHint: "One item per line, or separate items with commas or semicolons",
    emptyItem: "Blank items are not allowed.",
    unsupportedInput: "This input type is not supported by the current page.",
    activeWorkflows: "Active workflows",
    inactiveWorkflows: "Inactive workflows",
    inactive: "Inactive",
    inactiveHelp: "This workflow does not accept new runs, while its history and versions remain available.",
    manage: "Workflow management",
    createVersion: "Create new version",
    deactivate: "Deactivate workflow",
    activate: "Reactivate",
    delete: "Permanently delete",
    versionHistory: "Version history",
    currentVersion: "Current version",
    archivedVersion: "Historical version",
    viewVersion: "View version",
    bumpPatch: "Patch version",
    bumpMinor: "Minor version",
    bumpMajor: "Major version",
    reason: "Reason (optional)",
    cancel: "Cancel",
    confirm: "Confirm",
    deleteConfirm: "Enter the full workflow ID to confirm permanent deletion",
    deleteWarning: "Permanent deletion removes every version from the current catalog but does not rewrite Git history.",
    gitPending: "The operation completed. Commit the changes on this Git branch:",
    managementFailed: "Workflow management failed.",
    blockers: "Permanent deletion is currently blocked",
    noInactive: "There are no inactive workflows.",
    businessRuns: "Business run count",
    workflowMustBeInactive: "Deactivate the workflow first",
    workflowHasBusinessRuns: "The workflow has business runs",
    workflowHasOpenVersionDrafts: "An unfinished version draft exists",
    workflowIsReferenced: "Another published definition references this workflow",
    closeVersion: "Close version details",
  },
} as const;

export default function WorkflowCenter({ apiBase, locale, runPath, askPath }: CenterProps) {
  const [view, setView] = useState<CenterView>("published");
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const t = copy[locale];

  const readLocation = useCallback(() => {
    if (typeof window === "undefined") return;
    const query = new URLSearchParams(window.location.search);
    setView(query.get("view") === "create" ? "create" : "published");
    setWorkflowId(query.get("workflow"));
  }, []);

  useEffect(() => {
    readLocation();
    window.addEventListener("popstate", readLocation);
    return () => window.removeEventListener("popstate", readLocation);
  }, [readLocation]);

  const navigate = useCallback((nextView: CenterView, nextWorkflowId?: string | null) => {
    const query = new URLSearchParams();
    query.set("view", nextView);
    if (nextWorkflowId) query.set("workflow", nextWorkflowId);
    history.pushState({}, "", `${window.location.pathname}?${query.toString()}`);
    setView(nextView);
    setWorkflowId(nextWorkflowId ?? null);
  }, []);

  const openDraft = useCallback((draftId: string) => {
    const query = new URLSearchParams({ view: "create", draft: draftId, step: "review" });
    history.pushState({}, "", `${window.location.pathname}?${query.toString()}`);
    setView("create");
    setWorkflowId(null);
  }, []);

  return (
    <div className="workflow-center">
      <nav className="workflow-center-nav" aria-label={locale === "zh" ? "工作流视图" : "Workflow views"}>
        <div>
          <p className="eyebrow">Workflow Center</p>
          <strong>{t.heading}</strong>
        </div>
        <div className="workflow-center-tabs">
          <button className={view === "published" ? "is-active" : ""} aria-current={view === "published" ? "page" : undefined} onClick={() => navigate("published")}>{t.published}</button>
          <button className={view === "create" ? "is-active" : ""} aria-current={view === "create" ? "page" : undefined} onClick={() => navigate("create")}>{t.create}</button>
        </div>
      </nav>
      {view === "create" ? (
        <WorkflowBuilder
          apiBase={apiBase}
          locale={locale}
          runPath={runPath}
          askPath={askPath}
          onPublished={(id) => navigate("published", id)}
        />
      ) : (
        <PublishedWorkflowCatalog
          apiBase={apiBase}
          locale={locale}
          runPath={runPath}
          selectedWorkflowId={workflowId}
          onSelectWorkflow={(id) => navigate("published", id)}
          onCreate={() => navigate("create")}
          onEditDraft={openDraft}
        />
      )}
    </div>
  );
}

function PublishedWorkflowCatalog({
  apiBase,
  locale,
  runPath,
  selectedWorkflowId,
  onSelectWorkflow,
  onCreate,
  onEditDraft,
}: {
  apiBase: string;
  locale: Locale;
  runPath: string;
  selectedWorkflowId: string | null;
  onSelectWorkflow: (workflowId: string | null) => void;
  onCreate: () => void;
  onEditDraft: (draftId: string) => void;
}) {
  const t = copy[locale];
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [agents, setAgents] = useState<AgentDefinition[]>([]);
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [search, setSearch] = useState("");
  const [focusRun, setFocusRun] = useState(false);
  const [catalogState, setCatalogState] = useState<"active" | "inactive">("active");
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetch(`${apiBase}/api/workflows/catalog?state=all`),
      fetch(`${apiBase}/api/agents`),
    ]).then(async ([catalogResponse, agentResponse]) => {
      if (!catalogResponse.ok) throw new Error(t.loadFailed);
      const catalog = await catalogResponse.json() as CatalogItem[];
      const availableAgents = agentResponse.ok ? await agentResponse.json() as AgentDefinition[] : [];
      if (!cancelled) {
        setItems(catalog);
        setAgents(availableAgents);
        setError("");
      }
    }).catch(() => { if (!cancelled) setError(t.loadFailed); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiBase, reloadToken, t.loadFailed]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setDetailError("");
    if (!selectedWorkflowId) return () => { cancelled = true; };
    fetch(`${apiBase}/api/workflows/${encodeURIComponent(selectedWorkflowId)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(t.detailFailed);
        const value = await response.json() as WorkflowDetail;
        if (!cancelled) setDetail(value);
      })
      .catch(() => { if (!cancelled) setDetailError(t.detailFailed); });
    return () => { cancelled = true; };
  }, [apiBase, reloadToken, selectedWorkflowId, t.detailFailed]);

  useEffect(() => {
    if (!detail || !focusRun) return;
    window.requestAnimationFrame(() => document.getElementById("workflow-run-form")?.scrollIntoView({ behavior: "smooth", block: "start" }));
    setFocusRun(false);
  }, [detail, focusRun]);

  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase(locale === "zh" ? "zh-CN" : "en-US");
    const scoped = items.filter((item) => item.lifecycle.state === catalogState);
    if (!query) return scoped;
    return scoped.filter((item) => [item.id, item.title[locale], item.description[locale]]
      .some((value) => String(value ?? "").toLocaleLowerCase(locale === "zh" ? "zh-CN" : "en-US").includes(query)));
  }, [catalogState, items, locale, search]);
  const selectedItem = items.find((item) => item.id === selectedWorkflowId);
  const agentMap = useMemo(() => new Map(agents.map((agent) => [agent.slug, agent])), [agents]);

  if (selectedWorkflowId) {
    return (
      <main className="workflow-catalog-shell">
        <button className="workflow-back-button" onClick={() => { setFocusRun(false); onSelectWorkflow(null); }}>{t.back}</button>
        {detailError && <p className="workflow-catalog-error" role="alert">{detailError}</p>}
        {!detail && !detailError && <p className="workflow-catalog-state"><span className="workflow-spinner" />{t.loading}</p>}
        {detail && <WorkflowDetails item={selectedItem} workflow={detail} agents={agentMap} locale={locale} apiBase={apiBase} runPath={runPath} onEditDraft={onEditDraft} onChanged={() => setReloadToken((value) => value + 1)} onDeleted={() => onSelectWorkflow(null)} />}
      </main>
    );
  }

  return (
    <main className="workflow-catalog-shell">
      <header className="workflow-catalog-heading">
        <div><p className="eyebrow">Published workflows</p><h1>{t.published}</h1><p>{t.lead}</p></div>
        <button className="workflow-primary-button" onClick={onCreate}>{t.create}</button>
      </header>
      <div className="workflow-lifecycle-tabs" role="tablist"><button className={catalogState === "active" ? "is-active" : ""} onClick={() => setCatalogState("active")}>{t.activeWorkflows} ({items.filter((item) => item.lifecycle.state === "active").length})</button><button className={catalogState === "inactive" ? "is-active" : ""} onClick={() => setCatalogState("inactive")}>{t.inactiveWorkflows} ({items.filter((item) => item.lifecycle.state === "inactive").length})</button></div>
      <label className="workflow-catalog-search"><span>{t.search}</span><input type="search" value={search} placeholder={t.searchPlaceholder} onChange={(event) => setSearch(event.target.value)} /></label>
      {loading && <p className="workflow-catalog-state"><span className="workflow-spinner" />{t.loading}</p>}
      {error && <p className="workflow-catalog-error" role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && <section className="workflow-catalog-empty"><p>{t.empty}</p><button onClick={onCreate}>{t.create}</button></section>}
      {!loading && !error && items.length > 0 && filtered.length === 0 && <p className="workflow-catalog-empty">{catalogState === "inactive" && !search ? t.noInactive : t.noMatches}</p>}
      {!loading && !error && filtered.length > 0 && <section className="workflow-card-grid">{filtered.map((item) => (
        <article className="workflow-card" key={item.id}>
          <header><span className="workflow-readonly-badge">{item.lifecycle.state === "inactive" ? t.inactive : t.readOnly}</span><PublicationBadge publication={item.publication} locale={locale} /></header>
          <div><h2>{item.title[locale]}</h2><p>{item.description[locale]}</p></div>
          <dl><div><dt>{t.version}</dt><dd>{item.version}</dd></div><div><dt>{t.nodes}</dt><dd>{item.node_count}</dd></div></dl>
          <code>{item.id}</code>
          <footer><button onClick={() => { setFocusRun(false); onSelectWorkflow(item.id); }}>{t.view}</button>{item.lifecycle.state === "active" && <button className="workflow-primary-button" onClick={() => { setFocusRun(true); onSelectWorkflow(item.id); }}>{t.run}</button>}</footer>
        </article>
      ))}</section>}
    </main>
  );
}

function PublicationBadge({ publication, locale }: { publication: Publication; locale: Locale }) {
  const t = copy[locale];
  const status = publication.validation_status;
  const className = status === "pass" ? "is-pass" : status === "inconclusive" ? "is-inconclusive" : "is-unknown";
  const label = status === "pass" ? t.validatedPass : status === "inconclusive" ? t.validatedWithGaps : t.validationUnknown;
  return <span className={`workflow-publication-badge ${className}`}>{label}</span>;
}

function WorkflowDetails({
  item,
  workflow,
  agents,
  locale,
  apiBase,
  runPath,
  onEditDraft,
  onChanged,
  onDeleted,
}: {
  item?: CatalogItem;
  workflow: WorkflowDetail;
  agents: Map<string, AgentDefinition>;
  locale: Locale;
  apiBase: string;
  runPath: string;
  onEditDraft: (draftId: string) => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const t = copy[locale];
  const publication = item?.publication ?? {
    validation_status: "unknown",
    validation_run_id: null,
    validated_at: null,
    evidence_gap_codes: [],
    acknowledgement_recorded: false,
  };
  const requiredInputs = new Set(workflow.inputSchema.required ?? []);
  const requiredOutputs = new Set(workflow.outputSchema.required ?? []);
  const validationDate = publication.validated_at ? formatDate(publication.validated_at, locale) : "—";

  return (
    <article className="workflow-detail">
      <header className="workflow-detail-hero">
        <div><p className="eyebrow">{workflow.id}</p><h1>{workflow.title[locale]}</h1><p>{workflow.description[locale]}</p></div>
        <div className="workflow-detail-badges"><span className="workflow-readonly-badge">{t.readOnly}</span><PublicationBadge publication={publication} locale={locale} /></div>
      </header>
      {item?.lifecycle.state === "inactive" && <section className="workflow-inactive-notice"><strong>{t.inactive}</strong><p>{t.inactiveHelp}</p>{item.lifecycle.deactivation_reason && <small>{item.lifecycle.deactivation_reason}</small>}</section>}
      <section className="workflow-detail-overview"><article><span>{t.version}</span><strong>{workflow.version}</strong></article><article><span>{t.nodes}</span><strong>{workflow.nodes.length}</strong></article><article><span>{t.validatedAt}</span><strong>{validationDate}</strong></article></section>
      {item && <WorkflowManagementPanel item={item} workflow={workflow} locale={locale} apiBase={apiBase} onEditDraft={onEditDraft} onChanged={onChanged} onDeleted={onDeleted} />}
      <WorkflowVersionHistory workflowId={workflow.id} currentWorkflow={workflow} agents={agents} locale={locale} apiBase={apiBase} />
      <section className="workflow-detail-section"><h2>{t.purpose}</h2><p>{workflow.description[locale]}</p></section>
      <section className="workflow-detail-section"><h2>{t.sequence}</h2><ol className="workflow-detail-steps">{workflow.nodes.map((node, index) => {
        const agent = agents.get(node.agentId);
        return <li key={node.id}><span>{index + 1}</span><div><strong>{agent?.title?.[locale] ?? node.agentId}</strong><small>{node.agentId} · v{node.agentVersion ?? "—"}</small><p><b>{t.condition}:</b> {node.runIf ? t.conditional : t.always}{node.onSkip ? ` · ${t.skip}` : ""}</p></div></li>;
      })}</ol></section>
      <div className="workflow-detail-columns">
        <section className="workflow-detail-section"><h2>{t.inputs}</h2><ul className="workflow-contract-list">{Object.entries(workflow.inputSchema.properties).map(([name, schema]) => <li key={name}><div><strong>{schema.title?.[locale] ?? name}</strong><small>{schema.description?.[locale] ?? name}</small></div><span>{requiredInputs.has(name) ? t.required : t.optional}</span></li>)}</ul></section>
        <section className="workflow-detail-section"><h2>{t.outputs}</h2><ul className="workflow-contract-list">{Object.entries(workflow.outputSchema.properties).map(([name, schema]) => <li key={name}><div><strong>{schema.title?.[locale] ?? humanize(name)}</strong><small>{name}</small></div><span>{requiredOutputs.has(name) ? t.required : t.optional}</span></li>)}</ul></section>
      </div>
      <section className="workflow-detail-section workflow-publication-summary"><h2>{t.validation}</h2><dl><div><dt>{t.validation}</dt><dd><PublicationBadge publication={publication} locale={locale} /></dd></div><div><dt>{t.validationRun}</dt><dd>{publication.validation_run_id ? <a href={`${runPath}?run=${encodeURIComponent(publication.validation_run_id)}`}>{publication.validation_run_id}</a> : "—"}</dd></div><div><dt>{t.validatedAt}</dt><dd>{validationDate}</dd></div></dl>{publication.acknowledgement_recorded && <p>{t.acknowledged}</p>}<h3>{t.gaps}</h3>{publication.evidence_gap_codes.length ? <ul>{publication.evidence_gap_codes.map((code) => <li key={code}><code>{code}</code></li>)}</ul> : <p>{t.noGaps}</p>}</section>
      {item?.lifecycle.state === "active" && <WorkflowRunForm workflow={workflow} locale={locale} apiBase={apiBase} runPath={runPath} />}
    </article>
  );
}

function WorkflowManagementPanel({ item, workflow, locale, apiBase, onEditDraft, onChanged, onDeleted }: {
  item: CatalogItem;
  workflow: WorkflowDetail;
  locale: Locale;
  apiBase: string;
  onEditDraft: (draftId: string) => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const t = copy[locale];
  const [action, setAction] = useState<"version" | "deactivate" | "activate" | "delete" | null>(null);
  const [bump, setBump] = useState<"patch" | "minor" | "major">("patch");
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const blockerText = (code: string) => ({
    workflow_must_be_inactive: t.workflowMustBeInactive,
    workflow_has_business_runs: t.workflowHasBusinessRuns,
    workflow_has_open_version_drafts: t.workflowHasOpenVersionDrafts,
    workflow_is_referenced: t.workflowIsReferenced,
  } as Record<string, string>)[code] ?? code;

  const close = () => { setAction(null); setError(""); setReason(""); setConfirmation(""); };
  const execute = async () => {
    if (!action) return;
    setBusy(true);
    setError("");
    try {
      const common = {
        expectedVersion: item.lifecycle.current_version,
        expectedWorkflowHash: item.lifecycle.workflow_hash,
      };
      const url = action === "version"
        ? `${apiBase}/api/workflows/${encodeURIComponent(workflow.id)}/versions/draft`
        : action === "delete"
          ? `${apiBase}/api/workflows/${encodeURIComponent(workflow.id)}`
          : `${apiBase}/api/workflows/${encodeURIComponent(workflow.id)}/${action}`;
      const body = action === "version" ? { ...common, bump }
        : action === "delete" ? { ...common, confirmWorkflowId: confirmation }
          : { ...common, reason: reason.trim() || null };
      const response = await fetch(url, {
        method: action === "delete" ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, t.managementFailed));
      if (action === "version") {
        const draftId = String(payload?.draft?.draft_id ?? "");
        if (!draftId) throw new Error(t.managementFailed);
        onEditDraft(draftId);
        return;
      }
      if (action === "delete") {
        onDeleted();
        return;
      }
      setSuccess(`${t.gitPending} ${String(payload.branch ?? "—")}`);
      close();
      onChanged();
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : t.managementFailed);
    } finally {
      setBusy(false);
    }
  };

  return <section className="workflow-detail-section workflow-management-panel">
    <div className="workflow-management-heading"><div><h2>{t.manage}</h2><p>{t.businessRuns}: {item.lifecycle.business_run_count}</p></div><div className="workflow-management-actions"><button disabled={!item.management.can_create_version} onClick={() => setAction("version")}>{t.createVersion}</button>{item.management.can_deactivate && <button onClick={() => setAction("deactivate")}>{t.deactivate}</button>}{item.management.can_activate && <button onClick={() => setAction("activate")}>{t.activate}</button>}<button className="danger-button" disabled={!item.management.can_delete} onClick={() => setAction("delete")}>{t.delete}</button></div></div>
    {item.management.blockers.length > 0 && <div className="workflow-management-blockers"><strong>{t.blockers}</strong><ul>{item.management.blockers.map((code) => <li key={code}>{blockerText(code)}</li>)}</ul></div>}
    {success && <p className="workflow-management-success" role="status">{success}</p>}
    {action && <div className="workflow-modal-backdrop"><section className="workflow-modal" role="dialog" aria-modal="true"><h2>{action === "version" ? t.createVersion : action === "deactivate" ? t.deactivate : action === "activate" ? t.activate : t.delete}</h2>{action === "version" && <label><span>{t.version}</span><select value={bump} onChange={(event) => setBump(event.target.value as typeof bump)}><option value="patch">{t.bumpPatch}</option><option value="minor">{t.bumpMinor}</option><option value="major">{t.bumpMajor}</option></select></label>}{(action === "deactivate" || action === "activate") && <label><span>{t.reason}</span><textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>}{action === "delete" && <><p>{t.deleteWarning}</p><label><span>{t.deleteConfirm}</span><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={workflow.id} /></label></>}{error && <p className="workflow-catalog-error" role="alert">{error}</p>}<footer><button disabled={busy} onClick={close}>{t.cancel}</button><button className={action === "delete" ? "danger-button" : "workflow-primary-button"} disabled={busy || action === "delete" && confirmation !== workflow.id} onClick={execute}>{t.confirm}</button></footer></section></div>}
  </section>;
}

function WorkflowVersionHistory({ workflowId, currentWorkflow, agents, locale, apiBase }: {
  workflowId: string;
  currentWorkflow: WorkflowDetail;
  agents: Map<string, AgentDefinition>;
  locale: Locale;
  apiBase: string;
}) {
  const t = copy[locale];
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [selected, setSelected] = useState<WorkflowDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/workflows/${encodeURIComponent(workflowId)}/versions`)
      .then(async (response) => {
        if (!response.ok) throw new Error(t.detailFailed);
        const payload = await response.json() as VersionSummary[];
        if (!cancelled) setVersions(payload);
      })
      .catch(() => { if (!cancelled) setError(t.detailFailed); });
    return () => { cancelled = true; };
  }, [apiBase, t.detailFailed, workflowId, currentWorkflow.version]);

  const view = async (version: VersionSummary) => {
    if (version.current) { setSelected(currentWorkflow); return; }
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/workflows/${encodeURIComponent(workflowId)}/versions/${encodeURIComponent(version.version)}`);
      if (!response.ok) throw new Error(t.detailFailed);
      setSelected(await response.json() as WorkflowDetail);
    } catch {
      setError(t.detailFailed);
    }
  };

  return <section className="workflow-detail-section workflow-version-history"><h2>{t.versionHistory}</h2>{error && <p className="workflow-catalog-error">{error}</p>}<div className="workflow-version-table">{versions.map((version) => <article key={version.version}><div><strong>v{version.version}</strong><span>{version.current ? t.currentVersion : t.archivedVersion}</span></div><div><PublicationBadge publication={{ validation_status: version.validation_status, validation_run_id: version.validation_run_id, validated_at: version.validated_at, evidence_gap_codes: [], acknowledgement_recorded: false }} locale={locale} /></div><button onClick={() => view(version)}>{t.viewVersion}</button></article>)}</div>{selected && <div className="workflow-version-preview"><button onClick={() => setSelected(null)}>{t.closeVersion}</button><h3>{selected.title[locale]} · v{selected.version}</h3><p>{selected.description[locale]}</p><ol>{selected.nodes.map((node) => <li key={node.id}>{agents.get(node.agentId)?.title?.[locale] ?? node.agentId} · v{node.agentVersion ?? "—"}</li>)}</ol><p>{t.inputs}: {Object.keys(selected.inputSchema.properties).join(", ") || "—"}</p><p>{t.outputs}: {Object.keys(selected.outputSchema.properties).join(", ") || "—"}</p></div>}</section>;
}

function WorkflowRunForm({ workflow, locale, apiBase, runPath }: { workflow: WorkflowDetail; locale: Locale; apiBase: string; runPath: string }) {
  const t = copy[locale];
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const required = new Set(workflow.inputSchema.required ?? []);

  useEffect(() => {
    const defaults: Record<string, string | boolean> = {};
    for (const [name, schema] of Object.entries(workflow.inputSchema.properties)) {
      if (typeof schema.default === "boolean") defaults[name] = schema.default;
      else if (schema.default != null && typeof schema.default !== "object") defaults[name] = String(schema.default);
    }
    setValues(defaults);
    setError("");
  }, [workflow.id]);

  const submit = async (event: React.SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    let input: Record<string, unknown>;
    try {
      input = parseWorkflowInput(workflow, values, locale);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t.inputInvalid);
      return;
    }
    setSubmitting(true);
    try {
      const response = await fetch(`${apiBase}/api/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "workflow", workflowId: workflow.id, input }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(apiErrorMessage(payload, t.inputInvalid));
      const query = new URLSearchParams({ run: String(payload.run_id) });
      window.location.assign(`${runPath}?${query.toString()}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t.inputInvalid);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section id="workflow-run-form" className="workflow-detail-section workflow-run-form"><h2>{t.executeTitle}</h2><p>{t.executeHelp}</p><form onSubmit={submit}><div className="workflow-run-fields">{Object.entries(workflow.inputSchema.properties).map(([name, schema]) => {
      const type = scalarType(schema);
      const label = schema.title?.[locale] ?? name;
      const value = values[name] ?? "";
      if (type === "boolean") return <label className="workflow-checkbox-field" key={name}><input type="checkbox" checked={Boolean(value)} onChange={(event) => { const nextValue = event.target.checked; setValues((current) => ({ ...current, [name]: nextValue })); }} /><span>{label}{required.has(name) && <em>{t.required}</em>}</span></label>;
      if (type === "array") return <label key={name}><span>{label}{required.has(name) && <em>{t.required}</em>}</span><textarea rows={4} required={required.has(name)} value={String(value)} placeholder={schema.placeholder?.[locale] ?? t.arrayHint} onChange={(event) => { const nextValue = event.target.value; setValues((current) => ({ ...current, [name]: nextValue })); }} /><small>{schema.description?.[locale] ?? t.arrayHint}</small></label>;
      if (schema.enum?.length) return <label key={name}><span>{label}{required.has(name) && <em>{t.required}</em>}</span><select required={required.has(name)} value={String(value)} onChange={(event) => { const nextValue = event.target.value; setValues((current) => ({ ...current, [name]: nextValue })); }}><option value="">—</option>{schema.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select><small>{schema.description?.[locale]}</small></label>;
      if (!["string", "number", "integer"].includes(type)) return <p className="workflow-catalog-error" key={name}>{label}: {t.unsupportedInput}</p>;
      return <label key={name}><span>{label}{required.has(name) && <em>{t.required}</em>}</span><input type={schema.format === "date" ? "date" : type === "number" || type === "integer" ? "number" : "text"} required={required.has(name)} min={schema.minimum} max={schema.maximum} minLength={schema.minLength} maxLength={schema.maxLength} pattern={schema.pattern} step={type === "integer" ? "1" : type === "number" ? "any" : undefined} value={String(value)} placeholder={schema.placeholder?.[locale] ?? name} onInput={(event) => { const nextValue = event.currentTarget.value; setValues((current) => ({ ...current, [name]: nextValue })); }} /><small>{schema.description?.[locale]}</small></label>;
    })}</div>{error && <p className="workflow-catalog-error" role="alert">{error}</p>}<button className="workflow-primary-button" disabled={submitting} type="submit">{submitting ? t.submitting : t.submit}</button></form></section>
  );
}

function parseWorkflowInput(workflow: WorkflowDetail, values: Record<string, string | boolean>, locale: Locale): Record<string, unknown> {
  const t = copy[locale];
  const output: Record<string, unknown> = {};
  const required = new Set(workflow.inputSchema.required ?? []);
  for (const [name, schema] of Object.entries(workflow.inputSchema.properties)) {
    const type = scalarType(schema);
    const raw = values[name];
    if (type === "boolean") {
      if (raw !== undefined || required.has(name)) output[name] = Boolean(raw);
      continue;
    }
    const text = String(raw ?? "").trim();
    if (!text) {
      if (required.has(name)) throw new Error(`${schema.title?.[locale] ?? name}: ${t.required}`);
      continue;
    }
    if (type === "array") {
      const parts = text.split(/\r\n|[\n\r,，;；]/).map((value) => value.trim());
      if (parts.some((value) => !value)) throw new Error(`${schema.title?.[locale] ?? name}: ${t.emptyItem}`);
      if (schema.minItems != null && parts.length < schema.minItems) throw new Error(`${schema.title?.[locale] ?? name}: minItems ${schema.minItems}`);
      if (schema.maxItems != null && parts.length > schema.maxItems) throw new Error(`${schema.title?.[locale] ?? name}: maxItems ${schema.maxItems}`);
      if (schema.uniqueItems && new Set(parts).size !== parts.length) throw new Error(`${schema.title?.[locale] ?? name}: uniqueItems`);
      output[name] = parts.map((value) => parseScalar(value, schema.items));
    } else {
      output[name] = parseScalar(text, schema);
    }
  }
  return output;
}

function parseScalar(value: string, schema?: ExecutionInputProperty): unknown {
  const type = schema ? scalarType(schema) : "string";
  if (type === "integer") {
    const parsed = Number(value);
    if (!Number.isInteger(parsed)) throw new Error(`${value} is not an integer`);
    return parsed;
  }
  if (type === "number") {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) throw new Error(`${value} is not a number`);
    return parsed;
  }
  return value;
}

function scalarType(schema: ExecutionInputProperty): string {
  return Array.isArray(schema.type) ? schema.type.find((item) => item !== "null") ?? "string" : schema.type;
}

function apiErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(value: string, locale: Locale): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
