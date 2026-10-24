import { useState, type ReactNode } from "react";
import type { Locale } from "../lib/types";
import { draftStatus } from "../lib/agentDraft";
import { acceptanceAttempt, acceptanceBlockerText } from "../lib/acceptancePresentation";
import { acceptanceIcons } from "../lib/acceptanceIcons";
import AcceptanceReadiness from "./AcceptanceReadiness";

type Props = {
  locale: Locale; acceptance: any; campaign: any; readiness: any; effectiveTrial: any;
  staticLabel: string; staticPassed: boolean; staticKnown: boolean;
  canPublish: boolean; publicationKnown: boolean; blockers: any[];
  canStart: boolean; campaignActive: boolean; busy: boolean; startDisabledReason: string;
  currentRevision: number; diagnostics?: ReactNode;
  onRefresh: () => void; onSetup: () => void; onResult: () => void; onAnalyze: () => void;
};

function Icon({ name }: { name: keyof typeof acceptanceIcons }) {
  return <img className="acceptance-overview-icon" src={acceptanceIcons[name]} width="20" height="20" alt="" aria-hidden="true" />;
}

export default function AcceptanceOverview(props: Props) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const tr = (zh: string, en: string) => props.locale === "zh" ? zh : en;
  const { campaign, acceptance, readiness, effectiveTrial } = props;
  const attempt = acceptanceAttempt(campaign, props.locale);
  const source = acceptance?.source_version || acceptance?.reused_from_version;
  const qualification = acceptance?.verdict;
  const badge = qualification === "PASS" ? tr("验收通过", "Acceptance passed")
    : qualification === "FAIL" ? tr("验收未通过", "Acceptance failed")
    : qualification === "BLOCKED" ? tr("验收受阻", "Acceptance blocked")
    : qualification === "NOT_TESTED" ? tr("未完成", "Incomplete") : tr("待核对", "Needs verification");
  const trialLabel = !effectiveTrial ? tr("尚无有效记录", "No effective record")
    : effectiveTrial.verdict === "PASS" ? tr("通过", "Passed")
    : effectiveTrial.verdict === "INCONCLUSIVE" ? tr("证据不足", "Inconclusive") : draftStatus(effectiveTrial.verdict, props.locale);
  const readyLabel = readiness?.status === "ready" ? tr("就绪", "Ready")
    : readiness?.status === "reused" ? tr("沿用有效验收", "Effective acceptance reused")
    : readiness ? tr("需要处理", "Needs attention") : tr("待核对", "Needs verification");
  const states = [
    { label: tr("自动检查", "Automatic checks"), value: props.staticLabel, passed: props.staticPassed },
    { label: tr("试运行", "Trial"), value: trialLabel, passed: effectiveTrial?.verdict === "PASS" },
    { label: tr("验收准备", "Readiness"), value: readyLabel, passed: readiness?.status === "ready" },
  ];
  const blockers = [...new Set((props.blockers || []).map(item => acceptanceBlockerText(item, props.locale)))];
  const cleanupPending = campaign?.report?.diagnostics?.cleanup_complete === false || campaign?.phase === "cleanup";
  const runtime = campaign?.runtime || campaign?.runtime_snapshot;
  const caseCount = campaign?.cases?.length ?? campaign?.report?.case_count;
  const hasCases = Array.isArray(campaign?.cases) && campaign.cases.length > 0;
  const gateText = props.canPublish ? tr("当前发布条件已满足，发布仍需单独确认。", "Publication requirements are met; publication still needs separate confirmation.")
    : !props.publicationKnown ? tr("发布状态待核对，不能据此发布。", "Publication status needs verification; publication is not authorized.")
    : qualification === "PASS" ? tr("验收已通过，其他发布条件尚未满足。", "Acceptance passed; other publication requirements remain unmet.")
    : tr("当前修订尚未满足发布条件，发布仍锁定。", "This revision does not meet publication requirements; publication remains locked.");
  return <div className="acceptance-overview">
    <header className="acceptance-overview-heading"><div><h2>{tr("正式验收", "Formal acceptance")}</h2><span className={`acceptance-overview-badge ${qualification === "PASS" ? "success" : "warning"}`}>{badge}</span></div>
      <button type="button" className="agent-secondary-action acceptance-overview-refresh" disabled={props.busy} onClick={props.onRefresh} aria-label={tr("刷新验证状态", "Refresh validation status")}><Icon name="refresh-cw" />{tr("刷新", "Refresh")}</button>
    </header>
    <p className="acceptance-overview-caption">{tr("1–5组必选案例全部通过，才满足正式验收要求。", "All 1–5 required cases must pass to meet formal acceptance requirements.")}</p>
    <dl className="acceptance-overview-states">{states.map(state => <div key={state.label} data-state={state.passed ? "passed" : "pending"}>
      {state.passed && <Icon name="circle-check" />}<dt>{state.label}</dt><dd>{state.value}</dd>
    </div>)}</dl>
    <section className="acceptance-overview-attempt" aria-label={tr("最近一次尝试", "Latest attempt")}>
      <p className="acceptance-overview-caption">{tr("最近一次尝试", "Latest attempt")}{caseCount !== undefined && ` · ${caseCount} ${tr("组案例", "cases")}`}{campaign?.revision !== undefined && campaign.revision !== props.currentRevision && ` · ${tr("历史修订结果", "Historical revision result")}`}</p>
      <h3 aria-live="polite">{attempt.title}</h3>
      {attempt.preparingFailed && <div className="acceptance-overview-reason"><p>{tr("执行环境准备未完成，本次尚未开始SAP取证。", "Environment preparation did not complete; SAP evidence collection did not start.")}</p>{campaign?.report?.error?.code && <p>{acceptanceBlockerText(campaign.report.error, props.locale)}</p>}</div>}
      {!attempt.preparingFailed && campaign?.report?.error?.code && <p className="acceptance-overview-reason">{acceptanceBlockerText(campaign.report.error, props.locale)}</p>}
      {source && <p>{tr("当前有效验收引用来源版本：", "Effective acceptance references source version: ")}{source}</p>}
      {campaign && <p className="acceptance-overview-runtime">{tr("本次冻结绑定", "Frozen binding for this attempt")}: {runtime?.provider_id || tr("未记录", "Not recorded")} · {runtime?.model || tr("模型未记录", "Model not recorded")} · {campaign.acceptance_mode === "three_stage" ? tr("三级比较", "Three-stage comparison") : campaign.acceptance_mode === "deterministic_runtime" ? tr("确定性Runtime比较", "Deterministic Runtime comparison") : tr("验收模式未记录", "Acceptance mode not recorded")}</p>}
    </section>
    <AcceptanceReadiness value={readiness} locale={props.locale} compact />
    {cleanupPending && <p role="alert" className="acceptance-overview-reason">{tr("后台清理尚未确认完成，请等待或核对服务状态；不能开始新的验收。", "Cleanup is not confirmed. Wait or check service health; new acceptance cannot start.")}</p>}
    {props.campaignActive && <p role="status">{tr("已有验收任务，查看原任务进度，不会重复创建。", "An acceptance task already exists. View its progress; no duplicate is created.")}</p>}
    {!props.campaignActive && !props.canStart && <p className="acceptance-overview-disabled" role="status">{props.startDisabledReason}</p>}
    {blockers.length > 0 && <ul className="acceptance-overview-blockers">{blockers.map((text, index) => <li key={index}>{text}</li>)}</ul>}
    <div className="acceptance-overview-actions">
      <button type="button" className="acceptance-overview-primary" disabled={!props.campaignActive && (!props.canStart || cleanupPending)} onClick={props.campaignActive ? props.onResult : props.onSetup}>{props.campaignActive ? tr("查看进度", "View progress") : campaign ? tr("重新验收", "Set up acceptance again") : tr("设置正式验收", "Set up acceptance")}</button>
      {campaign && !props.campaignActive && <button type="button" className="agent-secondary-action" onClick={props.onResult}>{tr("查看结果", "View result")}</button>}
      {campaign && <button type="button" className="agent-secondary-action acceptance-overview-analysis" onClick={props.onAnalyze}><Icon name="sparkles" />{tr("AI分析", "AI analysis")}</button>}
    </div>
    {!props.campaignActive && <p className="acceptance-overview-caption">{tr("确认案例后才会开始，不自动重试。", "Starts only after case confirmation; never retries automatically.")}</p>}
    <details className="acceptance-overview-details" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}><summary><Icon name="list-checks" />{tr("案例与已有记录", "Cases and saved records")}{caseCount !== undefined && ` · ${caseCount} ${tr("组案例", "cases")}`}<Icon name="chevron-right" /></summary>
      {hasCases ? <ul>{campaign.cases.map((item: any, index: number) => <li key={item.case_id || index}>{item.case_id || tr("未命名案例", "Unnamed case")} · {draftStatus(item.result?.verdict || item.status, props.locale)}</li>)}</ul> : <p>{tr("暂无案例记录。", "No case records available.")}</p>}
      {campaign && <button type="button" className="agent-secondary-action" onClick={props.onResult}>{tr("查看完整进度与结果", "View full progress and result")}</button>}
    </details>
    <details className="acceptance-overview-details" open={diagnosticsOpen} onToggle={event => setDiagnosticsOpen(event.currentTarget.open)}><summary><Icon name="wrench" />{tr("技术诊断", "Technical diagnostics")}<Icon name="chevron-right" /></summary>
      {campaign?.report?.error?.code && <p><code>{campaign.report.error.code}</code></p>}
      {campaign?.report?.failure_stage && <p>{tr("失败阶段", "Failure stage")}: <code>{campaign.report.failure_stage}</code></p>}
      {(props.blockers || []).map((item, index) => <p key={index}><code>{typeof item === "string" ? item : item.code}</code></p>)}
      {props.diagnostics || <p>{tr("更多诊断保留在原验收结果中。", "Further diagnostics remain available in the original acceptance result.")}</p>}
    </details>
    <div className="acceptance-overview-gate"><Icon name="lock-keyhole" /><div><p>{gateText}</p><p className="acceptance-overview-caption">{tr("不会自动发布或启用Agent。", "No automatic publication or Agent activation.")}</p></div></div>
  </div>;
}
