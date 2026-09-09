import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftStatus, draftStepNames, draftTerminal, localText, publicValues, restoreDiscoveredInput, retainCompatibleInput, validateDraftInput } from "../lib/agentDraft";
import AgentDraftInputs from "./AgentDraftInputs";
import AgentDraftResult from "./AgentDraftResult";
import AgentDraftConversation from "./AgentDraftConversation";
import "../styles/agent-draft.css";

type Props = { initialDraft: any; apiBase: string; locale: Locale; runPath: string; initialStep?: string; onBack: () => void; onPublished: (result: any) => void };
type Step = typeof draftStepNames[number];

async function call(url: string, data?: any, method = "POST", signal?: AbortSignal) {
  const response = await fetch(url, { method: data === undefined && method === "POST" ? "GET" : method, headers: { "Content-Type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data), signal });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = value.detail;
    throw Object.assign(new Error(typeof detail === "object" ? detail?.code || detail?.message || `HTTP ${response.status}` : detail || `HTTP ${response.status}`), { detail });
  }
  return value;
}

export default function AgentDraftWorkspace({ initialDraft, apiBase, locale, runPath, initialStep, onBack, onPublished }: Props) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const [draft, setDraft] = useState(initialDraft);
  const draftRef = useRef(initialDraft);
  const [manifestText, setManifestText] = useState(JSON.stringify(initialDraft.package.manifest, null, 2));
  const [readme, setReadme] = useState(initialDraft.package.readme || "");
  const [rules, setRules] = useState(initialDraft.package.rules || "");
  const [step, setStep] = useState<Step>(draftStepNames.includes(initialStep as Step) ? initialStep as Step : "compose");
  const [input, setInput] = useState<Record<string, any>>(() => publicValues(initialDraft.package.manifest.execution?.inputSchema || {}, {}, true));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remoteConflict, setRemoteConflict] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [autoDiscover, setAutoDiscover] = useState(false);
  const [autoFilled, setAutoFilled] = useState<Record<string, any>>({});
  const autoFilledRef = useRef(autoFilled); autoFilledRef.current = autoFilled;
  const [discovery, setDiscovery] = useState<any>(null);
  const [confirmedSample, setConfirmedSample] = useState("");
  const [discoveryInputHash, setDiscoveryInputHash] = useState("");
  const [trial, setTrial] = useState<any>(initialDraft.trial || null);
  const [run, setRun] = useState<any>(null);
  const [report, setReport] = useState<any>(null);
  const [operation, setOperation] = useState<any>(initialDraft.active_operation || null);
  const [diff, setDiff] = useState<any>(null);
  const [compareFrom, setCompareFrom] = useState(Math.max(1, initialDraft.revision - 1));
  const [compareTo, setCompareTo] = useState(initialDraft.revision);
  const [targetVersion, setTargetVersion] = useState(initialDraft.target_version || initialDraft.package.manifest.version);
  const chatRef = useRef<HTMLTextAreaElement>(null);
  const progressRef = useRef<HTMLParagraphElement>(null);
  const pendingFocus = useRef(false);
  const base = `${apiBase}/api/authoring/agents/${encodeURIComponent(draft.draft_id)}`;
  const dirty = changedDefinition(draft, manifestText, readme, rules);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const inputRef = useRef(input); inputRef.current = input;
  const sampleApplied = useRef("");
  let manifest: any;
  try { manifest = JSON.parse(manifestText); } catch { manifest = draft.package.manifest; }
  const schema = draft.package.manifest.execution?.inputSchema || { type: "object", properties: {} };
  const active = Boolean(operation && !draftTerminal.has(operation.status));
  const locked = busy || active || draft.status === "published";
  const actionable = !locked && !dirty && !remoteConflict;
  const acceptance = report?.acceptance || draft.acceptance;
  const staticChecks = report?.static_checks || draft.static_checks || {};
  const staticLabel = staticChecks.errors?.length ? tr("自动检查未通过", "Automatic checks failed") : staticChecks.checks?.length ? tr("自动检查通过", "Automatic checks passed") : tr("尚未检查", "Not checked");
  const publishability = report?.publishability || draft.publishability;
  const canPublish = publishability?.can_publish === true || publishability?.allowed === true;
  const sampleFingerprint = discoveryFingerprint(draft.revision, input);
  const sampleConfirmed = Boolean(discovery && ["ready", "completed"].includes(discovery.status) && confirmedSample === sampleFingerprint && discoveryInputHash === sampleFingerprint);
  const requiresSampleConfirmation = autoDiscover || Object.keys(autoFilled).length > 0;
  const conversation = draft.conversation || [];
  const latestTurn = conversation[conversation.length - 1];
  const trialRunId = trial?.run_id || draft.trial?.run_id;

  const replace = (value: any, replaceEditor = true) => {
    if (!value?.package?.manifest) return;
    if (value.revision < draftRef.current.revision) return;
    if (!replaceEditor && dirtyRef.current && value.revision > draftRef.current.revision) { setRemoteConflict(true); return; }
    if (value.revision !== draftRef.current.revision) {
      const previousSchema = draftRef.current.package.manifest.execution?.inputSchema || {};
      setReport(null); setRun(null); setTrial(null); setDiscovery(null); setDiscoveryInputHash(""); setConfirmedSample(""); setAutoDiscover(false); setAutoFilled({}); setSecrets({}); sampleApplied.current = "";
      setInput((current) => retainCompatibleInput(previousSchema, value.package.manifest.execution?.inputSchema || {}, current));
      setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision);
    }
    draftRef.current = value;
    setDraft(value);
    setOperation(value.active_operation || null);
    setTrial(value.trial || null);
    if (replaceEditor) { setManifestText(JSON.stringify(value.package.manifest, null, 2)); setReadme(value.package.readme || ""); setRules(value.package.rules || ""); }
  };
  const refresh = async (replaceEditor = false) => {
    const value = await call(base);
    replace(value, replaceEditor && !dirtyRef.current);
    return value;
  };
  const action = async (work: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (failure: any) {
      const code = String(failure.message || "request_failed");
      const labels: Record<string, [string, string]> = {
        agent_draft_conflict: ["草稿已变化。请刷新并核对修改后再重试。", "The draft changed. Refresh and review before retrying."],
        agent_draft_operation_active: ["当前草稿有任务正在执行，请等待或取消。", "A draft operation is active. Wait or cancel it."],
        agent_draft_published: ["该草稿已经发布，请创建新版本。", "This draft is published. Create a new version."],
        runtime_disabled: ["Runtime已停用，仍可手工输入并试运行。", "The Runtime is disabled. Manual trial input remains available."],
      };
      setError(labels[code]?.[locale === "zh" ? 0 : 1] || `${tr("操作未完成，请核对输入或重试。", "The operation did not complete. Check inputs or retry.")} (${code})`);
    } finally { setBusy(false); }
  };

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => { window.removeEventListener("beforeunload", beforeUnload); };
  }, []);
  useEffect(() => { history.replaceState({}, "", `${window.location.pathname}?draft=${encodeURIComponent(draft.draft_id)}&step=${step}`); }, [step, draft.draft_id]);
  useEffect(() => { if (pendingFocus.current && active) { progressRef.current?.focus(); pendingFocus.current = false; } }, [active]);
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await call(base, undefined, "GET", controller.signal);
        if (stopped) return;
        replace(value, !dirtyRef.current);
        const sample = value.sample_discovery || value.metadata?.sample_discovery || (value.active_operation?.kind === "sample_discovery" ? value.active_operation.detail : null);
        if (sample?.run_id && sample.revision === value.revision) setDiscovery(sample);
        const id = value.trial?.run_id;
        if (id) {
          const latestRun = await call(`${apiBase}/api/runs/${encodeURIComponent(id)}`, undefined, "GET", controller.signal);
          if (!stopped) {
            setRun(latestRun);
            if (draftTerminal.has(latestRun.status)) {
              const latestReport = await call(`${base}/validation-report`, undefined, "GET", controller.signal);
              if (!stopped) { setReport(latestReport); if (latestReport.trial) setTrial(latestReport.trial); }
            }
          }
        }
      } catch (failure: any) { if (!stopped && failure.name !== "AbortError") setError(tr("进度连接中断，正在重试；已保存的任务不会丢失。", "Progress connection interrupted; retrying. Saved work is retained.")); }
      if (!stopped) timer = setTimeout(poll, document.hidden ? 10000 : 3000);
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [base, trialRunId]);

  useEffect(() => {
    if (!discovery?.run_id || sampleApplied.current === discovery.run_id) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const value = await call(`${base}/sample-discovery/${encodeURIComponent(discovery.run_id)}`, undefined, "GET", controller.signal);
        if (stopped) return;
        setDiscovery(value);
        if (["ready", "completed", "needs_input"].includes(value.status)) {
          const sourceRevision = value.revision ?? value.expected_revision ?? draft.revision;
          if (sourceRevision === draft.revision && !dirtyRef.current) {
            const restored = restoreDiscoveredInput(schema, draft.revision, inputRef.current, value, autoFilledRef.current);
            if (restored) {
              setInput(restored.input); setAutoFilled(restored.autoFilled); setAutoDiscover(restored.autoDiscover);
              setDiscoveryInputHash(restored.fingerprint); setConfirmedSample(restored.confirmedSample); sampleApplied.current = value.run_id;
              if (restored.scopeChanged) setError(tr("当前参数与已保存样本的查询范围不同。未回填旧样本，请重新查找或改用手工参数。", "Current parameters differ from the saved sample scope. No old sample was applied. Discover again or use manual input."));
            }
          }
          void refresh(false); return;
        }
        if (draftTerminal.has(value.status)) { void refresh(false); return; }
      } catch (failure: any) { if (!stopped && failure.name !== "AbortError") setError(tr("无法读取选样进度，请刷新重试。", "Cannot load discovery progress. Refresh to retry.")); }
      if (!stopped) timer = setTimeout(poll, 3000);
    };
    void poll(); return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [base, discovery?.run_id]);

  useEffect(() => {
    if (step !== "review") return;
    const controller = new AbortController();
    setDiff(null);
    call(`${base}/diff?fromRevision=${compareFrom}&toRevision=${compareTo}`, undefined, "GET", controller.signal).then(setDiff).catch((failure) => { if (failure.name !== "AbortError") setError(tr("无法加载修改对比，请重试。", "Cannot load revision comparison. Retry.")); });
    return () => controller.abort();
  }, [base, step, compareFrom, compareTo]);

  const save = () => action(async () => {
    const value = await call(base, { expectedRevision: draft.revision, manifest: JSON.parse(manifestText), readme, rules }, "PUT");
    replace(value); setReport(null); setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision); setConfirmedSample("");
    setNotice(tr("新修订已保存。可继续试运行，或查看修改前后对比。", "Revision saved. Continue with a trial or review the changes."));
  });
  const discard = () => { void action(async () => { replace(await call(base)); setRemoteConflict(false); setError(""); }); };
  const updateBasic = (key: string, value: string, lang?: string) => {
    let current;
    try { current = JSON.parse(manifestText); } catch { setError(tr("高级定义不是有效JSON，请先修正或放弃修改。", "Advanced definition is not valid JSON. Correct or discard it first.")); return; }
    if (lang) current[key] = { ...current[key], [lang]: value }; else current[key] = value;
    setManifestText(JSON.stringify(current, null, 2));
  };
  const discover = () => action(async () => {
    if (!actionable) return;
    setConfirmedSample(""); setDiscoveryInputHash("");
    const value = await call(`${base}/sample-discovery`, { expectedRevision: draft.revision, input: publicValues(schema, input), requestId: crypto.randomUUID() });
    setDiscovery(value); setOperation({ ...value, kind: "sample_discovery", status: value.status || "queued" });
  });
  const runTrial = () => action(async () => {
    if (!actionable) return;
    const errors = validateDraftInput(schema, input, secrets, locale); setFieldErrors(errors);
    if (Object.keys(errors).length) { setError(tr("请修正标出的查询参数。", "Correct the highlighted query parameters.")); return; }
    if (requiresSampleConfirmation && !sampleConfirmed) { setError(tr("请先检查并确认回填参数。关闭自动选样会清除未经手工修改的回填值，再使用手工参数。", "Review and confirm the proposed parameters. Turning off discovery clears unedited suggested values so you can enter parameters manually.")); return; }
    const value = await call(`${base}/live-validate`, { expectedRevision: draft.revision, input: publicValues(schema, input), sensitiveInputs: secrets, autoDiscover: false, requestId: crypto.randomUUID() });
    setSecrets({}); setRun(null); setTrial(value.trial || value); setOperation({ status: "queued", kind: "trial" }); setReport(null);
    progressRef.current?.focus();
  });
  const submitFeedback = () => action(async () => {
    if (!actionable || !feedback.trim()) return;
    const value = await call(`${base}/feedback`, { baseTurn: latestTurn?.turn ?? latestTurn?.turn_number ?? conversation.length, baseRevision: draft.revision, feedback, locale, requestId: crypto.randomUUID() });
    pendingFocus.current = true;
    setFeedback(""); setOperation({ ...(value.task || value), kind: "feedback", status: value.status || "queued", turn: value.turn ?? value.turn_number });
    await refresh(false); progressRef.current?.focus();
  });
  const cancel = () => action(async () => {
    if (discovery?.run_id && !draftTerminal.has(discovery.status)) await call(`${base}/sample-discovery/${discovery.run_id}/cancel`, {});
    else if (trialRunId && run && !draftTerminal.has(run.status)) await call(`${apiBase}/api/runs/${trialRunId}/cancel`, {});
    else if (operation?.kind === "feedback" || latestTurn?.status === "running" || latestTurn?.status === "queued") await call(`${base}/feedback/${operation?.turn ?? operation?.detail?.turn ?? latestTurn?.turn ?? latestTurn?.turn_number}/cancel`, {});
    setSecrets({}); await refresh(false);
  });
  const check = () => action(async () => { if (!actionable) return; replace(await call(`${base}/validate`, { expectedRevision: draft.revision }), false); setReport(await call(`${base}/validation-report`)); });
  const publish = (activate: boolean) => action(async () => {
    if (!actionable || !canPublish) return;
    onPublished(await call(`${base}/publish`, { expectedRevision: draft.revision, targetVersion, activate, validationReportDigest: acceptance?.report_digest || report?.report_digest || null }));
  });
  const restore = () => action(async () => {
    if (!actionable) return;
    const value = await call(`${base}/undo`, { baseRevision: draft.revision, targetRevision: compareFrom });
    replace(value); setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision); setReport(null); setConfirmedSample("");
    setNotice(tr("已恢复为新修订，原历史记录保持不变。", "Restored as a new revision; history is unchanged."));
  });
  const revisions: number[] = (draft.revisions?.length ? draft.revisions.map((item: any) => item.revision) : Array.from({ length: draft.revision }, (_, index) => index + 1)).sort((a: number, b: number) => a - b);

  return <main className="agent-management agent-draft-workspace"><header><button className="agent-secondary-action" disabled={dirty || busy} onClick={onBack}>{tr("返回管理列表", "Back to management")}</button><p className="eyebrow">{tr("未发布草稿 · 不影响当前活动Agent", "Unpublished draft · Active Agent unchanged")}</p><h1>{localText(manifest.title, locale) || draft.agent_id}</h1><p>{localText(manifest.summary, locale)}</p><div className="draft-meta"><span>{manifest.module}</span><code>{draft.agent_id}</code><span>{tr("目标版本", "Target version")} {draft.target_version}</span><span>{tr("修订", "Revision")} {draft.revision}</span></div></header>
    <nav className="agent-steps" aria-label={tr("编辑步骤", "Editing steps")}>{draftStepNames.map((name, index) => <button key={name} className={step === name ? "active" : "agent-secondary-action"} aria-current={step === name ? "step" : undefined} onClick={() => setStep(name)}>{index + 1}. {name === "compose" ? tr("定义与修改", "Define and revise") : name === "review" ? tr("检查修改内容", "Review changes") : tr("发布与启用", "Publish and activate")}</button>)}</nav>
    {error && <p className="agent-alert error" role="alert">{error}</p>}{notice && <p className="agent-alert" role="status">{notice}</p>}
    {remoteConflict && <p className="agent-alert error" role="alert">{tr("草稿已在其他操作中生成新修订。当前未保存内容已保留，请复制需要保留的修改，再放弃本地修改并加载最新修订。", "Another operation created a revision. Your unsaved content is retained. Copy any edits you need, then discard local edits and load the latest revision.")}</p>}
    {dirty && <aside className="agent-alert draft-unsaved"><p>{tr("有尚未保存的定义修改。请保存或放弃后，再进行选样、试运行、对话或发布。", "There are unsaved definition changes. Save or discard before discovery, trials, chat or publication.")}</p><div className="agent-actions"><button disabled={locked || remoteConflict} onClick={save}>{tr("保存新修订", "Save revision")}</button><button className="agent-secondary-action" disabled={locked} onClick={discard}>{tr("放弃未保存修改", "Discard unsaved edits")}</button></div></aside>}
    {active && <p className="agent-alert" role="status" ref={progressRef} tabIndex={-1}>{tr("草稿任务", "Draft operation")}: {draftStatus(operation.status, locale)} <button className="agent-secondary-action" disabled={busy} onClick={cancel}>{tr("取消任务", "Cancel task")}</button></p>}
    {step === "compose" && <div className="agent-document-body">
      <section className="agent-panel"><h2>{tr("基础信息", "Basic information")}</h2><div className="draft-basic-grid">{(["zh", "en"] as const).map((lang) => <div key={lang}><label>{tr(lang === "zh" ? "中文名称" : "英文名称", lang === "zh" ? "Chinese name" : "English name")}<input disabled={locked} value={manifest.title?.[lang] || ""} onChange={(event) => updateBasic("title", event.target.value, lang)} /></label><label>{tr(lang === "zh" ? "中文说明" : "英文说明", lang === "zh" ? "Chinese summary" : "English summary")}<textarea disabled={locked} rows={3} value={manifest.summary?.[lang] || ""} onChange={(event) => updateBasic("summary", event.target.value, lang)} /></label></div>)}</div><label>{tr("负责人或负责团队", "Owner or responsible team")}<input disabled={locked} value={manifest.owner || ""} onChange={(event) => updateBasic("owner", event.target.value)} /></label><p>{tr("技术ID、模块及来源版本保持不变。复杂结构可通过页面底部对话修改。", "Technical ID, module and source version are unchanged. Use the conversation below to revise complex definitions.")}</p><div className="agent-actions"><button disabled={locked || remoteConflict || !dirty} onClick={save}>{tr("保存新修订", "Save revision")}</button></div></section>
      <section className="agent-run-panel"><h2>{tr("试运行这个Agent", "Try this Agent")}</h2><p>{tr("填写业务参数，使用已保存的草稿执行只读查询。试运行不会修改Agent默认值，也不代表正式验收通过。", "Enter business parameters to run the saved draft read-only. Trial input does not change Agent defaults or establish formal acceptance.")}</p>
        <form onSubmit={(event) => { event.preventDefault(); void runTrial(); }} noValidate><AgentDraftInputs schema={schema} values={input} onChange={(next) => { setInput(next); setFieldErrors({}); setConfirmedSample(""); }} secrets={secrets} onSecrets={setSecrets} locale={locale} errors={fieldErrors} disabled={locked || dirty} />
          <div className="draft-discovery">
            <label className="draft-checkbox"><input type="checkbox" checked={autoDiscover} disabled={locked || dirty} onChange={(event) => { const enabled = event.target.checked; setAutoDiscover(enabled); setConfirmedSample(""); if (enabled) { void discover(); } else { setInput((current) => clearDiscoveredInput(current, autoFilledRef.current)); setAutoFilled({}); setDiscoveryInputHash(""); setFieldErrors({}); } }} />{tr("自动查找验证数据", "Find validation data automatically")}</label>
            {autoDiscover && <>
              <p>{tr("保留已填范围，有界读取真实SAP样本；回填后请确认。Runtime使用 gpt-5.6-sol。敏感参考号请手工填写。", "Keep the entered scope and read bounded live SAP samples. Confirm before trial. Runtime: gpt-5.6-sol. Enter sensitive references manually.")}</p>
              <button type="button" className="agent-secondary-action" disabled={!actionable} onClick={discover}>{tr(discovery ? "重新查找样本" : "查找样本", discovery ? "Find another sample" : "Find sample")}</button>
              {discovery && <div role="status">
                <p>{draftStatus(discovery.status, locale)}</p>
                <p>{localText(discovery.selection_reason || discovery.result?.selection_reason, locale)}</p>
                {Object.keys(discovery.field_sources || {}).length > 0 && <><p>{tr(`来源：本次SAP只读查询；已核对${Object.keys(discovery.field_sources).length}个参数。`, `Source: this read-only SAP query; ${Object.keys(discovery.field_sources).length} parameters verified.`)}</p><details><summary>{tr("样本来源详情", "Sample source details")}</summary>{discovery.query_count !== undefined && <p>{tr("数据读取次数：", "Data reads: ")}{discovery.query_count}</p>}<dl>{Object.entries(discovery.field_sources).map(([field, source]: [string, any]) => <div key={field}><dt>{localText(schema.properties?.[field]?.title, locale) || field}</dt><dd><code>{source.source_field}</code> · <code>{source.evidence_ref}</code></dd></div>)}</dl></details></>}
                {(discovery.missing_fields || []).length > 0 && <p>{tr("仍需补充：", "Still required: ")}{discovery.missing_fields.map((field: string) => localText(schema.properties?.[field]?.title, locale) || field).join("、")}</p>}
                {["ready", "completed"].includes(discovery.status) && <label className="draft-checkbox"><input type="checkbox" disabled={!actionable || discoveryInputHash !== sampleFingerprint} checked={sampleConfirmed} onChange={(event) => setConfirmedSample(event.target.checked ? sampleFingerprint : "")} />{tr("已核对回填参数，确认用于本次试运行", "I reviewed the proposed parameters and confirm this trial")}</label>}
              </div>}
            </>}
          </div>
          <div className="agent-actions"><button type="button" className="agent-secondary-action" disabled={!actionable} onClick={check}>{tr("自动检查定义", "Check definition")}</button><button type="submit" disabled={!actionable || (requiresSampleConfirmation && !sampleConfirmed)}>{tr("执行只读试运行", "Run read-only trial")}</button></div></form>
      </section>
      <section className="agent-panel"><h2>{tr("试运行与验收", "Trial and acceptance")}</h2><p>{tr("自动检查、试运行和正式验收分别记录，不相互替代。", "Automatic checks, trials and formal acceptance are recorded separately.")}</p><dl><dt>{tr("自动检查", "Automatic checks")}</dt><dd>{staticLabel}</dd><dt>{tr("正式验收", "Formal acceptance")}</dt><dd>{draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</dd></dl>{(acceptance?.source_version || acceptance?.reused_from_version) && <p>{tr("复用原版本验收：", "Acceptance reused from version: ")}{acceptance.source_version || acceptance.reused_from_version}</p>}{publishability?.blockers?.length > 0 && <ul className="draft-blockers">{publishability.blockers.map((item: any, index: number) => <li key={index}>{localText(item.message || item.description, locale) || tr("正式验收或发布条件尚未满足。", "Formal acceptance or publication conditions remain unmet.")}<details><summary>{tr("技术原因", "Technical reason")}</summary><code>{typeof item === "string" ? item : item.code}</code></details></li>)}</ul>}
        {trialRunId ? <><p>{tr("试运行修订", "Trial revision")}: {trial?.revision ?? trial?.draft_revision ?? "—"} · {draftStatus(run?.status || trial?.status, locale)}{(trial?.revision ?? trial?.draft_revision) !== undefined && (trial?.revision ?? trial?.draft_revision) !== draft.revision && <strong> · {tr("历史修订结果，不适用于当前定义", "Historical result; not acceptance for this revision")}</strong>}</p>{run && !draftTerminal.has(run.status) && <p role="status">{tr("当前阶段", "Current stage")}: {localText(run.current_stage?.title || run.progress?.current_activity, locale) || draftStatus(run.progress?.phase || run.status, locale)} · {tr("耗时", "Elapsed")}: {run.elapsed_seconds ?? run.progress?.elapsed_seconds ?? "—"} {tr("秒", "s")}<button disabled={busy} className="agent-secondary-action" onClick={cancel}>{tr("取消试运行", "Cancel trial")}</button></p>}{run && draftTerminal.has(run.status) && <AgentDraftResult run={run} locale={locale} runPath={runPath} apiBase={apiBase} />}{!run && <a href={`${runPath}?run=${encodeURIComponent(trialRunId)}`} target="_blank" rel="noreferrer">{tr("查看试运行记录", "Open trial run")}</a>}</> : <p>{tr("当前没有试运行记录。", "No trial has been run yet.")}</p>}<button className="agent-secondary-action" disabled={busy} onClick={() => action(async () => { setReport(await call(`${base}/validation-report`)); await refresh(false); })}>{tr("刷新验证状态", "Refresh validation status")}</button>
      </section>
      <section className="agent-panel"><h2>{tr("输入与输出", "Inputs and outputs")}</h2><div className="draft-basic-grid"><div><h3>{tr("您需要提供", "What you provide")}</h3><ul>{(manifest.inputs?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></div><div><h3>{tr("您将获得", "What you receive")}</h3><ul>{(manifest.outputs?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></div></div></section>
      <section className="agent-panel"><h2>{tr("业务处理步骤", "Business steps")}</h2><ol className="draft-business-steps">{(manifest.workflow || []).map((item: any, index: number) => <li key={item.id || index}><h3>{localText(item.title, locale)}</h3><p>{localText(item.description, locale)}</p></li>)}</ol></section>
      <section className="agent-panel"><h2>{tr("SAP范围与安全边界", "SAP scope and safety")}</h2><p>{(manifest.sapModules || []).join(" · ")}</p><ul>{(manifest.guardrails?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>
      <section><details className="agent-panel draft-advanced"><summary>{tr("高级技术编辑", "Advanced technical editing")}</summary><p>{tr("仅在了解数据契约时使用。所有修改仍须通过安全检查。", "Use only when familiar with the contracts. Every change remains subject to safety checks.")}</p><label>Agent JSON<textarea disabled={locked} rows={20} spellCheck={false} value={manifestText} onChange={(event) => setManifestText(event.target.value)} /></label><label>README<textarea disabled={locked} rows={8} value={readme} onChange={(event) => setReadme(event.target.value)} /></label><label>{tr("受控规则源码", "Managed rule source")}<textarea disabled={locked} rows={10} spellCheck={false} value={rules} onChange={(event) => setRules(event.target.value)} /></label><button disabled={locked || remoteConflict || !dirty} onClick={save}>{tr("保存新修订", "Save revision")}</button></details></section>
      <section className="agent-panel draft-conversation"><h2>{tr("您的修改意见是？", "What would you like to change?")}</h2><p>{tr("可以描述业务需求、回答澄清问题或继续调整。明确需求后会保存为新修订，不会自动发布。请勿在对话中输入敏感参考号。", "Describe your business needs, answer clarifications or refine the draft. Clear changes become a new revision, never an automatic publication. Do not enter sensitive references here.")}</p><AgentDraftConversation turns={conversation} locale={locale} /><label htmlFor="draft-feedback">{tr("修改意见或澄清回复", "Revision request or clarification reply")}<textarea ref={chatRef} id="draft-feedback" rows={4} value={feedback} disabled={locked || dirty} onChange={(event) => setFeedback(event.target.value)} /></label><div className="agent-actions"><button disabled={!actionable || !feedback.trim()} onClick={submitFeedback}>{tr("发送修改意见", "Send feedback")}</button><button className="agent-secondary-action" onClick={() => { setCompareFrom(Math.max(1, draft.revision - 1)); setCompareTo(draft.revision); setStep("review"); }}>{tr("查看修改前后对比", "Review before and after")}</button></div></section>
    </div>}
    {step === "review" && <section className="agent-panel"><h2>{tr("检查修改内容", "Review changes")}</h2><p>{tr("对比已保存的草稿修订，不代表与当前活动版本比较。", "Compare saved draft revisions, not necessarily the active version.")}</p><div className="draft-basic-grid"><label>{tr("修改前修订", "Before revision")}<select value={compareFrom} onChange={(event) => setCompareFrom(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label><label>{tr("修改后修订", "After revision")}<select value={compareTo} onChange={(event) => setCompareTo(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label></div>{!diff ? <p role="status">{tr("正在加载修改对比…", "Loading comparison…")}</p> : diff.changes?.length ? diff.changes.map((change: any, index: number) => <section className="draft-diff-change" key={index}><h3>{diffBusinessLabel(change, manifest, locale)} · {tr(change.change === "added" ? "新增" : change.change === "removed" ? "删除" : "修改", change.change === "added" ? "Added" : change.change === "removed" ? "Removed" : "Modified")}</h3><details><summary>{tr("技术位置", "Technical location")}</summary><code>{change.path}</code></details><div className="draft-diff-values">{(["before", "after"] as const).map((side) => <div key={side} className={`draft-diff-${side}`}><h4>{side === "before" ? tr("修改前", "Before") : tr("修改后", "After")}</h4><pre>{!(side in change) || change[`${side}_exists`] === false ? tr("未设置", "Not set") : change[side] === null ? tr("空值", "Null") : change[side] === "" ? tr("空内容", "Empty content") : typeof change[side] === "string" ? change[side] : JSON.stringify(change[side], null, 2)}</pre></div>)}</div>{change.unified_diff && <details><summary>{tr("查看逐行变更", "View line-by-line changes")}</summary><pre className="draft-unified-diff">{String(change.unified_diff).split("\n").map((line, number) => <span key={number} className={line.startsWith("+") ? "diff-added" : line.startsWith("-") ? "diff-removed" : ""}>{line}{"\n"}</span>)}</pre></details>}</section>) : <p>{tr("两个修订没有内容差异。", "There are no content differences.")}</p>}<div className="agent-actions"><button className="agent-secondary-action" disabled={!actionable || compareFrom === draft.revision} onClick={restore}>{tr("恢复修改前修订（创建新修订）", "Restore before revision (creates a new revision)")}</button><button onClick={() => setStep("compose")}>{tr("返回定义与试运行", "Back to definition and trial")}</button></div></section>}
    {step === "publish" && <section className="agent-panel"><h2>{tr("发布与启用", "Publish and activate")}</h2><p>{tr("正式验收通过后才可发布。一次试运行完成不代表通过正式验收。发布会创建本地Git分支和提交，不推送远端。", "Formal acceptance is required. A completed trial is not formal acceptance. Publication creates a local Git branch and commit without pushing.")}</p><p>{tr("正式验收", "Formal acceptance")}: {draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</p>{!canPublish && <p role="status">{tr("正式验收或发布条件尚未满足，暂不可发布。请在定义页面查看验证状态。", "Acceptance or publication requirements remain unmet. Review validation in the definition page.")}</p>}<label>{tr("目标版本", "Target version")}<input value={targetVersion} disabled={locked} onChange={(event) => setTargetVersion(event.target.value)} /></label><div className="agent-actions"><button disabled={!actionable || !canPublish} onClick={() => publish(false)}>{tr("发布为未启用版本", "Publish inactive")}</button><button disabled={!actionable || !canPublish} onClick={() => publish(true)}>{tr("发布并启用", "Publish and activate")}</button></div></section>}
  </main>;
}
