import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { canRetryFeedback, changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftStatus, draftStepNames, draftTerminal, legacyDraftStep, localText, prepareFeedbackRequest, publicInput, publicValues, restoreDiscoveredInput, retainCompatibleInput, technicalIdentity, technicalIdError, validateDraftInput } from "../lib/agentDraft";
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
import AcceptanceReadiness from "./AcceptanceReadiness";
import type { AcceptanceCaseDraft } from "./AgentAcceptance";
import { sampleFieldOptions } from "../lib/agentDraft";
import "../styles/agent-draft.css";

type Props = { initialDraft: any; apiBase: string; locale: Locale; runPath: string; initialStep?: string; onBack: () => void; onPublished: (result: any) => void; onActivatePublished?: (result: any) => void };
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

export default function AgentDraftWorkspace({ initialDraft, apiBase, locale, runPath, initialStep, onBack, onPublished, onActivatePublished }: Props) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const [draft, setDraft] = useState(initialDraft);
  const draftRef = useRef(initialDraft);
  const [manifestText, setManifestText] = useState(JSON.stringify(initialDraft.package.manifest, null, 2));
  const [readme, setReadme] = useState(initialDraft.package.readme || "");
  const [rules, setRules] = useState(initialDraft.package.rules || "");
  const [step, setStep] = useState<Step>(draftStepNames.includes(initialStep as Step) ? initialStep as Step : "purpose");
  const [input, setInput] = useState<Record<string, any>>(() => publicValues(initialDraft.package.manifest.execution?.inputSchema || {}, {}, true));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remoteConflict, setRemoteConflict] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackIntent, setFeedbackIntent] = useState<"explain" | "revise">("explain");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [feedbackContext, setFeedbackContext] = useState<{ fieldPath?: string; runId?: string; acceptanceCampaignId?: string }>({});
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
  const [compareBaseline, setCompareBaseline] = useState<"revision" | "source">(initialDraft.technical_identity?.kind === "version_upgrade" ? "source" : "revision");
  const [compareFrom, setCompareFrom] = useState(Math.max(1, initialDraft.revision - 1));
  const [compareTo, setCompareTo] = useState(initialDraft.revision);
  const [targetVersion, setTargetVersion] = useState(initialDraft.target_version || initialDraft.package.manifest.version);
  const [catalogModule, setCatalogModule] = useState(initialDraft.catalog_module || initialDraft.package.manifest.module || "Common");
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [uiStateUnsynced, setUiStateUnsynced] = useState(false);
  const uiStateQueue = useRef<Promise<void>>(Promise.resolve());
  const stepHistoryReady = useRef(false);
  const restoringStepHistory = useRef(false);
  const chatRef = useRef<HTMLTextAreaElement>(null);
  const assistantToggleRef = useRef<HTMLButtonElement>(null);
  const assistantWasOpen = useRef(false);
  const progressRef = useRef<HTMLParagraphElement>(null);
  const pendingFocus = useRef(false);
  const base = `${apiBase}/api/authoring/agents/${encodeURIComponent(draft.draft_id)}`;
  const dirty = changedDefinition(draft, manifestText, readme, rules);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const inputRef = useRef(input); inputRef.current = input;
  const sampleApplied = useRef("");
  let manifest: any;
  try { manifest = JSON.parse(manifestText); } catch { manifest = draft.package.manifest; }
  const schema = manifest.execution?.inputSchema || { type: "object", properties: {} };
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
  const acceptanceReadiness = draft.acceptance_readiness || staticChecks.acceptance_readiness;
  const effectiveTrial = report?.effective_trial || draft.effective_trial;
  const presentationReady = presentation.status === "ready";
  const staticLabel = staticChecks.errors?.length
    ? tr("自动检查未通过", "Automatic checks failed")
    : staticChecks.checks?.length && !presentationReady
      ? tr("执行检查通过，基础资料待完善", "Execution checks passed; documentation needs attention")
      : staticChecks.checks?.length
        ? tr("自动检查通过", "Automatic checks passed")
        : tr("尚未检查", "Not checked");
  const formalAcceptanceReady = validationActionable
    && acceptanceReadiness?.status === "ready"
    && acceptance?.reused_validation !== true
    && !staticChecks.errors?.length
    && presentationReady
    && ["PASS", "INCONCLUSIVE"].includes(String(effectiveTrial?.verdict || ""))
    && effectiveTrial?.business_output_available === true
    && effectiveTrial?.output_schema_valid === true
    && effectiveTrial?.read_only_audit === true;
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

  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("draft", draft.draft_id);
    url.searchParams.set("step", step);
    const location = `${url.pathname}${url.search}`;
    if (!stepHistoryReady.current || restoringStepHistory.current) history.replaceState({}, "", location);
    else history.pushState({}, "", location);
    stepHistoryReady.current = true;
    restoringStepHistory.current = false;
    uiStateQueue.current = uiStateQueue.current.then(async () => {
      try {
        await call(`${base}/ui-state`, { lastStep: step }, "PUT");
        setUiStateUnsynced(false);
      } catch {
        setUiStateUnsynced(true);
      }
    });
  }, [base, draft.draft_id, step]);

  useEffect(() => {
    const restoreStep = () => {
      const params = new URLSearchParams(window.location.search);
      if (params.get("draft") !== draft.draft_id) return;
      const restored = legacyDraftStep(params.get("step"));
      if (!restored || restored === step) return;
      restoringStepHistory.current = true;
      setStep(restored);
    };
    window.addEventListener("popstate", restoreStep);
    return () => window.removeEventListener("popstate", restoreStep);
  }, [draft.draft_id, step]);

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
        agent_acceptance_mode_invalid: ["当前验收模式无效，请在Agent定义中选择三级验收或确定性运行验收。", "The acceptance mode is invalid. Select three-stage or deterministic-runtime acceptance in the Agent definition."],
        agent_acceptance_contract_not_ready: ["验收定义或当前试运行输出尚未就绪，请查看验收准备检查。", "The contract or current trial output is not ready; review acceptance readiness."],
        agent_acceptance_request_conflict: ["该验收请求编号已用于其他案例，请重新开始。", "This acceptance request ID was already used for different cases. Start again."],
        agent_acceptance_reused: ["此纯文案修订已复用来源版本的PASS验收，无需再次访问SAP。", "This documentation-only revision already reuses the source version PASS acceptance; SAP does not need to be queried again."],
        agent_presentation_contract_invalid: ["请先补齐业务域、SAP业务组件和业务步骤详细说明。", "Complete the business domain, SAP components and business-step detail first."],
        runtime_not_selectable: ["请先在系统配置中启用Runtime，并检查模型与推理强度。", "Enable the Runtime and check its model and reasoning effort in system settings."],
        runtime_agent_explanation_unavailable: ["当前 Runtime 无法保障只读解释模式，请更换受支持的 Runtime。", "The current Runtime cannot guarantee read-only explanation mode. Select a supported Runtime."],
        agent_feedback_context_invalid: ["引用的字段或运行记录不属于当前草稿，请刷新后重试。", "The referenced field or run record does not belong to this draft. Refresh and retry."],
      };
      setError(labels[code]?.[locale === "zh" ? 0 : 1] || `${tr("操作未完成，请核对输入或重试。", "The operation did not complete. Check inputs or retry.")}${/^[a-z][a-z0-9_]{0,79}$/.test(code) ? ` (${code})` : ""}`);
    } finally { setBusy(false); }
  };

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (dirtyRef.current) event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => { window.removeEventListener("beforeunload", beforeUnload); };
  }, []);
  useEffect(() => {
    if (!assistantOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setAssistantOpen(false);
      requestAnimationFrame(() => assistantToggleRef.current?.focus());
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [assistantOpen]);
  useEffect(() => {
    if (assistantWasOpen.current && !assistantOpen) {
      requestAnimationFrame(() => assistantToggleRef.current?.focus());
    }
    assistantWasOpen.current = assistantOpen;
  }, [assistantOpen]);
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
    if (step !== "publish") return;
    const controller = new AbortController();
    setDiff(null);
    const query = compareBaseline === "source" ? `baseline=source&toRevision=${compareTo}` : `baseline=revision&fromRevision=${compareFrom}&toRevision=${compareTo}`;
    call(`${base}/diff?${query}`, undefined, "GET", controller.signal).then(setDiff).catch((failure) => {
      if (failure.name === "AbortError") return;
      setError(failure.message === "agent_source_baseline_unavailable"
        ? tr("无法读取创建草稿时绑定的来源版本，当前不会用相邻修订冒充全部发布变更。", "The source version pinned at draft creation is unavailable. Adjacent revisions are not being presented as the full publication change.")
        : tr("无法加载修改对比，请重试。", "Cannot load revision comparison. Retry."));
    });
    return () => controller.abort();
  }, [base, step, compareBaseline, compareFrom, compareTo]);

  const save = () => action(async () => {
    const value = await call(base, { expectedRevision: draft.revision, manifest: JSON.parse(manifestText), readme, rules }, "PUT");
    replace(value); setReport(null); setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision); setConfirmedSample("");
    const checked = await call(`${base}/validate`, { expectedRevision: value.revision });
    replace(checked, false);
    setReport(await call(`${base}/validation-report`));
    setNotice(tr("新修订已保存并完成自动检查。", "The revision was saved and automatic checks completed."));
  });
  const saveAndLeave = () => action(async () => {
    const value = await call(base, { expectedRevision: draft.revision, manifest: JSON.parse(manifestText), readme, rules }, "PUT");
    await call(`${base}/validate`, { expectedRevision: value.revision });
    setLeaveDialogOpen(false); onBack();
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
  const updateInputProperty = (key: string, updater: (property: any, root: any) => void) => {
    let current;
    try { current = JSON.parse(manifestText); } catch { setError(tr("高级定义不是有效JSON，请先修正或放弃修改。", "Advanced definition is not valid JSON. Correct or discard it first.")); return; }
    const root = current.execution?.inputSchema;
    const property = root?.properties?.[key];
    if (!root || !property) return;
    updater(property, root);
    setManifestText(JSON.stringify(current, null, 2));
  };
  const setInputDefault = (key: string, raw: string, property: any) => updateInputProperty(key, (next) => {
    if (raw === "__unset__") { delete next.default; return; }
    const type = Array.isArray(property.type) ? property.type.find((item: string) => item !== "null") : property.type;
    if (type === "boolean") next.default = raw === "true";
    else if (type === "number" || type === "integer") next.default = raw === "" ? undefined : Number(raw);
    else if (type === "array") {
      const itemType = Array.isArray(property.items?.type) ? property.items.type.find((item: string) => item !== "null") : property.items?.type;
      next.default = raw.split(/[\n,;，；]+/u).map((item) => item.trim()).filter(Boolean).map((item) => {
        if (itemType === "boolean") return item === "true" ? true : item === "false" ? false : item;
        if (itemType === "number" || itemType === "integer") return Number(item);
        return item;
      });
    }
    else next.default = raw;
    if (next.default === undefined) delete next.default;
  });
  const openAssistant = (intent: "explain" | "revise", prompt = "", context: typeof feedbackContext = {}) => {
    setFeedbackIntent(intent); setFeedback(prompt); setFeedbackContext(context); setAssistantOpen(true);
    window.setTimeout(() => chatRef.current?.focus(), 0);
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
    const request = prepareFeedbackRequest(pendingFeedbackRequest.current, {
      baseTurn: latestTurn?.turn ?? latestTurn?.turn_number ?? conversation.length,
      baseRevision: draft.revision, feedback, locale, intent: feedbackIntent, step,
      ...feedbackContext, ...(retryOfTurn === undefined ? {} : { retryOfTurn }),
    }, () => crypto.randomUUID());
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
    setFeedbackIntent(turn.decision?.intent === "explain" ? "explain" : "revise");
    setFeedbackContext({
      fieldPath: turn.decision?.context?.field_path,
      runId: turn.decision?.context?.run_id,
      acceptanceCampaignId: turn.decision?.context?.acceptance_campaign_id,
    });
    setAssistantOpen(true);
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
        const code = String((failure as any)?.detail?.code || (failure as any)?.message || "agent_acceptance_internal_error");
        setAcceptanceCampaign((current: any) => ({ ...current, status: "interrupted", phase: "completed", report: { verdict: "NOT_TESTED", error: { code } } }));
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
  const stepLabels: Record<Step, string> = {
    purpose: tr("明确用途", "Define purpose"), io: tr("设置查询条件与输出", "Set query conditions and output"),
    logic: tr("确认处理逻辑", "Confirm logic"), trial: tr("试运行", "Trial"),
    acceptance: tr("正式验收", "Acceptance"), publish: tr("审核与发布", "Review and publish"),
  };
  const statusLabels: Record<string, string> = {
    incomplete: tr("待完善", "Incomplete"), needs_action: tr("需处理", "Needs attention"), available: tr("可继续", "Available"),
    running: tr("执行中", "Running"), completed: tr("已完成", "Completed"), reused: tr("已复用", "Reused"),
  };
  const localStepStatus = (name: Step) => dirty && ["purpose", "io", "logic"].includes(name) ? "needs_action" : draft.wizard?.steps?.[name] || "available";
  const currentIndex = draftStepNames.indexOf(step);
  const move = (direction: -1 | 1) => {
    const target = draftStepNames[currentIndex + direction];
    if (!target) return;
    if (direction > 0 && dirty) {
      void action(async () => {
        const value = await call(base, { expectedRevision: draft.revision, manifest: JSON.parse(manifestText), readme, rules }, "PUT");
        replace(value); setCompareFrom(Math.max(1, value.revision - 1)); setCompareTo(value.revision);
        const checked = await call(`${base}/validate`, { expectedRevision: value.revision }); replace(checked, false);
        setReport(await call(`${base}/validation-report`)); setStep(target);
      });
    } else setStep(target);
  };
  const renderDiff = () => !diff ? <p role="status">{tr("正在加载修改对比…", "Loading comparison…")}</p> : diff.changes?.length ? diff.changes.map((change: any, index: number) => <section className="draft-diff-change" key={index}><h3>{diffBusinessLabel(change, manifest, locale)} · {tr(change.change === "added" ? "新增" : change.change === "removed" ? "删除" : "修改", change.change === "added" ? "Added" : change.change === "removed" ? "Removed" : "Modified")}</h3><details><summary>{tr("技术位置", "Technical location")}</summary><code>{change.path}</code></details><div className="draft-diff-values">{(["before", "after"] as const).map((side) => <div key={side} className={`draft-diff-${side}`}><h4>{side === "before" ? tr("修改前", "Before") : tr("修改后", "After")}</h4><pre>{!(side in change) || change[`${side}_exists`] === false ? tr("未设置", "Not set") : change[side] === null ? tr("空值", "Null") : change[side] === "" ? tr("空内容", "Empty content") : typeof change[side] === "string" ? change[side] : JSON.stringify(change[side], null, 2)}</pre></div>)}</div>{change.unified_diff && <details><summary>{tr("查看逐行变更", "View line-by-line changes")}</summary><pre className="draft-unified-diff">{String(change.unified_diff).split("\n").map((line: string, number: number) => <span key={number} className={line.startsWith("+") ? "diff-added" : line.startsWith("-") ? "diff-removed" : ""}>{line}{"\n"}</span>)}</pre></details>}</section>) : <p>{tr("比较范围内没有内容差异。", "There are no changes in this comparison.")}</p>;

  return <main className={`agent-management agent-draft-workspace${assistantOpen ? " assistant-open" : ""}`}>
    <header className="draft-workspace-header"><button className="agent-secondary-action" onClick={() => dirty ? setLeaveDialogOpen(true) : onBack()}>{tr("返回管理列表", "Back to management")}</button><div><p className="eyebrow">{tr("未发布草稿 · 不影响当前活动 Agent", "Unpublished draft · Active Agent unchanged")}</p><h1>{localText(manifest.title, locale) || draft.agent_id}</h1><p>{localText(manifest.summary, locale)}</p><div className="draft-meta"><span>{draft.catalog_module || manifest.module}</span><code>{draft.agent_id}</code>{draft.source_version && <span>{tr("来源版本", "Source version")} {draft.source_version}</span>}<span>{tr("目标版本", "Target version")} {draft.target_version}</span><span>{tr("修订", "Revision")} {draft.revision}</span><span className={dirty ? "draft-save-pending" : "draft-save-complete"}>{dirty ? tr("未保存", "Unsaved") : tr("已保存", "Saved")}</span></div></div><button ref={assistantToggleRef} className="agent-secondary-action draft-assistant-toggle" aria-expanded={assistantOpen} onClick={() => { if (assistantOpen) { setAssistantOpen(false); requestAnimationFrame(() => assistantToggleRef.current?.focus()); } else setAssistantOpen(true); }}>{assistantOpen ? tr("收起 AI 助手", "Close AI assistant") : tr("打开 AI 助手", "Open AI assistant")}</button></header>
    {error && <p className="agent-alert error" role="alert">{error}</p>}{notice && <p className="agent-alert" role="status">{notice}</p>}
    {uiStateUnsynced && <p className="agent-alert" role="status">{tr("当前位置尚未同步到服务端；您可以继续工作，系统会在下次切换步骤时重试。", "Your current position is not synced yet. You can keep working; the next step change will retry.")}</p>}
    {remoteConflict && <p className="agent-alert error" role="alert">{tr("草稿已在其他操作中生成新修订。当前未保存内容已保留，请复制需要保留的修改，再放弃本地修改并加载最新修订。", "Another operation created a revision. Your unsaved content is retained. Copy any edits you need, then discard local edits and load the latest revision.")}</p>}
    {active && <p className="agent-alert" role="status" ref={progressRef} tabIndex={-1}>{publicationActive ? tr("Agent 发布正在后台执行", "Agent publication is running in the background") : <>{tr("草稿任务", "Draft operation")}: {draftStatus(operation?.status || latestTurn?.status, locale)}</>} {!publicationActive && <button className="agent-secondary-action" disabled={busy || operation?.status === "cancelling"} onClick={cancel}>{tr("取消任务", "Cancel task")}</button>}</p>}

    <div className="draft-wizard-layout">
      <nav className="agent-steps draft-wizard-nav" aria-label={tr("Agent 编辑步骤", "Agent editing steps")}>{draftStepNames.map((name, index) => { const state = localStepStatus(name); return <button key={name} className={`${step === name ? "active " : ""}wizard-${state}`} aria-current={step === name ? "step" : undefined} onClick={() => setStep(name)}><span className="wizard-step-number">{index + 1}</span><span><strong>{stepLabels[name]}</strong><small>{statusLabels[state] || state}</small></span></button>; })}</nav>

      <div className="draft-wizard-main">
        {step === "io" && (schema.oneOf?.length || Object.keys(schema.dependentRequired || {}).length) > 0 && <aside className="agent-panel draft-conditional-rules"><h2>{tr("条件必填规则", "Conditional requirement rules")}</h2><p>{tr("以下为当前定义中的实际关系。首版表单只展示这些关系，修改请使用 AI 助手或高级编辑。", "These are the actual relationships in the current definition. The initial form displays them; use the AI assistant or advanced editor to change them.")}</p><ul>{(schema.oneOf || []).map((branch: any, index: number) => <li key={`oneof-${index}`}>{tr(`备选条件 ${index + 1} 必填：`, `Alternative ${index + 1} requires: `)}<code>{(branch.required || []).join(", ") || tr("无", "none")}</code></li>)}{Object.entries(schema.dependentRequired || {}).map(([trigger, fields]: [string, any]) => <li key={`dependent-${trigger}`}><code>{trigger}</code>{tr(" 有值时必填：", " requires when supplied: ")}<code>{(fields || []).join(", ")}</code></li>)}</ul></aside>}
        <div className="draft-mobile-step"><label>{tr("当前步骤", "Current step")}<select value={step} onChange={(event) => setStep(event.target.value as Step)}>{draftStepNames.map((name, index) => <option value={name} key={name}>{index + 1}. {stepLabels[name]} · {statusLabels[localStepStatus(name)]}</option>)}</select></label></div>
        {dirty && <aside className="agent-alert draft-unsaved"><p>{tr("有尚未保存的定义修改。保存后系统会自动检查定义；选样、试运行、AI 助手和发布暂不可用。", "There are unsaved definition changes. Saving automatically checks the definition; discovery, trials, AI and publication are unavailable until then.")}</p><div className="agent-actions"><button disabled={locked || remoteConflict} onClick={save}>{tr("保存并检查", "Save and check")}</button><button className="agent-secondary-action" disabled={locked} onClick={discard}>{tr("放弃未保存修改", "Discard unsaved edits")}</button></div></aside>}

        {step === "purpose" && <div className="agent-document-body"><section className="agent-panel"><h2>{stepLabels.purpose}</h2><p>{tr("说明这个 Agent 服务的业务场景，并确认发布后保持稳定的技术身份。", "Describe the business scenario and confirm the technical identity that remains stable after publication.")}</p><div className="draft-module-editor"><label>{tr("目录归类", "Catalog grouping")}<select value={catalogModule} disabled={!actionable} onChange={(event) => setCatalogModule(event.target.value)}>{catalogModules.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><button type="button" disabled={!actionable || catalogModule === (draft.catalog_module || manifest.module)} onClick={saveCatalogModule}>{tr("保存所属模块", "Save module")}</button></div><div className="draft-basic-grid">{(["zh", "en"] as const).map((lang) => <div key={lang}><label>{lang === "zh" ? tr("中文名称", "Chinese name") : tr("英文名称", "English name")}<input disabled={locked} value={manifest.title?.[lang] || ""} onChange={(event) => updateBasic("title", event.target.value, lang)} /></label><label>{lang === "zh" ? tr("中文说明", "Chinese summary") : tr("英文说明", "English summary")}<textarea disabled={locked} rows={3} value={manifest.summary?.[lang] || ""} onChange={(event) => updateBasic("summary", event.target.value, lang)} /></label></div>)}</div><div className="draft-basic-grid"><label>{tr("业务域", "Business domain")}<input disabled={locked} value={manifest.owner || ""} onChange={(event) => updateBasic("owner", event.target.value)} /></label><label>{tr("SAP 业务组件", "SAP business components")}<input disabled={locked} value={(manifest.sapModules || []).join(", ")} onChange={(event) => updateSapModules(event.target.value)} /></label></div></section><section className="agent-panel draft-technical-identity" id="draft-technical-identity"><div className="draft-identity-heading"><h2>{tr("技术 ID", "Technical ID")}</h2><span className={identityReady ? "draft-identity-confirmed" : "draft-identity-pending"}>{identityReady ? tr("已确认", "Confirmed") : tr("首次发布前需确认", "Confirm before publication")}</span></div><p>{identity.kind === "version_upgrade" ? tr("这是已有 Agent 的版本升级，技术 ID 保持不变。", "This is a version upgrade; its technical ID is unchanged.") : tr("技术 ID 发布后永久锁定。", "The technical ID is permanently locked after publication.")}</p>{identity.kind === "new_agent" && !identity.locked ? <><label htmlFor="draft-technical-id">{tr("拟使用的技术 ID", "Proposed technical ID")}<input id="draft-technical-id" value={identityInput} disabled={!identityEditable} onChange={(event) => { setIdentityInput(event.target.value); setIdentityCheck(null); }} /></label><p>{identityError}</p><div className="agent-actions"><button className="agent-secondary-action" disabled={!identityEditable || Boolean(identityError)} onClick={checkIdentity}>{tr("检查可用性", "Check availability")}</button><button disabled={!identityEditable || !checkedIdentity || Boolean(identityError) || (identityReady && !identityChanged)} onClick={confirmIdentity}>{tr("确认技术 ID", "Confirm technical ID")}</button></div></> : <code>{identity.agent_id}</code>}</section></div>}

        {step === "io" && <div className="agent-document-body">
          <section className="agent-panel">
            <div className="draft-section-heading">
              <div>
                <h2>{stepLabels.io}</h2>
                <p>{tr("编辑已有查询条件的名称、说明、必填状态和默认值。字段结构、类型和复杂依赖请交给 AI 助手。", "Edit the names, descriptions, required state and defaults of existing query conditions. Use the AI assistant for structure, types and complex dependencies.")}</p>
              </div>
              <button type="button" className="agent-secondary-action" disabled={!actionable} onClick={() => openAssistant("revise", tr("请调整 Agent 的查询条件。", "Please adjust the Agent query conditions."), { fieldPath: "/manifest/execution/inputSchema" })}>{tr("让 AI 调整", "Ask AI to adjust")}</button>
            </div>
            <ul className="draft-input-definition-list">
              {Object.entries(schema.properties || {}).filter(([, property]: [string, any]) => publicInput(property)).map(([key, property]: [string, any]) => {
                const type = Array.isArray(property.type) ? property.type.find((item: string) => item !== "null") : property.type;
                const complexRequired = Boolean(schema.oneOf?.some((item: any) => item.required?.includes(key)) || Object.values(schema.dependentRequired || {}).some((items: any) => items.includes(key)));
                const editableDefault = !property["x-sapba-sensitive"] && !property["x-sapba-server-default"] && ["string", "number", "integer", "boolean", "array"].includes(type) && !(type === "array" && property.items?.type === "object");
                const defaultValue = property.default === undefined ? "__unset__" : type === "array" ? property.default.join("\n") : String(property.default);
                return <li className="draft-input-definition" key={key}>
                  <details>
                    <summary>
                      <span className="draft-input-definition-title"><strong>{localText(property.title, locale) || key}</strong><code>{key}</code></span>
                      <span className="draft-input-definition-meta">
                        <span>{(schema.required || []).includes(key) ? tr("必填", "Required") : complexRequired ? tr("条件必填", "Conditionally required") : tr("可选", "Optional")}</span>
                        <span>{type || tr("未声明类型", "Type not declared")}</span>
                      </span>
                    </summary>
                    <div className="draft-input-definition-editor">
                      <div className="draft-basic-grid">
                        {(["zh", "en"] as const).map((lang) => <div key={lang}>
                          <label>{lang === "zh" ? tr("中文名称", "Chinese name") : tr("英文名称", "English name")}<input disabled={locked} value={property.title?.[lang] || ""} onChange={(event) => updateInputProperty(key, (next) => { next.title = { ...(next.title || {}), [lang]: event.target.value }; })} /></label>
                          <label>{lang === "zh" ? tr("中文说明", "Chinese description") : tr("英文说明", "English description")}<textarea disabled={locked} rows={2} value={property.description?.[lang] || ""} onChange={(event) => updateInputProperty(key, (next) => { next.description = { ...(next.description || {}), [lang]: event.target.value }; })} /></label>
                        </div>)}
                      </div>
                      <label className="draft-checkbox"><input type="checkbox" checked={(schema.required || []).includes(key)} disabled={locked || complexRequired} onChange={(event) => updateInputProperty(key, (_next, root) => { const required = new Set(root.required || []); event.target.checked ? required.add(key) : required.delete(key); root.required = [...required]; })} />{tr("根级必填", "Required at root")}</label>
                      {complexRequired && <p>{tr("该字段参与条件必填或依赖关系，请通过 AI 助手调整。", "This field participates in conditional requirements; use the AI assistant to adjust it.")}</p>}
                      {editableDefault && <label>{tr("默认值", "Default value")}{type === "boolean" ? <select value={defaultValue} disabled={locked} onChange={(event) => setInputDefault(key, event.target.value, property)}><option value="__unset__">{tr("未设置", "Not set")}</option><option value="true">true</option><option value="false">false</option></select> : type === "array" ? <><textarea rows={3} disabled={locked} value={defaultValue === "__unset__" ? "" : defaultValue} onChange={(event) => setInputDefault(key, event.target.value, property)} /><button type="button" className="agent-secondary-action" disabled={locked || property.default === undefined} onClick={() => setInputDefault(key, "__unset__", property)}>{tr("清除默认值", "Clear default")}</button></> : <><input type={type === "number" || type === "integer" ? "number" : "text"} disabled={locked} value={defaultValue === "__unset__" ? "" : defaultValue} onChange={(event) => setInputDefault(key, event.target.value, property)} /><button type="button" className="agent-secondary-action" disabled={locked || property.default === undefined} onClick={() => setInputDefault(key, "__unset__", property)}>{tr("清除默认值", "Clear default")}</button></>}</label>}
                      {!editableDefault && <p>{property["x-sapba-sensitive"] ? tr("敏感字段不保存默认值。", "Sensitive fields do not store defaults.") : property["x-sapba-server-default"] ? tr("默认值由服务端提供。", "The server provides this default.") : tr("复杂默认值请通过 AI 助手调整。", "Use the AI assistant for complex defaults.")}</p>}
                      <button type="button" className="agent-secondary-action" disabled={!actionable} onClick={() => openAssistant("revise", tr("请调整输入字段 " + key + "。", "Please adjust input field " + key + "."), { fieldPath: "/manifest/execution/inputSchema/properties/" + key })}>{tr("让 AI 调整此字段", "Ask AI to adjust this field")}</button>
                    </div>
                  </details>
                </li>;
              })}
            </ul>
          </section>
          <section className="agent-panel">
            <div className="draft-section-heading">
              <div>
                <h2>{tr("结果结构预览", "Result structure preview")}</h2>
                <p>{tr("这里展示定义中的结果结构，不代表真实运行结果。真实业务结果将在试运行后显示。", "This previews the defined result structure; it is not a real run result. Actual business output appears after a trial.")}</p>
              </div>
              <button type="button" className="agent-secondary-action" disabled={!actionable} onClick={() => openAssistant("revise", tr("请调整结果结构，并同步核对业务规则输出、报告展示和验收契约。", "Please adjust the result structure and check the rule output, report presentation and acceptance contract together."), { fieldPath: "/manifest/execution/outputSchema" })}>{tr("让 AI 调整", "Ask AI to adjust")}</button>
            </div>
            {Object.keys(manifest.execution?.outputSchema?.properties || {}).length ? <ul>{Object.entries(manifest.execution.outputSchema.properties).map(([key, property]: [string, any]) => <li key={key}><strong>{localText(property.title, locale) || key}</strong> <code>{Array.isArray(property.type) ? property.type.join(" | ") : property.type || "unknown"}</code></li>)}</ul> : <ul>{(manifest.outputs?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>}
          </section>
        </div>}

        {step === "logic" && <div className="agent-document-body"><section className="agent-panel"><div className="draft-section-heading"><div><h2>{stepLabels.logic}</h2><p>{tr("核对业务步骤、SAP 范围、规则和只读安全边界。", "Review business steps, SAP scope, rules and read-only guardrails.")}</p></div><button className="agent-secondary-action" onClick={() => openAssistant("revise", tr("请调整 Agent 的处理逻辑。", "Please adjust the Agent processing logic."))}>{tr("让 AI 调整", "Ask AI to adjust")}</button></div><dl><dt>{tr("自动检查", "Automatic checks")}</dt><dd>{staticLabel}</dd></dl>{staticChecks.errors?.length > 0 && <ul className="draft-blockers">{staticChecks.errors.map((item: any, index: number) => <li key={index}>{item.message || item.code} {item.path && <code>{item.path}</code>}</li>)}</ul>}<button className="agent-secondary-action" disabled={!actionable} onClick={check}>{tr("重新运行自动检查", "Run automatic checks again")}</button></section><AgentDefinitionDetails agent={manifest} presentation={presentation} locale={locale} idPrefix="draft" /><section className="agent-panel"><h2>{tr("安全边界", "Guardrails")}</h2><ul>{(manifest.guardrails?.[locale] || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section><details className="agent-panel draft-advanced"><summary>{tr("高级技术编辑", "Advanced technical editing")}</summary><p>{tr("仅在了解数据契约时使用。所有修改仍须通过安全检查。", "Use only when familiar with the contracts. Every change remains subject to safety checks.")}</p><label>Agent JSON<textarea disabled={locked} rows={20} spellCheck={false} value={manifestText} onChange={(event) => setManifestText(event.target.value)} /></label><label>README<textarea disabled={locked} rows={8} value={readme} onChange={(event) => setReadme(event.target.value)} /></label><label>{tr("受控规则源码", "Managed rule source")}<textarea disabled={locked} rows={10} value={rules} onChange={(event) => setRules(event.target.value)} /></label></details></div>}

        {step === "trial" && <section className="agent-run-panel"><h2>{stepLabels.trial}</h2><p>{tr("填写业务参数，使用已保存草稿执行只读查询。试运行不会修改默认值，也不代表正式验收通过。", "Enter business parameters to run the saved draft read-only. A trial does not change defaults or establish formal acceptance.")}</p><form onSubmit={(event) => { event.preventDefault(); void runTrial(); }} noValidate><AgentDraftInputs schema={schema} values={input} onChange={(next) => { setInput(next); setFieldErrors({}); setConfirmedSample(""); if (hasVerifiedSample && Object.entries(discovery.input || {}).every(([key, value]) => discoveryFingerprint(0, { value }) === discoveryFingerprint(0, { value: next[key] }))) setDiscoveryInputHash(discoveryFingerprint(draft.revision, next)); }} secrets={secrets} onSecrets={setSecrets} locale={locale} errors={fieldErrors} disabled={locked || dirty} /><div className="draft-discovery" ref={sampleInputsRef} tabIndex={-1}><label className="draft-checkbox"><input type="checkbox" checked={autoDiscover} disabled={!validationActionable} onChange={(event) => { const enabled = event.target.checked; setAutoDiscover(enabled); setConfirmedSample(""); if (enabled) chooseSampleFields(); else { setInput((current) => clearDiscoveredInput(current, autoFilledRef.current)); setAutoFilled({}); setDiscoveryInputHash(""); setFieldErrors({}); } }} />{tr("自动查找验证数据", "Find validation data automatically")}</label>{autoDiscover && <><p>{tr("保留已填范围，有界读取真实 SAP 样本；回填后请确认。敏感参考号请手工填写。", "Keep the entered scope and read bounded live SAP samples. Confirm proposed values; enter sensitive references manually.")}</p><button type="button" className="agent-secondary-action" disabled={!validationActionable} onClick={chooseSampleFields}>{tr(discovery ? "重新查找样本" : "查找样本", discovery ? "Find another sample" : "Find sample")}</button>{discovery && <div><SampleProgressSummary value={discovery} locale={locale} />{hasVerifiedSample && <label className="draft-checkbox"><input type="checkbox" disabled={!validationActionable || discoveryInputHash !== sampleFingerprint} checked={sampleConfirmed} onChange={(event) => confirmSample(event.target.checked)} />{tr("已核对回填参数，确认用于本次操作", "I reviewed the proposed parameters and confirm this use")}</label>}</div>}</>}</div><div className="agent-actions"><button type="submit" disabled={!validationActionable || (requiresSampleConfirmation && !sampleConfirmed)}>{tr("执行只读试运行", "Run read-only trial")}</button></div></form>{trialRunId ? <div className="draft-trial-history"><p>{tr("最近一次试运行", "Latest trial")}: {draftStatus(run?.status || trial?.status, locale)}{(trial?.revision ?? trial?.draft_revision) !== undefined && (trial?.revision ?? trial?.draft_revision) !== draft.revision && <strong> · {tr("历史修订结果", "Historical revision result")}</strong>}</p>{run && !draftTerminal.has(run.status) && <><TrialProgressSummary run={run} trial={trial} locale={locale} connectionError={trialConnectionError} /><button className="agent-secondary-action" onClick={() => setTrialDialogOpen(true)}>{tr("查看进度", "View progress")}</button></>}{run && draftTerminal.has(run.status) && <div ref={trialResultRef} tabIndex={-1}><AgentDraftResult run={run} trial={trial} locale={locale} runPath={runPath} apiBase={apiBase} /></div>}<button className="agent-secondary-action" onClick={() => openAssistant("explain", tr("请分析最近一次试运行的结果或失败原因，不要修改草稿。", "Analyze the latest trial result or failure without modifying the draft."), { runId: trialRunId })}>{tr("分析结果或失败原因", "Analyze result or failure")}</button></div> : <p>{tr("当前没有试运行记录。", "No trial has been run yet.")}</p>}{effectiveTrial?.run_id && <p>{tr("当前验收使用的有效试运行：", "Effective trial for acceptance: ")}<code>{effectiveTrial.run_id}</code>{effectiveTrial.run_id !== trialRunId && <span> · {tr("最近尝试未覆盖此有效结果", "The latest attempt did not replace this result")}</span>}</p>}</section>}

        {step === "acceptance" && <section className="agent-panel" ref={acceptanceResultRef} tabIndex={-1}><h2>{stepLabels.acceptance}</h2><p>{tr("自动检查、试运行和正式验收分别记录。1–5 组必选案例全部通过后才会解锁发布。", "Automatic checks, trials and formal acceptance are recorded separately. All 1–5 required cases must pass before publication is unlocked.")}</p><dl><dt>{tr("自动检查", "Automatic checks")}</dt><dd>{staticLabel}</dd><dt>{tr("正式验收", "Formal acceptance")}</dt><dd>{draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</dd></dl>{(acceptance?.source_version || acceptance?.reused_from_version) && <p>{tr("复用来源版本验收：", "Acceptance reused from source version: ")}{acceptance.source_version || acceptance.reused_from_version}</p>}<AcceptanceSummary campaign={acceptanceCampaign} locale={locale} onOpen={() => setAcceptanceProgressOpen(true)} /><AcceptanceReadiness value={acceptanceReadiness} locale={locale} /><div className="agent-actions"><button type="button" disabled={!formalAcceptanceReady || campaignActive} onClick={openAcceptance}>{tr(acceptanceCampaign ? "更换案例并重新验收" : "开始正式验收", acceptanceCampaign ? "Change cases and run again" : "Start formal acceptance")}</button><button className="agent-secondary-action" disabled={busy} onClick={() => action(async () => { setReport(await call(`${base}/validation-report`)); await refresh(false); })}>{tr("刷新验证状态", "Refresh validation status")}</button>{acceptanceCampaign && <button className="agent-secondary-action" onClick={() => openAssistant("explain", tr("请分析最近一次正式验收结果，不要修改草稿。", "Analyze the latest formal acceptance without modifying the draft."), { acceptanceCampaignId: acceptanceCampaign.campaign_id })}>{tr("分析验收结果", "Analyze acceptance")}</button>}</div>{publishability?.blockers?.length > 0 && <ul className="draft-blockers">{publishability.blockers.map((item: any, index: number) => <li key={index}>{localText(item.message || item.description, locale) || tr("正式验收或发布条件尚未满足。", "Acceptance or publication requirements remain unmet.")}<details><summary>{tr("技术原因", "Technical reason")}</summary><code>{typeof item === "string" ? item : item.code}</code></details></li>)}</ul>}</section>}

        {step === "publish" && <div className="agent-document-body"><section className="agent-panel draft-publish-details"><details><summary>{tr("详细变更信息", "Detailed change information")}</summary><div className="draft-publish-details-content"><p>{compareBaseline === "source" ? tr("正在与创建草稿时绑定的来源版本比较。", "Comparing with the source version pinned when this draft was created.") : tr("正在比较两个草稿修订。", "Comparing two saved draft revisions.")}</p><div className="draft-basic-grid"><label>{tr("比较基准", "Comparison baseline")}<select value={compareBaseline} onChange={(event) => setCompareBaseline(event.target.value as "revision" | "source")}><option value="source">{draft.source_version ? tr(`来源版本 ${draft.source_version}`, `Source version ${draft.source_version}`) : tr("新 Agent 的完整定义", "Full new Agent definition")}</option><option value="revision">{tr("草稿修订", "Draft revision")}</option></select></label>{compareBaseline === "revision" && <label>{tr("修改前修订", "Before revision")}<select value={compareFrom} onChange={(event) => setCompareFrom(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label>}<label>{tr("修改后修订", "After revision")}<select value={compareTo} onChange={(event) => setCompareTo(Number(event.target.value))}>{revisions.map((revision) => <option key={revision}>{revision}</option>)}</select></label></div>{renderDiff()}{compareBaseline === "revision" && <button className="agent-secondary-action" disabled={!actionable || compareFrom === draft.revision} onClick={restore}>{tr("恢复修改前修订（创建新修订）", "Restore before revision (creates a new revision)")}</button>}</div></details></section><section className="agent-panel"><h2>{stepLabels.publish}</h2><p>{tr("正式验收通过后才可发布。系统检查候选包和页面后提交到本地 main，不会推送远端。", "Formal acceptance is required. The package and candidate pages are checked before committing to local main; nothing is pushed.")}</p><p>{tr("技术 ID", "Technical ID")}: <code>{identity.agent_id}</code> · {identityReady ? tr("已确认", "Confirmed") : tr("尚未确认", "Not confirmed")}</p><p>{tr("正式验收", "Formal acceptance")}: {draftStatus(acceptance?.verdict || "NOT_TESTED", locale)}</p>{!canPublish && <p role="status">{tr("发布条件尚未满足，请按左侧状态返回处理。", "Publication requirements are not met. Use the step statuses to resolve them.")}</p>}<label>{tr("目标版本", "Target version")}<input value={targetVersion} disabled={locked} onChange={(event) => setTargetVersion(event.target.value)} /></label><div className="agent-actions"><button disabled={!actionable || !canPublish} onClick={() => publish(false)}>{tr("发布为未启用版本", "Publish inactive")}</button><button disabled={!actionable || !canPublish} onClick={() => publish(true)}>{tr("发布并启用", "Publish and activate")}</button></div>{publication && <div className="draft-publication-summary"><PublicationProgressSummary value={publication} locale={locale} connectionError={publicationConnectionError} /><div className="agent-actions"><button className="agent-secondary-action" onClick={() => setPublicationDialogOpen(true)}>{tr("查看发布进度", "View publication progress")}</button>{publication.publication_status === "published" && publication.site_refresh_status === "failed" && <button onClick={retrySiteRefresh}>{tr("重试刷新页面", "Retry page refresh")}</button>}{publication.publication_status === "published" && (publication.result?.active === false || publication.activate === false) && onActivatePublished && <button onClick={() => onActivatePublished(publication.result || publication)}>{tr("启用此版本", "Activate this version")}</button>}</div></div>}</section></div>}
      </div>

      {assistantOpen && <aside className="draft-assistant" aria-label={tr("AI 助手", "AI assistant")}><header><div><p className="eyebrow">{tr("当前步骤", "Current step")}</p><h2>{tr("AI 助手", "AI assistant")}</h2></div><button className="agent-secondary-action" onClick={() => setAssistantOpen(false)} aria-label={tr("关闭 AI 助手", "Close AI assistant")}>×</button></header><div className="draft-assistant-modes" role="group" aria-label={tr("助手模式", "Assistant mode")}><button className={feedbackIntent === "explain" ? "active" : "agent-secondary-action"} onClick={() => { setFeedbackIntent("explain"); pendingFeedbackRequest.current = null; }}>{tr("解释问题", "Explain")}</button><button className={feedbackIntent === "revise" ? "active" : "agent-secondary-action"} onClick={() => { setFeedbackIntent("revise"); pendingFeedbackRequest.current = null; }}>{tr("修改草稿", "Revise draft")}</button></div><p>{feedbackIntent === "explain" ? tr("只分析已保存定义和已有结果；服务端会拒绝任何修改。", "Analyzes only the saved definition and existing results; the server rejects any modification.") : tr("明确的修改会保存为新修订，并重新判断验证是否有效。", "Clear changes are saved as a new revision and validation applicability is recalculated.")}</p>{feedbackTaskActive && <div className="draft-feedback-progress-card"><FeedbackProgressSummary turn={feedbackProgressTurn} operation={feedbackOperation} locale={locale} connectionError={feedbackConnectionError} /><button className="agent-secondary-action" onClick={() => setFeedbackDialogOpen(true)}>{tr("查看进度", "View progress")}</button></div>}<AgentDraftConversation turns={conversation} locale={locale} onRetry={retryFeedback} retryDisabled={!actionable} />{uncertainFeedback && <p className="agent-alert">{tr("上次发送未确认；原样重传会复用同一请求。", "The previous send was unconfirmed; resending unchanged reuses the same request.")}</p>}<label htmlFor="draft-feedback">{feedbackIntent === "explain" ? tr("需要解释什么？", "What should be explained?") : tr("需要修改什么？", "What should change?")}<textarea ref={chatRef} id="draft-feedback" rows={5} value={feedback} disabled={locked || dirty} onChange={(event) => { setFeedback(event.target.value); pendingFeedbackRequest.current = null; setUncertainFeedback(false); }} /></label><button disabled={!actionable || !feedback.trim()} onClick={submitFeedback}>{uncertainFeedback ? tr("重传原请求", "Resend original request") : feedbackIntent === "explain" ? tr("请求解释", "Ask for explanation") : tr("提交修改要求", "Submit revision request")}</button></aside>}
    </div>

    <footer className="draft-wizard-footer"><button className="agent-secondary-action" disabled={currentIndex <= 0} onClick={() => move(-1)}>{tr("上一步", "Previous")}</button><span>{currentIndex + 1} / {draftStepNames.length} · {stepLabels[step]}</span>{currentIndex < draftStepNames.length - 1 && <button disabled={busy || remoteConflict} onClick={() => move(1)}>{dirty ? tr("保存并继续", "Save and continue") : tr("继续", "Continue")}</button>}</footer>

    <AgentSampleProgress open={sampleDialogOpen} value={discovery} locale={locale} canRetry={validationActionable} fields={selectingSampleFields ? sampleFields : undefined} selectedFields={selectedSampleFields} onSelection={setSelectedSampleFields} onStart={discover} onClose={() => setSampleDialogOpen(false)} onCancel={cancelSample} onRetry={chooseSampleFields} onReview={reviewSample} onAfterClose={() => { if (sampleReviewFocus.current) { sampleReviewFocus.current = false; sampleInputsRef.current?.focus(); } }} />
    <AgentTrialProgress open={trialDialogOpen} run={run} trial={trial} locale={locale} connectionError={trialConnectionError} errorMessage={error} onClose={() => setTrialDialogOpen(false)} onCancel={cancelTrial} onReview={() => { trialReviewFocus.current = true; setTrialDialogOpen(false); }} onAfterClose={() => { if (trialReviewFocus.current) { trialReviewFocus.current = false; trialResultRef.current?.focus(); } }} />
    <AgentAcceptanceSetup open={acceptanceSetupOpen} locale={locale} schema={schema} mode={manifest.validation?.acceptanceMode || "three_stage"} runtime={draft.formal_acceptance_runtime} cases={acceptanceCases} currentInput={acceptanceDefaultInput} currentSecrets={secrets} sampleInput={discovery?.input} canUseSample={sampleConfirmed} disabled={!formalAcceptanceReady} onCases={setAcceptanceCases} onFindSample={findAcceptanceSample} onClose={() => setAcceptanceSetupOpen(false)} onStart={startAcceptance} />
    <AgentAcceptanceProgress open={acceptanceProgressOpen} locale={locale} campaign={acceptanceCampaign} connectionError={acceptanceConnectionError} apiBase={apiBase} draftId={draft.draft_id} onClose={() => { setAcceptanceProgressOpen(false); window.setTimeout(() => acceptanceResultRef.current?.focus(), 0); }} onCancel={cancelAcceptance} onAdjust={() => { setAcceptanceProgressOpen(false); openAssistant("revise", tr("请根据正式验收结果调整草稿。", "Please revise the draft based on the formal acceptance result."), { acceptanceCampaignId: acceptanceCampaign?.campaign_id }); }} onRetry={() => { setAcceptanceProgressOpen(false); window.setTimeout(openAcceptance, 0); }} />
    <AgentFeedbackProgress open={feedbackDialogOpen} turn={feedbackProgressTurn} operation={feedbackOperation} locale={locale} connectionError={feedbackConnectionError} errorMessage={feedbackLaunch?.status === "failed" ? error : ""} onClose={() => setFeedbackDialogOpen(false)} onCancel={cancelFeedback} onRetry={() => { if (!feedbackProgressTurn) return; feedbackAfterClose.current = () => retryFeedback(feedbackProgressTurn); setFeedbackDialogOpen(false); }} onReview={() => { if (!feedbackProgressTurn) return; feedbackAfterClose.current = () => { setCompareFrom(feedbackProgressTurn.base_revision ?? Math.max(1, draft.revision - 1)); setCompareTo(feedbackProgressTurn.result_revision ?? draft.revision); setCompareBaseline("revision"); setStep("publish"); }; setFeedbackDialogOpen(false); }} onAfterClose={() => { const followUp = feedbackAfterClose.current; feedbackAfterClose.current = null; followUp?.(); }} />
    <AgentPublicationProgress open={publicationDialogOpen} value={publication} locale={locale} connectionError={publicationConnectionError} onClose={() => setPublicationDialogOpen(false)} onRetrySite={retrySiteRefresh} onComplete={finishPublication} onActivate={onActivatePublished ? () => { setPublicationDialogOpen(false); onActivatePublished(publication?.result || publication || {}); } : undefined} />
    {leaveDialogOpen && <dialog open className="sample-progress-dialog draft-leave-dialog" aria-labelledby="draft-leave-title"><h2 id="draft-leave-title">{tr("保存当前修改？", "Save current changes?")}</h2><p>{tr("离开前可以保存并自动检查、放弃未保存修改，或继续留在当前页面。", "Before leaving, save and check, discard unsaved changes, or stay on this page.")}</p><div className="agent-actions"><button className="agent-secondary-action" onClick={() => setLeaveDialogOpen(false)}>{tr("留在当前页", "Stay")}</button><button className="agent-secondary-action" onClick={() => { setLeaveDialogOpen(false); void discard(); onBack(); }}>{tr("放弃并离开", "Discard and leave")}</button><button onClick={saveAndLeave}>{tr("保存并离开", "Save and leave")}</button></div></dialog>}
  </main>;
}
