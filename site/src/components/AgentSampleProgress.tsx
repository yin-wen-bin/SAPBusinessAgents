import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus } from "../lib/agentDraft";

const phases: Record<string, [string, string]> = {
  starting: ["正在启动", "Starting"], queued: ["正在排队", "Queued"], preparing: ["准备中", "Preparing"],
  reading_metadata: ["读取元数据", "Reading metadata"], validating_query: ["校验查询", "Validating query"],
  reading_candidates: ["读取候选数据", "Reading candidates"], checking_sample: ["核对样本", "Checking sample"],
  finalizing: ["收尾中", "Finalizing"], cleaning_up: ["正在清理，请稍候", "Cleaning up; please wait"],
};
const activeStates = new Set(["starting", "queued", "running", "cancelling"]);
const formatTime = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
const sampleCodeText: Record<string, [string, string]> = {
  sample_live_stable_key_unproven: ["SAP元数据无法证明候选查询具有稳定排序，未读取候选数据。", "SAP metadata could not prove a stable candidate-query order, so candidate data was not read."],
  sample_combination_unproven: ["来自不同SAP对象的参数无法通过共同业务键证明属于同一组样本。", "Inputs from different SAP objects could not be linked into one sample through shared business keys."],
  schema_field_not_sortable: ["候选查询使用了SAP元数据标记为不可排序的字段。", "The candidate query used a field that SAP metadata marks as non-sortable."],
};

export function SampleProgressSummary({ value, locale }: { value: any; locale: Locale }) {
  const [now, setNow] = useState(Date.now());
  const active = activeStates.has(value?.status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const elapsed = active && value.started_at ? Math.max(0, (now - Date.parse(value.started_at)) / 1000) : value.elapsed_seconds;
  const phase = phases[value.status === "cancelling" ? "cleaning_up" : value.phase];
  const failedPhase = phases[value.failed_stage];
  const timedOut = value.status === "timed_out" || value.codes?.includes("sample_discovery_timeout");
  const reason = timedOut ? tr("未在时限内完成样本核对，尚未启动试运行。", "Sample verification did not finish within the time limit. No trial was started.")
    : value.status === "start_failed" ? tr("无法启动查找，请重试。", "Could not start discovery. Please retry.")
    : value.status === "start_uncertain" ? tr("尚未确认任务是否启动。重新连接会复用同一请求，不重复选样。", "Task startup is unconfirmed. Reconnecting reuses the same request.")
    : value.status === "cancelled" ? tr("查找已取消，未启动试运行。", "Discovery cancelled. No trial was started.")
    : value.status === "needs_input" ? tr("尚不能证明完整样本，请补充参数。", "A complete sample could not be proven. Supply the missing inputs.")
    : ["inconclusive", "unavailable", "failed"].includes(value.status) ? tr("未能核对有效样本，请查看错误代码或补充参数。", "A valid sample could not be verified. Review the error code or supply inputs.") : "";
  return <div className="sample-progress-summary">
    <p role="status">{active && <span className="sample-spinner" aria-hidden="true" />}{phase && active ? phase[locale === "zh" ? 0 : 1] : draftStatus(value.status, locale)}</p>
    <p>{tr("已耗时", "Elapsed")}: {elapsed == null ? tr("未记录", "Not recorded") : formatTime(elapsed)} / {tr("最长", "Limit")}: {value.timeout_seconds == null ? tr("历史时限未记录", "Historical limit not recorded") : formatTime(value.timeout_seconds)}</p>
    {active && !value.started_at && <p>{value.created_at && <>{tr("排队等待", "Queue wait")}: {formatTime(Math.max(0, (now - Date.parse(value.created_at)) / 1000))} · </>}{tr("运行计时将在任务实际开始后显示。", "Runtime timing starts when the task begins.")}</p>}
    <p>{tr("模型", "Model")}: {value.model || tr("等待运行快照", "Awaiting runtime snapshot")} · {tr("推理强度", "Reasoning effort")}: {value.reasoning_effort || tr("未记录", "Not recorded")}</p>
    {value.candidate_count != null && <p>{tr("已读取候选记录", "Candidate records read")}: {value.candidate_count} · {tr("读取次数", "Data reads")}: {value.query_count ?? 0}</p>}
    {reason && <p role="status">{reason}</p>}
    {failedPhase && <p>{tr("未完成阶段", "Unfinished stage")}: {failedPhase[locale === "zh" ? 0 : 1]}</p>}
    {value.connection_error && <p role="alert">{tr("进度连接异常，正在重连；不代表任务已失败。", "Progress connection lost. Reconnecting; this does not mean the task failed.")}</p>}
    {(value.codes || []).map((code: string) => <p key={code}>{sampleCodeText[code]?.[locale === "zh" ? 0 : 1] || <code>{code}</code>}{sampleCodeText[code] && <> <code>({code})</code></>}</p>)}
    {(value.validation_issues || []).filter((issue: any) => sampleCodeText[issue.code]).map((issue: any, index: number) => <p key={`${issue.code}-${index}`}>{sampleCodeText[issue.code][locale === "zh" ? 0 : 1]} <code>({issue.code})</code></p>)}
    {value.validation_issues?.some((issue: any) => issue.code === "invalid_order_by_expression") && <p>{tr("查询排序格式不正确，排序项必须为字段名。", "Invalid query ordering: use bare field names.")}</p>}
  </div>;
}

export default function AgentSampleProgress({ open, value, locale, canRetry, onClose, onCancel, onRetry, onReview, onAfterClose, fields, selectedFields = [], onSelection, onStart }: {
  open: boolean; value: any; locale: Locale; canRetry: boolean; onClose: () => void;
  onCancel: () => void; onRetry: () => void; onReview: () => void;
  onAfterClose?: () => void;
  fields?: { key: string; label: string; requirement: string; disabled: boolean; reason: string }[];
  selectedFields?: string[]; onSelection?: (fields: string[]) => void; onStart?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  useEffect(() => {
    if (!open) { dialog.current?.close(); return; }
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { dialog.current?.close(); previous?.focus(); };
  }, [open]);
  const ready = ["ready", "completed", "found"].includes(value?.status);
  const active = activeStates.has(value?.status);
  const selecting = fields !== undefined;
  useEffect(() => {
    // Switching from selection removes the focused Start button. Keep focus
    // inside the native modal rather than leaving it on document.body.
    if (open) dialog.current?.querySelector<HTMLButtonElement>("header button")?.focus();
  }, [open, selecting]);
  return <dialog ref={dialog} className="sample-progress-dialog" aria-labelledby="sample-progress-title" onClose={onAfterClose} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><h2 id="sample-progress-title">{selecting ? tr("选择需要查找测试数据的参数", "Choose parameters to find test data for") : ready ? tr("已找到测试样本数据", "Test sample data found") : active ? tr("正在查找测试样本数据", "Finding test sample data") : tr("测试样本查找结果", "Test sample discovery result")}</h2>
      <button type="button" className="agent-secondary-action" aria-label={tr("关闭对话框，不取消任务", "Close dialog without cancelling") } onClick={onClose}>×</button></header>
    <p>{selecting ? tr("请选择需要查找的参数（可多选）。已填写的参数作为查询条件保留；未选参数不会自动回填。点击开始后才读取SAP。", "Select one or more parameters. Existing values remain query scope; unselected inputs are not filled. SAP reads start only after you press Start.") : tr("正在通过SAP只读查询寻找合适样本，不会修改SAP数据。", "Finding suitable samples through read-only SAP queries. SAP data will not be changed.")}</p>
    {selecting ? <fieldset className="sample-parameter-selection"><legend>{tr("当前输入参数", "Current input parameters")}</legend>{fields.map((item) => <label key={item.key} className="draft-checkbox"><input type="checkbox" checked={selectedFields.includes(item.key)} disabled={item.disabled} onChange={(event) => onSelection?.(event.target.checked ? [...selectedFields, item.key] : selectedFields.filter((key) => key !== item.key))} /><span>{item.label} · {item.requirement}{item.reason && <small> · {item.reason}</small>}</span></label>)}</fieldset> : value && <SampleProgressSummary value={value} locale={locale} />}
    {!selecting && ready && <p>{tr("已核对参数数量", "Verified parameters")}: {Object.keys(value.field_sources || {}).length}</p>}
    <footer>
      {selecting ? <><button type="button" className="agent-secondary-action" onClick={onClose}>{tr("暂不查找", "Not now")}</button><button type="button" disabled={!canRetry || !selectedFields.length} onClick={onStart}>{tr("开始查找", "Start discovery")}</button></> : active ? <><button type="button" className="agent-secondary-action" onClick={onClose}>{tr("后台继续", "Continue in background")}</button><button type="button" disabled={!value.run_id || value.status === "cancelling"} onClick={onCancel}>{tr(value.status === "cancelling" ? "正在取消" : "取消查找", value.status === "cancelling" ? "Cancelling" : "Cancel discovery")}</button></>
        : ready ? <button type="button" onClick={onReview}>{tr("查看并确认参数", "Review and confirm inputs")}</button>
        : <><button type="button" className="agent-secondary-action" onClick={onReview}>{tr("返回填写参数", "Return to inputs")}</button><button type="button" disabled={!canRetry} onClick={onRetry}>{tr(value?.status === "start_uncertain" ? "重新连接" : "重新查找", value?.status === "start_uncertain" ? "Reconnect" : "Retry discovery")}</button></>}
    </footer>
  </dialog>;
}
