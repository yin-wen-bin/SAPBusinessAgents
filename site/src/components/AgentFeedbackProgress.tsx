import { useEffect, useRef, useState } from "react";
import type { Locale } from "../lib/types";
import { canRetryFeedback, feedbackDuration, feedbackEffort, feedbackFailureText, feedbackTiming } from "../lib/agentDraft";

const activeStates = new Set(["starting", "start_uncertain", "queued", "running", "cancelling"]);
const phaseLabels: Record<string, [string, string]> = {
  starting: ["正在启动修改任务", "Starting revision task"],
  queued: ["正在排队", "Queued"],
  preparing_context: ["准备草稿与对话上下文", "Preparing draft and conversation context"],
  generating_revision: ["调查并生成修改", "Investigating and preparing changes"],
  validating_response: ["校验修改结果", "Validating proposed changes"],
  applying_revision: ["保存候选修订", "Saving candidate revision"],
  finalizing: ["正在收尾", "Finalizing"],
  cancelling: ["正在取消并清理", "Cancelling and cleaning up"],
};

function failureCode(turn: any): string {
  if (turn?.decision?.error_code) return String(turn.decision.error_code);
  if (turn?.status === "cancelled") return "agent_feedback_cancelled";
  if (turn?.status === "interrupted") return "agent_feedback_interrupted";
  if (turn?.status === "timed_out" || turn?.status === "expired") return "agent_feedback_timeout";
  return "runtime_agent_feedback_failed";
}

export function FeedbackProgressSummary({ turn, operation, locale, connectionError = false }: {
  turn: any; operation?: any; locale: Locale; connectionError?: boolean;
}) {
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const operationStatus = String(operation?.status || "");
  const status = operationStatus === "cancelling" ? "cancelling" : String(turn?.status || operationStatus || "starting");
  const active = activeStates.has(status);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const detail = operation?.detail || {};
  const timingTurn = {
    ...turn,
    status,
    decision: {
      ...(turn?.decision || {}),
      execution: {
        timeout_seconds: detail.timeout_seconds,
        started_at: detail.started_at,
        deadline_at: detail.deadline_at,
        ...(turn?.decision?.execution || {}),
      },
    },
  };
  const timing = feedbackTiming(timingTurn, now);
  const snapshot = turn?.decision?.runtime_snapshot;
  const phaseKey = status === "cancelling" ? "cancelling" : String(detail.phase || status);
  const phase = phaseLabels[phaseKey]?.[locale === "zh" ? 0 : 1];
  const completedUnits = detail.completed_units;
  const totalUnits = detail.total_units;
  const changed = turn?.decision?.changed === true || (Number.isInteger(turn?.result_revision) && Number.isInteger(turn?.base_revision) && turn.result_revision !== turn.base_revision);
  const outcome = active ? phase || tr("正在处理修改意见", "Processing revision request")
    : status === "completed" && changed ? tr(`修改已完成，已保存修订 ${turn.result_revision}`, `Changes completed and saved as revision ${turn.result_revision}`)
    : status === "completed" ? tr("本轮处理已完成", "This revision turn is complete")
    : status === "cancelled" ? tr("本轮已取消", "This turn was cancelled")
    : status === "failed" || status === "timed_out" || status === "expired" || status === "interrupted" ? tr("本轮执行失败", "This turn failed")
    : phase || tr("正在启动修改任务", "Starting revision task");
  return <div className="feedback-progress-summary">
    <p role="status">{active && <span className="sample-spinner" aria-hidden="true" />}{outcome}</p>
    <p>{tr("已耗时", "Elapsed")}: {feedbackDuration(timing.elapsed_seconds, locale)} / {tr("本轮时限", "Turn limit")}: {feedbackDuration(timing.timeout_seconds, locale)}</p>
    <div className="draft-feedback-metrics">
      <span>{tr("模型", "Model")}: {snapshot?.model || tr("正在读取系统配置", "Reading system configuration")}</span>
      <span>{tr("推理强度", "Reasoning effort")}: {snapshot ? feedbackEffort(snapshot.reasoning_effort, locale) : tr("正在读取", "Loading")}</span>
    </div>
    {Number.isInteger(completedUnits) && Number.isInteger(totalUnits) && <p>{tr("已完成阶段", "Completed stages")}: {completedUnits} / {totalUnits}</p>}
    {timing.deadline_reached && <p role="status">{tr("已到本轮截止时间，正在等待服务确认终态；请勿重复发送。", "The turn deadline has been reached. Waiting for the server to confirm the terminal state; do not submit a duplicate.")}</p>}
    {!active && status !== "completed" && <p className="draft-feedback-failure" role="alert">{feedbackFailureText(failureCode(turn), locale)}</p>}
    {connectionError && <p role="alert">{tr("进度连接异常，正在重连；任务仍在后台保存和执行。", "Progress connection was interrupted. Reconnecting; the task remains saved and continues in the background.")}</p>}
  </div>;
}

export default function AgentFeedbackProgress({ open, turn, operation, locale, connectionError, errorMessage, onClose, onCancel, onRetry, onReview, onAfterClose }: {
  open: boolean; turn: any; operation?: any; locale: Locale; connectionError?: boolean; errorMessage?: string;
  onClose: () => void; onCancel: () => void; onRetry: () => void; onReview: () => void; onAfterClose?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  const operationStatus = String(operation?.status || "");
  const status = operationStatus === "cancelling" ? "cancelling" : String(turn?.status || operationStatus || "starting");
  const active = activeStates.has(status);
  const retryable = canRetryFeedback(turn);
  const changed = turn?.decision?.changed === true || (Number.isInteger(turn?.result_revision) && Number.isInteger(turn?.base_revision) && turn.result_revision !== turn.base_revision);
  const turnNumber = operation?.detail?.turn ?? turn?.turn ?? turn?.turn_number;

  useEffect(() => {
    if (!open) { if (dialog.current?.open) dialog.current.close(); return; }
    const previous = document.activeElement as HTMLElement | null;
    if (!dialog.current?.open) dialog.current?.showModal();
    return () => { if (dialog.current?.open) dialog.current.close(); previous?.focus(); };
  }, [open]);

  const title = active ? tr("正在处理修改意见", "Processing revision request")
    : status === "completed" ? tr("修改意见处理完成", "Revision request completed")
    : tr("修改意见处理结果", "Revision request result");
  return <dialog ref={dialog} className="sample-progress-dialog feedback-progress-dialog" aria-labelledby="feedback-progress-title" onClose={onAfterClose} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><h2 id="feedback-progress-title">{title}</h2><button type="button" className="agent-secondary-action" aria-label={tr("关闭对话框，不取消任务", "Close dialog without cancelling")} onClick={onClose}>×</button></header>
    <p>{tr("系统正在根据您的意见调查草稿、生成修改并校验结果。完成后仍需您检查Diff，Agent不会自动发布。", "The system is investigating the draft, preparing changes, and validating the result. You must still review the diff; the Agent is never published automatically.")}</p>
    <FeedbackProgressSummary turn={turn} operation={operation} locale={locale} connectionError={connectionError} />
    {!active && errorMessage && <p className="agent-alert error" role="alert">{errorMessage}</p>}
    <footer>{active ? <>
      <button type="button" className="agent-secondary-action" onClick={onClose}>{tr("后台继续", "Continue in background")}</button>
      <button type="button" disabled={!Number.isInteger(turnNumber) || status === "cancelling"} onClick={onCancel}>{status === "cancelling" ? tr("正在取消", "Cancelling") : tr("取消本轮", "Cancel turn")}</button>
    </> : retryable ? <button type="button" onClick={onRetry}>{tr("返回并重试", "Return and retry")}</button>
      : changed ? <button type="button" onClick={onReview}>{tr("查看修改前后对比", "Review before and after")}</button>
      : <button type="button" onClick={onClose}>{tr("返回修改意见", "Return to feedback")}</button>}</footer>
  </dialog>;
}
