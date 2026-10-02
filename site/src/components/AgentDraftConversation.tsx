import { useEffect, useState } from "react";
import type { Locale } from "../lib/types";
import { canRetryFeedback, draftStatus, feedbackDuration, feedbackEffort, feedbackFailureText, feedbackTiming, feedbackValidationIssues, localText } from "../lib/agentDraft";
import SafeMarkdown from "./SafeMarkdown";
import type { FeedbackObjectReference } from "../lib/agentDraft";

const auditKinds: Record<string, [string, string]> = {
  initial: ["创建草稿", "Draft created"], create: ["创建草稿", "Draft created"], clone: ["复制现有Agent", "Agent copied"],
  manual_edit: ["保存表单修改", "Form changes saved"], undo: ["恢复历史修订", "Revision restored"], import: ["导入草稿", "Draft imported"],
  technical_id: ["确认技术 ID", "Technical ID confirmed"], technical_id_confirmed: ["确认技术 ID", "Technical ID confirmed"], technical_id_renamed: ["修改技术 ID", "Technical ID changed"],
};
const sourceLabels: Record<string, [string, string]> = { blank: ["空白模板", "Blank template"], clone: ["现有Agent", "Existing Agent"], free_query: ["自由查询结果", "Free-query result"], workflow_gap: ["工作流能力缺口", "Workflow capability gap"] };

export default function AgentDraftConversation({ turns, locale, onRetry, onWithdraw, onLocate, currentRevision, diagnostics = [], retryDisabled = false }: { turns: any[]; locale: Locale; onRetry?: (turn: any) => void; onWithdraw?: (turn: any) => void; onLocate?: (reference: FeedbackObjectReference) => void; currentRevision?: number; diagnostics?: any[]; retryDisabled?: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  const hasActiveTurn = turns.some((turn) => feedbackTiming(turn).active);
  useEffect(() => {
    if (!hasActiveTurn) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveTurn]);
  let chatNumber = 0;
  const language = locale === "zh" ? 0 : 1;
  const tr = (zh: string, en: string) => locale === "zh" ? zh : en;
  return <div className="draft-conversation-history">{turns.map((turn, index) => {
    const audit = auditKinds[turn.kind];
    const timing = feedbackTiming(turn, now);
    const pending = timing.active;
    const reply = localText(turn.assistant_message || turn.decision?.assistant_message || turn.decision?.summary, locale);
    const userMessage = turn.user_message || turn.feedback;
    const source = sourceLabels[turn.decision?.source]?.[language];
    const identityChange = turn.kind === "technical_id" ? turn.diff?.find((change: any) => change.path === "/manifest/slug") : null;
    const snapshot = turn.decision?.runtime_snapshot;
    const failed = ["failed", "cancelled", "interrupted", "timed_out", "expired"].includes(turn.status);
    const failureCode = turn.decision?.error_code || (turn.status === "cancelled" ? "agent_feedback_cancelled" : turn.status === "interrupted" ? "agent_feedback_interrupted" : turn.status === "timed_out" ? "agent_feedback_timeout" : "");
    const validationIssues = failed ? feedbackValidationIssues(turn, locale) : [];
    const context = turn.decision?.context || {};
    const target = (reference: FeedbackObjectReference, label: string) => onLocate
      ? <button type="button" className="draft-object-link" onClick={() => onLocate(reference)}>{label}</button>
      : <span>{label}</span>;
    const toolEvents = turn.decision?.tool_events || [];
    const liveDiagnostics = [...(turn.decision?.runtime_diagnostics || []), ...diagnostics.filter((event) => event.turn === turn.turn)]
      .filter((event, index, all) => all.findIndex((item) => item.sequence === event.sequence) === index);
    if (!audit) chatNumber += 1;
    return <article key={turn.turn ?? turn.turn_number ?? index} className={audit ? "draft-audit-entry" : "draft-chat-entry"}>
      <h3>{audit ? audit[language] : locale === "zh" ? `第${chatNumber}轮对话` : `Conversation ${chatNumber}`}{!audit && <> · {draftStatus(turn.status || turn.decision?.action, locale)}</>}</h3>
      {audit ? <p>{source && <>{locale === "zh" ? "来源：" : "Source: "}{source} · </>}{identityChange ? <>{tr("技术 ID", "Technical ID")}: <code>{String(identityChange.before ?? "")}</code> → <code>{String(identityChange.after ?? "")}</code></> : reply || (locale === "zh" ? "此记录为草稿操作，不是待回复的对话。" : "This is a draft action, not a conversation awaiting reply.")}</p> : <>
        <p className="draft-user-message">{userMessage || (locale === "zh" ? "历史用户消息未记录" : "Historical user message was not recorded")}</p>
        <div className="draft-turn-context" aria-label={tr("当前对象", "Current objects")}>
          {target({ kind: "revision", revision: turn.base_revision, resultRevision: turn.result_revision }, `${tr("草稿修订", "Draft revision")} ${turn.base_revision}`)}
          {context.step && target({ revision: turn.base_revision, kind: "step", step: context.step }, `${tr("步骤", "Step")}: ${context.step}`)}
          {context.field_path && target({ revision: turn.base_revision, kind: "manifest_field", path: context.field_path }, context.field_path)}
          {context.run_id && target({ revision: turn.base_revision, kind: "trial_report", sourceId: context.run_id }, `${tr("试运行", "Trial")}: ${context.run_id}`)}
          {context.acceptance_campaign_id && target({ revision: turn.base_revision, kind: "acceptance_report", sourceId: context.acceptance_campaign_id }, `${tr("验收", "Acceptance")}: ${context.acceptance_campaign_id}`)}
          {(context.annotations || []).map((item: any, annotationIndex: number) => <span key={annotationIndex}>{target({ revision: item.revision, kind: item.kind, path: item.path, sourceId: item.source_id, sourceDigest: item.source_digest }, `${annotationIndex + 1}. ${item.path}`)}</span>)}
        </div>
        {turn.decision?.pending_clarification && <div className="draft-pending-question"><strong>{turns.some((later) => ["completed", "queued", "running", "waiting"].includes(later.status) && later.decision?.reply_to_clarification_id === turn.decision.pending_clarification.id) ? tr("已回复的澄清问题", "Answered clarification") : currentRevision !== undefined && turn.decision.pending_clarification.revision !== currentRevision ? tr("已过期的澄清问题", "Expired clarification") : tr("待确认问题", "Question to confirm")}</strong><SafeMarkdown text={localText(turn.decision.pending_clarification.question, locale)} />{turn.decision.pending_clarification.options?.length > 0 && <ul>{turn.decision.pending_clarification.options.map((option: any) => <li key={option.id}>{localText(option.label, locale)}</li>)}</ul>}</div>}
        <details className="draft-feedback-technical"><summary>{tr("技术详情", "Technical details")}</summary><div className="draft-feedback-metrics">
          <span>{tr("耗时", "Elapsed")}: {feedbackDuration(timing.elapsed_seconds, locale)}</span>
          <span>{tr("本轮时限", "Turn time limit")}: {feedbackDuration(timing.timeout_seconds, locale)}</span>
          <span>{tr("本轮模型", "Turn model")}: {snapshot?.model || tr("未记录", "Not recorded")}</span>
          <span>{tr("推理强度", "Reasoning effort")}: {feedbackEffort(snapshot?.reasoning_effort, locale)}</span>
          {turn.decision?.agent_id && <span>{tr("当时技术 ID", "Technical ID at this turn")}: <code>{turn.decision.agent_id}</code></span>}
        </div>
        {turn.decision?.authoring_policy?.mode === "isolated_tools" && <p className="draft-feedback-tool-policy">{tr("本轮使用隔离编写工具策略：预检通过后可查看源码、编辑 Agent 包及运行本地测试。已接受本机回环可达限制；这不表示网络隔离通过，SAP 只读约束与发布审批不变。", "This turn uses the isolated authoring tool policy: source inspection, Agent package editing and local tests require successful preflight. Loopback reachability is an accepted limitation, not a network-isolation pass. SAP read-only constraints and publication approval remain unchanged.")}</p>}
        {turn.decision?.authoring_policy?.mode === "full_access" && <p className="draft-feedback-tool-policy">{tr("本轮使用 SDK full access 调查工作副本和运行命令，但只允许修改 Agent 草稿定义；平台源码变更会被控制器拒绝。工作副本不是安全沙盒，原生命令不受操作系统级隔离。SAP 工具逐次只读授权，Diff 确认和发布审批不变。", "SDK full access allows work-copy investigation and commands, but only Agent draft definitions may change; the controller rejects platform source edits. The copy is not a security sandbox and native commands are not OS-isolated. Per-call read-only SAP authorization, Diff review and publication approval remain unchanged.")}</p>}
        {turn.decision?.harness?.change_set_id && <p>{tr("平台修改已保存为待核验变更集，尚未应用：", "Platform changes were saved for independent verification, not applied: ")}<code>{turn.decision.harness.change_set_id}</code></p>}
        {turn.decision?.harness?.live_testing === "not_performed" && <small>{tr("本轮工具检查不等于 SAP 真机验收。", "Tool checks in this turn are not SAP live acceptance.")}</small>}
        {toolEvents.length > 0 && <ul className="draft-tool-diagnostics">{toolEvents.map((event: any, i: number) => <li key={i}><code>{event.tool}</code> · {event.status}{event.code && <> · <code>{event.code}</code></>}</li>)}</ul>}
        {liveDiagnostics.length > 0 && <ul className="draft-tool-diagnostics">{liveDiagnostics.map((event: any) => <li key={event.sequence}><code>{event.payload?.tool || event.payload?.kind}</code> · {event.payload?.status || event.kind}</li>)}</ul>}
        {pending && timing.deadline_at && <p className="draft-feedback-deadline">{tr("截止时间", "Deadline")}: <time dateTime={timing.deadline_at}>{new Date(timing.deadline_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")}</time></p>}</details>
        {reply ? <SafeMarkdown text={reply} /> : <p>{turn.status === "waiting" ? tr("消息已排队，等待当前回合结束。", "Queued behind the current turn.") : turn.status === "needs_review" ? tr("前一回合改变了状态，请核对后重新发送。", "The previous turn changed the state. Review and resend.") : pending ? tr("正在处理，请稍候。", "Processing. Please wait.") : failed ? feedbackFailureText(failureCode, locale) : tr("该轮已结束，未记录助手回复。", "This turn ended without a recorded assistant reply.")}</p>}
        {failed && reply && <p className="draft-feedback-failure">{feedbackFailureText(failureCode, locale)}</p>}
        {validationIssues.length > 0 && <div className="draft-feedback-validation"><h4>{tr("定义校验未通过", "Definition validation failed")}</h4><ul>{validationIssues.map((issue, issueIndex) => <li key={issueIndex}>{issue.text}<code>{issue.path}</code></li>)}</ul></div>}
        {timing.deadline_reached && <p role="status">{tr("已到记录的截止时间，正在等待服务确认状态；请勿重复发送。", "The recorded deadline has passed. Waiting for the server to confirm the status; do not send a duplicate request.")}</p>}
        {canRetryFeedback(turn) && onRetry && <button type="button" className="agent-secondary-action" disabled={retryDisabled} onClick={() => onRetry(turn)}>{tr("重试这条意见", "Retry this feedback")}</button>}
        {turn.status === "waiting" && onWithdraw && <button type="button" className="agent-secondary-action" onClick={() => onWithdraw(turn)}>{tr("撤回排队消息", "Withdraw queued message")}</button>}
        {turn.status === "needs_review" && onRetry && <button type="button" className="agent-secondary-action" disabled={retryDisabled} onClick={() => onRetry(turn)}>{tr("核对并重新发送", "Review and resend")}</button>}
        {Array.isArray(turn.diff) && turn.diff.length > 0 && <details className="draft-turn-diff"><summary>{tr("查看本轮修改", "View changes in this turn")} · {tr("修订", "revision")} {turn.base_revision} → {turn.result_revision}</summary><ul>{turn.diff.map((change: any, changeIndex: number) => <li key={changeIndex}><code>{change.path}</code><pre>{JSON.stringify({ before: change.before, after: change.after }, null, 2)}</pre></li>)}</ul></details>}
      </>}
      {turn.result_revision != null && <small>{locale === "zh" ? "已保存修订" : "Saved revision"} {turn.result_revision}</small>}
    </article>;
  })}</div>;
}
