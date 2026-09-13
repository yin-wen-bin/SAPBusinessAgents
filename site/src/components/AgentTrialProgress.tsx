import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { draftStatus, localText } from "../lib/agentDraft";

const activeStates = new Set(["starting", "queued", "running", "finalizing", "cancelling"]);
const phases: Record<string, [string, string]> = {
  starting: ["正在启动只读试运行", "Starting read-only trial"],
  queued: ["正在排队", "Queued"],
  preparing: ["准备只读查询", "Preparing read-only query"],
  reading_sap: ["读取SAP证据", "Reading SAP evidence"],
  validating_evidence: ["执行业务规则", "Running business rules"],
  preparing_result: ["生成业务结果", "Preparing business result"],
  finalizing: ["生成业务结果", "Preparing business result"],
  cancelling: ["正在取消并清理", "Cancelling and cleaning up"],
};

const formatTime = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, "0")}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
const timestamp = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;

export function TrialProgressSummary({ run, trial, locale, connectionError = false }: { run: any; trial: any; locale: Locale; connectionError?: boolean }) {
  const [now, setNow] = useState(Date.now());
  const status = String(run?.status || trial?.status || "starting");
  const active = activeStates.has(status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const started = timestamp(run?.started_at || trial?.started_at);
  const completed = timestamp(run?.completed_at || trial?.completed_at);
  const recorded = Number.isFinite(run?.elapsed_seconds) && run.elapsed_seconds > 0 ? run.elapsed_seconds : Number.isFinite(run?.progress?.elapsed_seconds) && run.progress.elapsed_seconds > 0 ? run.progress.elapsed_seconds : null;
  const elapsed = active && started !== null ? Math.max(0, (now - started) / 1000) : recorded ?? (started !== null && completed !== null ? Math.max(0, (completed - started) / 1000) : null);
  const phaseKey = status === "cancelling" ? "cancelling" : String(run?.progress?.phase || trial?.progress?.phase || status);
  const phase = phases[phaseKey];
  const completedSteps = run?.progress?.completed_units;
  const totalSteps = run?.progress?.total_units;
  const verdict = String(trial?.verdict || "pending");
  const outcome = verdict === "PASS" ? tr("试运行通过", "Trial passed")
    : verdict === "INCONCLUSIVE" || status === "inconclusive" ? tr("证据不完整，结论待确认", "Evidence is incomplete; conclusion pending")
    : status === "cancelled" ? tr("试运行已取消", "Trial cancelled")
    : status === "timed_out" || run?.error?.code === "run_timeout" ? tr("试运行超时", "Trial timed out")
    : verdict === "FAIL" || status === "failed" ? tr("试运行失败", "Trial failed")
    : phase ? phase[locale === "zh" ? 0 : 1] : draftStatus(status, locale);
  return <div className="trial-progress-summary">
    <p role="status">{active && <span className="sample-spinner" aria-hidden="true" />}{outcome}</p>
    <p>{tr("已耗时", "Elapsed")}: {elapsed == null ? tr("尚未开始计时", "Not started") : formatTime(elapsed)} / {tr("最长", "Limit")}: {formatTime(Number(trial?.timeout_seconds || 600))}</p>
    {Number.isInteger(completedSteps) && Number.isInteger(totalSteps) && <p>{tr("已完成步骤", "Completed steps")}: {completedSteps} / {totalSteps}</p>}
    {localText(run?.current_stage?.title, locale) && <p>{localText(run.current_stage.title, locale)}</p>}
    {trial?.business_output_available === false && <p role="alert">{tr("SAP查询已经执行，但该草稿没有生成可展示的业务结果。", "The SAP query ran, but this draft did not generate a displayable business result.")}</p>}
    {connectionError && <p role="alert">{tr("进度连接异常，正在重连；不代表任务已经失败。", "Progress connection was interrupted. Reconnecting; this does not mean the task failed.")}</p>}
  </div>;
}

export default function AgentTrialProgress({ open, run, trial, locale, connectionError, errorMessage, onClose, onCancel, onReview, onAfterClose }: {
  open: boolean; run: any; trial: any; locale: Locale; connectionError?: boolean;
  errorMessage?: string;
  onClose: () => void; onCancel: () => void; onReview: () => void; onAfterClose?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const status = String(run?.status || trial?.status || "starting");
  const active = activeStates.has(status);
  const verdict = String(trial?.verdict || "pending");
  const success = verdict === "PASS" && trial?.business_output_available !== false;
  useEffect(() => {
    if (!open) { if (dialog.current?.open) dialog.current.close(); return; }
    const previous = document.activeElement as HTMLElement | null;
    if (!dialog.current?.open) dialog.current?.showModal();
    return () => { if (dialog.current?.open) dialog.current.close(); previous?.focus(); };
  }, [open]);
  const title = active ? tr("正在执行只读试运行", "Running read-only trial")
    : success ? tr("只读试运行已完成", "Read-only trial completed")
    : tr("只读试运行结果", "Read-only trial result");
  return <dialog ref={dialog} className="sample-progress-dialog trial-progress-dialog" aria-labelledby="trial-progress-title" onClose={onAfterClose} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><h2 id="trial-progress-title">{title}</h2><button type="button" className="agent-secondary-action" aria-label={tr("关闭对话框，不取消任务", "Close dialog without cancelling")} onClick={onClose}>×</button></header>
    <p>{tr("正在执行已保存草稿的SAP只读查询和确定性业务规则，不会修改SAP数据。", "Running the saved draft's read-only SAP query and deterministic business rules. SAP data will not be changed.")}</p>
    <TrialProgressSummary run={run} trial={trial} locale={locale} connectionError={connectionError} />
    {!active && errorMessage && <p className="agent-alert error" role="alert">{errorMessage}</p>}
    {trial?.business_record_count != null && !active && <p>{tr("业务记录", "Business records")}: {trial.business_record_count}</p>}
    <footer>{active ? <><button type="button" className="agent-secondary-action" onClick={onClose}>{tr("后台继续", "Continue in background")}</button><button type="button" disabled={!trial?.run_id || status === "cancelling"} onClick={onCancel}>{status === "cancelling" ? tr("正在取消", "Cancelling") : tr("取消试运行", "Cancel trial")}</button></>
      : run ? <button type="button" onClick={onReview}>{tr("查看完整结果", "View full result")}</button>
      : <button type="button" onClick={onClose}>{tr("返回修改参数", "Return to inputs")}</button>}</footer>
  </dialog>;
}
