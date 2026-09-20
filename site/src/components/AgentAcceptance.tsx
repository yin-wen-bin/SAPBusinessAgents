import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus } from "../lib/agentDraft";
import AgentDraftInputs from "./AgentDraftInputs";
import { ContractIssues } from "./AcceptanceReadiness";

export type AcceptanceCaseDraft = {
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
};

export function AgentAcceptanceSetup(props: SetupProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
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
    props.onCases([...props.cases, {
      caseId: `case-${props.cases.length + 1}`, source: "manual", input: {}, sensitiveInputs: {},
    }]);
  };
  return <dialog ref={dialog} className="sample-progress-dialog acceptance-setup-dialog" aria-labelledby="acceptance-setup-title" onCancel={(event) => { event.preventDefault(); props.onClose(); }}>
    <div className="acceptance-dialog-body">
      <h2 id="acceptance-setup-title">{tr("设置正式验收", "Set up formal acceptance")}</h2>
      <p>{tr("可设置1–5组必选案例。正式验收会自动读取SAP只读数据，生成独立基线，并与自由查询及当前草稿快照比较。不会自动发布或启用Agent。", "Set 1–5 required cases. Formal acceptance reads SAP data read-only, creates an independent baseline, and compares it with free query and the current draft snapshot. It never publishes or activates the Agent automatically.")}</p>
      <dl className="acceptance-summary-grid">
        <div><dt>{tr("验收模式", "Acceptance mode")}</dt><dd>{props.mode === "three_stage" ? tr("三级比较", "Three-stage comparison") : tr("确定性Runtime比较", "Deterministic Runtime comparison")}</dd></div>
        <div><dt>{tr("模型", "Model")}</dt><dd>{props.runtime?.model || tr("由系统配置决定", "System configured")}</dd></div>
        <div><dt>{tr("推理强度", "Reasoning effort")}</dt><dd>{props.runtime?.reasoning_effort || tr("由系统配置决定", "System configured")}</dd></div>
      </dl>
      {props.mode === "deterministic_runtime" && <p className="agent-alert">{tr("此Agent使用确定性Runtime模式，自由查询比较不适用。", "This Agent uses deterministic Runtime acceptance; free-query comparison is not applicable.")}</p>}
      <div className="acceptance-case-list">
        {props.cases.map((item, index) => <fieldset key={`${index}-${item.caseId}`} className="acceptance-case-editor">
          <legend>{tr(`案例 ${index + 1}`, `Case ${index + 1}`)}</legend>
          <label>{tr("案例名称", "Case name")}<input value={item.caseId} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9_-]*" onChange={(event) => change(index, { caseId: event.target.value })} /></label>
          <label>{tr("参数来源", "Input source")}<select value={item.source} onChange={(event) => source(index, event.target.value as AcceptanceCaseDraft["source"])}>
            <option value="current">{tr("使用最近试运行参数", "Use latest trial inputs")}</option>
            <option value="sample" disabled={!props.canUseSample}>{tr("复用已确认的自动选样", "Reuse confirmed sample")}</option>
            <option value="manual">{tr("手工填写参数", "Enter inputs manually")}</option>
            <option value="auto">{tr("单独自动查找样本", "Find a sample for this case")}</option>
          </select></label>
          {item.source === "auto" ? <div className="agent-alert"><p>{tr("将暂存本案例参数并打开自动选样。找到并确认样本后，系统会自动回到本案例并填入已核对参数。", "This case is staged while automatic discovery opens. After you confirm a sample, the setup returns to this case with the verified inputs filled in.")}</p><button type="button" className="agent-secondary-action" onClick={() => props.onFindSample(index)}>{tr("开始查找样本", "Find sample")}</button></div> : <AgentDraftInputs schema={props.schema} values={item.input} onChange={(input) => change(index, { input })} secrets={item.sensitiveInputs} onSecrets={(sensitiveInputs) => change(index, { sensitiveInputs })} locale={props.locale} errors={{}} disabled={false} />}
          {props.cases.length > 1 && <button type="button" className="agent-danger-action" onClick={() => props.onCases(props.cases.filter((_, position) => position !== index))}>{tr("删除案例", "Remove case")}</button>}
        </fieldset>)}
      </div>
      <button type="button" className="agent-secondary-action" disabled={props.cases.length >= 5} onClick={add}>{tr("增加验收案例", "Add acceptance case")}</button>
      <p>{tr(`共${props.cases.length}组必选案例，全部通过才会获得PASS。`, `${props.cases.length} required case(s); every case must pass.`)}</p>
      <div className="agent-actions"><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("取消", "Cancel")}</button><button type="button" disabled={props.disabled || props.cases.some((item) => item.source === "auto" || !item.caseId.trim())} onClick={props.onStart}>{tr("开始正式验收", "Start formal acceptance")}</button></div>
    </div>
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
  };
  return (labels[stage] || [stage, stage])[locale === "zh" ? 0 : 1];
};

const stageState = (phase: string, target: string, terminal: boolean, locale: Locale) => {
  const order = ["preparing", "baseline", "free_query", "fixed_agent", "comparing", "completed"];
  const current = order.indexOf(phase);
  const expected = order.indexOf(target);
  if (terminal || current > expected) return locale === "zh" ? "已完成" : "Completed";
  if (current === expected) return locale === "zh" ? "执行中" : "Running";
  return locale === "zh" ? "等待执行" : "Queued";
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
  const labels: Record<string, [string, string]> = {
    agent_acceptance_cancelled: ["验收已由用户取消，未生成发布证书。", "Acceptance was cancelled; no publication certificate was created."],
    agent_acceptance_interrupted: ["服务在验收期间重启或中断，请重新验收。", "The service restarted or stopped during acceptance. Run acceptance again."],
    agent_acceptance_queue_timeout: ["任务排队时间过长，尚未完成验收。", "The campaign remained queued too long and was not tested."],
    agent_acceptance_stage_timeout: ["当前验收阶段超时，未生成发布证书。", "The current acceptance stage timed out; no publication certificate was created."],
    agent_acceptance_internal_error: ["验收服务发生内部错误，请核对服务状态后重试。", "The acceptance service failed internally. Check service health and retry."],
    runtime_model_authentication_failed: ["Runtime认证失败，请在系统配置中重新登录。", "Runtime authentication failed. Sign in again in system settings."],
    runtime_model_check_required: ["模型与推理强度尚未通过兼容检查。", "The model and reasoning effort have not passed compatibility checking."],
  };
  const code = String(codeValue || "agent_acceptance_internal_error");
  return labels[code]?.[locale === "zh" ? 0 : 1]
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
  return <dialog ref={dialog} className="sample-progress-dialog acceptance-progress-dialog" aria-labelledby="acceptance-progress-title" onCancel={(event) => { event.preventDefault(); props.onClose(); }}>
    <div className="acceptance-dialog-body">
      <h2 id="acceptance-progress-title">{terminal ? tr("正式验收结果", "Formal acceptance result") : tr("正在进行正式验收", "Formal acceptance in progress")}</h2>
      {!terminal && <div className="agent-progress-spinner" aria-hidden="true" />}
      <p role="status">{terminal ? draftStatus(verdict, props.locale) : stageLabel(campaign.phase || "preparing", props.locale)}</p>
      {terminal && campaign.report?.failure_category && <p>{tr("问题类别", "Issue category")}: {({ contract: tr("验收定义或输出不符合契约，尚未完成业务比较", "Contract/output invalid; business comparison not completed"), evidence: tr("证据不足或来源变化", "Evidence gap or source drift"), business: tr("同一口径下的业务差异", "Business difference under the same definition"), environment: tr("执行环境故障", "Execution environment failure") } as Record<string, string>)[campaign.report.failure_category]} · {stageLabel(campaign.report.failure_stage || "comparing", props.locale)}</p>}
      <ContractIssues issues={campaign.report?.validation_issues || []} locale={props.locale} />
      {!terminal && campaign.current_case_id && <p>{tr("当前案例", "Current case")}: {campaign.current_case_index || "—"}/{campaign.cases?.length || "—"} · <code>{campaign.current_case_id}</code></p>}
      <p>{tr("SAP只读验收，不会发布或启用Agent。", "Read-only SAP acceptance; the Agent will not be published or activated.")}</p>
      <p>{tr("排队耗时", "Queue time")}: {format(queued)} · {tr("实际执行耗时", "Execution time")}: {format(elapsed)} · {tr("动态最长", "Maximum")}: {format(campaign.estimated_max_seconds || 0)}</p>
      {props.connectionError && <p className="agent-alert" role="status">{tr("进度连接异常，正在通过轮询恢复。任务不会重复创建。", "The progress stream is reconnecting with polling. The campaign is not duplicated.")}</p>}
      {terminal && campaign.report?.verdict === "NOT_TESTED" && <p className="agent-alert">{campaignErrorLabel(campaign.report?.error?.code, props.locale)}</p>}
      <ol className="acceptance-progress-cases">{(campaign.cases || []).map((item: any) => {
        const caseTerminal = ["pass", "fail", "failed", "blocked", "cancelled", "interrupted"].includes(item.status);
        const freeApplicable = campaign.acceptance_mode === "three_stage";
        return <li key={item.case_id}><strong>{item.case_id}</strong><span>{stageLabel(item.phase, props.locale)} · {draftStatus(item.result?.verdict || item.status, props.locale)}</span><dl className="acceptance-stage-grid"><div><dt>{tr("独立基线", "Independent baseline")}</dt><dd>{stageState(item.phase, "baseline", caseTerminal, props.locale)}</dd></div><div><dt>{tr("自由查询", "Free query")}</dt><dd>{freeApplicable ? stageState(item.phase, "free_query", caseTerminal, props.locale) : tr("不适用", "Not applicable")}</dd></div><div><dt>{tr("固定Agent", "Fixed Agent")}</dt><dd>{stageState(item.phase, "fixed_agent", caseTerminal, props.locale)}</dd></div><div><dt>{tr("逐字段比较", "Field comparison")}</dt><dd>{stageState(item.phase, "comparing", caseTerminal, props.locale)}</dd></div></dl></li>;
      })}</ol>
      {terminal && campaign.report?.cases?.map((item: any) => <details key={item.case_id} className="acceptance-case-result"><summary>{item.case_id} · {draftStatus(item.verdict, props.locale)}</summary><dl><dt>{tr("独立基线", "Independent baseline")}</dt><dd>{item.baseline?.result_hash || draftStatus(item.baseline?.status, props.locale)}</dd><dt>{tr("自由查询", "Free query")}</dt><dd>{item.free_query?.comparison === "NOT_APPLICABLE" ? tr("不适用", "Not applicable") : draftStatus(item.free_query?.comparison?.verdict, props.locale)}</dd><dt>{tr("固定Agent", "Fixed Agent")}</dt><dd>{draftStatus(item.fixed_agent?.comparison?.verdict, props.locale)}</dd><dt>{tr("SAP来源稳定性", "SAP source stability")}</dt><dd>{draftStatus(item.source_anchors?.verdict, props.locale)}</dd></dl>{(item.validation_issues || []).map((issue: any, index: number) => <p key={`issue-${index}`} className="agent-alert">{acceptanceIssueLabel(issue.code, props.locale)}</p>)}{[...(item.free_query?.comparison?.differences || []), ...(item.fixed_agent?.comparison?.differences || [])].map((difference: any, index: number) => <details key={index} className="acceptance-difference"><summary>{differenceLabel(difference, props.locale)}</summary><pre>{JSON.stringify(difference, null, 2)}</pre></details>)}</details>)}
      {terminal && campaign.artifacts_available && <div className="acceptance-downloads"><a href={`${props.apiBase}/api/authoring/agents/${encodeURIComponent(props.draftId)}/acceptance-campaigns/${encodeURIComponent(campaign.campaign_id)}/artifacts/report.md`} target="_blank">{tr("下载Markdown报告", "Download Markdown report")}</a><a href={`${props.apiBase}/api/authoring/agents/${encodeURIComponent(props.draftId)}/acceptance-campaigns/${encodeURIComponent(campaign.campaign_id)}/artifacts/report.json`} target="_blank">{tr("下载JSON报告", "Download JSON report")}</a></div>}
      <div className="agent-actions">{!terminal ? <><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("后台继续", "Continue in background")}</button><button type="button" className="agent-danger-action" disabled={campaign.status === "cancelling" || !campaign.campaign_id} onClick={props.onCancel}>{campaign.status === "cancelling" ? tr("正在取消", "Cancelling") : tr("取消验收", "Cancel acceptance")}</button></> : <><button type="button" className="agent-secondary-action" onClick={props.onClose}>{tr("关闭", "Close")}</button>{verdict !== "PASS" && <button type="button" className="agent-secondary-action" onClick={props.onAdjust}>{tr("调整草稿", "Adjust draft")}</button>}{verdict !== "PASS" && <button type="button" onClick={props.onRetry}>{tr("更换案例并重新验收", "Change cases and retry")}</button>}</>}</div>
    </div>
  </dialog>;
}

export function AcceptanceSummary({ campaign, locale, onOpen }: { campaign: any; locale: Locale; onOpen: () => void }) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  if (!campaign) return null;
  return <div className="acceptance-inline-summary"><p><strong>{tr("最近正式验收", "Latest formal acceptance")}</strong>: {draftStatus(campaign.report?.verdict || campaign.status, locale)} · {campaign.cases?.length ?? campaign.report?.case_count ?? "—"} {tr("组案例", "cases")}</p><button type="button" className="agent-secondary-action" onClick={onOpen}>{tr("查看验收进度与结果", "View acceptance progress and result")}</button></div>;
}
