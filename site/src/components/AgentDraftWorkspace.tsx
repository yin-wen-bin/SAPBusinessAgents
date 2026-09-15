import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { canRetryFeedback, changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftInputLabels, draftStatus, draftStepNames, draftTerminal, localText, prepareFeedbackRequest, publicValues, restoreDiscoveredInput, retainCompatibleInput, technicalIdentity, technicalIdError, validateDraftInput } from "../lib/agentDraft";
import type { FeedbackRequest } from "../lib/agentDraft";
import AgentDraftInputs from "./AgentDraftInputs";
import AgentDraftResult from "./AgentDraftResult";
import AgentDraftConversation from "./AgentDraftConversation";
import AgentSampleProgress, { SampleProgressSummary } from "./AgentSampleProgress";
import AgentTrialProgress, { TrialProgressSummary } from "./AgentTrialProgress";
import AgentFeedbackProgress, { FeedbackProgressSummary } from "./AgentFeedbackProgress";
import AgentPublicationProgress, { PublicationProgressSummary } from "./AgentPublicationProgress";
import { AcceptanceSummary, AgentAcceptanceProgress, AgentAcceptanceSetup } from "./AgentAcceptance";
import AgentDefinitionDetails from "./AgentDefinitionDetails";
import type { AcceptanceCaseDraft } from "./AgentAcceptance";
import { sampleFieldOptions } from "../lib/agentDraft";
import "../styles/agent-draft.css";

type Props = { initialDraft: any; apiBase: string; locale: Locale; runPath: string; initialStep?: string; onBack: () => void; onPublished: (result: any) => void };
type Step = typeof draftStepNames[number];
const catalogModules = ["CO", "Common", "FI", "MM", "PP", "SD"] as const;

async function call(url: string, data?: any, method = "POST", signal?: AbortSignal) {
  const response = await fetch(url, { method: data === undefined && method === "POST" ? "GET" : method, headers: { "Content-Type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data), signal });
  const value = await response.json().catch(() => { throw Object.assign(new Error("response_invalid"), { uncertain: true }); });
  if (!response.ok) {
    const detail = value.detail;
    throw Object.assign(new Error(typeof detail === "object" ? detail?.code || detail?.message || `HTTP ${response.status}` : detail || `HTTP ${response.status}`), { detail, status: response.status, uncertain: response.status >= 500 });
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
  const [retryOfTurn, setRetryOfTurn] = useState<number | undefined>();
  const [uncertainFeedback, setUncertainFeedback] = useState(false);
  const [feedbackDialogOpen, setFeedbackDialogOpen] = useState(false);
  const [feedbackLaunch, setFeedbackLaunch] = useState<any>(null);
  const [feedbackConnectionError, setFeedbackConnectionError] = useState(false);
  const pendingFeedbackRequest = useRef<FeedbackRequest | null>(null);
  const feedbackAfterClose = useRef<null | (() => void)>(null);
  const [identityInput, setIdentityInput] = useState(initialDraft.agent_id || "");
  const [identityCheck, setIdentityCheck] = useState<{ agent_id: string; revision: number; available: boolean } | null>(null);
  const [autoDiscover, setAutoDiscover] = useState(false);
  const [autoFilled, setAutoFilled] = useState<Record<string, any>>({});
  const autoFilledRef = useRef(autoFilled); autoFilledRef.current = autoFilled;
  const [discovery, setDiscovery] = useState<any>(null);
  const [sampleDialogOpen, setSampleDialogOpen] = useState(false);
  const [selectingSampleFields, setSelectingSampleFields] = useState(false);
  const [selectedSampleFields, setSelectedSampleFields] = useState<string[]>([]);
  const sampleSubmitting = useRef(false);
  const pendingSampleRequest = useRef<any>(null);
  const sampleInputsRef = useRef<HTMLDivElement>(null);
  const sampleReviewFocus = useRef(false);
  const [confirmedSample, setConfirmedSample] = useState("");
  const [discoveryInputHash, setDiscoveryInputHash] = useState("");
  const [trial, setTrial] = useState<any>(initialDraft.trial || null);
  const [run, setRun] = useState<any>(null);
  const [trialDialogOpen, setTrialDialogOpen] = useState(false);
  const [trialConnectionError, setTrialConnectionError] = useState(false);
  const trialResultRef = useRef<HTMLDivElement>(null);
  const trialReviewFocus = useRef(false);
  const [report, setReport] = useState<any>(null);
  const [acceptanceCampaign, setAcceptanceCampaign] = useState<any>(null);
  const [acceptanceSetupOpen, setAcceptanceSetupOpen] = useState(false);
  const [acceptanceProgressOpen, setAcceptanceProgressOpen] = useState(false);
  const [acceptanceConnectionError, setAcceptanceConnectionError] = useState(false);
  const [acceptanceCases, setAcceptanceCases] = useState<AcceptanceCaseDraft[]>([]);
  const [acceptanceSampleCase, setAcceptanceSampleCase] = useState<number | null>(null);
  const acceptanceResultRef = useRef<HTMLDivElement>(null);
  const [operation, setOperation] = useState<any>(initialDraft.active_operation || null);
  const [publication, setPublication] = useState<any>(initialDraft.publication_operation || null);
  const [publicationDialogOpen, setPublicationDialogOpen] = useState(false);
  const [publicationConnectionError, setPublicationConnectionError] = useState(false);
  const [diff, setDiff] = useState<any>(null);
  const [compareFrom, setCompareFrom] = useState(Math.max(1, initialDraft.revision - 1));
  const [compareTo, setCompareTo] = useState(initialDraft.revision);
  const [targetVersion, setTargetVersion] = useState(initialDraft.target_version || initialDraft.package.manifest.version);
  const [catalogModule, setCatalogModule] = useState(initialDraft.catalog_module || initialDraft.package.manifest.module || "Common");
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
  const sampleFields = sampleFieldOptions(schema, locale, input);
  const campaignActive = Boolean(acceptanceCampaign && !["passed", "failed", "blocked", "cancelled", "interrupted", "superseded"].includes(acceptanceCampaign.status));
  const publicationActive = Boolean(publication && !draftTerminal.has(publication.status));
  const active = Boolean(operation && !draftTerminal.has(operation.status)) || publicationActive || campaignActive || (draft.conversation || []).some((turn: any) => turn.kind === "feedback" && ["queued", "running", "cancelling"].includes(turn.status));
  const locked = busy || active || draft.status === "published";
  const actionable = !locked && !dirty && !remoteConflict;
  const identity = technicalIdentity(draft);
  const identityReady = identity.confirmed;
  const identityEditable = actionable && identity.can_rename;
  const identityChanged = identityInput !== identity.agent_id;
  const identityError = technicalIdError(identityInput, locale);
  const checkedIdentity = Boolean(identityCheck?.available && identityCheck.agent_id === identityInput && identityCheck.revision === draft.revision);
  const validationActionable = actionable && identityReady;
  const acceptance = report?.acceptance || draft.acceptance;
  const staticChecks = report?.static_checks || draft.static_checks || {};
  const presentation = staticChecks.presentation_contract || draft.presentation || {};
  const presentationReady = presentation.status === "ready";
  const staticLabel = staticChecks.errors?.length
    ? tr("自动检查未通过", "Automatic checks failed")
    : staticChecks.checks?.length && !presentationReady
      ? tr("执行检查通过，基础资料待完善", "Execution checks passed; documentation needs attention")
      : staticChecks.checks?.length
        ? tr("自动检查通过", "Automatic checks passed")
        : tr("尚未检查", "Not checked");
  const formalAcceptanceReady = validationActionable
    && acceptance?.reused_validation !== true
    && !staticChecks.errors?.length
    && presentationReady
    && ["PASS", "INCONCLUSIVE"].includes(String(trial?.verdict || draft.trial?.verdict || ""))
    && (trial?.business_output_available ?? draft.trial?.business_output_available) === true
    && (trial?.output_schema_valid ?? draft.trial?.output_schema_valid) === true
    && (trial?.read_only_audit ?? draft.trial?.read_only_audit) === true;
  const publishability = report?.publishability || draft.publishability;
  const canPublish = identityReady && (publishability?.can_publish === true || publishability?.allowed === true);
  const sampleFingerprint = discoveryFingerprint(draft.revision, input);
  const hasVerifiedSample = Boolean(discovery && ["ready", "completed", "needs_input"].includes(discovery.status) && Object.keys(discovery.field_sources || {}).length);
  const sampleConfirmed = Boolean(hasVerifiedSample && confirmedSample === sampleFingerprint && discoveryInputHash === sampleFingerprint);
  const requiresSampleConfirmation = autoDiscover || Object.keys(autoFilled).length > 0;
  const conversation = draft.conversation || [];
  const latestTurn = conversation[conversation.length - 1];
  const latestFeedbackTurn = [...conversation].reverse().find((item: any) => item.kind === "feedback");
  const feedbackTurnNumber = operation?.kind === "feedback" ? operation?.detail?.turn ?? operation?.turn : feedbackLaunch?.turn;
  const feedbackProgressTurn = (feedbackTurnNumber == null ? null : conversation.find((item: any) => (item.turn ?? item.turn_number) === feedbackTurnNumber && item.kind === "feedback")) || feedbackLaunch || latestFeedbackTurn;
  const feedbackOperation = operation?.kind === "feedback" ? operation : null;
  const feedbackTaskActive = Boolean(feedbackOperation && !draftTerminal.has(feedbackOperation.status)) || ["starting", "start_uncertain", "queued", "running", "cancelling"].includes(String(feedbackProgressTurn?.status || ""));
  const trialRunId = trial?.run_id || draft.trial?.run_id;
  const acceptanceDefaultInput = run && draftTerminal.has(run.status)
    && Number(trial?.revision ?? trial?.draft_revision) === Number(draft.revision)
    ? publicValues(schema, run.input || {})
    : publicValues(schema, input);

  const replace = (value: any, replaceEditor = true) => {
    if (!value?.package?.manifest) return;
    if (value.revision < draftRef.current.revision) return;
    if (!replaceEditor && dirtyRef.current && value.revision > draftRef.current.revision) { setRemoteConflict(true); return; }
    if (value.agent_id !== draftRef.current.agent_id) setIdentityInput(value.agent_id);
    setCatalogModule(value.catalog_module || value.package.manifest.module || "Common");
    if (value.revision !== draftRef.current.revision) {
      const previousSchema = draftRef.current.package.manifest.execution?.inputSchema || {};
      setReport(null); setRun(null); setTrial(null); setTrialDialogOpen(false); setTrialConnectionError(false); setDiscovery(null); setDiscoveryInputHash(""); setConfirmedSample(""); setAutoDiscover(false); setAutoFilled({}); setSecrets({}); setAcceptanceCampaign(null); setAcceptanceSetupOpen(false); setAcceptanceProgressOpen(false); setAcceptanceCases([]); setAcceptanceSampleCase(null); sampleApplied.current = "";
      setInput((current) => retainCompatibleInput(previousSchema, value.package.manifest.execution?.inputSchema || {}, current));
      setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision);
      setIdentityCheck(null);
    }
    draftRef.current = value;
    setDraft(value);
    setOperation(value.active_operation || null);
    if (value.publication_operation) setPublication(value.publication_operation);
    setTrial(value.trial || null);
    if (pendingFeedbackRequest.current && value.active_operation?.request_id === pendingFeedbackRequest.current.requestId) {
      pendingFeedbackRequest.current = null; setUncertainFeedback(false); setFeedback(""); setRetryOfTurn(undefined);
    }
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
        agent_technical_id_taken: ["该技术 ID 已被占用，请换一个名称并重新检查。", "This technical ID is already taken. Choose another name and check again."],
        agent_technical_id_invalid: ["技术 ID 格式无效或属于系统保留名称。", "The technical ID is invalid or is a reserved system name."],
        agent_identity_unknown: ["无法确认此草稿的身份来源，技术 ID 和发布操作已锁定。", "The draft identity origin cannot be confirmed. Technical ID and publication actions are locked."],
        agent_id_immutable: ["该 Agent 的技术 ID 已锁定，不能在此修改。", "This Agent's technical ID is locked and cannot be changed here."],
        agent_technical_id_confirmation_required: ["请先确认技术 ID，再选样、试运行或发布。", "Confirm the technical ID before discovery, trial or publication."],
        agent_business_output_contract_missing: ["该草稿只有技术状态，缺少确定性业务规则和业务输出。请先完善定义再试运行。", "This draft has technical status only and lacks a deterministic business rule and business output. Complete the definition before running a trial."],
        agent_trial_business_output_missing: ["SAP查询已执行，但没有生成可展示的业务结果。请完善业务规则和展示定义后重试。", "The SAP query ran, but no displayable business result was generated. Complete the business rule and presentation, then retry."],
        agent_trial_required: ["请先完成一次具备业务结果的只读试运行。", "Complete a read-only trial with business output first."],
        agent_acceptance_contract_missing: ["当前定义缺少正式验收比较契约。", "The definition has no formal acceptance comparison contract."],
        agent_acceptance_request_conflict: ["该验收请求编号已用于其他案例，请重新开始。", "This acceptance request ID was already used for different cases. Start again."],
        agent_acceptance_reused: ["此纯文案修订已复用来源版本的PASS验收，无需再次访问SAP。", "This documentation-only revision already reuses the source version PASS acceptance; SAP does not need to be queried again."],
        agent_presentation_contract_invalid: ["请先补齐业务域、SAP业务组件和业务步骤详细说明。", "Complete the business domain, SAP components and business-step detail first."],
        runtime_not_selectable: ["请先在系统配置中启用Runtime，并检查模型与推理强度。", "Enable the Runtime and check its model and reasoning effort in system settings."],
      };
      setError(labels[code]?.[locale === "zh" ? 0 : 1] || `${tr("操作未完成，请核对输入或重试。", "The operation did not complete. Check inputs or retry.")}${/^[a-z][a-z0-9_]{0,79}$/.test(code) ? ` (${code})` : ""}`);
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
        setFeedbackConnectionError(false);
        setPublicationConnectionError(false);
        const sample = value.sample_discovery || value.metadata?.sample_discovery || (value.active_operation?.kind === "sample_discovery" ? value.active_operation.detail : null);
        if (sample?.run_id && sample.revision === value.revision && !sampleSubmitting.current) {
          setDiscovery((current: any) => current?.run_id && current.run_id !== sample.run_id && current.created_at > sample.created_at ? current : sample);
          if (["queued", "running", "cancelling"].includes(sample.status)) setAutoDiscover(true);
        }
        const id = value.trial?.run_id;
        if (id) {
          const latestRun = await call(`${apiBase}/api/runs/${encodeURIComponent(id)}`, undefined, "GET", controller.signal);
          if (!stopped) {
            setRun(latestRun); setTrialConnectionError(false);
            if (draftTerminal.has(latestRun.status)) {
              const latestReport = await call(`${base}/validation-report`, undefined, "GET", controller.signal);
              if (!stopped) { setReport(latestReport); if (latestReport.trial) setTrial(latestReport.trial); }
            }
          }
        }
      } catch (failure: any) { if (!stopped && failure.name !== "AbortError") { if (trialRunId) setTrialConnectionError(true); setFeedbackConnectionError(true); if (publicationActive) setPublicationConnectionError(true); setError(tr("进度连接中断，正在重试；已保存的任务不会丢失。", "Progress connection interrupted; retrying. Saved work is retained.")); } }
      if (!stopped) timer = setTimeout(poll, document.hidden ? 10000 : 3000);
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [base, trialRunId, publicationActive]);

  useEffect(() => {
    const controller = new AbortController();
    call(`${base}/acceptance-campaigns`, undefined, "GET", controller.signal)
      .then((items: any[]) => {
        if (!items?.length) return;
        return call(`${base}/acceptance-campaigns/${encodeURIComponent(items[0].campaign_id)}`, undefined, "GET", controller.signal);
      })
      .then((value) => { if (value) setAcceptanceCampaign(value); })
      .catch((failure) => { if (failure.name !== "AbortError") setAcceptanceConnectionError(true); });
    return () => controller.abort();
  }, [base]);

  useEffect(() => {
    const campaignId = acceptanceCampaign?.campaign_id;
    if (!campaignId || !campaignActive) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        const value = await call(`${base}/acceptance-campaigns/${encodeURIComponent(campaignId)}`, undefined, "GET", controller.signal);
        if (stopped) return;
        setAcceptanceCampaign(value); setAcceptanceConnectionError(false);
        if (["passed", "failed", "blocked", "cancelled", "interrupted", "superseded"].includes(value.status)) {
          setReport(await call(`${base}/validation-report`, undefined, "GET", controller.signal));
          await refresh(false); return;
        }
      } catch (failure: any) {
        if (!stopped && failure.name !== "AbortError") setAcceptanceConnectionError(true);
      }
      if (!stopped) timer = setTimeout(load, document.hidden ? 10000 : 2500);
    };
    void load();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [base, acceptanceCampaign?.campaign_id, campaignActive]);

  useEffect(() => {
    const campaignId = acceptanceCampaign?.campaign_id;
    if (!campaignId || !campaignActive || typeof EventSource === "undefined") return;
    const stream = new EventSource(`${base}/acceptance-campaigns/${encodeURIComponent(campaignId)}/events`);
    const controller = new AbortController();
    let stopped = false;
    const update = async () => {
      try {
        const value = await call(`${base}/acceptance-campaigns/${encodeURIComponent(campaignId)}`, undefined, "GET", controller.signal);
        if (!stopped) { setAcceptanceCampaign(value); setAcceptanceConnectionError(false); }
      } catch (failure: any) {
        if (!stopped && failure.name !== "AbortError") setAcceptanceConnectionError(true);
      }
    };
    const events = ["campaign_queued", "campaign_started", "case_started", "case_stage_started", "source_anchor_captured", "case_completed", "campaign_completed", "campaign_cancelling", "campaign_finished_without_certificate", "campaign_superseded", "campaign_interrupted"];
    events.forEach((name) => stream.addEventListener(name, update));
    stream.onopen = () => setAcceptanceConnectionError(false);
    stream.onerror = () => setAcceptanceConnectionError(true);
    return () => {
      stopped = true;
      controller.abort();
      events.forEach((name) => stream.removeEventListener(name, update));
      stream.close();
    };
  }, [base, acceptanceCampaign?.campaign_id, campaignActive]);

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
      } catch (failure: any) { if (!stopped && failure.name !== "AbortError") setDiscovery((current: any) => ({ ...current, connection_error: true })); }
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
  const updateSapModules = (value: string) => {
    let current;
    try { current = JSON.parse(manifestText); } catch { setError(tr("高级定义不是有效JSON，请先修正或放弃修改。", "Advanced definition is not valid JSON. Correct or discard it first.")); return; }
    current.sapModules = [...new Set(value.split(/[，,]/).map((item) => item.trim()).filter(Boolean))];
    setManifestText(JSON.stringify(current, null, 2));
  };
  const chooseSampleFields = () => {
    if (!validationActionable || sampleSubmitting.current) return;
    if (pendingSampleRequest.current) { void discover(); return; }
    setSelectedSampleFields(sampleFields.filter((field) => !field.disabled && field.required).map((field) => field.key));
    setSelectingSampleFields(true); setSampleDialogOpen(true);
  };
  const discover = () => {
    if (!validationActionable || sampleSubmitting.current) return;
    const selected = selectedSampleFields.filter((key) => sampleFields.some((field) => field.key === key && !field.disabled));
    if (!pendingSampleRequest.current && !selected.length) return;
    sampleSubmitting.current = true;
    setSelectingSampleFields(false);
    setSampleDialogOpen(true);
    setDiscovery({ status: "starting", phase: "starting", timeout_seconds: 600 });
    return action(async () => {
    setConfirmedSample(""); setDiscoveryInputHash("");
    const fingerprint = discoveryFingerprint(draft.revision, { input: publicValues(schema, input), selectedFields: selected.sort() });
    const pending = pendingSampleRequest.current;
    if (pending && pending.fingerprint !== fingerprint) {
      setDiscovery({ status: "start_uncertain", codes: ["sample_request_scope_changed"] });
      sampleSubmitting.current = false;
      return;
    }
    const payload = pending?.payload || { expectedRevision: draft.revision, input: publicValues(schema, input), selectedFields: selected, requestId: crypto.randomUUID() };
    pendingSampleRequest.current = { fingerprint, payload };
    try {
      if (pending) {
        const current = await call(base);
        const restored = current.sample_discovery;
        if (restored?.request_id === payload.requestId) {
          setDiscovery(restored); setOperation(current.active_operation || null); pendingSampleRequest.current = null; return;
        }
      }
      const value = await call(`${base}/sample-discovery`, payload);
      setDiscovery(value); setOperation({ ...value, kind: "sample_discovery", status: value.status || "queued" });
      pendingSampleRequest.current = null;
    } catch (failure: any) {
      const uncertain = failure.uncertain || !failure.status;
      if (!uncertain) pendingSampleRequest.current = null;
      setDiscovery({ status: uncertain ? "start_uncertain" : "start_failed", codes: [uncertain ? "sample_start_unconfirmed" : "sample_discovery_start_failed"] });
    } finally { sampleSubmitting.current = false; }
    });
  };
  const reviewSample = () => {
    sampleReviewFocus.current = true;
    setSampleDialogOpen(false);
  };
  const cancelSample = async () => {
    if (!discovery?.run_id || discovery.status === "cancelling") return;
    setDiscovery((current: any) => ({ ...current, status: "cancelling", phase: "cleaning_up" }));
    try {
      setDiscovery(await call(`${base}/sample-discovery/${encodeURIComponent(discovery.run_id)}/cancel`, {}));
      await refresh(false);
    } catch { setDiscovery((current: any) => ({ ...current, connection_error: true })); }
  };
  const runTrial = () => {
    if (!validationActionable) return;
    const errors = validateDraftInput(schema, input, secrets, locale); setFieldErrors(errors);
    if (Object.keys(errors).length) { setError(tr("请修正标出的查询参数。", "Correct the highlighted query parameters.")); return; }
    if (requiresSampleConfirmation && !sampleConfirmed) { setError(tr("请先检查并确认回填参数。关闭自动选样会清除未经手工修改的回填值，再使用手工参数。", "Review and confirm the proposed parameters. Turning off discovery clears unedited suggested values so you can enter parameters manually.")); return; }
    setTrialConnectionError(false); setRun(null); setReport(null);
    setTrial({ status: "starting", verdict: "pending", timeout_seconds: 600, started_at: new Date().toISOString() });
    setTrialDialogOpen(true);
    return action(async () => {
      try {
        const value = await call(`${base}/live-validate`, { expectedRevision: draft.revision, input: publicValues(schema, input), sensitiveInputs: secrets, autoDiscover: false, requestId: crypto.randomUUID() });
        setSecrets({}); setTrial(value.trial || value); setOperation({ status: "queued", kind: "trial" });
        progressRef.current?.focus();
      } catch (failure) {
        setTrial((current: any) => ({ ...current, status: "start_failed", verdict: "FAIL", completed_at: new Date().toISOString() }));
        throw failure;
      }
    });
  };
  const submitFeedback = () => {
    if (!actionable || !feedback.trim()) return;
    const request = prepareFeedbackRequest(pendingFeedbackRequest.current, { baseTurn: latestTurn?.turn ?? latestTurn?.turn_number ?? conversation.length, baseRevision: draft.revision, feedback, locale, ...(retryOfTurn === undefined ? {} : { retryOfTurn }) }, () => crypto.randomUUID());
    const expectedTurn = (latestTurn?.turn ?? latestTurn?.turn_number ?? conversation.length) + 1;
    pendingFeedbackRequest.current = request;
    setFeedbackConnectionError(false);
    setFeedbackLaunch({
      turn: expectedTurn,
      kind: "feedback",
      status: "starting",
      user_message: feedback,
      base_revision: draft.revision,
      result_revision: null,
      decision: { execution: { timeout_seconds: 3600 } },
    });
    setFeedbackDialogOpen(true);
    return action(async () => {
      let value;
      try { value = await call(`${base}/feedback`, request); }
      catch (failure: any) {
        if (pendingFeedbackRequest.current !== request) return; // Polling already confirmed this exact request was accepted.
        const uncertain = failure instanceof TypeError || failure.uncertain;
        setFeedbackLaunch((current: any) => ({ ...current, status: uncertain ? "start_uncertain" : "failed", decision: { ...(current?.decision || {}), error_code: uncertain ? "runtime_agent_feedback_connection_failed" : String(failure.message || "runtime_agent_feedback_failed") }, completed_at: uncertain ? undefined : new Date().toISOString() }));
        if (uncertain) { setUncertainFeedback(true); setFeedbackConnectionError(true); }
        else { pendingFeedbackRequest.current = null; setUncertainFeedback(false); }
        throw failure;
      }
      pendingFeedbackRequest.current = null; setUncertainFeedback(false); setRetryOfTurn(undefined);
      pendingFocus.current = true;
      setFeedbackLaunch((current: any) => ({ ...current, turn: value.turn ?? value.turn_number ?? expectedTurn, status: value.status || "queued", decision: { ...(current?.decision || {}), task_id: value.task_id } }));
      setFeedback(""); setOperation({ ...(value.task || value), kind: "feedback", status: value.status || "queued", turn: value.turn ?? value.turn_number });
      await refresh(false); progressRef.current?.focus();
    });
  };
  const retryFeedback = (turn: any) => {
    if (!actionable || !canRetryFeedback(turn)) return;
    pendingFeedbackRequest.current = null; setUncertainFeedback(false);
    setFeedback(turn.user_message ?? turn.feedback); setRetryOfTurn(turn.turn ?? turn.turn_number);
    setNotice(tr("原意见已回填，请核对后确认发送。新一轮基于当前技术 ID 和修订，使用该绑定模型的最新推理强度。", "The original request is prefilled. Review it before confirming. The new turn uses the current technical ID and revision, and the bound model's latest reasoning effort."));
    chatRef.current?.focus();
  };
  const checkIdentity = () => action(async () => {
    if (!identityEditable || identityError) return;
    const result = await call(`${base}/technical-id/check`, { agentId: identityInput });
    setIdentityCheck({ agent_id: result.agent_id, revision: result.revision, available: result.available === true });
  });
  const confirmIdentity = () => action(async () => {
    if (!identityEditable || identityError || !checkedIdentity) return;
    const renamed = identityChanged;
    const value = await call(`${base}/technical-id`, { expectedRevision: draft.revision, agentId: identityInput }, "PUT");
    replace(value); setIdentityInput(value.agent_id); setIdentityCheck(null);
    setNotice(renamed ? tr("技术 ID 已更新并确认。已保存新修订，需重新验证；历史对话保持不变，后续修改基于当前 ID。", "Technical ID changed and confirmed. A new revision requires fresh validation. Conversation history is preserved; future changes use the current ID.") : tr("技术 ID 已确认，现有验收记录保持不变。", "Technical ID confirmed. Existing acceptance records are unchanged."));
  });
  const cancel = () => action(async () => {
    if (acceptanceCampaign?.campaign_id && campaignActive) await call(`${base}/acceptance-campaigns/${acceptanceCampaign.campaign_id}/cancel`, {});
    else if (discovery?.run_id && !draftTerminal.has(discovery.status)) await call(`${base}/sample-discovery/${discovery.run_id}/cancel`, {});
    else if (trialRunId && run && !draftTerminal.has(run.status)) await call(`${apiBase}/api/runs/${trialRunId}/cancel`, {});
    else if (operation?.kind === "feedback" || latestTurn?.status === "running" || latestTurn?.status === "queued") await call(`${base}/feedback/${operation?.turn ?? operation?.detail?.turn ?? latestTurn?.turn ?? latestTurn?.turn_number}/cancel`, {});
    setSecrets({}); await refresh(false);
  });
  const cancelTrial = async () => {
    const currentStatus = String(run?.status || trial?.status || "starting");
    if (!trialRunId || draftTerminal.has(currentStatus)) return;
    setRun((current: any) => ({ ...(current || {}), status: "cancelling" }));
    try { await call(`${apiBase}/api/runs/${encodeURIComponent(trialRunId)}/cancel`, {}); }
    catch { setTrialConnectionError(true); }
  };
  const cancelFeedback = async () => {
    const turnNumber = feedbackOperation?.detail?.turn ?? feedbackOperation?.turn ?? feedbackProgressTurn?.turn ?? feedbackProgressTurn?.turn_number;
    if (!Number.isInteger(turnNumber) || feedbackOperation?.status === "cancelling") return;
    setOperation((current: any) => current?.kind === "feedback" ? { ...current, status: "cancelling", detail: { ...(current.detail || {}), phase: "cancelling" } } : current);
    setFeedbackLaunch((current: any) => current ? { ...current, status: "cancelling" } : current);
    try {
      await call(`${base}/feedback/${turnNumber}/cancel`, {});
      await refresh(false);
    } catch {
      setFeedbackConnectionError(true);
      setError(tr("取消请求尚未确认，正在继续恢复任务状态。", "Cancellation was not confirmed. Continuing to recover the task status."));
    }
  };
  const openAcceptance = () => {
    const first: AcceptanceCaseDraft = {
      caseId: "case-1", source: "current",
      input: acceptanceDefaultInput, sensitiveInputs: { ...secrets },
    };
    setAcceptanceCases([first]); setAcceptanceSetupOpen(true); setError("");
  };
  const findAcceptanceSample = (index: number) => {
    const selected = acceptanceCases[index];
    const caseSampleFields = sampleFieldOptions(schema, locale, selected?.input || input);
    if (selected) setInput(selected.input);
    setAcceptanceSampleCase(index);
    setAcceptanceSetupOpen(false);
    window.setTimeout(() => {
      setAutoDiscover(true);
      setSelectedSampleFields(caseSampleFields.filter((field) => !field.disabled && field.required).map((field) => field.key));
      setSelectingSampleFields(true); setSampleDialogOpen(true);
    }, 0);
  };
  const confirmSample = (confirmed: boolean) => {
    const fingerprint = confirmed ? sampleFingerprint : "";
    setConfirmedSample(fingerprint);
    if (!confirmed || acceptanceSampleCase === null) return;
    setAcceptanceCases((current) => current.map((item, index) => index === acceptanceSampleCase ? {
      ...item,
      source: "sample",
      input: publicValues(schema, input),
    } : item));
    setAcceptanceSampleCase(null);
    setSampleDialogOpen(false);
    setAcceptanceSetupOpen(true);
  };
  const startAcceptance = () => {
    if (!validationActionable || !acceptanceCases.length) return;
    for (const item of acceptanceCases) {
      const errors = validateDraftInput(schema, item.input, item.sensitiveInputs, locale);
      if (Object.keys(errors).length) {
        setError(tr(`验收案例“${item.caseId}”的参数不完整或格式错误。`, `Acceptance case “${item.caseId}” has incomplete or invalid inputs.`));
        return;
      }
    }
    const ids = acceptanceCases.map((item) => item.caseId);
    if (new Set(ids).size !== ids.length || ids.some((value) => !/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/.test(value))) {
      setError(tr("案例名称必须唯一，并使用字母、数字、下划线或连字符。", "Case names must be unique and use letters, numbers, underscores or hyphens."));
      return;
    }
    setAcceptanceSetupOpen(false); setAcceptanceProgressOpen(true);
    setAcceptanceConnectionError(false);
    setAcceptanceCampaign({ status: "starting", phase: "preparing", cases: acceptanceCases.map((item) => ({ case_id: item.caseId, status: "queued", phase: "preparing" })), estimated_max_seconds: acceptanceCases.length * ((manifest.validation?.acceptanceMode === "three_stage" ? 1800 : 0) + 600 + 600 + 120), started_at: new Date().toISOString() });
    return action(async () => {
      try {
        const value = await call(`${base}/acceptance-campaigns`, {
          expectedRevision: draft.revision, requestId: crypto.randomUUID(),
          cases: acceptanceCases.map((item) => ({ caseId: item.caseId, input: item.input, sensitiveInputs: item.sensitiveInputs })),
        });
        setSecrets({}); setAcceptanceCampaign(value); setOperation({ status: value.status || "queued", kind: "formal_acceptance", detail: { campaign_id: value.campaign_id } });
      } catch (failure) {
        setAcceptanceCampaign((current: any) => ({ ...current, status: "interrupted", phase: "completed", report: { verdict: "NOT_TESTED" } }));
        throw failure;
      }
    });
  };
  const cancelAcceptance = async () => {
    const id = acceptanceCampaign?.campaign_id;
    if (!id || !campaignActive) return;
    setAcceptanceCampaign((current: any) => ({ ...current, status: "cancelling" }));
    try {
      setAcceptanceCampaign(await call(`${base}/acceptance-campaigns/${encodeURIComponent(id)}/cancel`, {}));
      await refresh(false);
    } catch {
      setAcceptanceConnectionError(true);
    }
  };
  const check = () => action(async () => { if (!actionable) return; replace(await call(`${base}/validate`, { expectedRevision: draft.revision }), false); setReport(await call(`${base}/validation-report`)); });
  const saveCatalogModule = () => action(async () => {
    if (!actionable || catalogModule === (draft.catalog_module || manifest.module)) return;
    const value = await call(`${base}/catalog-module`, {
      expectedRevision: draft.revision,
      expectedCatalogRevision: draft.catalog_revision || 1,
      module: catalogModule,
    }, "PUT");
    replace(value, false);
    setReport(value);
    const update = value.catalog_module_update;
    setNotice(update?.reload_required
      ? tr("所属模块已保存；前台目录需要手动刷新服务。", "Module saved; the site catalog requires a manual service refresh.")
      : tr("所属模块已保存，不影响执行逻辑或验收。", "Module saved without changing execution or acceptance."));
  });
  const publish = (activate: boolean) => {
    if (!actionable || !canPublish) return;
    const requestId = crypto.randomUUID();
    const request = { expectedRevision: draft.revision, requestId, targetVersion, activate, validationReportDigest: acceptance?.report_digest || report?.report_digest || null };
    setPublicationConnectionError(false);
    setPublication({ status: "starting", phase: "preparing", publication_status: "pending", site_refresh_status: activate ? "pending" : "not_required", version: targetVersion, activate, created_at: new Date().toISOString() });
    setPublicationDialogOpen(true);
    return action(async () => {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const value = await call(`${base}/publish`, request);
          setPublicationConnectionError(false);
          setPublication(value); setOperation({ ...value, kind: "publish" });
          return;
        } catch (failure: any) {
          if ((failure.uncertain || !failure.status) && attempt < 2) {
            setPublicationConnectionError(true);
            await new Promise((resolve) => window.setTimeout(resolve, 1500));
            continue;
          }
          if (failure.uncertain || !failure.status) {
            setPublicationConnectionError(true);
            return;
          }
          setPublication((current: any) => ({ ...current, status: "failed", phase: "completed", publication_status: "failed", failure_code: String(failure.message || "agent_publication_failed"), completed_at: new Date().toISOString() }));
          throw failure;
        }
      }
    });
  };
  const retrySiteRefresh = () => {
    setPublicationConnectionError(false);
    setPublication((current: any) => ({ ...current, status: "starting", phase: "building_site", publication_status: "published", site_refresh_status: "queued", failure_code: null, started_at: null, completed_at: null }));
    setPublicationDialogOpen(true);
    return action(async () => {
      const value = await call(`${base}/refresh-site`, { requestId: crypto.randomUUID() });
      setPublication(value); setOperation({ ...value, kind: "site_refresh" });
    });
  };
  const finishPublication = () => {
    setPublicationDialogOpen(false);
    onPublished(publication?.result || publication || {});
  };
  const restore = () => action(async () => {
    if (!actionable) return;
    const value = await call(`${base}/undo`, { baseRevision: draft.revision, targetRevision: compareFrom });
    replace(value); setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision); setReport(null); setConfirmedSample("");
    setNotice(tr("已恢复为新修订，原历史记录保持不变。", "Restored as a new revision; history is unchanged."));
  });
  const revisions: number[] = (draft.revisions?.length ? draft.revisions.map((item: any) => item.revision) : Array.from({ length: draft.revision }, (_, index) => index + 1)).sort((a: number, b: number) => a - b);

  return <main className="agent-management agent-draft-workspace"><header><button className="agent-secondary-action" disabled={dirty || busy} onClick={onBack}>{tr("返回管理列表", "Back to management")}</button><p className="eyebrow">{tr("未发布草稿 · 不影响当前活动Agent", "Unpublished draft · Active Agent unchanged")}</p><h1>{localText(manifest.title, locale) || draft.agent_id}</h1><p>{localText(manifest.summary, locale)}</p><div className="draft-meta"><span>{draft.catalog_module || manifest.module}</span><code>{draft.agent_id}</code><span>{tr("目标版本", "Target version")} {draft.target_version}</span><span>{tr("修订", "Revision")} {draft.revision}</span></div></header>
    <nav className="agent-steps" aria-label={tr("编辑步骤", "Editing steps")}>{draftStepNames.map((name, index) => <button key={name} className={step === name ? "active" : "agent-secondary-action"} aria-current={step === name ? "step" : undefined} onClick={() => setStep(name)}>{index + 1}. {name === "compose" ? tr("定义与修改", "Define and revise") : name === "review" ? tr("检查修改内容", "Review changes") : tr("发布与启用", "Publish and activate")}</button>)}</nav>
    {error && <p className="agent-alert error" role="alert">{error}</p>}{notice && <p className="agent-alert" role="status">{notice}</p>}
    {draft.technical_identity?.kind === "version_upgrade" && draft.has_publishable_changes === false && <p className="agent-alert" role="status">{tr("当前草稿没有需要发布的业务变更；所属模块已独立保存，可在管理列表中删除此草稿。", "This draft has no business changes to publish. Its catalog module is already saved independently, so you can delete this draft from the management list.")}</p>}
    {remoteConflict && <p className="agent-alert error" role="alert">{tr("草稿已在其他操作中生成新修订。当前未保存内容已保留，请复制需要保留的修改，再放弃本地修改并加载最新修订。", "Another operation created a revision. Your unsaved content is retained. Copy any edits you need, then discard local edits and load the latest revision.")}</p>}
    {step === "compose" && <section className="agent-panel draft-catalog-module"><h2>{tr("所属模块", "Catalog module")}</h2><div className="draft-module-editor"><label>{tr("目录归类", "Catalog grouping")}<select value={catalogModule} disabled={!actionable} onChange={(event) => setCatalogModule(event.target.value)}>{catalogModules.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><button type="button" disabled={!actionable || catalogModule === (draft.catalog_module || manifest.module)} onClick={saveCatalogModule}>{tr("保存所属模块", "Save module")}</button></div><p>{tr("仅调整目录归类、导航和发现方式，不改变SAP范围、执行逻辑或验收状态。", "This changes catalog grouping, navigation and discovery only. It does not change SAP scope, execution or acceptance.")}</p></section>}
    {dirty && <aside className="agent-alert draft-unsaved"><p>{tr("有尚未保存的定义修改。请保存或放弃后，再进行选样、试运行、对话或发布。", "There are unsaved definition changes. Save or discard before discovery, trials, chat or publication.")}</p><div className="agent-actions"><button disabled={locked || remoteConflict} onClick={save}>{tr("保存新修订", "Save revision")}</button><button className="agent-secondary-action" disabled={locked} onClick={discard}>{tr("放弃未保存修改", "Discard unsaved edits")}</button></div></aside>}
    {active && <p className="agent-alert" role="status" ref={progressRef} tabIndex={-1}>{publicationActive ? tr("Agent发布正在后台执行", "Agent publication is running in the background") : <>{tr("草稿任务", "Draft operation")}: {draftStatus(operation?.status || latestTurn?.status, locale)}</>} {!publicationActive && <button className="agent-secondary-action" disabled={busy || operation?.status === "cancelling"} onClick={cancel}>{tr("取消任务", "Cancel task")}</button>}</p>}
    {step === "compose" && <div className="agent-document-body">
      <section className="agent-panel draft-technical-identity" id="draft-technical-identity" aria-labelledby="draft-identity-title">
        <div className="draft-identity-heading"><h2 id="draft-identity-title">{tr("技术 ID", "Technical ID")}</h2><span className={identityReady ? "draft-identity-confirmed" : "draft-identity-pending"}>{identity.kind === "unknown" ? tr("身份待核对", "Identity needs review") : identity.locked ? tr("已锁定", "Locked") : identityReady ? tr("已确认", "Confirmed") : tr("首次发布前需确认", "Confirm before first publication")}</span></div>
        <p>{identity.kind === "version_upgrade" ? tr("这是已有 Agent 的版本升级，技术 ID 保持不变。", "This is a version upgrade of an existing Agent. Its technical ID is unchanged.") : identity.kind === "unknown" ? tr("无法确认草稿来源。可继续编辑或提供修改意见，但确认身份前不能选样、验证或发布。", "The draft origin cannot be confirmed. Editing and feedback remain available, but discovery, validation and publication require a confirmed identity.") : identity.locked ? tr("此 Agent 已发布，技术 ID 不再允许修改。", "This Agent has been published. Its technical ID can no longer change.") : tr("新 Agent 可以在首次发布前改名。修改 ID 会保存新修订并使当前验证失效；发布后永久锁定。", "A new Agent can be renamed before its first publication. Changing the ID creates a revision and invalidates current validation; publication locks it permanently.")}</p>
        {identity.kind === "new_agent" && !identity.locked ? <>
          <label htmlFor="draft-technical-id">{tr("拟使用的技术 ID", "Proposed technical ID")}<input id="draft-technical-id" value={identityInput} disabled={!identityEditable} minLength={3} maxLength={80} spellCheck={false} autoCapitalize="none" autoComplete="off" aria-invalid={Boolean(identityError)} aria-describedby="draft-identity-help" onChange={(event) => { setIdentityInput(event.target.value); setIdentityCheck(null); }} /></label>
          <p id="draft-identity-help">{identityError || tr("3–80个小写字母或数字，可用单个连字符分段。不允许系统保留名。", "3–80 lowercase letters or digits, optionally separated by single hyphens. Reserved system names are not allowed.")}</p>
          {checkedIdentity && <p role="status">{tr("该 ID 当前可用，但尚未预留；确认时会再次检查。", "This ID is currently available but not reserved; confirmation checks it again.")}</p>}
          <div className="agent-actions"><button className="agent-secondary-action" disabled={!identityEditable || Boolean(identityError)} onClick={checkIdentity}>{tr("检查可用性", "Check availability")}</button><button disabled={!identityEditable || !checkedIdentity || Boolean(identityError) || (identityReady && !identityChanged)} onClick={confirmIdentity}>{identityChanged ? tr("确认改名并重新验证", "Confirm rename and revalidate") : tr("确认使用此 ID", "Confirm this ID")}</button></div>
        </> : <code>{identity.agent_id}</code>}
        {!identityReady && <p className="draft-identity-warning" role="status">{tr("技术 ID 尚未确认：可以编辑和对话；选样、试运行、验收和发布暂不可用。", "Technical ID is not confirmed: editing and chat are available; discovery, trials, acceptance and publication are disabled.")}</p>}
        {(active || dirty) && identity.kind === "new_agent" && <p>{tr("请先结束当前任务，或保存/放弃定义修改，再操作技术 ID。", "Finish the active task or save/discard definition edits before changing the technical ID.")}</p>}
      </section>
      <section className="agent-panel"><h2>{tr("基础信息", "Basic information")}</h2><div className="draft-basic-grid">{(["zh", "en"] as const).map((lang) => <div key={lang}><label>{tr(lang === "zh" ? "中文名称" : "英文名称", lang === "zh" ? "Chinese name" : "English name")}<input disabled={locked} value={manifest.title?.[lang] || ""} onChange={(event) => updateBasic("title", event.target.value, lang)} /></label><label>{tr(lang === "zh" ? "中文说明" : "英文说明", lang === "zh" ? "Chinese summary" : "English summary")}<textarea disabled={locked} rows={3} value={manifest.summary?.[lang] || ""} onChange={(event) => updateBasic("summary", event.target.value, lang)} /></label></div>)}</div><div className="draft-basic-grid"><label>{tr("业务域", "Business domain")}<input disabled={locked} value={manifest.owner || ""} placeholder={tr("例如：MM-PUR / MM-IM", "For example: MM-PUR / MM-IM")} onChange={(event) => updateBasic("owner", event.target.value)} /></label><label>{tr("SAP业务组件", "SAP business components")}<input disabled={locked} value={(manifest.sapModules || []).join(", ")} placeholder={tr("使用逗号分隔，例如：MM-PUR, MM-IM", "Comma-separated, for example: MM-PUR, MM-IM")} onChange={(event) => updateSapModules(event.target.value)} /></label></div><p>{tr("所属模块仅控制目录归类；业务域和SAP业务组件描述真实业务范围。技术 ID 仅可通过上方确认区域修改；复杂结构可通过页面底部对话修改。", "Catalog module controls grouping only; business domain and SAP components describe the real business scope. Change the technical ID only in the confirmation section above; use the conversation below for complex definitions.")}</p>{presentation.issues?.length > 0 && <aside className="agent-presentation-diagnostics" role="status"><h3>{tr("基础资料待完善", "Documentation needs attention")}</h3><ul>{presentation.issues.filter((item: any) => item.blocking).map((item: any, index: number) => <li key={`${item.code}-${index}`}>{localText(item.message, locale)} {item.path && <code>{item.path}</code>}</li>)}</ul></aside>}<div className="agent-actions"><button disabled={locked || remoteConflict || !dirty} onClick={save}>{tr("保存新修订", "Save revision")}</button></div></section>
      <section className="agent-run-panel"><h2>{tr("试运行这个Agent", "Try this Agent")}</h2><p>{tr("填写业务参数，使用已保存的草稿执行只读查询。试运行不会修改Agent默认值，也不代表正式验收通过。", "Enter business parameters to run the saved draft read-only. Trial input does not change Agent defaults or establish formal acceptance.")}</p>
        <form onSubmit={(event) => { event.preventDefault(); void runTrial(); }} noValidate><AgentDraftInputs schema={schema} values={input} onChange={(next) => { setInput(next); setFieldErrors({}); setConfirmedSample(""); if (hasVerifiedSample && Object.entries(discovery.input || {}).every(([key, value]) => discoveryFingerprint(0, { value }) === discoveryFingerprint(0, { value: next[key] }))) setDiscoveryInputHash(discoveryFingerprint(draft.revision, next)); }} secrets={secrets} onSecrets={setSecrets} locale={locale} errors={fieldErrors} disabled={locked || dirty} />
          <div className="draft-discovery" ref={sampleInputsRef} tabIndex={-1}>
            <label className="draft-checkbox"><input type="checkbox" checked={autoDiscover} disabled={!validationActionable} onChange={(event) => { const enabled = event.target.checked; setAutoDiscover(enabled); setConfirmedSample(""); if (enabled) { chooseSampleFields(); } else { setInput((current) => clearDiscoveredInput(current, autoFilledRef.current)); setAutoFilled({}); setDiscoveryInputHash(""); setFieldErrors({}); } }} />{tr("自动查找验证数据", "Find validation data automatically")}</label>
            {autoDiscover && <>
              <p>{tr("保留已填范围，有界读取真实SAP样本；回填后请确认。Runtime使用 gpt-5.6-sol。敏感参考号请手工填写。", "Keep the entered scope and read bounded live SAP samples. Confirm before trial. Runtime: gpt-5.6-sol. Enter sensitive references manually.")}</p>
              <button type="button" className="agent-secondary-action" disabled={!validationActionable} onClick={chooseSampleFields}>{tr(discovery ? "重新查找样本" : "查找样本", discovery ? "Find another sample" : "Find sample")}</button>
              {discovery && <div>
                <SampleProgressSummary value={discovery} locale={locale} />
                <button type="button" className="agent-secondary-action" onClick={() => { setSelectingSampleFields(false); setSampleDialogOpen(true); }}>{tr("查看查找进度", "View discovery progress")}</button>
                <p>{localText(discovery.selection_reason || discovery.result?.selection_reason, locale)}</p>
                {Object.keys(discovery.field_sources || {}).length > 0 && <><p>{tr(`来源：本次SAP只读查询；已核对${Object.keys(discovery.field_sources).length}个参数。`, `Source: this read-only SAP query; ${Object.keys(discovery.field_sources).length} parameters verified.`)}</p><details><summary>{tr("样本来源详情", "Sample source details")}</summary>{discovery.query_count !== undefined && <p>{tr("数据读取次数：", "Data reads: ")}{discovery.query_count}</p>}<dl>{Object.entries(discovery.field_sources).map(([field, source]: [string, any]) => <div key={field}><dt>{localText(schema.properties?.[field]?.title, locale) || field}</dt><dd><code>{source.source_field}</code> · <code>{source.evidence_ref}</code></dd></div>)}</dl></details></>}
                {(discovery.missing_fields || []).length > 0 && <p>{tr("仍需补充：", "Still required: ")}{discovery.missing_fields.map((field: string) => localText(schema.properties?.[field]?.title, locale) || field).join("、")}</p>}
                {hasVerifiedSample && <label className="draft-checkbox"><input type="checkbox" disabled={!validationActionable || discoveryInputHash !== sampleFingerprint} checked={sampleConfirmed} onChange={(event) => confirmSample(event.target.checked)} />{tr(acceptanceSampleCase === null ? "已核对回填参数，确认用于本次试运行" : "已核对回填参数，确认用于当前验收案例", acceptanceSampleCase === null ? "I reviewed the proposed parameters and confirm this trial" : "I reviewed the proposed parameters and confirm this acceptance case")}</label>}
              </div>}
            </>}
          </div>
          <div className="agent-actions"><button type="button" className="agent-secondary-action" disabled={!actionable} onClick={check}>{tr("自动检查定义", "Check definition")}</button><button type="submit" disabled={!validationActionable || (requiresSampleConfirmation && !sampleConfirmed)}>{tr("执行只读试运行", "Run read-only trial")}</button></div></form>
        <AgentSampleProgress open={sampleDialogOpen} value={discovery} locale={locale} canRetry={validationActionable} fields={selectingSampleFields ? sampleFields : undefined} selectedFields={selectedSampleFields} onSelection={setSelectedSampleFields} onStart={discover} onClose={() => setSampleDialogOpen(false)} onCancel={cancelSample} onRetry={chooseSampleFields} onReview={reviewSample} onAfterClose={() => { if (sampleReviewFocus.current) { sampleReviewFocus.current = false; sampleInputsRef.current?.focus(); } }} />
        <AgentTrialProgress open={trialDialogOpen} run={run} trial={trial} locale={locale} connectionError={trialConnectionError} errorMessage={error} onClose={() => setTrialDialogOpen(false)} onCancel={cancelTrial} onReview={() => { trialReviewFocus.current = true; setTrialDialogOpen(false); }} onAfterClose={() => { if (trialReviewFocus.current) { trialReviewFocus.current = false; trialResultRef.current?.focus(); } }} />
      </section>
      <section className="agent-panel" ref={acceptanceResultRef} tabIndex={-1}><h2>{tr("试运行与验收", "Trial and acceptance")}</h2><p>{tr("自动检查、试运行和正式验收分别记录，不相互替代。正式验收支持1–5组必选案例，全部通过才会解锁发布。", "Automatic checks, trials and formal acceptance are recorded separately. Formal acceptance supports 1–5 required cases; every case must pass before publication is unlocked.")}</p><dl><dt>{tr("自动检查", "Automatic checks")}</dt><dd>{staticLabel}</dd><dt>{tr("正式验收", "Formal acceptance")}</dt><dd>{draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</dd></dl>{(acceptance?.source_version || acceptance?.reused_from_version) && <p>{tr("复用原版本验收：", "Acceptance reused from version: ")}{acceptance.source_version || acceptance.reused_from_version}</p>}{publishability?.blockers?.length > 0 && <ul className="draft-blockers">{publishability.blockers.map((item: any, index: number) => <li key={index}>{localText(item.message || item.description, locale) || tr("正式验收或发布条件尚未满足。", "Formal acceptance or publication conditions remain unmet.")}<details><summary>{tr("技术原因", "Technical reason")}</summary><code>{typeof item === "string" ? item : item.code}</code></details></li>)}</ul>}
        {trialRunId ? <><p>{tr("试运行修订", "Trial revision")}: {trial?.revision ?? trial?.draft_revision ?? "—"} · {draftStatus(run?.status || trial?.status, locale)}{(trial?.revision ?? trial?.draft_revision) !== undefined && (trial?.revision ?? trial?.draft_revision) !== draft.revision && <strong> · {tr("历史修订结果，不适用于当前定义", "Historical result; not acceptance for this revision")}</strong>}</p>{run && !draftTerminal.has(run.status) && <><TrialProgressSummary run={run} trial={trial} locale={locale} connectionError={trialConnectionError} /><button type="button" className="agent-secondary-action" onClick={() => setTrialDialogOpen(true)}>{tr("查看试运行进度", "View trial progress")}</button></>}{run && draftTerminal.has(run.status) && <div ref={trialResultRef} tabIndex={-1}><AgentDraftResult run={run} trial={trial} locale={locale} runPath={runPath} apiBase={apiBase} /></div>}{!run && <a href={`${runPath}?run=${encodeURIComponent(trialRunId)}`} target="_blank" rel="noreferrer">{tr("查看试运行记录", "Open trial run")}</a>}</> : <p>{tr("当前没有试运行记录。", "No trial has been run yet.")}</p>}
        {trial?.verdict === "INCONCLUSIVE" && <p className="agent-alert">{tr("当前试运行证据不完整。允许继续正式验收，但最终结果很可能为BLOCKED。", "The current trial has incomplete evidence. Formal acceptance may continue, but is likely to be BLOCKED.")}</p>}
        <AcceptanceSummary campaign={acceptanceCampaign} locale={locale} onOpen={() => setAcceptanceProgressOpen(true)} />
        <div className="agent-actions"><button type="button" disabled={!formalAcceptanceReady || campaignActive} onClick={openAcceptance}>{tr(acceptanceCampaign ? "更换案例并重新验收" : "开始正式验收", acceptanceCampaign ? "Change cases and run again" : "Start formal acceptance")}</button><button className="agent-secondary-action" disabled={busy} onClick={() => action(async () => { setReport(await call(`${base}/validation-report`)); await refresh(false); })}>{tr("刷新验证状态", "Refresh validation status")}</button></div>
        {!formalAcceptanceReady && <p>{acceptance?.reused_validation ? tr("此纯文案修订已复用来源版本的PASS验收，无需再次访问SAP。", "This documentation-only revision already reuses the source version PASS acceptance; SAP does not need to be queried again.") : tr("请先确认技术ID、保存修改、通过自动检查，并完成一次具备业务结果且只读审计通过的试运行。", "Confirm the technical ID, save edits, pass automatic checks, and complete a trial with business output and a passing read-only audit.")}</p>}
        <AgentAcceptanceSetup open={acceptanceSetupOpen} locale={locale} schema={schema} mode={manifest.validation?.acceptanceMode || "three_stage"} runtime={draft.formal_acceptance_runtime} cases={acceptanceCases} currentInput={acceptanceDefaultInput} currentSecrets={secrets} sampleInput={discovery?.input} canUseSample={sampleConfirmed} disabled={!formalAcceptanceReady} onCases={setAcceptanceCases} onFindSample={findAcceptanceSample} onClose={() => setAcceptanceSetupOpen(false)} onStart={startAcceptance} />
        <AgentAcceptanceProgress open={acceptanceProgressOpen} locale={locale} campaign={acceptanceCampaign} connectionError={acceptanceConnectionError} apiBase={apiBase} draftId={draft.draft_id} onClose={() => { setAcceptanceProgressOpen(false); window.setTimeout(() => acceptanceResultRef.current?.focus(), 0); }} onCancel={cancelAcceptance} onAdjust={() => { setAcceptanceProgressOpen(false); window.setTimeout(() => chatRef.current?.focus(), 0); }} onRetry={() => { setAcceptanceProgressOpen(false); window.setTimeout(openAcceptance, 0); }} />
      </section>
      <section className="agent-panel"><h2>{tr("输入与输出", "Inputs and outputs")}</h2><div className="draft-basic-grid"><div><h3>{tr("您需要提供", "What you provide")}</h3><ul>{draftInputLabels(manifest.execution?.inputSchema || {}, locale, input).map((item) => <li key={item.key}>{item.label}<span className="draft-field-requirement">{item.requirement}</span></li>)}</ul></div><div><h3>{tr("您将获得", "What you receive")}</h3><ul>{(manifest.outputs?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></div></div></section>
      <AgentDefinitionDetails agent={manifest} presentation={draft.presentation} locale={locale} idPrefix="draft" />
      <section className="agent-panel"><h2>{tr("安全边界", "Guardrails")}</h2><ul>{(manifest.guardrails?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>
      <section><details className="agent-panel draft-advanced"><summary>{tr("高级技术编辑", "Advanced technical editing")}</summary><p>{tr("仅在了解数据契约时使用。所有修改仍须通过安全检查。", "Use only when familiar with the contracts. Every change remains subject to safety checks.")}</p><label>Agent JSON<textarea disabled={locked} rows={20} spellCheck={false} value={manifestText} onChange={(event) => setManifestText(event.target.value)} /></label><label>README<textarea disabled={locked} rows={8} value={readme} onChange={(event) => setReadme(event.target.value)} /></label><label>{tr("受控规则源码", "Managed rule source")}<textarea disabled={locked} rows={10} spellCheck={false} value={rules} onChange={(event) => setRules(event.target.value)} /></label><button disabled={locked || remoteConflict || !dirty} onClick={save}>{tr("保存新修订", "Save revision")}</button></details></section>
      <section className="agent-panel draft-conversation"><h2>{tr("您的修改意见是？", "What would you like to change?")}</h2>
        <p>{tr("可以描述业务需求、回答澄清问题或继续调整。明确需求后会保存为新修订，不会自动发布。请勿在对话中输入敏感参考号。", "Describe your business needs, answer clarifications or refine the draft. Clear changes become a new revision, never an automatic publication. Do not enter sensitive references here.")}</p>
        {feedbackTaskActive && <div className="draft-feedback-progress-card"><FeedbackProgressSummary turn={feedbackProgressTurn} operation={feedbackOperation} locale={locale} connectionError={feedbackConnectionError} /><button type="button" className="agent-secondary-action" onClick={() => setFeedbackDialogOpen(true)}>{tr("查看修改进度", "View revision progress")}</button></div>}
        <AgentDraftConversation turns={conversation} locale={locale} onRetry={retryFeedback} retryDisabled={!actionable} />
        {retryOfTurn !== undefined && <div className="draft-retry-context" role="status"><p>{tr(`正在重试原记录 #${retryOfTurn}。请核对意见后确认发送；将基于当前 ID ${draft.agent_id}、修订 ${draft.revision} 创建新一轮，不会改写历史或自动发布。`, `Retrying record #${retryOfTurn}. Review and confirm before sending. A new turn will use current ID ${draft.agent_id}, revision ${draft.revision}, without rewriting history or publishing.`)}</p><button type="button" className="agent-secondary-action" disabled={!actionable} onClick={() => { setRetryOfTurn(undefined); pendingFeedbackRequest.current = null; setUncertainFeedback(false); }}>{tr("改为新的修改意见", "Use as new feedback")}</button></div>}
        {uncertainFeedback && <p className="agent-alert" role="status">{tr("上次发送的响应未确认。保持原意见再次发送会重传同一请求，不会创建重复对话；您也可以等待进度刷新。", "The previous submission was not acknowledged. Sending the unchanged request again retransmits the same request without creating a duplicate; you can also wait for progress to refresh.")}</p>}
        <label htmlFor="draft-feedback">{tr("修改意见或澄清回复", "Revision request or clarification reply")}<textarea ref={chatRef} id="draft-feedback" rows={4} value={feedback} disabled={locked || dirty} onChange={(event) => { setFeedback(event.target.value); pendingFeedbackRequest.current = null; setUncertainFeedback(false); }} /></label>
        <div className="agent-actions"><button disabled={!actionable || !feedback.trim()} onClick={submitFeedback}>{uncertainFeedback ? tr("重传原请求", "Resend original request") : retryOfTurn !== undefined ? tr("确认发送重试", "Confirm retry") : tr("发送修改意见", "Send feedback")}</button><button className="agent-secondary-action" onClick={() => { setCompareFrom(Math.max(1, draft.revision - 1)); setCompareTo(draft.revision); setStep("review"); }}>{tr("查看修改前后对比", "Review before and after")}</button></div>
        <AgentFeedbackProgress open={feedbackDialogOpen} turn={feedbackProgressTurn} operation={feedbackOperation} locale={locale} connectionError={feedbackConnectionError} errorMessage={feedbackLaunch?.status === "failed" ? error : ""} onClose={() => setFeedbackDialogOpen(false)} onCancel={cancelFeedback} onRetry={() => { if (!feedbackProgressTurn) return; feedbackAfterClose.current = () => retryFeedback(feedbackProgressTurn); setFeedbackDialogOpen(false); }} onReview={() => { if (!feedbackProgressTurn) return; feedbackAfterClose.current = () => { setCompareFrom(feedbackProgressTurn.base_revision ?? Math.max(1, draft.revision - 1)); setCompareTo(feedbackProgressTurn.result_revision ?? draft.revision); setStep("review"); }; setFeedbackDialogOpen(false); }} onAfterClose={() => { const followUp = feedbackAfterClose.current; feedbackAfterClose.current = null; followUp?.(); }} />
      </section>
    </div>}
    {step === "review" && <section className="agent-panel"><h2>{tr("检查修改内容", "Review changes")}</h2><p>{tr("对比已保存的草稿修订，不代表与当前活动版本比较。", "Compare saved draft revisions, not necessarily the active version.")}</p><div className="draft-basic-grid"><label>{tr("修改前修订", "Before revision")}<select value={compareFrom} onChange={(event) => setCompareFrom(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label><label>{tr("修改后修订", "After revision")}<select value={compareTo} onChange={(event) => setCompareTo(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label></div>{!diff ? <p role="status">{tr("正在加载修改对比…", "Loading comparison…")}</p> : diff.changes?.length ? diff.changes.map((change: any, index: number) => <section className="draft-diff-change" key={index}><h3>{diffBusinessLabel(change, manifest, locale)} · {tr(change.change === "added" ? "新增" : change.change === "removed" ? "删除" : "修改", change.change === "added" ? "Added" : change.change === "removed" ? "Removed" : "Modified")}</h3><details><summary>{tr("技术位置", "Technical location")}</summary><code>{change.path}</code></details><div className="draft-diff-values">{(["before", "after"] as const).map((side) => <div key={side} className={`draft-diff-${side}`}><h4>{side === "before" ? tr("修改前", "Before") : tr("修改后", "After")}</h4><pre>{!(side in change) || change[`${side}_exists`] === false ? tr("未设置", "Not set") : change[side] === null ? tr("空值", "Null") : change[side] === "" ? tr("空内容", "Empty content") : typeof change[side] === "string" ? change[side] : JSON.stringify(change[side], null, 2)}</pre></div>)}</div>{change.unified_diff && <details><summary>{tr("查看逐行变更", "View line-by-line changes")}</summary><pre className="draft-unified-diff">{String(change.unified_diff).split("\n").map((line, number) => <span key={number} className={line.startsWith("+") ? "diff-added" : line.startsWith("-") ? "diff-removed" : ""}>{line}{"\n"}</span>)}</pre></details>}</section>) : <p>{tr("两个修订没有内容差异。", "There are no content differences.")}</p>}<div className="agent-actions"><button className="agent-secondary-action" disabled={!actionable || compareFrom === draft.revision} onClick={restore}>{tr("恢复修改前修订（创建新修订）", "Restore before revision (creates a new revision)")}</button><button onClick={() => setStep("compose")}>{tr("返回定义与试运行", "Back to definition and trial")}</button></div></section>}
    {step === "publish" && <section className="agent-panel"><h2>{tr("发布与启用", "Publish and activate")}</h2><p>{tr("正式验收通过后才可发布。系统会先在隔离工作区检查Agent包并构建候选页面，再用一个提交快进合并到本地main；不会推送远端。", "Formal acceptance is required. The package and candidate pages are checked in an isolated worktree before one commit is fast-forwarded into local main. Nothing is pushed.")}</p><p>{tr("将发布的技术 ID", "Technical ID to publish")}: <code>{identity.agent_id}</code> · {identityReady ? tr("已确认", "Confirmed") : tr("尚未确认", "Not confirmed")}</p>{!identityReady && <button className="agent-secondary-action" onClick={() => setStep("compose")}>{tr("返回定义并确认技术 ID", "Return to confirm technical ID")}</button>}<p>{tr("正式验收", "Formal acceptance")}: {draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</p>{!canPublish && <p role="status">{tr("正式验收或发布条件尚未满足，暂不可发布。请在定义页面查看验证状态。", "Acceptance or publication requirements remain unmet. Review validation in the definition page.")}</p>}<label>{tr("目标版本", "Target version")}<input value={targetVersion} disabled={locked} onChange={(event) => setTargetVersion(event.target.value)} /></label><div className="agent-actions"><button disabled={!actionable || !canPublish} onClick={() => publish(false)}>{tr("发布为未启用版本", "Publish inactive")}</button><button disabled={!actionable || !canPublish} onClick={() => publish(true)}>{tr("发布并启用", "Publish and activate")}</button></div>{publication && <div className="draft-publication-summary"><PublicationProgressSummary value={publication} locale={locale} connectionError={publicationConnectionError} /><div className="agent-actions"><button type="button" className="agent-secondary-action" onClick={() => setPublicationDialogOpen(true)}>{tr("查看发布进度", "View publication progress")}</button>{publication.publication_status === "published" && publication.site_refresh_status === "failed" && <button type="button" onClick={retrySiteRefresh}>{tr("重试刷新页面", "Retry page refresh")}</button>}</div></div>}<AgentPublicationProgress open={publicationDialogOpen} value={publication} locale={locale} connectionError={publicationConnectionError} onClose={() => setPublicationDialogOpen(false)} onRetrySite={retrySiteRefresh} onComplete={finishPublication} /></section>}
  </main>;
}
