import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus, validateDraftInput } from "../lib/agentDraft";
import AgentDraftInputs from "./AgentDraftInputs";
import { ContractIssues } from "./AcceptanceReadiness";
import { acceptanceAttempt, acceptanceErrorLabels, acceptanceFailureCategory } from "../lib/acceptancePresentation";

export type AcceptanceCaseDraft = {
  uiKey?: string;
  caseId: string;
  source: "current" | "sample" | "manual" | "auto";
  input: Record<string, any>;
  sensitiveInputs: Record<string, string>;
};

type SetupProps = {
  open: boolean;
  locale: Locale;
  schema: any;
  mode: string;
  runtime: any;
  runtimeChecking?: boolean;
  runtimeStartedAt?: number;
  runtimeError?: string;
  onReadRuntime?: () => void;
  cases: AcceptanceCaseDraft[];
  currentInput: Record<string, any>;
  currentSecrets: Record<string, string>;
  sampleInput?: Record<string, any>;
  canUseSample: boolean;
  disabled?: boolean;
  onCases: (cases: AcceptanceCaseDraft[]) => void;
  onFindSample: (index: number) => void;
  onClose: () => void;
  onStart: () => void;
  caseErrors?: Record<number, Record<string, string>>;
  error?: string;
  submitting?: boolean;
  submissionUnknown?: boolean;
};

export function AgentAcceptanceSetup(props: SetupProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const tr = (zh: string, en: string) => props.locale === "zh" ? zh : en;
  const [expanded, setExpanded] = useState<string>(() => props.cases[0]?.uiKey || "case-position-0");
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!props.open || !props.runtimeChecking) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [props.open, props.runtimeChecking]);
  const caseKey = (item: AcceptanceCaseDraft, index: number) => item.uiKey || `case-position-${index}`;
  useEffect(() => {
    if (props.open && !props.cases.some((item, index) => caseKey(item, index) === expanded)) {
      setExpanded(props.cases[0] ? caseKey(props.cases[0], 0) : "");
    }
  }, [props.open, props.cases]);
  useEffect(() => {
    const failed = Object.entries(props.caseErrors || {}).find(([, errors]) => Object.keys(errors).length);
    if (failed && props.cases[Number(failed[0])]) {
      const index = Number(failed[0]); setExpanded(caseKey(props.cases[index], index));
      requestAnimationFrame(() => dialog.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
    }
  }, [props.caseErrors]);
  useEffect(() => {
    if (props.open && !dialog.current?.open) {
      previousFocus.current = document.activeElement as HTMLElement | null;
      dialog.current?.showModal();
    }
    if (!props.open && dialog.current?.open) {
      dialog.current.close();
      previousFocus.current?.focus();
    }
  }, [props.open]);
  const change = (index: number, next: Partial<AcceptanceCaseDraft>) => {
    props.onCases(props.cases.map((item, position) => position === index ? { ...item, ...next } : item));
  };
  const source = (index: number, value: AcceptanceCaseDraft["source"]) => {
    const input = value === "sample" ? { ...(props.sampleInput || {}) }
      : value === "current" ? { ...props.currentInput } : props.cases[index].input;
    const sensitiveInputs = value === "current" ? { ...props.currentSecrets } : props.cases[index].sensitiveInputs;
    change(index, { source: value, input, sensitiveInputs });
  };
  const add = () => {
    if (props.cases.length >= 5) return;
    const uiKey = crypto.randomUUID();
    let number = props.cases.length + 1;
    while (props.cases.some(item => item.caseId === `case-${number}`)) number++;
    setExpanded(uiKey);
    props.onCases([...props.cases, {
      uiKey, caseId: `case-${number}`, source: "manual", input: {}, sensitiveInputs: {},
    }]);
  };
  return <dialog ref={dialog} className="sample-progress-dialog acceptance-setup-dialog" aria-labelledby="acceptance-setup-title" onCancel={(event) => { event.preventDefault(); props.onClose(); }}>
    <header className="acceptance-setup-header">
      <h2 id="acceptance-setup-title">{tr("设置正式验收", "Set up formal acceptance")}</h2>
      <button type="button" className="agent-secondary-action acceptance-setup-close" aria-label={tr("关闭验收设置", "Close acceptance setup")} onClick={props.onClose}>×</button>
      <p>{tr("1–5组必选案例 · SAP只读 · 全部通过才获PASS · 不自动发布或启用", "1–5 required cases · Read-only SAP · All must pass · No automatic publication or activation")}</p>
    </header>
    <div className="acceptance-dialog-body">
      <p className="acceptance-runtime-caption">{tr("本次使用系统默认 Runtime", "Using the system default Runtime")}: {props.runtime?.provider_id || tr("待核对", "Pending verification")}</p>
      {(props.runtimeChecking || !props.runtime && !props.runtimeError) && <p role="status" className="acceptance-runtime-wait">{tr("正在核对运行环境…", "Verifying the runtime…")} {Math.max(0, Math.floor((now - (props.runtimeStartedAt || now)) / 1000))}{tr("秒", "s")} · {tr("可继续填写案例", "You can keep editing cases")}</p>}
      {props.runtimeError && <div role="alert" className="agent-alert acceptance-runtime-error"><p>{props.runtimeError === "agent_draft_revision_conflict" ? tr("草稿修订已变化，请先刷新草稿；本次案例仍保留。", "The draft revision changed. Refresh the draft; case inputs are retained.") : tr("运行环境核对失败，尚不能提交。已保留上次信息和案例，请重新读取或在系统配置核对。", "Runtime verification failed; submission is blocked. Previous information and cases are retained. Read again or review system settings.")}</p><button type="button" className="agent-secondary-action" disabled={props.runtimeChecking} onClick={props.onReadRuntime}>{tr("重新读取", "Read again")}</button><a href={`/${props.locale}/settings/`}>{tr("系统配置", "System settings")}</a><details><summary>{tr("原因", "Reason")}</summary><code>{props.runtimeError}</code></details></div>}
      <dl className="acceptance-summary-grid">
        <div><dt>{tr("验收模式", "Acceptance mode")}</dt><dd>{props.mode === "three_stage" ? tr("三级比较", "Three-stage comparison") : tr("确定性Runtime比较", "Deterministic Runtime comparison")}</dd></div>
        <div><dt>{tr("模型", "Model")}</dt><dd>{props.runtime?.model || tr("由系统配置决定", "System configured")}</dd></div>
        <div><dt>{tr("推理强度", "Reasoning effort")}</dt><dd>{props.runtime?.provider_id === "workbuddy" ? tr("由 SDK 决定", "SDK controlled") : props.runtime?.reasoning_effort || tr("由系统配置决定", "System configured")}</dd></div>
      </dl>
      {props.runtime?.available === false && <p role="alert" className="agent-alert">{tr("默认模型尚不满足正式验收要求，请在系统配置核对认证、兼容检查和具体模型身份。", "The default model is not ready for formal acceptance. Check authentication, compatibility and concrete identity in system settings.")} <a href={`/${props.locale}/settings/`}>{tr("系统配置", "System settings")}</a> <small>{props.runtime?.error?.code}</small></p>}
      {props.error && <p role="alert" className="agent-alert">{props.error}</p>}
      {props.mode === "deterministic_runtime" && <p className="agent-alert">{tr("此Agent使用确定性Runtime模式，自由查询比较不适用。", "This Agent uses deterministic Runtime acceptance; free-query comparison is not applicable.")}</p>}
      <div className="acceptance-case-list">
        {props.cases.map((item, index) => <details key={caseKey(item, index)} className="acceptance-case-group" open={expanded === caseKey(item, index)} onToggle={(event) => { if (event.currentTarget.open) setExpanded(caseKey(item, index)); else setExpanded(current => current === caseKey(item, index) ? "" : current); }}>
          <summary>{tr(`案例 ${index + 1}`, `Case ${index + 1}`)} · {item.caseId || tr("未命名", "Unnamed")}<small>{({current: tr("最近试运行", "Latest trial"), sample: tr("已确认样本", "Confirmed sample"), manual: tr("手工参数", "Manual inputs"), auto: tr("待查样", "Sample required")})[item.source]} · {item.source === "auto" || Object.keys(validateDraftInput(props.schema, item.input, item.sensitiveInputs, props.locale)).length ? tr("参数需补充", "Inputs incomplete") : tr("参数完整", "Inputs complete")}</small></summary>
          <fieldset className="acceptance-case-editor" disabled={props.submitting || props.submissionUnknown}><legend className="sr-only">{tr(`案例 ${index + 1}`, `Case ${index + 1}`)}</legend>
          <div className="acceptance-case-meta">
          <label>{tr("案例名称", "Case name")}<input value={item.caseId} aria-invalid={Boolean(props.caseErrors?.[index]?._caseId)} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9_-]*" onChange={(event) => change(index, { caseId: event.target.value })} />{props.caseErrors?.[index]?._caseId && <small role="alert">{props.caseErrors[index]._caseId}</small>}</label>
          <label>{tr("参数来源", "Input source")}<select value={item.source} onChange={(event) => source(index, event.target.value as AcceptanceCaseDraft["source"])}>
            <option value="current">{tr("使用最近试运行参数", "Use latest trial inputs")}</option>
            <option value="sample" disabled={!props.canUseSample}>{tr("复用已确认的自动选样", "Reuse confirmed sample")}</option>
            <option value="manual">{tr("手工填写参数", "Enter inputs manually")}</option>
            <option value="auto">{tr("单独自动查找样本", "Find a sample for this case")}</option>
          </select></label>
          </div>
          {item.source === "auto" ? <div className="agent-alert"><p>{tr("确认样本后会返回本案例，不会自动开始验收。", "After confirming a sample you return here; acceptance does not start automatically.")}</p><button type="button" className="agent-secondary-action" onClick={() => props.onFindSample(index)}>{tr("开始查找样本", "Find sample")}</button></div> : <AgentDraftInputs schema={props.schema} values={item.input} onChange={(input) => change(index, { input })} secrets={item.sensitiveInputs} onSecrets={(sensitiveInputs) => change(index, { sensitiveInputs })} locale={props.locale} errors={props.caseErrors?.[index] || {}} idPrefix={`acceptance-${caseKey(item, index)}`} disabled={Boolean(props.submitting)} />}
          {props.cases.length > 1 && <button type="button" className="agent-danger-action" onClick={() => props.onCases(props.cases.filter((_, position) => position !== index))}>{tr("删除案例", "Remove case")}</button>}
        </fieldset></details>)}
      </div>
      <button type="button" className="agent-secondary-action" disabled={props.cases.length >= 5 || props.submitting || props.submissionUnknown} onClick={add}>{tr("增加验收案例", "Add acceptance case")}</button>
    </div>
    <footer className="acceptance-setup-footer"><span>{tr(`共${props.cases.length}组必选案例`, `${props.cases.length} required case(s)`)}</span><div className="agent-actions"><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("取消", "Cancel")}</button><button type="button" disabled={props.disabled || props.submitting || !props.submissionUnknown && (props.runtimeChecking || Boolean(props.runtimeError) || props.runtime?.available === false || props.cases.some((item) => item.source === "auto" || !item.caseId.trim()))} onClick={props.onStart}>{props.submissionUnknown ? tr("核对提交状态", "Check submission status") : props.submitting ? tr("正在提交", "Submitting") : props.runtimeChecking ? tr("正在核对运行环境…", "Verifying runtime…") : tr("开始正式验收", "Start formal acceptance")}</button></div></footer>
  </dialog>;
}

type ProgressProps = {
  open: boolean;
  locale: Locale;
  campaign: any;
  connectionError?: boolean;
  apiBase: string;
  draftId: string;
  onClose: () => void;
  onCancel: () => void;
  onAdjust: () => void;
  onRetry: () => void;
};

const stageLabel = (stage: string, locale: Locale) => {
  const labels: Record<string, [string, string]> = {
    preparing: ["准备并校验", "Preparing and checking"], baseline: ["独立SAP基线", "Independent SAP baseline"],
    free_query: ["自由查询比较", "Free-query comparison"], fixed_agent: ["固定Agent快照", "Fixed-Agent snapshot"],
    comparing: ["逐字段比较", "Field comparison"], completed: ["已完成", "Completed"],
    cleanup: ["清理尚未确认完成，请检查服务状态，当前草稿仍保持锁定", "Cleanup is unconfirmed. Check service health; this draft remains locked"],
    source_anchor_before: ["基线来源复核", "Baseline source verification"],
  };
  return (labels[stage] || [stage, stage])[locale === "zh" ? 0 : 1];
};

const stageState = (item: any, target: string, terminal: boolean, locale: Locale) => {
    const result = item.result || {};
    const stage = target === "comparing" ? null : result[target === "free_query" ? "free_query" : target === "fixed_agent" ? "fixed_agent" : "baseline"];
    const verified = target === "comparing" ? Boolean(result.fixed_agent?.comparison)
      : target === "baseline" ? Boolean(stage?.result_hash) : stage?.status === "completed";
    if (verified) return locale === "zh" ? "已完成" : "Completed";
    if (!terminal && item.phase === target) return locale === "zh" ? "执行中" : "Running";
    const runKey = ({ baseline: "baseline_run_id", free_query: "free_query_run_id", fixed_agent: "fixed_agent_run_id" } as Record<string, string>)[target];
    return runKey && (item[runKey] || stage?.run_id)
      ? (locale === "zh" ? "已启动，结果未核验" : "Started; result not verified")
      : terminal ? (locale === "zh" ? "未执行" : "Not run") : (locale === "zh" ? "等待执行" : "Queued");
};

const differenceLabel = (value: any, locale: Locale) => {
  const labels: Record<string, [string, string]> = {
    record_missing: ["基线记录在比较结果中缺失", "A baseline record is missing from the compared result"],
    unexpected_record: ["比较结果出现基线外记录", "The compared result contains a record outside the baseline"],
    record_set_mismatch: ["业务记录范围不一致", "Business record sets differ"],
    fact_mismatch: ["业务事实不一致", "Business facts differ"],
    decimal_mismatch: ["数量或金额不一致", "A quantity or amount differs"],
    currency_or_unit_mismatch: ["币种或单位不一致", "A currency or unit differs"],
    metric_mismatch: ["业务指标不一致", "Business metrics differ"],
    completeness_mismatch: ["完整性结论不一致", "Completeness conclusions differ"],
    business_status_mismatch: ["业务状态不一致", "Business statuses differ"],
    evidence_gap_mismatch: ["证据缺口不一致", "Evidence gaps differ"],
    required_limitations_missing: ["缺少必须披露的限制", "Required limitations are missing"],
    baseline_limitations_missing: ["遗漏独立基线已识别的限制", "A limitation identified by the baseline is missing"],
    unexpected_limitations: ["比较结果增加了基线没有的限制", "The compared result adds limitations absent from the baseline"],
    acceptance_normalization_invalid: ["业务结果无法按验收契约规范化", "The business result cannot be normalized to the acceptance contract"],
    free_query_waiting_input: ["自由查询仍需要补充参数", "Free query still requires input"],
    acceptance_projection_report_mismatch: ["页面业务结果与验收投影不一致", "The visible business result differs from its acceptance projection"],
  };
  const code = String(value?.code || "difference");
  return labels[code]?.[locale === "zh" ? 0 : 1]
    || (locale === "zh" ? `发现差异（${code}）` : `Difference found (${code})`);
};

const campaignErrorLabel = (codeValue: unknown, locale: Locale) => {
  const code = String(codeValue || "agent_acceptance_internal_error");
  return acceptanceErrorLabels[code]?.[locale === "zh" ? 0 : 1]
    || (locale === "zh" ? `验收未完成（${code}）。` : `Acceptance did not complete (${code}).`);
};

const acceptanceIssueLabel = (codeValue: unknown, locale: Locale) => {
  const labels: Record<string, [string, string]> = {
    test_data_gap: ["没有找到能够完成本案例比较的合格SAP样本。", "No qualified SAP sample was found for this case."],
    free_query_waiting_input: ["自由查询仍需要补充业务参数。", "Free query still requires additional business input."],
    sap_source_changed_during_acceptance: ["验收期间SAP来源数据发生变化，请在数据稳定后重试。", "SAP source data changed during acceptance. Retry when the data is stable."],
    source_anchor_timeout: ["SAP来源稳定性复核超时。", "SAP source stability verification timed out."],
    source_anchor_coverage_missing: ["当前证据无法完成SAP来源前后复核。", "The available evidence cannot support before-and-after SAP source verification."],
    source_anchor_plan_rejected: ["SAP来源复核查询未通过只读计划校验。", "The SAP source replay did not pass read-only plan validation."],
    acceptance_report_validation_failed: ["验收报告未通过当前业务契约校验。", "The acceptance report did not pass the current business contract."],
    acceptance_report_schema_invalid: ["验收报告结构与当前契约不一致。", "The acceptance report structure differs from the current contract."],
    acceptance_projection_field_missing: ["验收结果缺少契约要求的字段。", "The acceptance result is missing a contract field."],
    acceptance_projection_field_unexpected: ["验收结果包含契约未声明的字段。", "The acceptance result contains an undeclared field."],
    acceptance_projection_type_invalid: ["验收结果字段类型不符合契约。", "An acceptance result field has the wrong type."],
    acceptance_projection_enum_invalid: ["验收结果字段值不在契约允许范围内。", "An acceptance result field value is outside the contract enum."],
    inventory_fifo_assessment_required: ["本次验收要求有效的库存FIFO评估，但尚未取得。", "This acceptance requires a valid inventory FIFO assessment, but none is available."],
  };
  const code = String(codeValue || "acceptance_issue");
  return labels[code]?.[locale === "zh" ? 0 : 1]
    || (locale === "zh" ? `验收条件受阻（${code}）。` : `Acceptance condition blocked (${code}).`);
};

export function AgentAcceptanceProgress(props: ProgressProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [now, setNow] = useState(Date.now());
  const [expandedCases, setExpandedCases] = useState<Record<string, boolean>>({});
  const tr = (zh: string, en: string) => props.locale === "zh" ? zh : en;
  useEffect(() => {
    if (props.open && !dialog.current?.open) {
      previousFocus.current = document.activeElement as HTMLElement | null;
      dialog.current?.showModal();
    }
    if (!props.open && dialog.current?.open) {
      dialog.current.close();
      previousFocus.current?.focus();
    }
  }, [props.open]);
  const campaign = props.campaign || {};
  const terminal = ["passed", "failed", "blocked", "cancelled", "interrupted", "superseded"].includes(campaign.status);
  useEffect(() => {
    if (!props.open || terminal) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [props.open, terminal]);
  const elapsed = campaign.started_at ? Math.max(0, Math.round(((campaign.completed_at ? Date.parse(campaign.completed_at) : now) - Date.parse(campaign.started_at)) / 1000)) : 0;
  const queued = campaign.created_at ? Math.max(0, Math.round(((campaign.started_at ? Date.parse(campaign.started_at) : now) - Date.parse(campaign.created_at)) / 1000)) : 0;
  const format = (value: number) => `${Math.floor(value / 60).toString().padStart(2, "0")}:${Math.floor(value % 60).toString().padStart(2, "0")}`;
  const verdict = campaign.report?.verdict || (campaign.status === "passed" ? "PASS" : campaign.status === "blocked" ? "BLOCKED" : campaign.status === "failed" ? "FAIL" : "NOT_TESTED");
  const preparationFailed = terminal && campaign.status !== "cancelled" && verdict === "NOT_TESTED"
    && campaign.report?.failure_stage === "preparing"
    && (campaign.cases || []).every((item: any) => !item.baseline_run_id && !item.free_query_run_id && !item.fixed_agent_run_id);
  const currentCase = (campaign.cases || []).find((item: any) => item.case_id === campaign.current_case_id)
    || (campaign.cases || []).find((item: any) => item.status === "running");
  const currentPhase = currentCase?.phase || campaign.phase || "preparing";
  const phaseRecorded = ["baseline", "free_query", "fixed_agent", "comparing", "source_anchor_before"].includes(currentPhase);
  const cleanupPending = campaign.phase === "cleanup" || campaign.report?.diagnostics?.cleanup_complete === false;
  const attempt = acceptanceAttempt(campaign, props.locale);
  return <dialog ref={dialog} className="sample-progress-dialog acceptance-progress-dialog" aria-labelledby="acceptance-progress-title" onCancel={(event) => { event.preventDefault(); props.onClose(); }}>
    <header className="acceptance-progress-header">
      <div><h2 id="acceptance-progress-title">{terminal ? tr("正式验收结果", "Formal acceptance result") : tr("正在进行正式验收", "Formal acceptance in progress")}</h2><p role="status">{preparationFailed ? tr("验收准备失败，尚未开始SAP取证", "Acceptance preparation failed; SAP evidence collection has not started") : campaign.status === "starting" ? tr("正在提交验收请求", "Submitting acceptance request") : attempt.title}</p></div>
      <button type="button" className="agent-secondary-action" aria-label={tr("收起验收窗口，后台继续", "Close acceptance window; continue in background")} onClick={props.onClose}>×</button>
      <p className="acceptance-progress-runtime">{campaign.runtime?.provider_id || tr("等待运行快照", "Waiting for runtime snapshot")} · {campaign.runtime?.model || tr("模型待确认", "Model unconfirmed")} · {tr("案例", "Case")} {campaign.current_case_index || (currentCase ? (campaign.cases.indexOf(currentCase) + 1) : "—")}/{campaign.cases?.length || "—"}</p>
    </header>
    <div className="acceptance-dialog-body">
      {!terminal && <section className="acceptance-current-progress" aria-label={tr("当前进度", "Current progress")}><strong>{campaign.status === "cancelling" ? tr("正在停止并清理", "Stopping and cleaning up") : stageLabel(currentPhase, props.locale)}</strong><p>{phaseRecorded ? tr(`平台已记录进入「${stageLabel(currentPhase, props.locale)}」阶段。`, `The platform recorded entry into ${stageLabel(currentPhase, props.locale)}.`) : tr("等待进展更新", "Waiting for a progress update")}</p></section>}
      {terminal && <p className="acceptance-business-verdict"><strong>{tr("业务结论", "Business verdict")}: {draftStatus(verdict, props.locale)}</strong></p>}
      <p className="acceptance-progress-timing">{tr("排队", "Queued")}: {format(queued)} · {tr("执行", "Execution")}: {format(elapsed)} · {tr("预算上限（非预计完成时间）", "Budget limit (not an ETA)")}: {campaign.estimated_max_seconds ? format(campaign.estimated_max_seconds) : tr("待确认", "Unconfirmed")}</p>
      {props.connectionError && <p className="agent-alert" role="status">{tr("进度连接异常，正在通过轮询恢复。任务不会重复创建。", "The progress stream is reconnecting with polling. The campaign is not duplicated.")}</p>}
      {cleanupPending && <p className="agent-alert" role="alert">{tr("未能确认后台清理完成，草稿操作锁仍然保留。请检查服务状态。", "Background cleanup is unconfirmed. The draft remains locked; check service health.")}</p>}
      {terminal && acceptanceFailureCategory(campaign.report) && <p>{tr("问题类别", "Issue category")}: {({ input: tr("业务输入需要核对", "Business inputs need verification"), scope: tr("平台查询范围契约受阻", "Platform query-scope contract blocked"), contract: tr("验收定义或输出不符合契约，尚未完成业务比较", "Contract/output invalid; business comparison not completed"), evidence: tr("证据不足或来源变化", "Evidence gap or source drift"), business: tr("同一口径下的业务差异", "Business difference under the same definition"), environment: tr("执行环境故障", "Execution environment failure") } as Record<string, string>)[acceptanceFailureCategory(campaign.report)] || tr("验收条件受阻", "Acceptance requirement blocked")} · {stageLabel(campaign.report.failure_stage || "comparing", props.locale)}</p>}
      {campaign.report?.diagnostics?.clarification_question && <p className="agent-alert" role="alert">{campaign.report.diagnostics.clarification_question}</p>}
      {Boolean(campaign.report?.validation_issues?.length) && <ContractIssues issues={campaign.report.validation_issues} locale={props.locale} />}
      {campaign.report?.diagnostics && <details className="acceptance-diagnostics"><summary>{tr("查看执行诊断", "Execution diagnostics")}</summary>
        <dl><dt>{tr("最后失败工具", "Last failed tool")}</dt><dd>{campaign.report.diagnostics.last_failed_tool || tr("未记录", "Not recorded")}</dd>
          <dt>{tr("安全错误代码", "Safe error code")}</dt><dd>{campaign.report.diagnostics.last_error_code || tr("未记录", "Not recorded")}</dd>
          <dt>{tr("最后完成工具", "Last completed tool")}</dt><dd>{campaign.report.diagnostics.last_completed_tool || tr("未记录", "Not recorded")}</dd></dl>
        {campaign.report.diagnostics.dependency_module && <p>{tr("未能加载的组件", "Component that failed to load")}: <code>{campaign.report.diagnostics.dependency_module}</code></p>}
        <ContractIssues issues={campaign.report.diagnostics.unresolved_issues || []} locale={props.locale} />
        {campaign.report?.error?.code && <p><code>{campaign.report.error.code}</code></p>}
      </details>}
      {terminal && campaign.report?.verdict === "NOT_TESTED" && <p className="agent-alert">{campaignErrorLabel(campaign.report?.error?.code, props.locale)}</p>}
      <ol className="acceptance-progress-cases">{(campaign.cases || []).map((item: any) => {
        const caseTerminal = ["pass", "fail", "failed", "blocked", "cancelled", "interrupted"].includes(item.status);
        const freeApplicable = campaign.acceptance_mode === "three_stage";
        const key = `${campaign.campaign_id || "pending"}:${item.case_id}`;
        const open = expandedCases[key] ?? (item === currentCase || terminal && item === campaign.cases[0]);
        return <li key={item.case_id}><details open={open}><summary aria-expanded={open} onClick={(event) => { event.preventDefault(); setExpandedCases(old => ({ ...old, [key]: !open })); }}><strong>{item.case_id}</strong><span>{preparationFailed ? tr("准备失败", "Preparation failed") : caseTerminal ? draftStatus(item.result?.verdict || item.status, props.locale) : stageLabel(item.phase || "preparing", props.locale)}</span></summary><dl className="acceptance-stage-grid"><div><dt>{tr("独立基线", "Independent baseline")}</dt><dd>{stageState(item, "baseline", caseTerminal || terminal, props.locale)}</dd></div><div><dt>{tr("自由查询", "Free query")}</dt><dd>{freeApplicable ? stageState(item, "free_query", caseTerminal || terminal, props.locale) : tr("不适用", "Not applicable")}</dd></div><div><dt>{tr("固定Agent", "Fixed Agent")}</dt><dd>{stageState(item, "fixed_agent", caseTerminal || terminal, props.locale)}</dd></div><div><dt>{tr("逐字段比较", "Field comparison")}</dt><dd>{stageState(item, "comparing", caseTerminal || terminal, props.locale)}</dd></div></dl></details></li>;
      })}</ol>
      {terminal && campaign.report?.cases?.map((item: any) => <details key={item.case_id} className="acceptance-case-result"><summary>{item.case_id} · {draftStatus(item.verdict, props.locale)}</summary><dl><dt>{tr("独立基线", "Independent baseline")}</dt><dd>{item.baseline?.result_hash || draftStatus(item.baseline?.status, props.locale)}</dd><dt>{tr("自由查询", "Free query")}</dt><dd>{item.free_query?.comparison === "NOT_APPLICABLE" ? tr("不适用", "Not applicable") : draftStatus(item.free_query?.comparison?.verdict, props.locale)}</dd><dt>{tr("固定Agent", "Fixed Agent")}</dt><dd>{draftStatus(item.fixed_agent?.comparison?.verdict, props.locale)}</dd><dt>{tr("SAP来源稳定性", "SAP source stability")}</dt><dd>{draftStatus(item.source_anchors?.verdict, props.locale)}</dd></dl>{(item.validation_issues || []).map((issue: any, index: number) => <p key={`issue-${index}`} className="agent-alert">{acceptanceIssueLabel(issue.code, props.locale)}</p>)}{[...(item.free_query?.comparison?.differences || []), ...(item.fixed_agent?.comparison?.differences || [])].map((difference: any, index: number) => <details key={index} className="acceptance-difference"><summary>{differenceLabel(difference, props.locale)}</summary><pre>{JSON.stringify(difference, null, 2)}</pre></details>)}</details>)}
      {terminal && campaign.artifacts_available && <details><summary>{tr("下载验收报告", "Download acceptance reports")}</summary><div className="acceptance-downloads"><a href={`${props.apiBase}/api/authoring/agents/${encodeURIComponent(props.draftId)}/acceptance-campaigns/${encodeURIComponent(campaign.campaign_id)}/artifacts/report.md`} target="_blank">{tr("下载Markdown报告", "Download Markdown report")}</a><a href={`${props.apiBase}/api/authoring/agents/${encodeURIComponent(props.draftId)}/acceptance-campaigns/${encodeURIComponent(campaign.campaign_id)}/artifacts/report.json`} target="_blank">{tr("下载JSON报告", "Download JSON report")}</a></div></details>}
    </div>
    <footer className="acceptance-progress-footer"><small>{tr("SAP只读 · 不发布或启用", "Read-only SAP · No publication or activation")}</small><div className="agent-actions">{!terminal ? <><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("收起，后台继续", "Close; continue in background")}</button><button type="button" className="agent-danger-action" disabled={campaign.status === "cancelling" || !campaign.campaign_id} onClick={props.onCancel}>{campaign.status === "cancelling" ? tr("正在停止", "Stopping") : tr("停止验收", "Stop acceptance")}</button></> : <><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("关闭", "Close")}</button>{verdict !== "PASS" && <button type="button" className="agent-secondary-action" onClick={props.onAdjust}>{tr("调整草稿", "Adjust draft")}</button>}{verdict !== "PASS" && <button type="button" onClick={props.onRetry}>{tr("重新设置验收", "Set up acceptance again")}</button>}</>}</div></footer>
  </dialog>;
}

export function AcceptanceSummary({ campaign, locale, onOpen }: { campaign: any; locale: Locale; onOpen: () => void }) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  if (!campaign) return null;
  return <div className="acceptance-inline-summary"><p><strong>{tr("最近正式验收", "Latest formal acceptance")}</strong>: {draftStatus(campaign.report?.verdict || campaign.status, locale)} · {campaign.cases?.length ?? campaign.report?.case_count ?? "—"} {tr("组案例", "cases")}</p><button type="button" className="agent-secondary-action" onClick={onOpen}>{tr("查看验收进度与结果", "View acceptance progress and result")}</button></div>;
}
